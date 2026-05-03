from datetime import UTC, datetime
from typing import cast

from flask.testing import FlaskClient


def _j(body: object) -> dict[str, object]:
    return {"json": body}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def make_project(
    client: FlaskClient, name: str = "Test Co", slug: str | None = None
) -> dict[str, object]:
    slug = slug or name.lower().replace(" ", "-")
    r = client.post("/api/projects/", **_j({"name": name, "slug": slug}))
    assert r.status_code == 201
    return cast(dict[str, object], r.get_json()["data"])


def make_test(
    client: FlaskClient, project_id: str, name: str = "Checkout Flow"
) -> dict[str, object]:
    r = client.post("/api/tests/", **_j({"project_id": project_id, "name": name}))
    assert r.status_code == 201
    return cast(dict[str, object], r.get_json()["data"])


def make_run(client: FlaskClient, test_id: str) -> dict[str, object]:
    r = client.post("/api/runs/", **_j({"test_id": test_id}))
    assert r.status_code == 201
    return cast(dict[str, object], r.get_json()["data"])
