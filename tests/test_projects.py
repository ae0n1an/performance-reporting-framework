from flask.testing import FlaskClient

from tests.helpers import _j, make_project

NULL_ID = "00000000-0000-0000-0000-000000000000"


def test_list_empty(client: FlaskClient) -> None:
    r = client.get("/api/projects/")
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["items"] == []
    assert data["total"] == 0


def test_list_pagination(client: FlaskClient) -> None:
    for i in range(3):
        make_project(client, f"Project {i}", f"project-{i}")
    r = client.get("/api/projects/?per_page=2&page=1")
    data = r.get_json()["data"]
    assert len(data["items"]) == 2
    assert data["total"] == 3
    assert data["pages"] == 2


def test_create_missing_name(client: FlaskClient) -> None:
    r = client.post("/api/projects/", **_j({"slug": "no-name"}))
    assert r.status_code == 400
    assert "name" in r.get_json()["error"]


def test_create_missing_slug(client: FlaskClient) -> None:
    r = client.post("/api/projects/", **_j({"name": "No Slug"}))
    assert r.status_code == 400
    assert "slug" in r.get_json()["error"]


def test_get_not_found(client: FlaskClient) -> None:
    r = client.get(f"/api/projects/{NULL_ID}")
    assert r.status_code == 404


def test_update_no_fields(client: FlaskClient) -> None:
    p = make_project(client)
    r = client.patch(f"/api/projects/{p['id']}", **_j({}))
    assert r.status_code == 400


def test_update_not_found(client: FlaskClient) -> None:
    r = client.patch(f"/api/projects/{NULL_ID}", **_j({"name": "X"}))
    assert r.status_code == 404


def test_delete_not_found(client: FlaskClient) -> None:
    r = client.delete(f"/api/projects/{NULL_ID}")
    assert r.status_code == 404
