from flask.testing import FlaskClient


def test_health_ok(client: FlaskClient) -> None:
    r = client.get("/api/health/")
    assert r.status_code == 200
    assert r.get_json()["data"]["status"] == "ok"
