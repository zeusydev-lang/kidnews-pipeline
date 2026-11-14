"""Story functions: the only way the pipeline reads or changes stories.

Each function uses the least-privileged login for its job:

    agent    find_existing, save_draft (with eval results)
    admin    list_stories, get_story, get_eval_results, approve, reject
    publish  get_publishable, get_published, mark_published

The database enforces the same rules (permissions, status trigger), so a bug
here fails loudly instead of doing something unsafe.
"""

from typing import Any

import psycopg
from psycopg import errors, sql
from psycopg.types.json import Jsonb

from core.db import connect
from core.models import EvalResult, StoryDraft, StoryStatus

# --- Errors ----------------------------------------------------------------


class StoryError(Exception):
    """Base class for story errors the caller can handle."""


class DuplicateStory(StoryError):
    """This article (publisher + source URL) is already stored."""


class SlugTaken(StoryError):
    """Another story already uses this slug."""


class StoryNotFound(StoryError):
    """No story with this id."""


class InvalidStatusChange(StoryError):
    """The story's current status doesn't allow this change."""


# --- Agent -----------------------------------------------------------------

_DRAFT_COLUMNS = (
    "publisher_id",
    "source_url",
    "title",
    "author",
    "image_credit",
    "published_on",
    "text_hash",
    "source_text",
    "kid_title",
    "summary",
    "body",
    "slug",
    "topic",
    "reading_level",
    "image_url",
    "image_alt",
    "concepts",
    "glossary",
    "model",
    "prompt_version",
)


def get_publisher_id(name: str) -> int:
    """The id of a publisher by name, e.g. "NASA Earth Observatory"."""
    with connect("agent") as conn:
        row = conn.execute(
            "select id from publishers where name = %s", [name]
        ).fetchone()
    if row is None:
        raise StoryError(f"No publisher named {name!r}. Run the migrations first.")
    return row["id"]


def find_existing(publisher_id: int, source_url: str) -> int | None:
    """Return the story id if this article is already stored, else None.

    The agent calls this before the Claude call, so duplicates cost nothing.
    """
    with connect("agent") as conn:
        row = conn.execute(
            "select id from stories where publisher_id = %s and source_url = %s",
            [publisher_id, source_url],
        ).fetchone()
    return row["id"] if row else None


def save_draft(draft: StoryDraft, evals: list[EvalResult] | None = None) -> int:
    """Save a story, its quiz and its check results together as a draft.
    Returns the new story id.

    Raises DuplicateStory or SlugTaken. Nothing is saved if any insert fails.
    """
    values: dict[str, Any] = draft.model_dump(include=set(_DRAFT_COLUMNS))
    for key in ("concepts", "glossary"):
        if values[key] is not None:
            values[key] = Jsonb(values[key])

    insert_story = sql.SQL(
        "insert into stories ({cols}) values ({vals}) returning id"
    ).format(
        cols=sql.SQL(", ").join(map(sql.Identifier, _DRAFT_COLUMNS)),
        vals=sql.SQL(", ").join(map(sql.Placeholder, _DRAFT_COLUMNS)),
    )
    questions = [q.model_dump() for q in draft.quiz.questions]

    try:
        with connect("agent") as conn:
            story_id = conn.execute(insert_story, values).fetchone()["id"]
            conn.execute(
                "insert into quizzes (story_id, questions) values (%s, %s)",
                [story_id, Jsonb(questions)],
            )
            for result in evals or []:
                conn.execute(
                    "insert into eval_runs"
                    " (story_id, check_name, passed, details, judge_model)"
                    " values (%s, %s, %s, %s, %s)",
                    [
                        story_id,
                        result.check_name,
                        result.passed,
                        Jsonb(result.details),
                        result.judge_model,
                    ],
                )
    except errors.UniqueViolation as err:
        constraint = err.diag.constraint_name
        if constraint == "stories_publisher_id_source_url_key":
            raise DuplicateStory(f"Already stored: {draft.source_url}") from err
        if constraint == "stories_slug_key":
            raise SlugTaken(f"Slug already used: {draft.slug}") from err
        raise
    return story_id


