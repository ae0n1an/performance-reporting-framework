import re

from flask import Blueprint, redirect, render_template, request
from werkzeug.wrappers import Response as WerkzeugResponse

from app.db import get_conn

bp = Blueprint("ui", __name__)


@bp.get("/")
def index() -> str:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, name, slug, description, created_at
            FROM projects
            ORDER BY created_at DESC
            LIMIT 6
            """
        ).fetchall()
    projects = [dict(r) for r in rows]
    breadcrumbs = [{"label": "Home", "url": None}]
    return render_template("index.html", projects=projects, breadcrumbs=breadcrumbs)


@bp.get("/projects/new")
def new_project_form() -> str:
    breadcrumbs = [
        {"label": "Home", "url": "/"},
        {"label": "New project", "url": None},
    ]
    return render_template("projects/new.html", breadcrumbs=breadcrumbs, errors={}, values={})


@bp.post("/projects/new")
def create_project_form() -> WerkzeugResponse | str:
    name = request.form.get("name", "").strip()
    slug = request.form.get("slug", "").strip()
    description = request.form.get("description", "").strip() or None

    errors: dict[str, str] = {}
    if not name:
        errors["name"] = "Name is required."
    if not slug:
        errors["slug"] = "Slug is required."
    elif not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        errors["slug"] = "Slug must be lowercase letters, numbers, and hyphens only."

    if not errors:
        with get_conn() as conn:
            existing = conn.execute(
                "SELECT id FROM projects WHERE slug = %s", (slug,)
            ).fetchone()
            if existing:
                errors["slug"] = f"Slug '{slug}' is already taken."

    if errors:
        breadcrumbs = [
            {"label": "Home", "url": "/"},
            {"label": "New project", "url": None},
        ]
        values = {"name": name, "slug": slug, "description": description or ""}
        return render_template(
            "projects/new.html", breadcrumbs=breadcrumbs, errors=errors, values=values
        )

    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO projects (name, slug, description) VALUES (%s, %s, %s) RETURNING id",
            (name, slug, description),
        ).fetchone()
        assert row is not None

    return redirect(f"/projects/{row['id']}")
