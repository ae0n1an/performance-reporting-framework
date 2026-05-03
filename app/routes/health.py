from flask import Blueprint, Response

from app.db import get_conn
from app.utils import error, ok

bp = Blueprint("health", __name__)


@bp.get("/")
def health() -> tuple[Response, int]:
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return ok({"status": "ok"})
    except Exception:
        return error("database unavailable", 503)
