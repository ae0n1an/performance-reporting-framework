from flask.testing import FlaskClient

from tests.helpers import _j, make_project, make_test

NULL_ID = "00000000-0000-0000-0000-000000000000"


def test_list_filter_by_project(client: FlaskClient) -> None:
    p1 = make_project(client, "P1", "p1")
    p2 = make_project(client, "P2", "p2")
    make_test(client, str(p1["id"]), "T1")
    make_test(client, str(p1["id"]), "T2")
    make_test(client, str(p2["id"]), "T3")
    r = client.get(f"/api/tests/?project_id={p1['id']}")
    assert r.get_json()["data"]["total"] == 2


def test_create_missing_project_id(client: FlaskClient) -> None:
    r = client.post("/api/tests/", **_j({"name": "Test"}))
    assert r.status_code == 400


def test_create_missing_name(client: FlaskClient) -> None:
    p = make_project(client)
    r = client.post("/api/tests/", **_j({"project_id": str(p["id"])}))
    assert r.status_code == 400


def test_create_project_not_found(client: FlaskClient) -> None:
    r = client.post("/api/tests/", **_j({"project_id": NULL_ID, "name": "T"}))
    assert r.status_code == 404


def test_get_not_found(client: FlaskClient) -> None:
    r = client.get(f"/api/tests/{NULL_ID}")
    assert r.status_code == 404


def test_update_config(client: FlaskClient) -> None:
    p = make_project(client)
    t = make_test(client, str(p["id"]))
    r = client.patch(f"/api/tests/{t['id']}", **_j({"config": {"vusers": 10}}))
    assert r.status_code == 200
    assert r.get_json()["data"]["config"]["vusers"] == 10


def test_update_not_found(client: FlaskClient) -> None:
    r = client.patch(f"/api/tests/{NULL_ID}", **_j({"name": "X"}))
    assert r.status_code == 404


def test_delete(client: FlaskClient) -> None:
    p = make_project(client)
    t = make_test(client, str(p["id"]))
    r = client.delete(f"/api/tests/{t['id']}")
    assert r.status_code == 200
    assert client.get(f"/api/tests/{t['id']}").status_code == 404


def test_delete_not_found(client: FlaskClient) -> None:
    r = client.delete(f"/api/tests/{NULL_ID}")
    assert r.status_code == 404
