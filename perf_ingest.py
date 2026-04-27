"""
perf_ingest.py
──────────────
Universal ingestion client for the performance results stack.
Handles LRE exports, Azure Pipeline job results, and generic
metric payloads (e.g. from Event Hub).

Usage (LRE):
    python perf_ingest.py lre \
        --run-id <lre-run-id> \
        --csv-path results.csv \
        --test-def-id <postgres-uuid> \
        --vusers 100 \
        --build-id $(Build.BuildId)

Usage (generic / Event Hub):
    python perf_ingest.py generic \
        --payload event.json \
        --source event_hub \
        --test-def-id <postgres-uuid>

Requirements:
    pip install influxdb-client psycopg2-binary python-dotenv
"""

import argparse
import csv
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
import psycopg2
from psycopg2.extras import RealDictCursor, Json

load_dotenv()

# ── Config from environment ────────────────────────────────────────────────
INFLUX_URL    = os.getenv("INFLUX_URL",    "http://localhost:8086")
INFLUX_TOKEN  = os.getenv("INFLUX_TOKEN",  "my-super-secret-token")
INFLUX_ORG    = os.getenv("INFLUX_ORG",    "perf-org")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "perf-results")

PG_HOST = os.getenv("POSTGRES_HOST",     "localhost")
PG_PORT = os.getenv("POSTGRES_PORT",     "5432")
PG_DB   = os.getenv("POSTGRES_DB",       "perfdb")
PG_USER = os.getenv("POSTGRES_USER",     "perfuser")
PG_PASS = os.getenv("POSTGRES_PASSWORD", "changeme123")


# ── DB connections ─────────────────────────────────────────────────────────

def get_influx_write_api():
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    return client, client.write_api(write_options=SYNCHRONOUS)


def get_pg_conn():
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB,
        user=PG_USER, password=PG_PASS
    )


# ── Postgres helpers ───────────────────────────────────────────────────────

def create_run(conn, test_def_id: str, source_tool: str, **kwargs) -> str:
    """Insert a test_runs row and return the new UUID."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO test_runs (
                test_definition_id, status, data_quality,
                build_id, pipeline_name, git_branch, git_commit,
                triggered_by, lre_run_id, vuser_count,
                ramp_up_seconds, steady_state_seconds, ramp_down_seconds,
                started_at, completed_at
            ) VALUES (
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s
            ) RETURNING id
        """, (
            test_def_id,
            kwargs.get("status", "completed"),
            kwargs.get("data_quality", "unknown"),
            kwargs.get("build_id"),
            kwargs.get("pipeline_name"),
            kwargs.get("git_branch"),
            kwargs.get("git_commit"),
            kwargs.get("triggered_by", "ci"),
            kwargs.get("lre_run_id"),
            kwargs.get("vuser_count"),
            kwargs.get("ramp_up_seconds"),
            kwargs.get("steady_state_seconds"),
            kwargs.get("ramp_down_seconds"),
            kwargs.get("started_at"),
            kwargs.get("completed_at", datetime.now(timezone.utc)),
        ))
        run_id = str(cur.fetchone()[0])
    conn.commit()
    return run_id


def insert_transaction(conn, run_id: str, txn: dict):
    """Insert one row into transaction_results."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO transaction_results (
                run_id, transaction_name,
                avg_ms, min_ms, max_ms,
                p50_ms, p75_ms, p90_ms, p95_ms, p99_ms, stddev_ms,
                total_hits, hits_per_second,
                error_count, error_rate_pct
            ) VALUES (
                %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s,
                %s, %s
            )
        """, (
            run_id, txn.get("transaction_name"),
            txn.get("avg_ms"),   txn.get("min_ms"),  txn.get("max_ms"),
            txn.get("p50_ms"),   txn.get("p75_ms"),  txn.get("p90_ms"),
            txn.get("p95_ms"),   txn.get("p99_ms"),  txn.get("stddev_ms"),
            txn.get("total_hits"), txn.get("hits_per_second"),
            txn.get("error_count"), txn.get("error_rate_pct"),
        ))
    conn.commit()


