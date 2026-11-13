"""Business layer (core/stories.py) against the Neon test branch."""

import psycopg
import pytest

from core import stories
from core.models import Quiz, QuizQuestion
from core.stories import DuplicateStory, InvalidStatusChange, SlugTaken, StoryNotFound

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_db")]

PRIVATE_FIELDS = {"source_text", "review_notes", "text_hash", "model", "prompt_version"}


# --- Saving drafts -----------------------------------------------------------


def test_save_draft_stores_story_and_quiz(make_draft):
    story_id = stories.save_draft(make_draft(1))

    story = stories.get_story(story_id)
    assert story["status"] == "draft"
    assert story["kid_title"] == "Kid title 1"
    assert story["publisher_name"] == "NASA Earth Observatory"
    assert story["concepts"][0]["name"] == "Albedo"
    assert len(story["questions"]) == 4
    assert story["questions"][0]["correct_index"] == 2


def test_duplicate_article_is_refused(make_draft):
    stories.save_draft(make_draft(1))
    with pytest.raises(DuplicateStory):
        stories.save_draft(make_draft(1, slug="a-different-slug"))


def test_duplicate_slug_is_refused(make_draft):
    stories.save_draft(make_draft(1))
    with pytest.raises(SlugTaken):
        stories.save_draft(make_draft(2, slug="story-1"))


def test_bad_quiz_saves_nothing(make_draft):
    """If the quiz insert fails, the story must not be saved either."""
    good = make_draft(1)
    three = [
        QuizQuestion.model_construct(**q.model_dump()) for q in good.quiz.questions[:3]
    ]
    bad = good.model_copy(
        update={"quiz": Quiz.model_construct(questions=three)}
    )  # skips Python checks

    with pytest.raises(psycopg.errors.CheckViolation):
        stories.save_draft(bad)
    assert stories.find_existing(good.publisher_id, good.source_url) is None


def test_find_existing(make_draft, publisher_id):
    story_id = stories.save_draft(make_draft(1))
    assert stories.find_existing(publisher_id, make_draft(1).source_url) == story_id
    assert stories.find_existing(publisher_id, "https://example.com/never-seen") is None


# --- Listing and reading -----------------------------------------------------


def test_list_drafts(make_draft):
    a = stories.save_draft(make_draft(1))
    b = stories.save_draft(make_draft(2))
    stories.reject(b, "Off topic")

    assert [s["id"] for s in stories.list_stories("draft")] == [a]
    assert [s["id"] for s in stories.list_stories("rejected")] == [b]
    assert [s["id"] for s in stories.list_stories()] == [b, a]  # newest first


def test_get_missing_story():
    with pytest.raises(StoryNotFound):
        stories.get_story(999_999)


# --- Approve and reject ------------------------------------------------------


def test_approve_draft(make_draft):
    story_id = stories.save_draft(make_draft(1))
    stories.approve(story_id)

    story = stories.get_story(story_id)
    assert story["status"] == "approved"
    assert story["approved_at"] is not None


def test_approve_twice_is_refused(make_draft):
    story_id = stories.save_draft(make_draft(1))
    stories.approve(story_id)
    with pytest.raises(InvalidStatusChange):
        stories.approve(story_id)


def test_approve_missing_story():
    with pytest.raises(StoryNotFound):
        stories.approve(999_999)


def test_reject_with_reason(make_draft):
    story_id = stories.save_draft(make_draft(1))
    stories.reject(story_id, "  Too hard for grade 6  ")

    story = stories.get_story(story_id)
    assert story["status"] == "rejected"
    assert story["review_notes"] == "Too hard for grade 6"


@pytest.mark.parametrize("reason", ["", "   "])
def test_reject_without_reason_is_refused(make_draft, reason):
    story_id = stories.save_draft(make_draft(1))
    with pytest.raises(ValueError):
        stories.reject(story_id, reason)
    assert stories.get_story(story_id)["status"] == "draft"


def test_rejected_story_cannot_be_approved(make_draft):
    story_id = stories.save_draft(make_draft(1))
    stories.reject(story_id, "Not accurate")
    with pytest.raises(InvalidStatusChange):
        stories.approve(story_id)


def test_approved_story_cannot_be_rejected(make_draft):
    story_id = stories.save_draft(make_draft(1))
    stories.approve(story_id)
    with pytest.raises(InvalidStatusChange):
        stories.reject(story_id, "Changed my mind")


# --- Publish -----------------------------------------------------------------


def test_publish_flow(make_draft):
    approved = stories.save_draft(make_draft(1))
    draft = stories.save_draft(make_draft(2))
    stories.approve(approved)

    publishable = stories.get_publishable()
    assert [s["id"] for s in publishable] == [approved]  # drafts are not included
    assert len(publishable[0]["questions"]) == 4
    assert not PRIVATE_FIELDS & publishable[0].keys()

    stories.mark_published(approved)
    assert stories.get_publishable() == []
    published = stories.get_published()
    assert [s["id"] for s in published] == [approved]
    assert published[0]["published_at"] is not None

    with pytest.raises(InvalidStatusChange):
        stories.mark_published(approved)  # already published
    with pytest.raises(InvalidStatusChange):
        stories.mark_published(draft)  # never approved
