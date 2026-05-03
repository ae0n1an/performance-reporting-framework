from collections.abc import Sequence
from typing import TypedDict

from flask import Response, jsonify, request


class PaginatedResponse(TypedDict):
    items: Sequence[object]
    total: int
    page: int
    pages: int
    per_page: int


def ok(data: object, status: int = 200) -> tuple[Response, int]:
    return jsonify({"data": data}), status


def created(data: object) -> tuple[Response, int]:
    return ok(data, 201)


def error(message: str, status: int = 400) -> tuple[Response, int]:
    return jsonify({"error": message}), status


def not_found(resource: str = "Resource") -> tuple[Response, int]:
    return error(f"{resource} not found", 404)


def get_page_params() -> tuple[int, int, int]:
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 20, type=int), 100)
    offset = (page - 1) * per_page
    return page, per_page, offset


def paginated(
    items: Sequence[object], total: int, page: int, per_page: int
) -> PaginatedResponse:
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        pages=-(-total // per_page),  # ceiling division
        per_page=per_page,
    )