def compute_and_store_deltas(conn, run_id: str, test_def_id: str):
    """
    For each transaction in this run, find the most recent previous run
    and compute p95 / avg delta percentages. Updates transaction_results rows.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Get all transactions for this run
        cur.execute("""
            SELECT t.id, t.transaction_name, t.p95_ms, t.avg_ms
            FROM transaction_results t
            WHERE t.run_id = %s
        """, (run_id,))
        current_txns = cur.fetchall()

        for txn in current_txns:
            # Find the same transaction in the most recent completed prior run
            cur.execute("""
                SELECT t.run_id, t.p95_ms, t.avg_ms
                FROM transaction_results t
                JOIN test_runs r ON r.id = t.run_id
                WHERE r.test_definition_id = %s
                  AND r.id != %s
                  AND r.status IN ('completed', 'data_partial')
                  AND t.transaction_name = %s
                  AND t.p95_ms IS NOT NULL
                ORDER BY r.started_at DESC
                LIMIT 1
            """, (test_def_id, run_id, txn["transaction_name"]))
            prev = cur.fetchone()

            if prev and prev["p95_ms"] and txn["p95_ms"]:
                p95_delta = ((float(txn["p95_ms"]) - float(prev["p95_ms"])) /
                             float(prev["p95_ms"])) * 100
                avg_delta = ((float(txn["avg_ms"]) - float(prev["avg_ms"])) /
                             float(prev["avg_ms"])) * 100 if prev["avg_ms"] and txn["avg_ms"] else None

                cur.execute("""
                    UPDATE transaction_results
                    SET prev_run_id = %s,
                        p95_delta_pct = %s,
                        avg_delta_pct = %s
                    WHERE id = %s
                """, (str(prev["run_id"]), round(p95_delta, 2),
                      round(avg_delta, 2) if avg_delta is not None else None,
                      txn["id"]))
    conn.commit()


def evaluate_slas(conn, run_id: str, test_def_id: str):
    """
    Check each transaction result against sla_definitions.
    Updates sla_passed / sla_breach_metrics on transaction_results
    and rolls up to test_runs.sla_passed.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT t.id, t.transaction_name,
                   t.avg_ms, t.p90_ms, t.p95_ms, t.p99_ms,
                   t.error_rate_pct, t.hits_per_second,
                   COALESCE(
                       (SELECT s FROM sla_definitions s
                        WHERE s.test_definition_id = %s
                          AND s.transaction_name = t.transaction_name
                          AND s.active = TRUE LIMIT 1),
                       (SELECT s FROM sla_definitions s
                        WHERE s.test_definition_id = %s
                          AND s.transaction_name = '*'
                          AND s.active = TRUE LIMIT 1)
                   ) AS sla
            FROM transaction_results t
            WHERE t.run_id = %s
        """, (test_def_id, test_def_id, run_id))
        txns = cur.fetchall()

        overall_passed = True
        for txn in txns:
            sla = txn["sla"]
            if not sla:
                continue
            breaches = []
            if sla["max_avg_ms"] and txn["avg_ms"] and float(txn["avg_ms"]) > float(sla["max_avg_ms"]):
                breaches.append("avg_ms")
            if sla["max_p90_ms"] and txn["p90_ms"] and float(txn["p90_ms"]) > float(sla["max_p90_ms"]):
                breaches.append("p90_ms")
            if sla["max_p95_ms"] and txn["p95_ms"] and float(txn["p95_ms"]) > float(sla["max_p95_ms"]):
                breaches.append("p95_ms")
            if sla["max_p99_ms"] and txn["p99_ms"] and float(txn["p99_ms"]) > float(sla["max_p99_ms"]):
                breaches.append("p99_ms")
            if sla["max_error_rate_pct"] and txn["error_rate_pct"] and \
               float(txn["error_rate_pct"]) > float(sla["max_error_rate_pct"]):
                breaches.append("error_rate_pct")

            passed = len(breaches) == 0
            if not passed and sla.get("severity", "hard") == "hard":
                overall_passed = False

            cur.execute("""
                UPDATE transaction_results
                SET sla_passed = %s, sla_breach_metrics = %s
                WHERE id = %s
            """, (passed, breaches, txn["id"]))

        # Roll up to run level
        cur.execute("""
            UPDATE test_runs SET sla_passed = %s WHERE id = %s
        """, (overall_passed, run_id))

    conn.commit()
    return overall_passed


# ── InfluxDB writer ────────────────────────────────────────────────────────

def write_to_influx(write_api, run_id: str, test_def_id: str,
                    transactions: list[dict], tags: dict, timestamp: datetime):
    """
    Write transaction metrics to InfluxDB.
    Measurement: transaction_metrics
    Tags:        run_id, test_def_id, transaction_name, environment, application, source_tool
    Fields:      all numeric metrics
    """
    points = []
    for txn in transactions:
        p = (
            Point("transaction_metrics")
            .tag("run_id",          run_id)
            .tag("test_def_id",     test_def_id)
            .tag("transaction_name", txn.get("transaction_name", "unknown"))
            .tag("environment",     tags.get("environment", "unknown"))
            .tag("application",     tags.get("application", "unknown"))
            .tag("source_tool",     tags.get("source_tool", "unknown"))
        )
        # Write all numeric fields that are present
        for field in ("avg_ms", "min_ms", "max_ms", "p50_ms", "p75_ms",
                      "p90_ms", "p95_ms", "p99_ms", "stddev_ms",
                      "total_hits", "hits_per_second", "error_count", "error_rate_pct"):
            val = txn.get(field)
            if val is not None:
                p = p.field(field, float(val))

        p = p.time(timestamp, WritePrecision.SECONDS)
        points.append(p)

    write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=points)
    print(f"  ✓ Wrote {len(points)} transaction points to InfluxDB")


# ── LRE CSV parser ─────────────────────────────────────────────────────────

def parse_lre_csv(csv_path: str) -> tuple[list[dict], str]:
    """
    Parse a LoadRunner/LRE exported CSV results file.
    Returns (list of transaction dicts, data_quality flag).

    LRE CSV export columns vary by version. This parser handles
    the most common format. Adjust column names to match your LRE export.

    Expected columns (case-insensitive, partial match):
        Transaction Name, Avg Response Time, Min, Max,
        Percentile 90, Percentile 95, Percentile 99,
        Total Hits, Hits/Second, Error Count, Error %
    """
    transactions = []
    data_quality = "good"

    # Column name normalisation map — extend this if your LRE version differs
    COL_MAP = {
        "transaction name":  "transaction_name",
        "transaction":       "transaction_name",
        "avg response time": "avg_ms",
        "average":           "avg_ms",
        "minimum":           "min_ms",
        "min":               "min_ms",
        "maximum":           "max_ms",
        "max":               "max_ms",
        "percentile 50":     "p50_ms",
        "percentile 75":     "p75_ms",
        "percentile 90":     "p90_ms",
        "90th percentile":   "p90_ms",
        "percentile 95":     "p95_ms",
        "95th percentile":   "p95_ms",
        "percentile 99":     "p99_ms",
        "total hits":        "total_hits",
        "hits":              "total_hits",
        "hits/second":       "hits_per_second",
        "throughput":        "hits_per_second",
        "error count":       "error_count",
        "errors":            "error_count",
        "error %":           "error_rate_pct",
        "error rate":        "error_rate_pct",
        "std. deviation":    "stddev_ms",
        "std deviation":     "stddev_ms",
    }

    try:
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                print("  ⚠ CSV appears empty — flagging as partial")
                return [], "partial"

            # Normalise header names
            norm_headers = {h.lower().strip(): h for h in reader.fieldnames}

            for row in reader:
                txn = {}
                for norm_key, field in COL_MAP.items():
                    # Find the original header that matches this normalised key
                    orig_header = norm_headers.get(norm_key)
                    if orig_header and row.get(orig_header) not in (None, "", "N/A", "-"):
                        try:
                            raw = row[orig_header].strip().replace(",", "").replace("%", "")
                            txn[field] = float(raw) if "." in raw or field not in ("total_hits", "error_count") \
                                         else int(raw)
                        except ValueError:
                            pass  # skip unparseable values

                if "transaction_name" not in txn:
                    continue  # skip header-only or malformed rows

                # LRE times are sometimes in seconds — convert to ms if < 1000 avg
                for ms_field in ("avg_ms", "min_ms", "max_ms", "p90_ms", "p95_ms", "p99_ms"):
                    if txn.get(ms_field) is not None and txn[ms_field] < 100:
                        txn[ms_field] = txn[ms_field] * 1000  # convert s → ms

                transactions.append(txn)

        if len(transactions) == 0:
            data_quality = "partial"
            print("  ⚠ Parsed 0 transactions from CSV — check column format")
        elif len(transactions) < 3:
            data_quality = "partial"
            print(f"  ⚠ Only {len(transactions)} transactions parsed — may be incomplete")
        else:
            print(f"  ✓ Parsed {len(transactions)} transactions from CSV")

    except FileNotFoundError:
        print(f"  ✗ CSV not found: {csv_path}")
        return [], "partial"
    except Exception as e:
        print(f"  ✗ CSV parse error: {e}")
        return [], "partial"

    return transactions, data_quality


# ── Sub-commands ───────────────────────────────────────────────────────────

def cmd_lre(args):
    """Ingest a completed LRE run from a CSV export."""
    print(f"\n── LRE ingest: run {args.lre_run_id} ──")

    transactions, data_quality = parse_lre_csv(args.csv_path)

    if not transactions:
        print("  ✗ No usable transaction data. Check export. Storing stub run with data_missing status.")
        data_quality = "partial"

    conn = get_pg_conn()
    influx_client, write_api = get_influx_write_api()

    try:
        # Look up test definition to get tags
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM test_definitions WHERE id = %s", (args.test_def_id,))
            test_def = cur.fetchone()
            if not test_def:
                print(f"  ✗ test_definition_id {args.test_def_id} not found in Postgres")
                sys.exit(1)

        # Create the run row
        run_id = create_run(
            conn, args.test_def_id, "loadrunner",
            status="completed" if transactions else "data_missing",
            data_quality=data_quality,
            lre_run_id=args.lre_run_id,
            lre_mdb_generated=not args.no_mdb,
            build_id=args.build_id,
            pipeline_name=args.pipeline_name,
            git_branch=args.git_branch,
            git_commit=args.git_commit,
            triggered_by=args.triggered_by,
            vuser_count=args.vusers,
            ramp_up_seconds=args.ramp_up,
            steady_state_seconds=args.steady_state,
            started_at=datetime.fromisoformat(args.started_at) if args.started_at else None,
        )
        print(f"  ✓ Created run: {run_id}")

        if transactions:
            # Write to Postgres
            for txn in transactions:
                insert_transaction(conn, run_id, txn)
            print(f"  ✓ Stored {len(transactions)} transactions in Postgres")

            # Write to InfluxDB
            tags = {
                "environment": test_def["environment"],
                "application":  test_def["application"],
                "source_tool":  "loadrunner",
            }
            ts = datetime.now(timezone.utc)
            write_to_influx(write_api, run_id, args.test_def_id, transactions, tags, ts)

            # Compute deltas and evaluate SLAs
            compute_and_store_deltas(conn, run_id, args.test_def_id)
            sla_passed = evaluate_slas(conn, run_id, args.test_def_id)
            print(f"  {'✓ SLA PASSED' if sla_passed else '✗ SLA FAILED'}")

            # Exit non-zero if SLA failed and hard gate is requested
            if not sla_passed and args.fail_on_sla:
                print("\n  Pipeline gate: failing build due to SLA breach.")
                sys.exit(1)

        print(f"\n  Run ID (for Confluence reporter): {run_id}")

    finally:
        conn.close()
        influx_client.close()


def cmd_generic(args):
    """Ingest a generic JSON payload (Azure Pipeline, Event Hub, etc.)."""
    print(f"\n── Generic ingest: source={args.source} ──")

    with open(args.payload) as f:
        payload = json.load(f)

    conn = get_pg_conn()
    influx_client, write_api = get_influx_write_api()

    try:
        # Log raw event
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO ingest_events (source, event_type, raw_payload)
                VALUES (%s, %s, %s) RETURNING id
            """, (args.source, payload.get("event_type", "unknown"), Json(payload)))
            event_id = cur.fetchone()[0]
        conn.commit()
        print(f"  ✓ Logged ingest event: {event_id}")

        # Expect payload to contain a 'transactions' list and run metadata
        # Adjust this mapping to match your Event Hub schema
        transactions = payload.get("transactions", [])
        meta = payload.get("run", {})

        if not transactions:
            print("  ⚠ No transactions in payload — event logged but no run created")
            return

        run_id = create_run(
            conn, args.test_def_id, args.source,
            status="completed",
            data_quality="good",
            build_id=meta.get("build_id"),
            pipeline_name=meta.get("pipeline_name"),
            git_branch=meta.get("git_branch"),
            triggered_by=args.source,
            vuser_count=meta.get("vuser_count"),
        )

        for txn in transactions:
            insert_transaction(conn, run_id, txn)
        print(f"  ✓ Stored {len(transactions)} transactions (run: {run_id})")

        # Link event to run
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE ingest_events SET run_id = %s, processed = TRUE, processed_at = NOW()
                WHERE id = %s
            """, (run_id, event_id))
        conn.commit()

        # Look up tags from test definition
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM test_definitions WHERE id = %s", (args.test_def_id,))
            test_def = cur.fetchone()

        tags = {
            "environment": test_def["environment"] if test_def else "unknown",
            "application":  test_def["application"] if test_def else "unknown",
            "source_tool":  args.source,
        }
        write_to_influx(write_api, run_id, args.test_def_id, transactions,
                        tags, datetime.now(timezone.utc))

        compute_and_store_deltas(conn, run_id, args.test_def_id)
        evaluate_slas(conn, run_id, args.test_def_id)
        print(f"\n  Run ID: {run_id}")

    finally:
        conn.close()
        influx_client.close()


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Perf results ingest client")
    sub = parser.add_subparsers(dest="command", required=True)

    # ── lre sub-command
    lre = sub.add_parser("lre", help="Ingest LRE CSV export")
    lre.add_argument("--csv-path",      required=True)
    lre.add_argument("--test-def-id",   required=True)
    lre.add_argument("--lre-run-id",    default="unknown")
    lre.add_argument("--vusers",        type=int)
    lre.add_argument("--ramp-up",       type=int, default=0)
    lre.add_argument("--steady-state",  type=int, default=0)
    lre.add_argument("--build-id")
    lre.add_argument("--pipeline-name")
    lre.add_argument("--git-branch")
    lre.add_argument("--git-commit")
    lre.add_argument("--started-at",    help="ISO datetime")
    lre.add_argument("--triggered-by",  default="ci")
    lre.add_argument("--no-mdb",        action="store_true",
                     help="Flag that MDB was not confirmed generated")
    lre.add_argument("--fail-on-sla",   action="store_true",
                     help="Exit 1 if any hard SLA is breached (pipeline gate)")
    lre.set_defaults(func=cmd_lre)

    # ── generic sub-command
    gen = sub.add_parser("generic", help="Ingest JSON payload (Event Hub, pipeline webhook, etc.)")
    gen.add_argument("--payload",     required=True, help="Path to JSON payload file")
    gen.add_argument("--test-def-id", required=True)
    gen.add_argument("--source",      default="event_hub",
                     help="Source identifier: event_hub, azure_pipeline, manual_api, etc.")
    gen.set_defaults(func=cmd_generic)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
