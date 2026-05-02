"""
Tests require a real Postgres instance.

Set TEST_DATABASE_URL in your environment or .env, e.g.:
  TEST_DATABASE_URL=postgresql://loadrunner:secret@localhost:5432/loadrunner_test

With Docker:
  docker compose -f docker-compose.dev.yml up db -d
  TEST_DATABASE_URL=postgresql://loadrunner:secret@localhost:5432/loadrunner_test pytest
"""

import os
import pytest
from dotenv import load_dotenv

load_dotenv()

TEST_DSN = os.getenv("TEST_DATABASE_URL", "")


def pytest_configure(config):
    if not TEST_DSN:
        print("\nWARNING: TEST_DATABASE_URL not set — tests will be skipped.")


@pytest.fixture(scope="session")
def app():
    if not TEST_DSN:
        pytest.skip("TEST_DATABASE_URL not set")

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
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def clean_db(app):
    """Truncate all tables between tests (preserve schema)."""
    yield
    from app.db import get_conn
    with app.app_context():
        with get_conn() as conn:
            conn.execute("""
                TRUNCATE TABLE
                    correlation_links,
                    fire_and_forget_messages,
                    transaction_steps,
                    transactions,
                    test_runs,
                    tests,
                    projects
                RESTART IDENTITY CASCADE
            """)
