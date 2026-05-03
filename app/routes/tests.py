import json

from flask import Blueprint, Response, request

from app.db import get_conn
from app.utils import created, error, get_page_params, not_found, ok, paginated

bp = Blueprint("tests", __name__)


@bp.get("/")
def list_tests() -> tuple[Response, int]:
    page, per_page, offset = get_page_params()
    project_id = request.args.get("project_id")

    with get_conn() as conn:
        if project_id:
            count_row = conn.execute(
                "SELECT COUNT(*) AS n FROM tests WHERE project_id = %s", (project_id,)
            ).fetchone()
            assert count_row is not None
            total = int(count_row["n"])
            rows = conn.execute(
                "SELECT * FROM tests WHERE project_id = %s "
                "ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (project_id, per_page, offset),
            ).fetchall()
        else:
            count_row = conn.execute("SELECT COUNT(*) AS n FROM tests").fetchone()
            assert count_row is not None
            total = int(count_row["n"])
            rows = conn.execute(
                "SELECT * FROM tests ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (per_page, offset),
            ).fetchall()

    return ok(paginated([dict(r) for r in rows], total, page, per_page))


@bp.post("/")
def create_test() -> tuple[Response, int]:
    body: dict[str, object] = request.get_json(silent=True) or {}
    project_id = str(body.get("project_id", "")).strip()
    name = str(body.get("name", "")).strip()

    if not project_id:
        return error("project_id is required")
    if not name:
        return error("name is required")

    config_raw = body.get("config")
    config = config_raw if isinstance(config_raw, dict) else {}
    description_raw = body.get("description")
    description = str(description_raw) if description_raw is not None else None

    with get_conn() as conn:
        if not conn.execute(
            "SELECT id FROM projects WHERE id = %s", (project_id,)
        ).fetchone():
            return not_found("Project")

        row = conn.execute(
            """
            INSERT INTO tests (project_id, name, description, config)
            VALUES (%s, %s, %s, %s)
            RETURNING *
            """,
            (project_id, name, description, json.dumps(config)),
        ).fetchone()
        assert row is not None

    return created(dict(row))


@bp.get("/<test_id>")
def get_test(test_id: str) -> tuple[Response, int]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tests WHERE id = %s", (test_id,)).fetchone()
    if not row:
        return not_found("Test")
    return ok(dict(row))


@bp.patch("/<test_id>")
def update_test(test_id: str) -> tuple[Response, int]:
    body: dict[str, object] = request.get_json(silent=True) or {}
    allowed = {"name", "description", "config"}
    fields: dict[str, object] = {k: v for k, v in body.items() if k in allowed}
    if not fields:
        return error("Nothing to update")

    if "config" in fields:
        config_raw = fields["config"]
        fields["config"] = json.dumps(config_raw if isinstance(config_raw, dict) else {})

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    set_clause += ", updated_at = NOW()"
    values: list[object] = list(fields.values()) + [test_id]

    with get_conn() as conn:
        row = conn.execute(
            f"UPDATE tests SET {set_clause} WHERE id = %s RETURNING *",
            values,
        ).fetchone()
    if not row:
        return not_found("Test")
    return ok(dict(row))


@bp.delete("/<test_id>")
def delete_test(test_id: str) -> tuple[Response, int]:
    with get_conn() as conn:
        row = conn.execute(
            "DELETE FROM tests WHERE id = %s RETURNING id", (test_id,)
        ).fetchone()
    if not row:
        return not_found("Test")
    return ok({"deleted": str(row["id"])})
