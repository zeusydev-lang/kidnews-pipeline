"""Database connections, one role at a time.

Usage:
    with connect("agent") as conn:
        conn.execute("select ...")

Everything inside the with-block is one transaction: it commits when the block
ends normally and rolls back if an error is raised. Rows come back as dicts.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from core.settings import Role, database_url


@contextmanager
def connect(role: Role) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(
        database_url(role),
        row_factory=dict_row,
        application_name=f"kidnews-{role}",  # shows up in Neon's monitoring
        connect_timeout=15,
    )
    try:
        with conn.transaction():
            yield conn
    finally:
        conn.close()
