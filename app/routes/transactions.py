import json
from flask import Blueprint, request

from app.db import get_conn
from app.utils import ok, created, error, not_found, get_page_params, paginated

bp = Blueprint("transactions", __name__)


@bp.get("/")
def list_transactions():
    page, per_page, offset = get_page_params()
    run_id         = request.args.get("run_id")
    name           = request.args.get("name")
    status         = request.args.get("status")
    kind           = request.args.get("kind")
    correlation_id = request.args.get("correlation_id")

    where_parts, params = [], []
    if run_id:
        where_parts.append("run_id = %s"); params.append(run_id)
    if name:
        where_parts.append("name = %s"); params.append(name)
    if status:
        where_parts.append("status = %s"); params.append(status)
    if kind:
        where_parts.append("kind = %s"); params.append(kind)
    if correlation_id:
        where_parts.append("(start_correlation_id = %s OR end_correlation_id = %s)")
        params.extend([correlation_id, correlation_id])

    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM transactions {where}", params
        ).fetchone()["n"]
        rows = conn.execute(
            f"SELECT * FROM transactions {where} ORDER BY start_time LIMIT %s OFFSET %s",
            params + [per_page, offset]
        ).fetchall()

    return ok(paginated([dict(r) for r in rows], total, page, per_page))


@bp.post("/")
def create_transaction():
    body   = request.get_json() or {}
    run_id = body.get("run_id", "").strip()
    name   = body.get("name", "").strip()
    kind   = body.get("kind", "transaction")

    if not run_id: return error("run_id is required")
    if not name:   return error("name is required")
    if kind not in ("transaction", "message"):
        return error("kind must be 'transaction' or 'message'")
    if not body.get("start_time"):
        return error("start_time is required")

    with get_conn() as conn:
        if not conn.execute("SELECT id FROM test_runs WHERE id = %s", (run_id,)).fetchone():
            return not_found("TestRun")

        row = conn.execute(
            """
            INSERT INTO transactions
              (run_id, kind, name, status, start_time, end_time, duration_ms,
               start_correlation_id, end_correlation_id,
               topic, payload, source, acknowledged_at,
               vuser_id, iteration, error_message, extra)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                run_id, kind, name,
                body.get("status", "pass"),
                body.get("start_time"),
                body.get("end_time"),
                body.get("duration_ms"),
                body.get("start_correlation_id"),
                body.get("end_correlation_id"),
                body.get("topic"),
                json.dumps(body.get("payload", {})),
                body.get("source"),
                body.get("acknowledged_at"),
                body.get("vuser_id"),
                body.get("iteration", 1),
                body.get("error_message"),
                json.dumps(body.get("extra", {})),
            )
        ).fetchone()

    return created(dict(row))


@bp.post("/bulk")
def bulk_create_transactions():
    """Ingest many transactions at once — useful for batch reporting at run end."""
    body   = request.get_json() or {}
    run_id = body.get("run_id", "").strip()
    items  = body.get("transactions", [])

    if not run_id: return error("run_id is required")
    if not items:  return error("transactions list is empty")

    with get_conn() as conn:
        if not conn.execute("SELECT id FROM test_runs WHERE id = %s", (run_id,)).fetchone():
            return not_found("TestRun")

        ids = []
        for i, item in enumerate(items):
            name = item.get("name", "").strip()
            kind = item.get("kind", "transaction")
            if not name:
                return error(f"item[{i}]: name is required")
            if not item.get("start_time"):
                return error(f"item[{i}]: start_time is required")
            if kind not in ("transaction", "message"):
                return error(f"item[{i}]: kind must be 'transaction' or 'message'")

            row = conn.execute(
                """
                INSERT INTO transactions
                  (run_id, kind, name, status, start_time, end_time, duration_ms,
                   start_correlation_id, end_correlation_id,
                   topic, payload, source, acknowledged_at,
                   vuser_id, iteration, error_message, extra)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    run_id, kind, name,
                    item.get("status", "pass"),
                    item.get("start_time"),
                    item.get("end_time"),
                    item.get("duration_ms"),
                    item.get("start_correlation_id"),
                    item.get("end_correlation_id"),
                    item.get("topic"),
                    json.dumps(item.get("payload", {})),
                    item.get("source"),
                    item.get("acknowledged_at"),
                    item.get("vuser_id"),
                    item.get("iteration", 1),
                    item.get("error_message"),
                    json.dumps(item.get("extra", {})),
                )
            ).fetchone()
            ids.append(str(row["id"]))

    return created({"created": len(ids), "ids": ids})


@bp.get("/trace/<correlation_id>")
def trace_correlation(correlation_id):
    """All transactions whose start or end boundary carries this correlation ID."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM transactions
            WHERE start_correlation_id = %s OR end_correlation_id = %s
            ORDER BY start_time
            """,
            (correlation_id, correlation_id)
        ).fetchall()
    return ok([dict(r) for r in rows])


@bp.get("/<tx_id>")
def get_transaction(tx_id):
    with get_conn() as conn:
        tx = conn.execute(
            "SELECT * FROM transactions WHERE id = %s", (tx_id,)
        ).fetchone()
        if not tx:
            return not_found("Transaction")
        steps = conn.execute(
            "SELECT * FROM transaction_steps WHERE transaction_id = %s ORDER BY sequence",
            (tx_id,)
        ).fetchall()

    result = dict(tx)
    result["steps"] = [dict(s) for s in steps]
    return ok(result)


@bp.patch("/<tx_id>")
def update_transaction(tx_id):
    body    = request.get_json() or {}
    allowed = {
        "status", "end_time", "duration_ms", "error_message",
        "start_correlation_id", "end_correlation_id", "acknowledged_at",
    }
    updates = {k: v for k, v in body.items() if k in allowed}

    if not updates:
        return error("no updatable fields provided")

    set_clause = ", ".join(f"{k} = %s" for k in updates)
    params     = list(updates.values()) + [tx_id]

    with get_conn() as conn:
        row = conn.execute(
            f"UPDATE transactions SET {set_clause} WHERE id = %s RETURNING *",
            params
        ).fetchone()
    if not row:
        return not_found("Transaction")
    return ok(dict(row))


@bp.post("/<tx_id>/steps")
def add_step(tx_id):
    body = request.get_json() or {}
    name = body.get("name", "").strip()
    if not name:
        return error("name is required")

    with get_conn() as conn:
        if not conn.execute("SELECT id FROM transactions WHERE id = %s", (tx_id,)).fetchone():
            return not_found("Transaction")

        row = conn.execute(
            """
            INSERT INTO transaction_steps
              (transaction_id, name, sequence, start_time, end_time, duration_ms, status, extra)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                tx_id, name,
                body.get("sequence", 0),
                body.get("start_time"),
                body.get("end_time"),
                body.get("duration_ms"),
                body.get("status", "pass"),
                json.dumps(body.get("extra", {})),
            )
        ).fetchone()

    return created(dict(row))
