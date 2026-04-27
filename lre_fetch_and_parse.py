"""
lre_fetch_and_parse.py
──────────────────────
Fetches the ANALYZED RESULT zip from the LRE REST API for a completed run,
extracts the SQLite results DB (CE 2023+) or MDB (older), and computes
full transaction statistics including percentiles — without needing
LoadRunner Analysis installed anywhere.

Flow:
    1. Authenticate to LRE (LWSSO cookie)
    2. List run result files → find ANALYZED RESULT zip
    3. Download and unzip → locate .db (SQLite) or .mdb file
    4. Query Event_meter + Event_map → raw per-transaction durations
    5. Compute min/avg/max/p50/p75/p90/p95/p99/stddev/error rate in Python
    6. Write results to CSV (ready for perf_ingest.py)

Usage:
    python lre_fetch_and_parse.py --run-id 42 --output results.csv

    # Skip API download and parse a local zip directly (useful for testing):
    python lre_fetch_and_parse.py --run-id 42 --skip-download Results_42.zip

Requirements:
    pip install requests numpy python-dotenv
    # mdbtools only needed for old MDB fallback:
    # apt-get install mdbtools   OR   brew install mdbtools

CE 25.x note:
    LRE CE 2023+ writes a SQLite .db file inside Results_<ID>.zip by default.
    Check Controller -> Tools -> Options -> Database to confirm your setting.
    This script handles both SQLite and MDB automatically.
"""

import argparse
import csv
import io
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import requests
from dotenv import load_dotenv

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────
LRE_HOST     = os.getenv("LRE_HOST",     "https://your-lre-server")
LRE_USER     = os.getenv("LRE_USER",     "admin")
LRE_PASSWORD = os.getenv("LRE_PASSWORD", "changeme")
LRE_DOMAIN   = os.getenv("LRE_DOMAIN",   "DEFAULT")
LRE_PROJECT  = os.getenv("LRE_PROJECT",  "MyProject")

# How long to wait for the ANALYZED RESULT to appear after run completion.
# LRE generates this asynchronously — can take several minutes for large runs.
RESULT_WAIT_SECONDS  = int(os.getenv("LRE_RESULT_WAIT_SECONDS",  "180"))
RESULT_POLL_INTERVAL = int(os.getenv("LRE_RESULT_POLL_INTERVAL", "20"))


# ── LRE Client ─────────────────────────────────────────────────────────────

class LREClient:
    """Thin wrapper around the LRE REST API with session cookie management."""

    def __init__(self):
        self.session = requests.Session()
        self.base = f"{LRE_HOST.rstrip('/')}/LoadTest/rest"
        self.project_base = (
            f"{self.base}/domains/{LRE_DOMAIN}/projects/{LRE_PROJECT}"
        )
        self._authenticated = False

    def authenticate(self):
        """
        POST to the LRE authentication endpoint.
        CE 25.1+ note: the cookie path is case-sensitive. Always use
        /LoadTest (capital L, capital T) as the base path — a different
        case will cause subsequent requests to return 403.
        """
        url = f"{self.base}/server/login"
        resp = self.session.post(
            url,
            data={"login": LRE_USER, "password": LRE_PASSWORD},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            verify=False,  # set verify=True in prod with a proper cert
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"LRE authentication failed: {resp.status_code}\n{resp.text[:300]}"
            )
        print(f"  Authenticated to LRE as {LRE_USER}")
        self._authenticated = True

    def _get(self, path: str, **kwargs) -> requests.Response:
        if not self._authenticated:
            self.authenticate()
        url = f"{self.project_base}{path}"
        resp = self.session.get(url, verify=False, **kwargs)
        resp.raise_for_status()
        return resp

    def list_run_results(self, run_id: int) -> list[dict]:
        """
        GET /Runs/{ID}/Results
        Returns descriptors for all result files available for this run:
            [{"id": "1057", "name": "Results_13.zip", "type": "ANALYZED RESULT"}, ...]
        """
        resp = self._get(f"/Runs/{run_id}/Results")
        root = ET.fromstring(resp.text)
        # Namespace handling — LRE XML may or may not include the HP namespace
        ns = {"pc": "http://www.hp.com/PC/REST/API"}

        results = []
        for rr in (root.findall(".//RunResult", ns) or
                   root.findall(".//RunResult")):
            def text(tag):
                return (rr.findtext(tag, default="") or
                        rr.findtext(f"pc:{tag}", default="", namespaces=ns) or "")
            results.append({"id": text("ID"), "name": text("Name"), "type": text("Type")})
        return results

    def download_result(self, run_id: int, result_id: str, dest_dir: Path) -> Path:
        """
        GET /Runs/{ID}/Results/{ResultID}/data
        Streams the zip to dest_dir. Returns the local file path.
        """
        print(f"  Downloading result ID {result_id} for run {run_id}...")
        resp = self._get(
            f"/Runs/{run_id}/Results/{result_id}/data",
            stream=True,
        )
        cd = resp.headers.get("Content-Disposition", "")
        fname_match = re.search(r'filename="?([^";\s]+)"?', cd)
        filename = fname_match.group(1) if fname_match else f"Results_{run_id}.zip"
        out_path = dest_dir / filename

        total_bytes = 0
        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    total_bytes += len(chunk)

        print(f"  Downloaded {filename} ({total_bytes / 1024 / 1024:.1f} MB)")
        return out_path

    def logout(self):
        try:
            self.session.get(f"{self.base}/server/logout", verify=False)
        except Exception:
            pass


