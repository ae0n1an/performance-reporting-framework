"""
Migration runner.

Conventions:
  - Migration files live in ./migrations/
  - Named: V001__description.sql, V002__description.sql, ...
  - Applied migrations are recorded in the schema_migrations table
  - Migrations are run in version order and are idempotent (skipped if already applied)

Usage:
  python -m app.db.migrate            # apply all pending
  python -m app.db.migrate --status   # show applied / pending
"""

import hashlib
import os
import re
import sys
from pathlib import Path
from typing import TypedDict

import psycopg
from psycopg.rows import DictRow, dict_row

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"
MIGRATION_PATTERN = re.compile(r"^V(\d+)__(.+)\.sql$")


class MigrationFile(TypedDict):
    version: int
    name: str
    filename: str
    path: Path


def get_conn(dsn: str) -> psycopg.Connection[DictRow]:
    return psycopg.connect(dsn, row_factory=dict_row)


def ensure_migrations_table(conn: psycopg.Connection[DictRow]) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            name        TEXT    NOT NULL,
            checksum    TEXT    NOT NULL,
            applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.commit()


def load_migration_files() -> list[MigrationFile]:
    files: list[MigrationFile] = []
    for f in sorted(MIGRATIONS_DIR.glob("V*.sql")):
        m = MIGRATION_PATTERN.match(f.name)
        if m:
            files.append(MigrationFile(
                version=int(m.group(1)),
                name=m.group(2),
                filename=f.name,
                path=f,
            ))
    return files


def checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()[:16]


def get_applied(conn: psycopg.Connection[DictRow]) -> dict[int, str]:
    rows = conn.execute(
        "SELECT version, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    return {int(r["version"]): str(r["checksum"]) for r in rows}


def apply(dsn: str) -> None:
    files = load_migration_files()
    if not files:
        print("No migration files found.")
        return

    with get_conn(dsn) as conn:
        ensure_migrations_table(conn)
        applied = get_applied(conn)

        pending = [f for f in files if f["version"] not in applied]
        if not pending:
            print("Nothing to migrate — all up to date.")
            return

        for mig in pending:
            sql = mig["path"].read_text()
            cs = checksum(sql)
            print(f"  Applying V{mig['version']:03d}__{mig['name']}...", end=" ")
            try:
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                    (mig["version"], mig["name"], cs),
                )
                conn.commit()
                print("ok")
            except Exception as e:
                conn.rollback()
                print(f"FAILED\n  → {e}")
                sys.exit(1)

        print(f"\n{len(pending)} migration(s) applied.")


def status(dsn: str) -> None:
    files = load_migration_files()
    with get_conn(dsn) as conn:
        ensure_migrations_table(conn)
        applied = get_applied(conn)

    print(f"{'Ver':<6} {'Status':<10} {'Name'}")
    print("-" * 50)
    for f in files:
        state = "applied" if f["version"] in applied else "pending"
        print(f"  V{f['version']:<4} {state:<10} {f['name']}")

    pending_count = sum(1 for f in files if f["version"] not in applied)
    print(f"\n{len(applied)} applied, {pending_count} pending.")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    dsn = os.environ["DATABASE_URL"]

    if "--status" in sys.argv:
        status(dsn)
    else:
        apply(dsn)
