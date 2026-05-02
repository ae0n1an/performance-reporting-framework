import json
from flask import Blueprint, request

from app.db import get_conn
from app.utils import ok, created, error, not_found, get_page_params, paginated

bp = Blueprint("messages", __name__)

VALID_STATUSES = ("sent", "delivered", "failed", "timeout")


@bp.get("/")
def list_messages():
    page, per_page, offset = get_page_params()
    run_id = request.args.get("run_id")
    correlation_id = request.args.get("correlation_id")
    status = request.args.get("status")

    where_parts, params = [], []
    if run_id:
        where_parts.append("run_id = %s"); params.append(run_id)
    if correlation_id:
        where_parts.append("correlation_id = %s"); params.append(correlation_id)
    if status:
        where_parts.append("status = %s"); params.append(status)

    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM fire_and_forget_messages {where}", params
        ).fetchone()["n"]
        rows = conn.execute(
            f"SELECT * FROM fire_and_forget_messages {where} ORDER BY sent_at DESC LIMIT %s OFFSET %s",
            params + [per_page, offset]
        ).fetchall()

    return ok(paginated([dict(r) for r in rows], total, page, per_page))


@bp.post("/")
def send_message():
    body = request.get_json() or {}
    run_id = body.get("run_id", "").strip()
    correlation_id = body.get("correlation_id", "").strip()
    transaction_id = body.get("transaction_id")  # optional: auto-link

    if not run_id:          return error("run_id is required")
    if not correlation_id:  return error("correlation_id is required")

    with get_conn() as conn:
        if not conn.execute("SELECT id FROM test_runs WHERE id = %s", (run_id,)).fetchone():
            return not_found("TestRun")

        if transaction_id and not conn.execute(
            "SELECT id FROM transactions WHERE id = %s", (transaction_id,)
        ).fetchone():
            return not_found("Transaction")

        msg = conn.execute(
            """
            INSERT INTO fire_and_forget_messages
              (run_id, correlation_id, topic, payload, status, source)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                run_id, correlation_id,
                body.get("topic"),
                json.dumps(body.get("payload", {})),
                body.get("status", "sent"),
                body.get("source"),
            )
        ).fetchone()

        # Auto-create a correlation link if a transaction_id was provided
        if transaction_id:
            conn.execute(
                """
                INSERT INTO correlation_links (correlation_id, transaction_id, message_id)
                VALUES (%s, %s, %s)
                """,
                (correlation_id, transaction_id, msg["id"])
            )

    return created(dict(msg))


@bp.get("/trace/<correlation_id>")
def trace_correlation(correlation_id):
    """Full trace for a correlation_id: all linked transactions + messages."""
    with get_conn() as conn:
        transactions = conn.execute(
            """
            SELECT DISTINCT t.* FROM transactions t
            JOIN correlation_links cl ON cl.transaction_id = t.id
            WHERE cl.correlation_id = %s
            """,
            (correlation_id,)
        ).fetchall()

        messages = conn.execute(
            """
            SELECT DISTINCT m.* FROM fire_and_forget_messages m
            JOIN correlation_links cl ON cl.message_id = m.id
            WHERE cl.correlation_id = %s
            """,
            (correlation_id,)
        ).fetchall()

    return ok({
        "correlation_id": correlation_id,
        "transactions": [dict(r) for r in transactions],
        "messages": [dict(r) for r in messages],
    })


@bp.get("/<msg_id>")
def get_message(msg_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM fire_and_forget_messages WHERE id = %s", (msg_id,)
        ).fetchone()
    if not row:
        return not_found("Message")
    return ok(dict(row))


@bp.patch("/<msg_id>/status")
def update_status(msg_id):
    body = request.get_json() or {}
    new_status = body.get("status")
    if new_status not in VALID_STATUSES:
        return error(f"status must be one of: {', '.join(VALID_STATUSES)}")

    with get_conn() as conn:
        row = conn.execute(
            """
            UPDATE fire_and_forget_messages
            SET status = %s,
                acknowledged_at = CASE WHEN %s = 'delivered' THEN NOW() ELSE acknowledged_at END,
                error_message = COALESCE(%s, error_message)
            WHERE id = %s
            RETURNING *
            """,
            (new_status, new_status, body.get("error_message"), msg_id)
        ).fetchone()
    if not row:
        return not_found("Message")
    return ok(dict(row))


@bp.post("/correlate")
def correlate():
    """Explicitly link a transaction and/or message under a correlation_id."""
    body = request.get_json() or {}
    correlation_id = body.get("correlation_id", "").strip()
    transaction_id = body.get("transaction_id")
    message_id = body.get("message_id")

    if not correlation_id:
        return error("correlation_id is required")
    if not transaction_id and not message_id:
        return error("provide at least one of transaction_id or message_id")

    with get_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO correlation_links (correlation_id, transaction_id, message_id)
            VALUES (%s, %s, %s)
            RETURNING *
            """,
            (correlation_id, transaction_id, message_id)
        ).fetchone()

    return created(dict(row))
