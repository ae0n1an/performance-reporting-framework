from flask.testing import FlaskClient

from tests.helpers import _j, make_project, make_run, make_test

NULL_ID = "00000000-0000-0000-0000-000000000000"


def test_list_filter_by_status(client: FlaskClient) -> None:
    p = make_project(client)
    t = make_test(client, str(p["id"]))
    run = make_run(client, str(t["id"]))
    client.post(f"/api/runs/{run['id']}/start")
    r = client.get("/api/runs/?status=running")
    data = r.get_json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["id"] == str(run["id"])


def test_create_missing_test_id(client: FlaskClient) -> None:
    r = client.post("/api/runs/", **_j({}))
    assert r.status_code == 400


def test_create_test_not_found(client: FlaskClient) -> None:
    r = client.post("/api/runs/", **_j({"test_id": NULL_ID}))
    assert r.status_code == 404


def test_get_not_found(client: FlaskClient) -> None:
    r = client.get(f"/api/runs/{NULL_ID}")
    assert r.status_code == 404


def test_start_not_found(client: FlaskClient) -> None:
    r = client.post(f"/api/runs/{NULL_ID}/start")
    assert r.status_code == 404


def test_finish_not_running(client: FlaskClient) -> None:
    p = make_project(client)
    t = make_test(client, str(p["id"]))
    run = make_run(client, str(t["id"]))
    r = client.post(f"/api/runs/{run['id']}/finish", **_j({"status": "passed"}))
    assert r.status_code == 400


def test_finish_invalid_status(client: FlaskClient) -> None:
    p = make_project(client)
    t = make_test(client, str(p["id"]))
    run = make_run(client, str(t["id"]))
    client.post(f"/api/runs/{run['id']}/start")
    r = client.post(f"/api/runs/{run['id']}/finish", **_j({"status": "banana"}))
    assert r.status_code == 400


def test_finish_not_found(client: FlaskClient) -> None:
    r = client.post(f"/api/runs/{NULL_ID}/finish", **_j({"status": "passed"}))
    assert r.status_code == 404


def test_delete(client: FlaskClient) -> None:
    p = make_project(client)
    t = make_test(client, str(p["id"]))
    run = make_run(client, str(t["id"]))
    r = client.delete(f"/api/runs/{run['id']}")
    assert r.status_code == 200
    assert client.get(f"/api/runs/{run['id']}").status_code == 404


def test_delete_not_found(client: FlaskClient) -> None:
    r = client.delete(f"/api/runs/{NULL_ID}")
    assert r.status_code == 404
