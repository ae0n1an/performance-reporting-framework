"""
Tests require a real Postgres instance.

Set TEST_DATABASE_URL in your environment or .env, e.g.:
  TEST_DATABASE_URL=postgresql://loadrunner:secret@localhost:5432/loadrunner_test

With Docker:
  docker compose -f docker-compose.dev.yml up db -d
  TEST_DATABASE_URL=postgresql://loadrunner:secret@localhost:5432/loadrunner_test pytest
"""

import os
from collections.abc import Generator
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest
from dotenv import load_dotenv
from flask import Flask
from flask.testing import FlaskClient

load_dotenv()

TEST_DSN = os.getenv("TEST_DATABASE_URL", "")


def _ensure_db_exists(dsn: str) -> None:
    """Create the test database if it doesn't exist yet."""
    parsed = urlparse(dsn)
    db_name = parsed.path.lstrip("/")
    admin_dsn = urlunparse(parsed._replace(path="/postgres"))
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{db_name}"')


def pytest_configure(config: pytest.Config) -> None:
    if not TEST_DSN:
        print("\nWARNING: TEST_DATABASE_URL not set — tests will be skipped.")


@pytest.fixture(scope="session")
def app() -> Flask:
    if not TEST_DSN:
        pytest.skip("TEST_DATABASE_URL not set")

    _ensure_db_exists(TEST_DSN)

    from app import create_app
    from app.db.migrate import apply

    flask_app = create_app({
        "DATABASE_URL": TEST_DSN,
        "TESTING": True,
        "DB_POOL_MIN": 1,
        "DB_POOL_MAX": 3,
    })

    with flask_app.app_context():
        apply(TEST_DSN)

    return flask_app


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


@pytest.fixture(autouse=True)
def clean_db(app: Flask) -> Generator[None, None, None]:
    """Truncate all tables between tests (preserve schema)."""
    yield
    from app.db import get_conn
    with app.app_context():
        with get_conn() as conn:
            conn.execute("""
                TRUNCATE TABLE
                    transaction_steps,
                    transactions,
                    test_runs,
                    tests,
                    projects
                RESTART IDENTITY CASCADE
            """)
