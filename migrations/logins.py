"""Turn on login for the kidnews roles and write their connection strings.

For each role (agent, admin, publish) this:
  1. generates a strong random password,
  2. runs ALTER ROLE ... LOGIN PASSWORD using the owner connection,
  3. writes AGENT_DATABASE_URL, ADMIN_DATABASE_URL and PUBLISH_DATABASE_URL
     into the env file you name. Passwords are never printed.

Running it again rotates all three passwords.
Run migrations first, so the roles exist.

Usage:
    uv run python migrations/logins.py --env-file .env
    uv run python migrations/logins.py --env-file .env.production
"""

import argparse
import secrets
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from dotenv import dotenv_values
from psycopg import sql

ROLES = {
    "kidnews_agent": "AGENT_DATABASE_URL",
    "kidnews_admin": "ADMIN_DATABASE_URL",
    "kidnews_publish": "PUBLISH_DATABASE_URL",
}


def url_for(owner_url: str, role: str, password: str) -> str:
    """Same host, database and options as the owner URL, different login."""
    parts = urlsplit(owner_url)
    host = parts.netloc.rsplit("@", 1)[-1]
    netloc = f"{quote(role, safe='')}:{quote(password, safe='')}@{host}"
    return urlunsplit(parts._replace(netloc=netloc))


def write_env(path: Path, values: dict[str, str]) -> None:
    """Replace KEY=... lines that exist, append the ones that don't."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            out.append(f'{key}="{remaining.pop(key)}"')
        else:
            out.append(line)
    out.extend(f'{key}="{value}"' for key, value in remaining.items())
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args()

    owner_url = dotenv_values(args.env_file).get("DATABASE_URL")
    if not owner_url:
        print(f"DATABASE_URL is not set in {args.env_file}.", file=sys.stderr)
        return 1

    info = psycopg.conninfo.conninfo_to_dict(owner_url)
    print(f"Target: {info.get('host')} / {info.get('dbname')}")

    new_urls = {}
    with psycopg.connect(owner_url, autocommit=True) as conn:
        for role, env_key in ROLES.items():
            exists = conn.execute(
                "select 1 from pg_roles where rolname = %s", [role]
            ).fetchone()
            if not exists:
                print(
                    f"Role {role} not found. Run migrations/migrate.py first.",
                    file=sys.stderr,
                )
                return 1
            password = secrets.token_urlsafe(32)
            conn.execute(
                sql.SQL("alter role {} with login password {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
            new_urls[env_key] = url_for(owner_url, role, password)
            print(f"  {role}: login on, password set")

    write_env(args.env_file, new_urls)
    print(f"Wrote {', '.join(new_urls)} to {args.env_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
