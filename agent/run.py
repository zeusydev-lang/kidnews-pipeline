"""Daily agent run (build plan task 14).

  1. Fetch the NASA Earth Observatory feed and pick the newest article that
     isn't stored yet (checked before any model call, so repeats cost nothing).
  2. Write the story (StoryOutput), then check it. Wording that credits NASA,
     or a reading level far from grades 6-8, gets one retry with the problems.
  3. Write the quiz from the finished story, then check it the same way.
  4. Evals: code checks (reading level, attribution, glossary) plus the judge
     (agent/judge.py: grounding, quiz answerability, safety, tone). An unsafe
     verdict stops the run; everything else is flagged for the reviewer.
  5. Save story + quiz + eval results as a draft with the agent login.
     A person approves it.

Stops if the run's model spend passes DAILY_SPEND_CAP_USD (default $0.25).

Usage:
    uv run python -m agent.run              # save a draft (dev database)
    uv run python -m agent.run --dry-run    # print the draft, save nothing
    uv run python -m agent.run --url <article url from the feed>
"""

import argparse
import asyncio
import json
import os
import re
import sys

from agent.judge import judge_client, judge_story
from agent.llm import LlmClient, LlmError, LlmUsage
from agent.nasa_eo import Article, SourceError, fetch_articles
from agent.outputs import QuizOutput, StoryOutput, to_quiz, to_story_draft
from agent.prompts import (
    PROMPT_VERSION,
    QUIZ_INSTRUCTIONS,
    STORY_INSTRUCTIONS,
    fix_request,
    quiz_input,
    story_input,
)
from agent.structured import StructuredOutputError, generate_structured
from core import stories
from core.checks import READING_LEVEL_ALLOWED, attribution_problems, reading_level
from core.models import EvalResult, StoryDraft

PUBLISHER = "NASA Earth Observatory"


class RunError(Exception):
    """The run stopped without saving a story."""


class BudgetExceeded(RunError):
    pass


# --- Checks ------------------------------------------------------------------


def story_problems(story: StoryOutput) -> tuple[list[str], list[str]]:
    """(must-fix problems, soft problems) for a story."""
    texts = [story.kid_title, story.summary, story.body]
    texts += [f"{c.name} {c.explanation}" for c in story.concepts]
    texts += [f"{g.term} {g.definition}" for g in story.glossary]
    hard = [
        f'Uses wording that credits NASA for this text: "{p}". Remove it.'
        for p in attribution_problems(*texts)
    ]
    soft = []
    unused = unused_glossary_terms(story)
    if unused:
        soft.append(
            "Glossary words not used in the summary or body: "
            + ", ".join(unused)
            + ". Only define words the story uses."
        )
    grade = reading_level(story.body)
    # Only "too hard" is worth a retry. A bit too easy is harmless; the
    # reviewer sees the grade on the admin page.
    if grade > READING_LEVEL_ALLOWED[1]:
        soft.append(
            f"Reading level is grade {grade}; aim for grade 6 to 8 "
            "(shorter sentences and simpler words)."
        )
    return hard, soft


def unused_glossary_terms(story: StoryOutput) -> list[str]:
    """Glossary terms that don't appear in the summary or body (plurals count)."""
    text = f"{story.summary} {story.body}".casefold()
    unused = []
    for g in story.glossary:
        term = g.term.casefold().strip()
        stem = re.sub(r"(es|s)$", "", term) if len(term) > 3 else term
        if not re.search(r"\b" + re.escape(stem), text):
            unused.append(g.term)
    return unused


def quiz_problems(quiz: QuizOutput) -> tuple[list[str], list[str]]:
    texts = []
    for q in quiz.questions:
        texts += [q.prompt, q.explanation, q.correct_answer, *q.wrong_answers]
    hard = [
        f'Uses wording that credits NASA: "{p}". Remove it.'
        for p in attribution_problems(*texts)
    ]
    return hard, []


# --- Steps -------------------------------------------------------------------


def check_budget(usage: LlmUsage, cap: float) -> None:
    if usage.cost_usd is not None and usage.cost_usd > cap:
        raise BudgetExceeded(
            f"Spent ${usage.cost_usd:.4f}, over the ${cap:.2f} cap. Stopping."
        )


