from flask import jsonify, request


def ok(data, status=200):
    return jsonify({"data": data}), status


def created(data):
    return ok(data, 201)


def error(message, status=400):
    return jsonify({"error": message}), status


def not_found(resource="Resource"):
    return error(f"{resource} not found", 404)


def get_page_params():
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", 20, type=int), 100)
    offset = (page - 1) * per_page
    return page, per_page, offset


def paginated(items: list, total: int, page: int, per_page: int) -> dict:
    return {
        "items": items,
        "total": total,
        "page": page,
        "pages": -(-total // per_page),  # ceiling division
        "per_page": per_page,
    }
