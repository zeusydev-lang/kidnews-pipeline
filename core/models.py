"""Shapes and rules for stories and quizzes, checked in Python before saving.

The database also enforces some of these (status rules, 4-5 questions),
but checking here gives clear error messages and catches bad model output
before it ever reaches Postgres.
"""

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

StoryStatus = Literal["draft", "approved", "rejected", "published"]

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
OPTIONS_PER_QUESTION = 4


class _Strict(BaseModel):
    # Trim spaces and refuse unknown fields, so typos in model output are caught.
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class QuizQuestion(_Strict):
    prompt: str = Field(min_length=1, max_length=300)
    options: list[str] = Field(
        min_length=OPTIONS_PER_QUESTION, max_length=OPTIONS_PER_QUESTION
    )
    correct_index: int = Field(ge=0, lt=OPTIONS_PER_QUESTION)
    explanation: str = Field(min_length=1, max_length=400)

    @field_validator("options")
    @classmethod
    def options_are_filled_and_unique(cls, options: list[str]) -> list[str]:
        cleaned = [o.strip() for o in options]
        if any(not o for o in cleaned):
            raise ValueError("options must not be empty")
        if len({o.casefold() for o in cleaned}) != len(cleaned):
            raise ValueError("options must not repeat")
        return cleaned


class Quiz(_Strict):
    questions: list[QuizQuestion] = Field(min_length=4, max_length=5)

    @model_validator(mode="after")
    def prompts_are_unique(self) -> "Quiz":
        prompts = [q.prompt.casefold() for q in self.questions]
        if len(set(prompts)) != len(prompts):
            raise ValueError("questions must not repeat")
        return self


class StoryDraft(_Strict):
    """Everything the agent saves for one article. Status is always 'draft'."""

    # Source article
    publisher_id: int = Field(gt=0)
    source_url: str = Field(pattern=r"^https?://", max_length=500)
    title: str = Field(min_length=1, max_length=300)
    author: str | None = Field(default=None, max_length=200)
    image_credit: str | None = Field(default=None, max_length=300)
    published_on: date | None = None
    text_hash: str = Field(min_length=1, max_length=128)
    source_text: str | None = None  # private: never published

    # AI-written story
    kid_title: str = Field(min_length=1, max_length=150)
    summary: str = Field(min_length=1, max_length=1500)
    body: str = Field(min_length=1, max_length=10000)
    slug: str = Field(min_length=1, max_length=80)
    topic: str | None = Field(default=None, max_length=60)
    reading_level: float | None = Field(default=None, ge=0, le=20)
    image_url: str | None = Field(default=None, pattern=r"^https?://", max_length=500)
    concepts: list[dict[str, Any]] | None = None
    glossary: list[dict[str, Any]] | None = None

    # How it was made
    model: str = Field(min_length=1, max_length=100)
    prompt_version: str = Field(min_length=1, max_length=50)

    quiz: Quiz

    @field_validator("slug")
    @classmethod
    def slug_is_url_safe(cls, slug: str) -> str:
        if not SLUG_PATTERN.fullmatch(slug):
            raise ValueError(
                "slug must be lowercase letters, numbers and single hyphens"
            )
        return slug


def slugify(text: str, max_length: int = 80) -> str:
    """Turn a title into a slug: 'Melting Glaciers!' -> 'melting-glaciers'."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return slug[:max_length].rstrip("-")