# ── Wait for ANALYZED RESULT ────────────────────────────────────────────────

def wait_for_analyzed_result(client: LREClient, run_id: int) -> dict:
    """
    Poll /Runs/{ID}/Results until ANALYZED RESULT appears.
    LRE generates this asynchronously post-run — can take minutes for
    large tests. Raises TimeoutError if not ready within RESULT_WAIT_SECONDS.
    """
    deadline = time.time() + RESULT_WAIT_SECONDS
    attempt  = 0

    while time.time() < deadline:
        attempt += 1
        try:
            results = client.list_run_results(run_id)
        except Exception as e:
            print(f"  Result list fetch failed (attempt {attempt}): {e}")
            time.sleep(RESULT_POLL_INTERVAL)
            continue

        if attempt == 1:
            types = [r["type"] for r in results]
            print(f"  Available result types: {types or '(none yet)'}")

        analyzed = next(
            (r for r in results if "ANALYZED" in r["type"].upper()), None
        )
        if analyzed:
            print(f"  ANALYZED RESULT ready: {analyzed['name']}")
            return analyzed

        remaining = int(deadline - time.time())
        print(f"  Not ready yet — retrying in {RESULT_POLL_INTERVAL}s "
              f"(timeout in {remaining}s)...")
        time.sleep(RESULT_POLL_INTERVAL)

    raise TimeoutError(
        f"ANALYZED RESULT did not appear within {RESULT_WAIT_SECONDS}s "
        f"for run {run_id}. The analysis step in LRE may have failed."
    )


# ── Extract DB from zip ─────────────────────────────────────────────────────

