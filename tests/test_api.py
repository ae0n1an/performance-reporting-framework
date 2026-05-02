import json
from datetime import datetime, timezone


def _j(body):
    return json.dumps(body), {"Content-Type": "application/json"}


def _now():
    return datetime.now(timezone.utc).isoformat()


# ── helpers ──────────────────────────────────────────────────────────────────

def make_project(client, name="Test Co", slug=None):
    slug = slug or name.lower().replace(" ", "-")
    r = client.post("/api/projects/", *_j({"name": name, "slug": slug}))
    assert r.status_code == 201
    return r.get_json()["data"]


def make_test(client, project_id, name="Checkout Flow"):
    r = client.post("/api/tests/", *_j({"project_id": project_id, "name": name}))
    assert r.status_code == 201
    return r.get_json()["data"]


def make_run(client, test_id):
    r = client.post("/api/runs/", *_j({"test_id": test_id}))
    assert r.status_code == 201
    return r.get_json()["data"]


# ── project tests ─────────────────────────────────────────────────────────────

def test_create_and_get_project(client):
    p = make_project(client, "My Project", "my-project")
    assert p["slug"] == "my-project"

    r = client.get(f"/api/projects/{p['id']}")
    assert r.status_code == 200
    assert r.get_json()["data"]["name"] == "My Project"


def test_duplicate_slug_rejected(client):
    make_project(client, "Proj A", "proj-a")
    r = client.post("/api/projects/", *_j({"name": "Proj B", "slug": "proj-a"}))
    assert r.status_code == 409


def test_update_project(client):
    p = make_project(client)
    r = client.patch(f"/api/projects/{p['id']}", *_j({"description": "updated"}))
    assert r.get_json()["data"]["description"] == "updated"


def test_delete_project(client):
    p = make_project(client, "To Delete", "to-delete")
    r = client.delete(f"/api/projects/{p['id']}")
    assert r.status_code == 200
    r = client.get(f"/api/projects/{p['id']}")
    assert r.status_code == 404


# ── run lifecycle ─────────────────────────────────────────────────────────────

def test_run_lifecycle(client):
    p = make_project(client, "Run Co", "run-co")
    t = make_test(client, p["id"])
    run = make_run(client, t["id"])
    assert run["status"] == "pending"

    r = client.post(f"/api/runs/{run['id']}/start")
    assert r.get_json()["data"]["status"] == "running"

    # Can't start twice
    r = client.post(f"/api/runs/{run['id']}/start")
    assert r.status_code == 400

    r = client.post(f"/api/runs/{run['id']}/finish", *_j({"status": "passed"}))
    assert r.get_json()["data"]["status"] == "passed"
    assert r.get_json()["data"]["ended_at"] is not None


# ── transactions + correlation ────────────────────────────────────────────────

def test_transaction_and_correlation_trace(client):
    p = make_project(client, "Trace Co", "trace-co")
    t = make_test(client, p["id"])
    run = make_run(client, t["id"])

    # Producer transaction: fires correlation ID at its end boundary
    r = client.post("/api/transactions/", *_j({
        "run_id": run["id"],
        "name": "checkout",
        "status": "pass",
        "start_time": _now(),
        "duration_ms": 320,
        "end_correlation_id": "order-99",
    }))
    assert r.status_code == 201
    producer_id = r.get_json()["data"]["id"]

    # Message transaction: fired with this correlation ID
    r = client.post("/api/transactions/", *_j({
        "run_id": run["id"],
        "kind": "message",
        "name": "orders.placed",
        "start_time": _now(),
        "start_correlation_id": "order-99",
        "topic": "orders.placed",
        "payload": {"order_id": 99},
    }))
    assert r.status_code == 201

    # Consumer transaction: picks up the correlation ID at its start boundary
    r = client.post("/api/transactions/", *_j({
        "run_id": run["id"],
        "name": "process-order",
        "start_time": _now(),
        "start_correlation_id": "order-99",
    }))
    assert r.status_code == 201
    consumer_id = r.get_json()["data"]["id"]

    # Trace returns all three
    r = client.get("/api/transactions/trace/order-99")
    assert r.status_code == 200
    trace = r.get_json()["data"]
    ids = {tx["id"] for tx in trace}
    assert producer_id in ids
    assert consumer_id in ids
    assert len(trace) == 3


def test_bulk_transactions(client):
    p = make_project(client, "Bulk Co", "bulk-co")
    t = make_test(client, p["id"])
    run = make_run(client, t["id"])

    r = client.post("/api/transactions/bulk", *_j({
        "run_id": run["id"],
        "transactions": [
            {"name": "login", "start_time": _now(), "duration_ms": 100},
            {"name": "search", "start_time": _now(), "duration_ms": 200},
            {"name": "checkout", "start_time": _now(), "duration_ms": 300},
        ]
    }))
    assert r.status_code == 201
    assert r.get_json()["data"]["created"] == 3
