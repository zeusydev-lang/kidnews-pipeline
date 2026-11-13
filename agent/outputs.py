"""What the model is allowed to write, and how it becomes a StoryDraft.

StoryOutput holds only the AI-written fields. Source details (URL, title,
credit, text hash) and bookkeeping (model, prompt version, slug) come from
code, so the model can never invent them. The quiz uses core.models.Quiz.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from core.models import Quiz, StoryDraft, slugify


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Concept(_Output):
    name: str = Field(min_length=1, max_length=80, description="Short concept name")
    explanation: str = Field(
        min_length=1,
        max_length=400,
        description="One or two sentences a 6th to 8th grader understands",
    )


class GlossaryTerm(_Output):
    term: str = Field(min_length=1, max_length=60)
    definition: str = Field(min_length=1, max_length=200)


class StoryOutput(_Output):
    """A middle-school story based on one NASA Earth Observatory article."""

    kid_title: str = Field(min_length=1, max_length=150)
    summary: str = Field(
        min_length=1, max_length=1500, description="2-3 sentences, original wording"
    )
    body: str = Field(
        min_length=1,
        max_length=10000,
        description="The story, in short paragraphs separated by blank lines",
    )
    topic: str = Field(min_length=1, max_length=60, description="e.g. Ice and snow")
    concepts: list[Concept] = Field(min_length=2, max_length=3)
    glossary: list[GlossaryTerm] = Field(min_length=1, max_length=8)


def to_story_draft(
    story: StoryOutput,
    quiz: Quiz,
    *,
    publisher_id: int,
    source_url: str,
    title: str,
    text_hash: str,
    model: str,
    prompt_version: str,
    source_text: str | None = None,
    author: str | None = None,
    image_credit: str | None = None,
    image_url: str | None = None,
    published_on: date | None = None,
    reading_level: float | None = None,
) -> StoryDraft:
    """Combine the AI-written parts with the source details from code."""
    return StoryDraft(
        publisher_id=publisher_id,
        source_url=source_url,
        title=title,
        author=author,
        image_credit=image_credit,
        image_url=image_url,
        published_on=published_on,
        text_hash=text_hash,
        source_text=source_text,
        kid_title=story.kid_title,
        summary=story.summary,
        body=story.body,
        slug=slugify(story.kid_title),
        topic=story.topic,
        reading_level=reading_level,
        concepts=[c.model_dump() for c in story.concepts],
        glossary=[g.model_dump() for g in story.glossary],
        model=model,
        prompt_version=prompt_version,
        quiz=quiz,
    )
