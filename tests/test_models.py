"""Validation rules for stories and quizzes. No database needed."""

import pytest
from pydantic import ValidationError

from core.models import Quiz, QuizQuestion, StoryDraft, slugify


def question(n: int = 1, **overrides) -> dict:
    q = {
        "prompt": f"Question {n}?",
        "options": ["Ice", "Rain", "Wind", "Sun"],
        "correct_index": 0,
        "explanation": "Because the article says so.",
    }
    return q | overrides


def draft(**overrides) -> dict:
    d = {
        "publisher_id": 1,
        "source_url": "https://earthobservatory.nasa.gov/images/1",
        "title": "Source title",
        "text_hash": "abc123",
        "kid_title": "Why Glaciers Melt",
        "summary": "A short summary.",
        "body": "The body of the story.",
        "slug": "why-glaciers-melt",
        "model": "claude-haiku-5-5",
        "prompt_version": "v1",
        "quiz": {"questions": [question(i) for i in range(1, 5)]},
    }
    return d | overrides


def test_valid_draft():
    story = StoryDraft(**draft())
    assert len(story.quiz.questions) == 4


def test_whitespace_is_trimmed():
    story = StoryDraft(**draft(kid_title="  Why Glaciers Melt  "))
    assert story.kid_title == "Why Glaciers Melt"


@pytest.mark.parametrize("field", ["kid_title", "summary", "body"])
def test_empty_text_is_rejected(field):
    with pytest.raises(ValidationError):
        StoryDraft(**draft(**{field: "   "}))


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        StoryDraft(**draft(status="approved"))


@pytest.mark.parametrize(
    "slug", ["Has-Caps", "two--hyphens", "-leading", "spaces here", "emoji-🌍"]
)
def test_bad_slug_is_rejected(slug):
    with pytest.raises(ValidationError):
        StoryDraft(**draft(slug=slug))


def test_source_url_must_be_http():
    with pytest.raises(ValidationError):
        StoryDraft(**draft(source_url="javascript:alert(1)"))


@pytest.mark.parametrize("count", [3, 6])
def test_quiz_needs_4_to_5_questions(count):
    with pytest.raises(ValidationError):
        Quiz(questions=[question(i) for i in range(count)])


def test_quiz_with_5_questions_is_ok():
    Quiz(questions=[question(i) for i in range(5)])


@pytest.mark.parametrize("index", [-1, 4])
def test_correct_index_must_point_to_an_option(index):
    with pytest.raises(ValidationError):
        QuizQuestion(**question(correct_index=index))


@pytest.mark.parametrize(
    "options",
    [
        ["Ice", "Rain", "Wind"],  # too few
        ["Ice", "Rain", "Wind", "Sun", "Fog"],  # too many
        ["Ice", "ice", "Wind", "Sun"],  # repeat (case-insensitive)
        ["Ice", " ", "Wind", "Sun"],  # empty
    ],
)
def test_bad_options_are_rejected(options):
    with pytest.raises(ValidationError):
        QuizQuestion(**question(options=options))


def test_repeated_questions_are_rejected():
    with pytest.raises(ValidationError):
        Quiz(questions=[question(1)] * 4)


def test_slugify():
    assert slugify("Melting Glaciers in Greenland!") == "melting-glaciers-in-greenland"
    assert slugify("  Ice & Snow -- 2026 ") == "ice-snow-2026"
