from flask.testing import FlaskClient

from tests.helpers import _j, _now, make_project, make_run, make_test

NULL_ID = "00000000-0000-0000-0000-000000000000"


def test_create_missing_run_id(client: FlaskClient) -> None:
    r = client.post("/api/transactions/", **_j({"name": "T", "start_time": _now()}))
    assert r.status_code == 400


def test_create_missing_name(client: FlaskClient) -> None:
    p = make_project(client)
    t = make_test(client, str(p["id"]))
    run = make_run(client, str(t["id"]))
    r = client.post("/api/transactions/", **_j({"run_id": run["id"], "start_time": _now()}))
    assert r.status_code == 400


def test_create_missing_start_time(client: FlaskClient) -> None:
    p = make_project(client)
    t = make_test(client, str(p["id"]))
    run = make_run(client, str(t["id"]))
    r = client.post("/api/transactions/", **_j({"run_id": run["id"], "name": "T"}))
    assert r.status_code == 400


def test_create_invalid_kind(client: FlaskClient) -> None:
    p = make_project(client)
    t = make_test(client, str(p["id"]))
    run = make_run(client, str(t["id"]))
    r = client.post("/api/transactions/", **_j({
        "run_id": run["id"], "name": "T", "start_time": _now(), "kind": "banana",
    }))
    assert r.status_code == 400


def test_create_run_not_found(client: FlaskClient) -> None:
    r = client.post("/api/transactions/", **_j({
        "run_id": NULL_ID, "name": "T", "start_time": _now(),
    }))
    assert r.status_code == 404


def test_list_filter_by_run_id(client: FlaskClient) -> None:
    p = make_project(client)
    t = make_test(client, str(p["id"]))
    run1 = make_run(client, str(t["id"]))
    run2 = make_run(client, str(t["id"]))
    for run in (run1, run2):
        client.post("/api/transactions/", **_j({
            "run_id": run["id"], "name": "T", "start_time": _now(),
        }))
    r = client.get(f"/api/transactions/?run_id={run1['id']}")
    assert r.get_json()["data"]["total"] == 1


def test_get_with_steps(client: FlaskClient) -> None:
    p = make_project(client)
    t = make_test(client, str(p["id"]))
    run = make_run(client, str(t["id"]))
    tx_id = client.post("/api/transactions/", **_j({
        "run_id": run["id"], "name": "T", "start_time": _now(),
    })).get_json()["data"]["id"]
    client.post(f"/api/transactions/{tx_id}/steps", **_j({"name": "step1", "sequence": 1}))
    r = client.get(f"/api/transactions/{tx_id}")
    assert r.status_code == 200
    steps = r.get_json()["data"]["steps"]
    assert len(steps) == 1
    assert steps[0]["name"] == "step1"


def test_update(client: FlaskClient) -> None:
    p = make_project(client)
    t = make_test(client, str(p["id"]))
    run = make_run(client, str(t["id"]))
    tx_id = client.post("/api/transactions/", **_j({
        "run_id": run["id"], "name": "T", "start_time": _now(),
    })).get_json()["data"]["id"]
    r = client.patch(
        f"/api/transactions/{tx_id}", **_j({"status": "fail", "error_message": "timeout"})
    )
    assert r.status_code == 200
    assert r.get_json()["data"]["status"] == "fail"
    assert r.get_json()["data"]["error_message"] == "timeout"


def test_update_not_found(client: FlaskClient) -> None:
    r = client.patch(f"/api/transactions/{NULL_ID}", **_j({"status": "fail"}))
    assert r.status_code == 404


def test_update_no_fields(client: FlaskClient) -> None:
    p = make_project(client)
    t = make_test(client, str(p["id"]))
    run = make_run(client, str(t["id"]))
    tx_id = client.post("/api/transactions/", **_j({
        "run_id": run["id"], "name": "T", "start_time": _now(),
    })).get_json()["data"]["id"]
    r = client.patch(f"/api/transactions/{tx_id}", **_j({}))
    assert r.status_code == 400


def test_add_step_not_found(client: FlaskClient) -> None:
    r = client.post(f"/api/transactions/{NULL_ID}/steps", **_j({"name": "s"}))
    assert r.status_code == 404


def test_add_step_missing_name(client: FlaskClient) -> None:
    p = make_project(client)
    t = make_test(client, str(p["id"]))
    run = make_run(client, str(t["id"]))
    tx_id = client.post("/api/transactions/", **_j({
        "run_id": run["id"], "name": "T", "start_time": _now(),
    })).get_json()["data"]["id"]
    r = client.post(f"/api/transactions/{tx_id}/steps", **_j({}))
    assert r.status_code == 400


def test_bulk_validates_before_inserting(client: FlaskClient) -> None:
    p = make_project(client)
    t = make_test(client, str(p["id"]))
    run = make_run(client, str(t["id"]))
    r = client.post("/api/transactions/bulk", **_j({
        "run_id": run["id"],
        "transactions": [
            {"name": "login", "start_time": _now()},
            {"name": "", "start_time": _now()},
        ],
    }))
    assert r.status_code == 400
    r2 = client.get(f"/api/transactions/?run_id={run['id']}")
    assert r2.get_json()["data"]["total"] == 0


def test_bulk_run_not_found(client: FlaskClient) -> None:
    r = client.post("/api/transactions/bulk", **_j({
        "run_id": NULL_ID,
        "transactions": [{"name": "T", "start_time": _now()}],
    }))
    assert r.status_code == 404


def test_bulk_empty_list(client: FlaskClient) -> None:
    p = make_project(client)
    t = make_test(client, str(p["id"]))
    run = make_run(client, str(t["id"]))
    r = client.post("/api/transactions/bulk", **_j({"run_id": run["id"], "transactions": []}))
    assert r.status_code == 400


def test_trace_empty(client: FlaskClient) -> None:
    r = client.get("/api/transactions/trace/nonexistent-id")
    assert r.status_code == 200
    assert r.get_json()["data"] == []