# --- Admin -----------------------------------------------------------------


def get_eval_results(story_id: int) -> list[dict]:
    """Check results for one story, in the order they were saved."""
    with connect("admin") as conn:
        return conn.execute(
            "select check_name, passed, details, judge_model, run_at"
            " from eval_runs where story_id = %s order by id",
            [story_id],
        ).fetchall()


_LIST_COLUMNS = """
    id, status, kid_title, title, slug, topic, reading_level, source_url,
    fetched_on, approved_at, published_at, review_notes
"""


def list_stories(status: StoryStatus | None = None, limit: int = 100) -> list[dict]:
    """Stories for the admin page, newest first. Leaves out long text fields."""
    with connect("admin") as conn:
        if status is None:
            rows = conn.execute(
                f"select {_LIST_COLUMNS} from stories order by id desc limit %s",
                [limit],
            )
        else:
            rows = conn.execute(
                f"select {_LIST_COLUMNS} from stories where status = %s order by id desc limit %s",
                [status, limit],
            )
        return rows.fetchall()


def get_story(story_id: int) -> dict:
    """One story with every field, its publisher name and its quiz questions."""
    with connect("admin") as conn:
        row = conn.execute(
            """
            select s.*, p.name as publisher_name, q.questions
              from stories s
              join publishers p on p.id = s.publisher_id
              left join quizzes q on q.story_id = s.id
             where s.id = %s
            """,
            [story_id],
        ).fetchone()
    if row is None:
        raise StoryNotFound(f"No story {story_id}")
    return row


def approve(story_id: int) -> None:
    """draft -> approved. approved_at is set by the database."""
    with connect("admin") as conn:
        try:
            updated = conn.execute(
                "update stories set status = 'approved' where id = %s and status = 'draft' returning id",
                [story_id],
            ).fetchone()
        except errors.CheckViolation as err:
            raise InvalidStatusChange(
                f"Story {story_id} is missing content needed to approve"
            ) from err
        if updated is None:
            _explain_no_change(conn, story_id, "approve")


def reject(story_id: int, reason: str) -> None:
    """draft -> rejected, saving the reason in review_notes."""
    reason = reason.strip()
    if not reason:
        raise ValueError("A reason is required to reject a story")
    with connect("admin") as conn:
        updated = conn.execute(
            """
            update stories set status = 'rejected', review_notes = %s
             where id = %s and status = 'draft'
            returning id
            """,
            [reason, story_id],
        ).fetchone()
        if updated is None:
            _explain_no_change(conn, story_id, "reject")


def _explain_no_change(conn: psycopg.Connection, story_id: int, action: str) -> None:
    row = conn.execute(
        "select status from stories where id = %s", [story_id]
    ).fetchone()
    if row is None:
        raise StoryNotFound(f"No story {story_id}")
    raise InvalidStatusChange(f"Can't {action} story {story_id}: it is {row['status']}")


# --- Publish ---------------------------------------------------------------


def get_publishable() -> list[dict]:
    """Approved stories waiting to go live, oldest approval first. Public fields only."""
    with connect("publish") as conn:
        return conn.execute(
            "select * from public_stories where status = 'approved' order by approved_at"
        ).fetchall()


def get_published() -> list[dict]:
    """Stories already live, newest first. Used to rebuild the site index."""
    with connect("publish") as conn:
        return conn.execute(
            "select * from public_stories where status = 'published' order by published_at desc"
        ).fetchall()


def mark_published(story_id: int) -> None:
    """approved -> published. Only works on approved stories."""
    with connect("publish") as conn:
        ok = conn.execute("select mark_published(%s) as ok", [story_id]).fetchone()[
            "ok"
        ]
    if not ok:
        raise InvalidStatusChange(f"Story {story_id} is not approved, or doesn't exist")
