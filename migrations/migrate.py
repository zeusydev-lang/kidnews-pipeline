"""Apply every .sql file in this folder, in name order (001_, 002_, ...).

Each file runs in its own transaction: if any statement fails, that file
makes no changes and the runner stops.

Rule for migration files: they must be safe to run again
(use IF NOT EXISTS, ON CONFLICT DO NOTHING, ADD COLUMN IF NOT EXISTS).

Usage:
    uv run python migrations/migrate.py
"""

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

MIGRATIONS_DIR = Path(__file__).parent


def main() -> int:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set. Add it to .env.", file=sys.stderr)
        return 1

    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print("No .sql files found.")
        return 0

    info = psycopg.conninfo.conninfo_to_dict(url)
    print(f"Target: {info.get('host')} / {info.get('dbname')}")

    with psycopg.connect(url, autocommit=True) as conn:
        for path in files:
            print(f"Applying {path.name} ... ", end="", flush=True)
            try:
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
            except psycopg.Error as err:
                print("FAILED")
                print(f"  {err}", file=sys.stderr)
                return 1
            print("ok")

    print(f"Done. {len(files)} file(s) applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
