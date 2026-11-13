"""What the model is allowed to write, and how it becomes a StoryDraft.

StoryOutput holds only the AI-written fields. Source details (URL, title,
credit, text hash) and bookkeeping (model, prompt version, slug) come from
code, so the model can never invent them.

QuizOutput has the model write the correct answer and three wrong answers as
text. Code shuffles them and works out correct_index, so the model can never
mark the wrong option as correct.
"""

import hashlib
import random
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.models import OPTIONS_PER_QUESTION, Quiz, QuizQuestion, StoryDraft, slugify


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


class QuizItem(_Output):
    prompt: str = Field(min_length=1, max_length=300)
    correct_answer: str = Field(min_length=1, max_length=150)
    wrong_answers: list[str] = Field(
        min_length=OPTIONS_PER_QUESTION - 1,
        max_length=OPTIONS_PER_QUESTION - 1,
        description="Three believable but clearly wrong answers",
    )
    explanation: str = Field(
        min_length=1, max_length=400, description="One sentence: what the story says"
    )

    @field_validator("wrong_answers")
    @classmethod
    def wrong_answers_are_filled(cls, answers: list[str]) -> list[str]:
        cleaned = [a.strip() for a in answers]
        if any(not a for a in cleaned):
            raise ValueError("wrong answers must not be empty")
        return cleaned

    @model_validator(mode="after")
    def answers_are_all_different(self) -> "QuizItem":
        answers = [self.correct_answer, *self.wrong_answers]
        if len({a.casefold() for a in answers}) != len(answers):
            raise ValueError("the correct answer and wrong answers must all differ")
        return self


class QuizOutput(_Output):
    """A multiple-choice quiz about one story."""

    questions: list[QuizItem] = Field(min_length=4, max_length=5)


def to_quiz(output: QuizOutput, seed: str = "") -> Quiz:
    """Shuffle each question's answers into place and record where the correct
    one went. Correct answers are spread across A-D, and the same seed always
    gives the same order. Raises ValidationError if options repeat."""
    rng = random.Random(hashlib.sha256(seed.encode("utf-8")).hexdigest())
    positions: list[int] = []
    questions = []
    for item in output.questions:
        if not positions:  # each round of 4 uses every position once
            positions = list(range(OPTIONS_PER_QUESTION))
            rng.shuffle(positions)
        correct_index = positions.pop()
        options = list(item.wrong_answers)
        rng.shuffle(options)
        options.insert(correct_index, item.correct_answer)
        questions.append(
            QuizQuestion(
                prompt=item.prompt,
                options=options,
                correct_index=correct_index,
                explanation=item.explanation,
            )
        )
    return Quiz(questions=questions)


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
    image_alt: str | None = None,
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
        image_alt=image_alt,
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
