"""
Database connection handling.

Uses psycopg3's ConnectionPool for efficient connection reuse.
Each request borrows a connection and returns it when done.

Usage in routes:
    from app.db import get_conn

    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM projects").fetchall()
"""

import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None


def init_pool(dsn: str, min_size: int = 2, max_size: int = 10):
    global _pool
    _pool = ConnectionPool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        kwargs={"row_factory": dict_row},
    )


def close_pool():
    global _pool
    if _pool:
        _pool.close()
        _pool = None


@contextmanager
def get_conn():
    """Yield a connection from the pool. Commits on clean exit, rolls back on error."""
    if _pool is None:
        raise RuntimeError("Database pool not initialised. Call init_pool() first.")
    with _pool.connection() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
