"""Publish approved stories and upload the site to Cloudflare Pages.

Production only: refuses to run unless the publish login points at the
production database (or it's running in GitHub Actions, which only has
production secrets). Published data is built in dist/ and never committed.

Local:
    npx wrangler@4.155.0 login          # once, opens the browser
    uv run --env-file .env.production python -m publish.deploy

GitHub Actions sets CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID instead of login.

Options:
    --build-only   build dist/ but don't upload
"""

import argparse
import os
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

from dotenv import dotenv_values

from core.settings import database_url
from publish.build import DIST_MARKER, ROOT, publish

WRANGLER = "wrangler@4.155.0"
DIST = ROOT / "dist"


def require_production() -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return
    prod_file = ROOT / ".env.production"
    prod_url = (
        dotenv_values(prod_file).get("DATABASE_URL") if prod_file.exists() else None
    )
    if (
        not prod_url
        or urlsplit(prod_url).hostname != urlsplit(database_url("publish")).hostname
    ):
        sys.exit(
            "Refusing to deploy: the publish login doesn't point at production.\n"
            "Run: uv run --env-file .env.production python -m publish.deploy"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish and deploy to Cloudflare Pages."
    )
    parser.add_argument(
        "--build-only", action="store_true", help="build dist/ but don't upload"
    )
    args = parser.parse_args()

    require_production()
    publish(DIST)

    if args.build_only:
        print(f"Built {DIST}. Not uploaded (--build-only).")
        return 0

    (DIST / DIST_MARKER).unlink(missing_ok=True)  # helper file, not part of the site
    project = os.environ.get("CLOUDFLARE_PAGES_PROJECT", "kidnews-earth")
    npx = shutil.which("npx")
    if not npx:
        sys.exit("npx not found. Install Node.js to deploy.")
    command = [
        npx,
        "--yes",
        WRANGLER,
        "pages",
        "deploy",
        str(DIST),
        "--project-name",
        project,
        "--branch",
        "main",
        "--commit-dirty=true",
    ]
    print(f"Uploading to Cloudflare Pages project '{project}'...")
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
