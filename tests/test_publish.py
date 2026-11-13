"""Publish script against the Neon test branch, writing into a temp folder."""

import json

import pytest

from core import stories
from publish.build import PUBLIC_FIELDS, public_story, write_site_data

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_db")]

PRIVATE = (
    "PRIVATE SOURCE TEXT",
    "review_notes",
    "source_text",
    "text_hash",
    "prompt_version",
)


def publish(out):
    for row in stories.get_publishable():
        stories.mark_published(row["id"])
    return write_site_data(stories.get_published(), out, images=False)


def test_publish_end_to_end(tmp_path, make_draft):
    approved = stories.save_draft(make_draft(1))
    stories.save_draft(make_draft(2))  # stays a draft
    rejected = stories.save_draft(make_draft(3))
    stories.approve(approved)
    stories.reject(rejected, "Not accurate")

    assert publish(tmp_path) == 1
    assert stories.get_story(approved)["status"] == "published"

    index = json.loads((tmp_path / "data" / "index.json").read_text(encoding="utf-8"))
    assert [s["slug"] for s in index["stories"]] == ["story-1"]

    files = sorted(p.name for p in (tmp_path / "data" / "stories").iterdir())
    assert files == ["story-1.json"]
    story = json.loads(
        (tmp_path / "data" / "stories" / "story-1.json").read_text(encoding="utf-8")
    )
    assert set(story) == set(PUBLIC_FIELDS)
    assert len(story["questions"]) == 4
    assert story["publisher_name"] == "NASA Earth Observatory"


def test_no_private_text_anywhere(tmp_path, make_draft):
    story_id = stories.save_draft(make_draft(1))
    stories.approve(story_id)
    publish(tmp_path)
    for path in (tmp_path / "data").rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        for word in PRIVATE:
            assert word not in text, f"{word} found in {path.name}"


def test_rebuild_removes_old_files(tmp_path, make_draft):
    old = tmp_path / "data" / "stories" / "sample-old.json"
    old.parent.mkdir(parents=True)
    old.write_text("{}")
    story_id = stories.save_draft(make_draft(1))
    stories.approve(story_id)
    publish(tmp_path)
    assert not old.exists()


def test_running_twice_is_safe(tmp_path, make_draft):
    story_id = stories.save_draft(make_draft(1))
    stories.approve(story_id)
    publish(tmp_path)
    assert publish(tmp_path) == 1


def test_images_are_never_hotlinked_without_download(tmp_path, make_draft):
    draft = make_draft(1, image_url="https://example.com/picture.jpg")
    stories.approve(stories.save_draft(draft))
    publish(tmp_path)  # images=False
    story = json.loads(
        (tmp_path / "data" / "stories" / "story-1.json").read_text(encoding="utf-8")
    )
    assert story["image_url"] is None


def test_private_field_in_row_fails_loudly():
    with pytest.raises(RuntimeError):
        public_story({"slug": "x", "source_text": "secret"})