def extract_db_from_zip(zip_path: Path, extract_dir: Path) -> tuple[Path, str]:
    """
    Unzip the ANALYZED RESULT archive and locate the results database.
    Returns (db_path, db_type) where db_type is 'sqlite' or 'mdb'.

    CE 2023+ produces a .db (SQLite) file named after the run.
    Older versions produce a .mdb (MS Access) file.
    Some versions double-wrap in a nested zip — handled here.

    The SqliteDb.db file is error messages only — excluded.
    """
    print(f"  Extracting {zip_path.name}...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)
        names = zf.namelist()
    print(f"  Archive contents ({len(names)} files): "
          f"{names[:8]}{'...' if len(names) > 8 else ''}")

    def find_dbs(search_dir: Path):
        db_files  = [f for f in search_dir.rglob("*.db")
                     if "sqlitedb" not in f.name.lower()
                     and "error"   not in f.name.lower()]
        mdb_files = list(search_dir.rglob("*.mdb"))
        lra_files = list(search_dir.rglob("*.lra"))
        return db_files, mdb_files, lra_files

    db_files, mdb_files, lra_files = find_dbs(extract_dir)

    # Handle nested zips (some LRE versions double-wrap the results)
    for nested in extract_dir.rglob("*.zip"):
        nested_dir = extract_dir / nested.stem
        nested_dir.mkdir(exist_ok=True)
        try:
            with zipfile.ZipFile(nested, "r") as zf:
                zf.extractall(nested_dir)
            nd, nm, nl = find_dbs(nested_dir)
            db_files  += nd
            mdb_files += nm
            lra_files += nl
        except Exception:
            pass

    # Prefer SQLite; use largest file as the results DB
    if db_files:
        db_path = max(db_files, key=lambda f: f.stat().st_size)
        print(f"  Found SQLite DB: {db_path.name} "
              f"({db_path.stat().st_size / 1024:.0f} KB)")
        return db_path, "sqlite"

    if lra_files:
        # .lra is also SQLite in modern LR versions
        db_path = max(lra_files, key=lambda f: f.stat().st_size)
        print(f"  Found LRA (SQLite): {db_path.name}")
        return db_path, "sqlite"

    if mdb_files:
        db_path = max(mdb_files, key=lambda f: f.stat().st_size)
        print(f"  Found MDB: {db_path.name} "
              f"({db_path.stat().st_size / 1024:.0f} KB)")
        return db_path, "mdb"

    raise FileNotFoundError(
        f"No results database (.db, .lra, or .mdb) found in {zip_path}.\n"
        "The ANALYZED RESULT may be incomplete — check LRE Analysis logs.\n"
        f"Files found in archive: {names}"
    )


# ── SQLite parser ───────────────────────────────────────────────────────────

# Primary query — uses TransactionEndStatus table for readable status names
_Q_FULL = """
    SELECT
        em."Event Name"                         AS txn_name,
        e.Value                                 AS duration_sec,
        tes."Transaction End Status"            AS status
    FROM  Event_meter e
    JOIN  Event_map em   ON e."Event ID"  = em."Event ID"
    JOIN  TransactionEndStatus tes ON e.Status1 = tes.Status1
    WHERE em."Event Type" = 'Transaction'
"""

# Fallback — works if TransactionEndStatus table is absent (schema varies)
_Q_SIMPLE = """
    SELECT
        em."Event Name"  AS txn_name,
        e.Value          AS duration_sec,
        e.Status1        AS status_code
    FROM  Event_meter e
    JOIN  Event_map em ON e."Event ID" = em."Event ID"
    WHERE em."Event Type" = 'Transaction'
"""


def parse_sqlite(db_path: Path) -> list[dict]:
    """
    Read raw per-sample transaction data from the SQLite results DB
    and compute aggregated statistics per transaction name.
    Percentiles are computed with numpy — no SQLite extension needed.
    """
    print(f"  Querying SQLite: {db_path.name}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    print(f"  Tables: {tables}")

    for req in ("Event_meter", "Event_map"):
        if req not in tables:
            conn.close()
            raise ValueError(
                f"Required table '{req}' missing. Available: {tables}\n"
                "This may be a partial or corrupt results DB. "
                "Check LRE Analysis completed successfully."
            )

    # Try full query first (better status labels)
    use_status_name = True
    try:
        rows = conn.execute(_Q_FULL).fetchall()
    except sqlite3.OperationalError as e:
        print(f"  Full query failed ({e}), using simplified query")
        rows = conn.execute(_Q_SIMPLE).fetchall()
        use_status_name = False

    conn.close()

    if not rows:
        # Diagnostic: check what Event Types actually exist
        conn2 = sqlite3.connect(str(db_path))
        types = conn2.execute(
            'SELECT DISTINCT "Event Type", COUNT(*) FROM Event_map GROUP BY "Event Type"'
        ).fetchall()
        conn2.close()
        raise ValueError(
            f"Query returned 0 rows — no 'Transaction' events found.\n"
            f"Event Types in this DB: {dict(types)}\n"
            "Check that your LRE test actually executed transactions "
            "and that the Event Type name matches exactly."
        )

    print(f"  Raw samples: {len(rows):,}")

    # Group raw samples by transaction name
    groups: dict[str, dict] = defaultdict(lambda: {
        "pass_durations": [],
        "fail_count":     0,
        "total_count":    0,
    })

    for row in rows:
        name = row["txn_name"]
        dur  = row["duration_sec"]
        groups[name]["total_count"] += 1

        if use_status_name:
            is_pass = str(row["status"]).strip().lower() == "pass"
        else:
            is_pass = int(row["status_code"] or 1) == 0  # 0 = Pass in LRE

        if is_pass and dur is not None and float(dur) > 0:
            groups[name]["pass_durations"].append(float(dur))
        else:
            groups[name]["fail_count"] += 1

    # Compute statistics per transaction
    transactions = []
    for txn_name, g in sorted(groups.items()):
        total = g["total_count"]
        fails = g["fail_count"]
        durs  = g["pass_durations"]

        if not durs:
            transactions.append({
                "transaction_name": txn_name,
                "total_hits":       total,
                "error_count":      fails,
                "error_rate_pct":   100.0 if total > 0 else 0.0,
            })
            continue

        # LRE stores durations in seconds — convert to milliseconds
        arr = np.array(durs) * 1000.0

        transactions.append({
            "transaction_name": txn_name,
            "avg_ms":           round(float(np.mean(arr)),              2),
            "min_ms":           round(float(np.min(arr)),               2),
            "max_ms":           round(float(np.max(arr)),               2),
            "p50_ms":           round(float(np.percentile(arr, 50)),    2),
            "p75_ms":           round(float(np.percentile(arr, 75)),    2),
            "p90_ms":           round(float(np.percentile(arr, 90)),    2),
            "p95_ms":           round(float(np.percentile(arr, 95)),    2),
            "p99_ms":           round(float(np.percentile(arr, 99)),    2),
            "stddev_ms":        round(float(np.std(arr)),               2),
            "total_hits":       total,
            "error_count":      fails,
            "error_rate_pct":   round((fails / total) * 100, 3) if total else 0.0,
            "hits_per_second":  None,  # requires test duration; set by pipeline
        })

    print(f"  Computed stats for {len(transactions)} transactions")
    return transactions


# ── MDB parser via mdbtools (Linux fallback for older LRE) ─────────────────

def parse_mdb(db_path: Path) -> list[dict]:
    """
    Parse a legacy .mdb file using mdbtools.
    Install: apt-get install mdbtools  OR  brew install mdbtools
    """
    if not shutil.which("mdb-export"):
        raise RuntimeError(
            "mdbtools not installed. Run: apt-get install mdbtools\n"
            "Or on macOS: brew install mdbtools\n"
            "Windows alternative: use pyodbc with the Access ODBC driver."
        )

    print(f"  Parsing MDB via mdbtools: {db_path.name}")

    def export_table(name: str) -> list[dict]:
        out = subprocess.run(
            ["mdb-export", str(db_path), name],
            capture_output=True, text=True, check=True
        )
        return list(csv.DictReader(io.StringIO(out.stdout)))

    event_map   = {r["Event ID"]: r for r in export_table("Event_map")}
    event_meter = export_table("Event_meter")

    groups: dict[str, dict] = defaultdict(lambda: {
        "pass_durations": [], "fail_count": 0, "total_count": 0
    })

    for row in event_meter:
        eid = row.get("Event ID", "")
        em  = event_map.get(eid, {})
        if em.get("Event Type", "").strip() != "Transaction":
            continue

        name = em.get("Event Name", "unknown")
        groups[name]["total_count"] += 1

        try:
            dur     = float(row.get("Value", 0))
            is_pass = str(row.get("Status1", "1")) == "0"
        except (ValueError, TypeError):
            groups[name]["fail_count"] += 1
            continue

        if is_pass and dur > 0:
            groups[name]["pass_durations"].append(dur)
        else:
            groups[name]["fail_count"] += 1

    transactions = []
    for txn_name, g in sorted(groups.items()):
        total = g["total_count"]
        fails = g["fail_count"]
        durs  = g["pass_durations"]

        if not durs:
            transactions.append({
                "transaction_name": txn_name,
                "total_hits":       total,
                "error_count":      fails,
                "error_rate_pct":   100.0,
            })
            continue

        arr = np.array(durs) * 1000.0
        transactions.append({
            "transaction_name": txn_name,
            "avg_ms":           round(float(np.mean(arr)),           2),
            "min_ms":           round(float(np.min(arr)),            2),
            "max_ms":           round(float(np.max(arr)),            2),
            "p50_ms":           round(float(np.percentile(arr, 50)), 2),
            "p75_ms":           round(float(np.percentile(arr, 75)), 2),
            "p90_ms":           round(float(np.percentile(arr, 90)), 2),
            "p95_ms":           round(float(np.percentile(arr, 95)), 2),
            "p99_ms":           round(float(np.percentile(arr, 99)), 2),
            "stddev_ms":        round(float(np.std(arr)),            2),
            "total_hits":       total,
            "error_count":      fails,
            "error_rate_pct":   round((fails / total) * 100, 3) if total else 0.0,
            "hits_per_second":  None,
        })

    print(f"  Computed stats for {len(transactions)} transactions (mdbtools)")
    return transactions


# ── CSV output ──────────────────────────────────────────────────────────────

CSV_FIELDS = [
    "transaction_name",
    "avg_ms", "min_ms", "max_ms",
    "p50_ms", "p75_ms", "p90_ms", "p95_ms", "p99_ms", "stddev_ms",
    "total_hits", "hits_per_second", "error_count", "error_rate_pct",
]


def write_csv(transactions: list[dict], output_path: Path):
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(transactions)
    print(f"  Wrote {len(transactions)} rows -> {output_path}")


# ── Entry point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fetch and parse LRE run results (SQLite DB or MDB)"
    )
    parser.add_argument("--run-id",        required=True, type=int)
    parser.add_argument("--output",        default="lre_results.csv")
    parser.add_argument("--work-dir",      default=None,
                        help="Temp dir for downloads (default: system temp)")
    parser.add_argument("--keep-zip",      action="store_true",
                        help="Keep downloaded zip after parsing")
    parser.add_argument("--skip-download", metavar="ZIP_PATH",
                        help="Parse a local zip directly, skip API download")
    args = parser.parse_args()

    output_path = Path(args.output)
    work_dir    = Path(args.work_dir or tempfile.mkdtemp())
    work_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n-- LRE fetch & parse: run {args.run_id} --")
    client = LREClient()

    try:
        if args.skip_download:
            zip_path = Path(args.skip_download)
            print(f"  Using local zip: {zip_path}")
        else:
            client.authenticate()
            analyzed = wait_for_analyzed_result(client, args.run_id)
            zip_path = client.download_result(args.run_id, analyzed["id"], work_dir)

        extract_dir = work_dir / "extracted"
        extract_dir.mkdir(exist_ok=True)
        db_path, db_type = extract_db_from_zip(zip_path, extract_dir)

        transactions = (parse_sqlite(db_path) if db_type == "sqlite"
                        else parse_mdb(db_path))

        if not transactions:
            print("\n  No transactions parsed — check DB file manually.")
            sys.exit(2)

        write_csv(transactions, output_path)

        # Print summary table
        print(f"\n  {'Transaction':<45} {'Avg ms':>8} {'P95 ms':>8} {'Err%':>6} {'Hits':>6}")
        print(f"  {'-'*45} {'-'*8} {'-'*8} {'-'*6} {'-'*6}")
        for t in transactions[:25]:
            print(
                f"  {str(t['transaction_name'])[:45]:<45} "
                f"{t.get('avg_ms', '-'):>8} "
                f"{t.get('p95_ms', '-'):>8} "
                f"{t.get('error_rate_pct', 0):>6.2f} "
                f"{t.get('total_hits', 0):>6}"
            )
        if len(transactions) > 25:
            print(f"  ... and {len(transactions) - 25} more transactions")

        if not args.keep_zip and not args.skip_download:
            zip_path.unlink(missing_ok=True)

        print(f"\n  Done. Pass to perf_ingest.py: --csv-path {output_path}")

    except TimeoutError as e:
        print(f"\n  TIMEOUT: {e}")
        sys.exit(3)
    except FileNotFoundError as e:
        print(f"\n  FILE NOT FOUND: {e}")
        sys.exit(4)
    except Exception as e:
        print(f"\n  ERROR: {e}")
        raise
    finally:
        client.logout()


if __name__ == "__main__":
    main()
