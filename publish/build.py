"""Publish approved stories to the static website.

Steps:
  1. Mark every approved story as published (kidnews_publish login).
  2. Read ALL published stories from the public_stories view.
  3. Download each story image into web/images/stories/ so visitors never
     load images from other sites. If a download fails, the story has no image.
  4. Rebuild web/data/ from scratch: index.json plus stories/<slug>.json.
     Only fields on the PUBLIC_FIELDS list are written.

Running it again is safe: it always rebuilds the full set of published stories.

Usage:
    uv run python -m publish.build                 # writes data into web/ (local preview)
    uv run python -m publish.build --out dist      # full site (static files + data) in dist/
    uv run python -m publish.build --no-images     # skip image downloads

To publish to the live site, use publish.deploy instead.
"""

import argparse
import json
import shutil
import sys
import tempfile
import urllib.request
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from core import stories
from core.models import SLUG_PATTERN

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DIST_MARKER = ".kidnews-dist"  # only folders with this file are ever wiped

# The only fields that ever reach the public site.
PUBLIC_FIELDS = (
    "slug",
    "kid_title",
    "summary",
    "body",
    "concepts",
    "glossary",
    "topic",
    "reading_level",
    "grade_band",
    "image_url",
    "image_credit",
    "source_title",
    "source_url",
    "source_author",
    "source_published_on",
    "publisher_name",
    "published_at",
    "questions",
)
INDEX_FIELDS = (
    "slug",
    "kid_title",
    "summary",
    "topic",
    "image_url",
    "image_credit",
    "published_at",
)

IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def to_json(value):
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Can't write {type(value).__name__} to JSON")


def public_story(row: dict) -> dict:
    """Keep only public fields. Fails loudly if a private field ever shows up."""
    leaked = {"source_text", "review_notes", "text_hash"} & row.keys()
    if leaked:
        raise RuntimeError(f"Private fields in publish data: {sorted(leaked)}")
    return {k: row.get(k) for k in PUBLIC_FIELDS}


def download_image(url: str, slug: str, images_dir: Path) -> str | None:
    """Save the image locally and return its site path, or None if it can't be used."""
    if urlsplit(url).scheme != "https":
        return None
    request = urllib.request.Request(url, headers={"User-Agent": "kidnews-publish/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content_type = response.headers.get_content_type()
            if content_type not in IMAGE_TYPES:
                print(f"  ! {slug}: not an image ({content_type}), skipping image")
                return None
            data = response.read(MAX_IMAGE_BYTES + 1)
    except OSError as err:
        print(f"  ! {slug}: image download failed ({err}), skipping image")
        return None
    if len(data) > MAX_IMAGE_BYTES:
        print(
            f"  ! {slug}: image larger than {MAX_IMAGE_BYTES // 1024 // 1024} MB, skipping image"
        )
        return None
    name = f"{slug}{IMAGE_TYPES[content_type]}"
    (images_dir / name).write_bytes(data)
    return f"images/stories/{name}"


def write_site_data(rows: list[dict], out: Path, images: bool = True) -> int:
    """Rebuild out/data and out/images/stories from the given published rows."""
    data_dir, images_dir = out / "data", out / "images" / "stories"
    old_images = (
        {p.name: p for p in images_dir.glob("*")} if images_dir.exists() else {}
    )

    with tempfile.TemporaryDirectory(dir=out) as tmp:
        tmp_data, tmp_images = Path(tmp) / "data", Path(tmp) / "images"
        (tmp_data / "stories").mkdir(parents=True)
        tmp_images.mkdir()

        index = []
        for row in rows:
            story = public_story(row)
            slug = story["slug"]
            if not slug or not SLUG_PATTERN.fullmatch(slug):
                print(f"  ! skipping story with bad slug {slug!r}")
                continue

            image = story["image_url"]
            if image and images:
                existing = next(
                    (n for n in old_images if n.rsplit(".", 1)[0] == slug), None
                )
                if existing:  # already downloaded on an earlier run
                    shutil.copy2(old_images[existing], tmp_images / existing)
                    story["image_url"] = f"images/stories/{existing}"
                else:
                    story["image_url"] = download_image(image, slug, tmp_images)
            elif image:
                story["image_url"] = None  # never hotlink

            (tmp_data / "stories" / f"{slug}.json").write_text(
                json.dumps(story, default=to_json, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            index.append({k: story[k] for k in INDEX_FIELDS})

        index.sort(key=lambda s: s["published_at"] or "", reverse=True)
        (tmp_data / "index.json").write_text(
            json.dumps(
                {"generated_at": datetime.now(UTC), "stories": index},
                default=to_json,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # Swap in the new files only after everything was written.
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(images_dir, ignore_errors=True)
        images_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(tmp_data, data_dir)
        shutil.move(tmp_images, images_dir)
    return len(index)


def copy_static(dest: Path) -> None:
    """Fresh copy of the site's static files (no data, no story images) into dest."""
    if dest.exists() and any(dest.iterdir()):
        if (
            not (dest / DIST_MARKER).exists()
            and dest.resolve() != (ROOT / "dist").resolve()
        ):
            raise SystemExit(
                f"{dest} is not empty and wasn't made by this script. Refusing to wipe it."
            )
        shutil.rmtree(dest)
    shutil.copytree(
        WEB,
        dest,
        ignore=lambda d, names: [
            n
            for n in names
            if (Path(d) == WEB and n == "data")
            or (Path(d) == WEB / "images" and n == "stories")
        ],
    )
    (dest / DIST_MARKER).write_text("Built by publish/build.py. Safe to delete.\n")


def publish(out: Path, images: bool = True) -> int:
    """Mark approved stories published, then write the site data into out."""
    for row in stories.get_publishable():
        stories.mark_published(row["id"])
        print(f"  published {row['slug']}")
    if out.resolve() != WEB.resolve():
        copy_static(out)
    count = write_site_data(stories.get_published(), out, images=images)
    print(f"Wrote {count} stories to {out}")
    return count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish approved stories to the static site."
    )
    parser.add_argument("--out", type=Path, default=WEB)
    parser.add_argument(
        "--no-images", action="store_true", help="don't download images"
    )
    args = parser.parse_args()
    publish(args.out, images=not args.no_images)
    return 0


if __name__ == "__main__":
    sys.exit(main())
