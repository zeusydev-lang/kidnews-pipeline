"""Connection settings for each database login.

Each part of the pipeline asks for its own role:
    agent   -> AGENT_DATABASE_URL    (insert drafts only)
    admin   -> ADMIN_DATABASE_URL    (approve / reject)
    publish -> PUBLISH_DATABASE_URL  (read public view, mark published)

The owner login (DATABASE_URL) is deliberately not available here.
It is only for migrations/.
"""

import os
from typing import Literal

from dotenv import load_dotenv

Role = Literal["agent", "admin", "publish"]

ENV_KEYS: dict[Role, str] = {
    "agent": "AGENT_DATABASE_URL",
    "admin": "ADMIN_DATABASE_URL",
    "publish": "PUBLISH_DATABASE_URL",
}

# Values already set in the environment (GitHub Secrets, --env-file) win over .env.
load_dotenv(override=False)


class SettingsError(RuntimeError):
    """A required setting is missing."""


def database_url(role: Role) -> str:
    """Return the connection string for one role, or fail with a clear message."""
    if role not in ENV_KEYS:
        raise SettingsError(
            f"Unknown role {role!r}. Use one of: {', '.join(ENV_KEYS)}."
        )
    key = ENV_KEYS[role]
    url = os.environ.get(key, "").strip()
    if not url:
        raise SettingsError(
            f"{key} is not set. Run: uv run python migrations/logins.py --env-file .env"
        )
    return url
