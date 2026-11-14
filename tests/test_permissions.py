"""Database guard rails: each login can only do its own job, and the status
rules hold even for the owner. Uses raw SQL to bypass the business layer.
"""

import os

import psycopg
import pytest

from core import stories
from core.settings import database_url

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_db")]


@pytest.fixture
def story_id(make_draft) -> int:
    return stories.save_draft(make_draft(1))


def run(role: str, query: str, params=None):
    url = os.environ["DATABASE_URL"] if role == "owner" else database_url(role)
    with psycopg.connect(url, autocommit=True) as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if cur.description else None


NEW_STORY = (
    "insert into stories (publisher_id, source_url, title, text_hash) "
    "values (1, 'https://example.com/x', 'T', 'h')"
)

DENIED = [
    # Agent: drafts only
    (
        "agent",
        (
            "insert into stories (publisher_id, source_url, title, text_hash, status) "
            "values (1, 'https://example.com/y', 'T', 'h', 'approved')"
        ),
    ),
    ("agent", "update stories set status = 'approved' where id = %(id)s"),
    ("agent", "delete from stories where id = %(id)s"),
    ("agent", "select source_text from stories"),
    ("agent", "select * from public_stories"),
    ("agent", "select mark_published(%(id)s)"),
    # Admin: status and notes only
    ("admin", "update stories set body = 'changed' where id = %(id)s"),
    ("admin", NEW_STORY),
    ("admin", "delete from stories where id = %(id)s"),
    ("admin", "select mark_published(%(id)s)"),
    # Publish: public view and mark_published only
    ("publish", "select source_text from stories"),
    ("publish", "select review_notes from stories"),
    ("publish", "update stories set status = 'published' where id = %(id)s"),
    ("publish", "delete from stories where id = %(id)s"),
    ("publish", NEW_STORY),
]


@pytest.mark.parametrize(
    ("role", "query"), DENIED, ids=[f"{r}: {q[:45]}" for r, q in DENIED]
)
def test_permission_denied(story_id, role, query):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        run(role, query, {"id": story_id})


def test_public_view_has_no_private_columns():
    rows = run(
        "publish",
        "select column_name from information_schema.columns "
        "where table_name = 'public_stories'",
    )
    columns = {r[0] for r in rows}
    assert "kid_title" in columns
    assert (
        not {"source_text", "review_notes", "text_hash", "model", "prompt_version"}
        & columns
    )


# --- Status rules hold even for the owner login ------------------------------


def test_new_story_must_start_as_draft():
    with pytest.raises(psycopg.errors.CheckViolation):
        run(
            "owner",
            "insert into stories (publisher_id, source_url, title, text_hash, status) "
            "values (1, 'https://example.com/z', 'T', 'h', 'published')",
        )


@pytest.mark.parametrize("status", ["published", "draft"])
def test_cannot_skip_or_go_back(story_id, status):
    if status == "draft":
        stories.approve(story_id)  # approved -> draft is the "go back" case
    with pytest.raises(psycopg.errors.CheckViolation):
        run(
            "owner",
            "update stories set status = %(s)s where id = %(id)s",
            {"s": status, "id": story_id},
        )