async def write_checked[T: (StoryOutput, QuizOutput)](
    client: LlmClient,
    model_cls: type[T],
    instructions: str,
    content: str,
    checker,
    usage: LlmUsage,
    cap: float,
) -> tuple[T, list[str]]:
    """Generate, check, retry once with the problems. Returns (result, warnings)."""
    request = content
    for attempt in (1, 2):
        result = await generate_structured(
            client, model_cls, instructions, request, usage=usage
        )
        check_budget(usage, cap)
        hard, soft = checker(result)
        if not hard and not soft:
            return result, []
        if attempt == 2:
            if hard:
                raise RunError(f"{model_cls.__name__} still fails checks: {hard}")
            return result, soft  # soft problems: save, let the reviewer decide
        print(f"  retrying {model_cls.__name__}: {'; '.join(hard + soft)}")
        request = content + fix_request(hard + soft)
    raise AssertionError("unreachable")


def code_eval_results(story: StoryOutput, quiz_output: QuizOutput) -> list[EvalResult]:
    """The code checks, recorded as EvalResults on the final story and quiz."""
    grade = reading_level(story.body)
    low, high = READING_LEVEL_ALLOWED
    texts = [story.kid_title, story.summary, story.body]
    texts += [f"{c.name} {c.explanation}" for c in story.concepts]
    for q in quiz_output.questions:
        texts += [q.prompt, q.explanation, q.correct_answer, *q.wrong_answers]
    phrases = attribution_problems(*texts)
    unused = unused_glossary_terms(story)
    return [
        EvalResult(
            check_name="reading_level",
            passed=low <= grade <= high,
            details={"grade": grade, "allowed": [low, high]},
        ),
        EvalResult(
            check_name="attribution",
            passed=not phrases,
            details={"phrases": phrases},
        ),
        EvalResult(
            check_name="glossary_used",
            passed=not unused,
            details={"unused": unused},
        ),
    ]


def describe(result: EvalResult) -> str:
    """One line for the run log."""
    d = result.details
    if result.check_name == "reading_level":
        info = f"grade {d['grade']}"
    elif result.check_name == "grounding":
        info = f"{d.get('claims_checked', 0)} claims, {len(d.get('unsupported', []))} unsupported"
        for claim in d.get("unsupported", []):
            info += f"\n        - {claim['claim']}"
    elif result.check_name == "quiz_answerable":
        info = f"{len(d.get('problems', []))} problems"
    elif result.check_name in ("safety", "tone"):
        info = d.get("notes", "") if not result.passed else ""
    else:
        info = ", ".join(
            map(str, d.get("phrases") or d.get("unused") or [d.get("error", "")])
        )
    return f"  {'PASS' if result.passed else 'FLAG'}  {result.check_name:16} {info}".rstrip()


def pick_article(articles: list[Article], publisher_id: int, url: str | None):
    if url:
        chosen = [a for a in articles if a.url.rstrip("/") == url.rstrip("/")]
        if not chosen:
            raise RunError(f"{url} isn't in the current feed.")
        candidates = chosen
    else:
        candidates = articles
    for article in candidates:
        if stories.find_existing(publisher_id, article.url) is None:
            return article
    return None


