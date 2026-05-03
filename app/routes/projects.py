from flask import Blueprint, Response, request

from app.db import get_conn
from app.utils import created, error, get_page_params, not_found, ok, paginated

bp = Blueprint("projects", __name__)


@bp.get("/")
def list_projects() -> tuple[Response, int]:
    page, per_page, offset = get_page_params()
    with get_conn() as conn:
        count_row = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()
        assert count_row is not None
        total = int(count_row["n"])
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (per_page, offset),
        ).fetchall()
    return ok(paginated([dict(r) for r in rows], total, page, per_page))


@bp.post("/")
def create_project() -> tuple[Response, int]:
    body: dict[str, object] = request.get_json(silent=True) or {}
    name = str(body.get("name", "")).strip()
    slug = str(body.get("slug", "")).strip()
    description_raw = body.get("description")
    description = str(description_raw) if description_raw is not None else None

    if not name:
        return error("name is required")
    if not slug:
        return error("slug is required")

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM projects WHERE slug = %s", (slug,)
        ).fetchone()
        if existing:
            return error(f"slug '{slug}' already taken", 409)

        row = conn.execute(
            """
            INSERT INTO projects (name, slug, description)
            VALUES (%s, %s, %s)
            RETURNING *
            """,
            (name, slug, description),
        ).fetchone()
        assert row is not None

    return created(dict(row))


@bp.get("/<project_id>")
def get_project(project_id: str) -> tuple[Response, int]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE id = %s", (project_id,)
        ).fetchone()
    if not row:
        return not_found("Project")
    return ok(dict(row))


@bp.patch("/<project_id>")
def update_project(project_id: str) -> tuple[Response, int]:
    body: dict[str, object] = request.get_json(silent=True) or {}
    fields = {k: v for k, v in body.items() if k in ("name", "slug", "description")}
    if not fields:
        return error("Nothing to update")

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    set_clause += ", updated_at = NOW()"
    values: list[object] = list(fields.values()) + [project_id]

    with get_conn() as conn:
        row = conn.execute(
            f"UPDATE projects SET {set_clause} WHERE id = %s RETURNING *",
            values,
        ).fetchone()
    if not row:
        return not_found("Project")
    return ok(dict(row))


@bp.delete("/<project_id>")
def delete_project(project_id: str) -> tuple[Response, int]:
    with get_conn() as conn:
        row = conn.execute(
            "DELETE FROM projects WHERE id = %s RETURNING id", (project_id,)
        ).fetchone()
    if not row:
        return not_found("Project")
    return ok({"deleted": str(row["id"])})
