import json

from flask import Blueprint, Response, request

from app.db import get_conn
from app.utils import created, error, get_page_params, not_found, ok, paginated

bp = Blueprint("test_runs", __name__)

VALID_FINISH_STATUSES = ("passed", "failed", "aborted")


@bp.get("/")
def list_runs() -> tuple[Response, int]:
    page, per_page, offset = get_page_params()
    test_id = request.args.get("test_id")
    status = request.args.get("status")

    where_parts: list[str] = []
    params: list[object] = []
    if test_id:
        where_parts.append("test_id = %s")
        params.append(test_id)
    if status:
        where_parts.append("status = %s")
        params.append(status)

    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    with get_conn() as conn:
        count_row = conn.execute(
            f"SELECT COUNT(*) AS n FROM test_runs {where}", params
        ).fetchone()
        assert count_row is not None
        total = int(count_row["n"])
        rows = conn.execute(
            f"SELECT * FROM test_runs {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            params + [per_page, offset],
        ).fetchall()

    return ok(paginated([dict(r) for r in rows], total, page, per_page))


@bp.post("/")
def create_run() -> tuple[Response, int]:
    body: dict[str, object] = request.get_json(silent=True) or {}
    test_id = str(body.get("test_id", "")).strip()
    if not test_id:
        return error("test_id is required")

    metadata_raw = body.get("run_metadata")
    run_metadata = metadata_raw if isinstance(metadata_raw, dict) else {}

    with get_conn() as conn:
        if not conn.execute("SELECT id FROM tests WHERE id = %s", (test_id,)).fetchone():
            return not_found("Test")

        row = conn.execute(
            "INSERT INTO test_runs (test_id, run_metadata) VALUES (%s, %s) RETURNING *",
            (test_id, json.dumps(run_metadata)),
        ).fetchone()
        assert row is not None

    return created(dict(row))


@bp.get("/<run_id>")
def get_run(run_id: str) -> tuple[Response, int]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM test_runs WHERE id = %s", (run_id,)).fetchone()
    if not row:
        return not_found("TestRun")
    return ok(dict(row))


@bp.post("/<run_id>/start")
def start_run(run_id: str) -> tuple[Response, int]:
    with get_conn() as conn:
        row = conn.execute(
            """
            UPDATE test_runs
            SET status = 'running', started_at = NOW()
            WHERE id = %s AND status = 'pending'
            RETURNING *
            """,
            (run_id,),
        ).fetchone()
    if not row:
        existing = _get_run(run_id)
        if not existing:
            return not_found("TestRun")
        return error(f"Run is already {existing['status']}")
    return ok(dict(row))


@bp.post("/<run_id>/finish")
def finish_run(run_id: str) -> tuple[Response, int]:
    body: dict[str, object] = request.get_json(silent=True) or {}
    finish_status = str(body.get("status", "passed"))
    if finish_status not in VALID_FINISH_STATUSES:
        return error(f"status must be one of: {', '.join(VALID_FINISH_STATUSES)}")

    with get_conn() as conn:
        row = conn.execute(
            """
            UPDATE test_runs
            SET status = %s, ended_at = NOW()
            WHERE id = %s AND status = 'running'
            RETURNING *
            """,
            (finish_status, run_id),
        ).fetchone()
    if not row:
        existing = _get_run(run_id)
        if not existing:
            return not_found("TestRun")
        return error(f"Run is not running (current status: {existing['status']})")
    return ok(dict(row))


@bp.delete("/<run_id>")
def delete_run(run_id: str) -> tuple[Response, int]:
    with get_conn() as conn:
        row = conn.execute(
            "DELETE FROM test_runs WHERE id = %s RETURNING id", (run_id,)
        ).fetchone()
    if not row:
        return not_found("TestRun")
    return ok({"deleted": str(row["id"])})


def _get_run(run_id: str) -> dict[str, object] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM test_runs WHERE id = %s", (run_id,)
        ).fetchone()
    return dict(row) if row else None