async def make_story(
    article: Article,
    publisher_id: int,
    client: LlmClient,
    judge: LlmClient,
    usage: LlmUsage,
    cap: float,
) -> tuple[StoryDraft, list[EvalResult]]:
    """Write, check and judge one story. Saves nothing.

    Used by the daily run and by scripts/prompt_eval.py, so both measure
    exactly the same steps. Raises RunError for hard failures (including an
    unsafe verdict).
    """
    story, story_warnings = await write_checked(
        client,
        StoryOutput,
        STORY_INSTRUCTIONS,
        story_input(article),
        story_problems,
        usage,
        cap,
    )
    quiz_output, quiz_warnings = await write_checked(
        client,
        QuizOutput,
        QUIZ_INSTRUCTIONS,
        quiz_input(story),
        quiz_problems,
        usage,
        cap,
    )
    # Code, not the model, decides where each correct answer goes.
    quiz = to_quiz(quiz_output, seed=article.url)

    draft = to_story_draft(
        story,
        quiz,
        publisher_id=publisher_id,
        source_url=article.url,
        title=article.title,
        text_hash=article.text_hash,
        source_text=article.text,
        author=article.author,
        image_credit=article.image_credit,
        image_url=article.image_url,
        image_alt=article.image_alt,
        published_on=article.published_on,
        model=client.model,
        prompt_version=PROMPT_VERSION,
        reading_level=reading_level(story.body),
    )

    print(f"Story: {draft.kid_title}")
    print(f"  reading level: grade {draft.reading_level}, topic: {draft.topic}")
    if (
        draft.reading_level is not None
        and draft.reading_level < READING_LEVEL_ALLOWED[0]
    ):
        story_warnings.append(f"Reading level is low (grade {draft.reading_level}).")
    for warning in story_warnings + quiz_warnings:
        print(f"  warning: {warning}")
    # Evals: code checks, then the judge. A judge failure (API or bad answer)
    # doesn't stop the daily story; it's recorded and flagged instead.
    evals = code_eval_results(story, quiz_output)
    try:
        _, judge_results = await judge_story(judge, article.text, story, quiz, usage)
    except (LlmError, StructuredOutputError) as err:
        judge_results = [
            EvalResult(
                check_name="judge",
                passed=False,
                details={"error": str(err)},
                judge_model=judge.model,
            )
        ]
    evals += judge_results
    check_budget(usage, cap)

    print(f"Checks (judge: {judge.model}):")
    for result in evals:
        print(describe(result))
    unsafe = [r for r in evals if r.check_name == "safety" and not r.passed]
    if unsafe:
        raise RunError(
            "The judge marked the story unsafe for kids, so it wasn't saved: "
            + (unsafe[0].details.get("notes") or "no details")
        )
    return draft, evals


async def run(
    *,
    dry_run: bool = False,
    url: str | None = None,
    client: LlmClient | None = None,
    judge: LlmClient | None = None,
    articles: list[Article] | None = None,
    cap: float | None = None,
) -> StoryDraft | None:
    """One full run. Returns the draft (saved unless dry_run), or None if
    there's nothing new."""
    client = client or LlmClient()
    cap = (
        cap if cap is not None else float(os.environ.get("DAILY_SPEND_CAP_USD", "0.25"))
    )
    usage = LlmUsage()

    publisher_id = stories.get_publisher_id(PUBLISHER)
    articles = articles if articles is not None else fetch_articles()
    article = pick_article(articles, publisher_id, url)
    if article is None:
        print("Nothing new in the feed. Every article is already stored.")
        return None
    print(
        f"Article: {article.title} ({article.published_on}, {article.word_count} words)"
    )
    print(f"  {article.url}")

    draft, evals = await make_story(
        article, publisher_id, client, judge or judge_client(), usage, cap
    )

    cost = f"${usage.cost_usd:.4f}" if usage.cost_usd is not None else "unknown"
    print(f"  tokens: {usage.input_tokens} in / {usage.output_tokens} out, cost {cost}")

    if dry_run:
        print("\nDry run, not saved:")
        print(
            json.dumps(draft.model_dump(mode="json", exclude={"source_text"}), indent=2)
        )
        return draft

    story_id = stories.save_draft(draft, evals)
    print(f"Saved draft {story_id}. Review it on the admin page.")
    return draft


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make today's kid story as a draft.")
    parser.add_argument("--dry-run", action="store_true", help="print, don't save")
    parser.add_argument("--url", help="use this article from the feed")
    args = parser.parse_args(argv)
    try:
        asyncio.run(run(dry_run=args.dry_run, url=args.url))
    except (RunError, SourceError, LlmError, StructuredOutputError) as err:
        print(f"Run stopped: {err}", file=sys.stderr)
        return 1
    except stories.DuplicateStory as err:
        print(f"Already stored: {err}", file=sys.stderr)
        return 0
    except stories.SlugTaken:
        print(
            "Run stopped: another story already has this title's slug.", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
