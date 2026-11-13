"""Test setup.

Database tests (marked `db`) run ONLY against the Neon test branch in .env.test.
They wipe the stories and quizzes tables before each test.

Safety:
  - If .env.test is missing or incomplete, database tests are skipped.
  - If .env.test points at the same host as .env (dev) or .env.production,
    the whole test run stops before touching anything.

One-time setup for the test branch:
    uv run --env-file .env.test python migrations/migrate.py
    uv run python migrations/logins.py --env-file .env.test
"""

import os
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
TEST_ENV = ROOT / ".env.test"
KEYS = (
    "DATABASE_URL",
    "AGENT_DATABASE_URL",
    "ADMIN_DATABASE_URL",
    "PUBLISH_DATABASE_URL",
)


def _host(url: str) -> str | None:
    return urlsplit(url).hostname


def _other_hosts() -> dict[str, str]:
    hosts = {}
    for name in (".env", ".env.production"):
        path = ROOT / name
        url = dotenv_values(path).get("DATABASE_URL") if path.exists() else None
        if url and _host(url):
            hosts[name] = _host(url)
    return hosts


def _load_test_env() -> tuple[str | None, str | None]:
    """Point every login at the test branch.

    Returns (skip_reason, stop_reason). Both None means database tests can run.
    """
    if not TEST_ENV.exists():
        return ".env.test not found (see tests/conftest.py for setup)", None

    values = dotenv_values(TEST_ENV)
    missing = [k for k in KEYS if not values.get(k)]
    if missing:
        return (
            f".env.test is missing {', '.join(missing)}. "
            "Run: uv run python migrations/logins.py --env-file .env.test"
        ), None

    hosts = {_host(values[k]) for k in KEYS}
    if len(hosts) != 1:
        return None, ".env.test logins point at different hosts"
    test_host = hosts.pop()
    for name, host in _other_hosts().items():
        if host == test_host:
            return (
                None,
                f".env.test points at the same database as {name}. Refusing to run.",
            )

    # Set before core.settings is imported, and override anything from .env.
    for k in KEYS:
        os.environ[k] = values[k]
    return None, None


SKIP_REASON, STOP_REASON = _load_test_env()


def pytest_configure(config):
    config.addinivalue_line("markers", "db: needs the Neon test branch (.env.test)")
    if STOP_REASON:
        pytest.exit(STOP_REASON, returncode=2)


def pytest_collection_modifyitems(config, items):
    if SKIP_REASON:
        skip = pytest.mark.skip(reason=SKIP_REASON)
        for item in items:
            if "db" in item.keywords:
                item.add_marker(skip)


# --- Fixtures ----------------------------------------------------------------


@pytest.fixture
def clean_db():
    """Empty the story tables on the test branch before each database test."""
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("truncate quizzes, stories restart identity")
    yield


@pytest.fixture
def publisher_id() -> int:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        return conn.execute(
            "select id from publishers where name = 'NASA Earth Observatory'"
        ).fetchone()[0]


@pytest.fixture
def make_draft(publisher_id):
    """Build a valid StoryDraft. Pass n for a different article, plus any overrides."""
    from core.models import StoryDraft

    def build(n: int = 1, **overrides):
        data = {
            "publisher_id": publisher_id,
            "source_url": f"https://earthobservatory.nasa.gov/images/test-{n}",
            "title": f"Source title {n}",
            "text_hash": f"hash-{n}",
            "source_text": "PRIVATE SOURCE TEXT",
            "kid_title": f"Kid title {n}",
            "summary": "A short summary.",
            "body": "The body of the story.",
            "slug": f"story-{n}",
            "model": "claude-haiku-5-5",
            "prompt_version": "v1",
            "concepts": [
                {"name": "Albedo", "explanation": "How much light bounces off."}
            ],
            "quiz": {
                "questions": [
                    {
                        "prompt": f"Question {i}?",
                        "options": ["Ice", "Rain", "Wind", "Sun"],
                        "correct_index": 2,
                        "explanation": "The story says so.",
                    }
                    for i in range(4)
                ]
            },
        }
        return StoryDraft(**(data | overrides))

    return build
