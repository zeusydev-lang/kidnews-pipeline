"""Measure judge models against your own review decisions. Writes nothing.

Runs the judge on stories you've already approved or rejected, and shows
whether the judge would have flagged them. Use it to pick JUDGE_MODEL and to
check a new judge prompt before switching.

Usage (dev database, needs ANTHROPIC_API_KEY):
    uv run python -m scripts.judge_eval
    uv run python -m scripts.judge_eval --ids 17 18 19
    uv run python -m scripts.judge_eval --models anthropic/claude-haiku-5-5
"""

import argparse
import asyncio
import sys

from agent.judge import judge_story
from agent.llm import LlmClient, LlmError, LlmUsage
from agent.outputs import StoryOutput
from agent.structured import StructuredOutputError
from core import stories
from core.models import Quiz

DEFAULT_MODELS = ["anthropic/claude-haiku-5-5", "anthropic/claude-sonnet-5-5"]
REVIEWED = ("approved", "published", "rejected")


def load(story_id: int) -> tuple[dict, StoryOutput, Quiz]:
    row = stories.get_story(story_id)
    story = StoryOutput(
        kid_title=row["kid_title"],
        summary=row["summary"],
        body=row["body"],
        topic=row["topic"] or "Earth",
        concepts=row["concepts"] or [],
        glossary=row["glossary"] or [],
    )
    return row, story, Quiz(questions=row["questions"])


async def evaluate(model: str, ids: list[int]) -> dict:
    client = LlmClient(model)
    usage = LlmUsage()
    agree = 0
    print(f"\n=== {model}")
    for story_id in ids:
        row, story, quiz = load(story_id)
        human = "reject" if row["status"] == "rejected" else "approve"
        try:
            _, results = await judge_story(
                client, row["source_text"], story, quiz, usage
            )
        except (LlmError, StructuredOutputError) as err:
            print(f"  #{story_id}: judge failed: {err}")
            continue
        flags = [r for r in results if not r.passed]
        judge = "flag" if flags else "pass"
        match = (judge == "flag") == (human == "reject")
        agree += match
        print(f"  #{story_id} {row['kid_title'][:45]!r}")
        print(
            f"     you: {human}"
            + (f" ({row['review_notes']})" if row["review_notes"] else "")
        )
        print(f"     judge: {judge} {'(agrees)' if match else '(DISAGREES)'}")
        for r in flags:
            if r.check_name == "grounding":
                for c in r.details["unsupported"]:
                    print(f"       unsupported: {c['claim']}")
                    print(f"         why: {c['evidence']}")
            elif r.check_name == "quiz_answerable":
                for q in r.details["problems"]:
                    print(f"       quiz Q{q['question_number']}: {q['note']}")
            else:
                print(f"       {r.check_name}: {r.details.get('notes', '')}")
    cost = f"${usage.cost_usd:.4f}" if usage.cost_usd is not None else "unknown"
    print(f"  agreement: {agree}/{len(ids)}   cost: {cost}")
    return {"model": model, "agree": agree, "total": len(ids), "cost": usage.cost_usd}


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure judge models.")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--ids", nargs="+", type=int)
    args = parser.parse_args(argv)

    ids = args.ids or [
        s["id"] for s in stories.list_stories() if s["status"] in REVIEWED
    ]
    if not ids:
        print("No reviewed stories yet. Approve or reject some drafts first.")
        return 1
    summary = [await evaluate(m, ids) for m in args.models]
    print("\n=== Summary")
    for s in summary:
        cost = f"${s['cost']:.4f}" if s["cost"] is not None else "unknown"
        print(
            f"  {s['model']:35} agrees with you on {s['agree']}/{s['total']}   {cost}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
