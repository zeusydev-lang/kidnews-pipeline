"""Measure the current prompts on a fixed set of NASA articles. Saves no stories.

Runs the same steps as the daily run (write, check, judge) on every article in
evals/articles.json, then prints pass rates per check and writes a summary to
evals/results/<prompt_version>.json so versions can be compared later.

The article set is a snapshot, so every prompt version is measured on exactly
the same text, even after NASA's feed has moved on.

Usage (needs ANTHROPIC_API_KEY; about $0.03 per article):
    uv run python -m scripts.prompt_eval --snapshot 5   # save the 5 newest feed articles
    uv run python -m scripts.prompt_eval                # measure current prompts
    uv run python -m scripts.prompt_eval --compare      # table of all saved versions
"""

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from agent.judge import judge_client
from agent.llm import LlmClient, LlmError, LlmUsage
from agent.nasa_eo import Article, fetch_articles
from agent.prompts import PROMPT_VERSION
from agent.run import RunError, make_story
from agent.structured import StructuredOutputError

ROOT = Path(__file__).resolve().parent.parent
ARTICLES = ROOT / "evals" / "articles.json"
RESULTS = ROOT / "evals" / "results"
CAP_PER_ARTICLE = 0.25


def snapshot(n: int) -> None:
    articles = fetch_articles()[:n]
    ARTICLES.parent.mkdir(parents=True, exist_ok=True)
    ARTICLES.write_text(
        json.dumps([a.model_dump(mode="json") for a in articles], indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Saved {len(articles)} articles to {ARTICLES.relative_to(ROOT)}:")
    for a in articles:
        print(f"  {a.published_on}  {a.title}")


async def measure() -> dict:
    articles = [Article(**a) for a in json.loads(ARTICLES.read_text(encoding="utf-8"))]
    writer, judge = LlmClient(), judge_client()
    total_cost = 0.0
    per_check: dict[str, list[bool]] = defaultdict(list)
    stories = []
    print(f"Prompt {PROMPT_VERSION}, writer {writer.model}, judge {judge.model}\n")

    for article in articles:
        usage = LlmUsage()
        print(f"- {article.title}")
        try:
            draft, evals = await make_story(
                article,
                1,
                writer,
                judge,
                usage,
                CAP_PER_ARTICLE,  # never saved
            )
        except (RunError, LlmError, StructuredOutputError) as err:
            print(f"    stopped: {err}")
            per_check["completed"].append(False)
            stories.append({"article": article.title, "stopped": str(err)})
            continue
        finally:
            total_cost += usage.cost_usd or 0
        per_check["completed"].append(True)
        for e in evals:
            per_check[e.check_name].append(e.passed)
        stories.append(
            {
                "article": article.title,
                "kid_title": draft.kid_title,
                "reading_level": draft.reading_level,
                "flags": {e.check_name: e.details for e in evals if not e.passed},
            }
        )

    rates = {name: {"passed": sum(v), "total": len(v)} for name, v in per_check.items()}
    print(f"\nPrompt {PROMPT_VERSION}: pass rates on {len(articles)} articles")
    for name, r in rates.items():
        print(f"  {name:16} {r['passed']}/{r['total']}")
    unsupported = unsupported_claims(stories)
    print(f"  unsupported claims in total: {unsupported}")
    print(f"  cost: ${total_cost:.4f}")

    result = {
        "prompt_version": PROMPT_VERSION,
        "writer": writer.model,
        "judge": judge.model,
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "articles": len(articles),
        "cost_usd": round(total_cost, 4),
        "rates": rates,
        "stories": stories,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{PROMPT_VERSION}.json"
    out.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"  saved {out.relative_to(ROOT)}")
    return result


def unsupported_claims(stories: list[dict]) -> int:
    """Total unsupported claims across all stories. Finer than the grounding
    pass rate, which fails a whole story for a single claim."""
    return sum(
        len(s.get("flags", {}).get("grounding", {}).get("unsupported", []))
        for s in stories
    )


def compare() -> None:
    files = sorted(RESULTS.glob("*.json"))
    if not files:
        print("No results yet. Run without --compare first.")
        return
    results = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    checks = list(dict.fromkeys(c for r in results for c in r["rates"]))
    print("| Check | " + " | ".join(r["prompt_version"] for r in results) + " |")
    print("|---" * (len(results) + 1) + "|")
    for c in checks:
        cells = []
        for r in results:
            rate = r["rates"].get(c)
            cells.append(f"{rate['passed']}/{rate['total']}" if rate else "-")
        print(f"| {c} | " + " | ".join(cells) + " |")
    print(
        "| unsupported claims (total) | "
        + " | ".join(str(unsupported_claims(r["stories"])) for r in results)
        + " |"
    )
    print("| cost | " + " | ".join(f"${r['cost_usd']:.2f}" for r in results) + " |")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure prompts on fixed articles.")
    parser.add_argument(
        "--snapshot", type=int, metavar="N", help="save N feed articles"
    )
    parser.add_argument("--compare", action="store_true", help="compare saved results")
    args = parser.parse_args(argv)
    if args.snapshot:
        snapshot(args.snapshot)
    elif args.compare:
        compare()
    else:
        if not ARTICLES.exists():
            print("No article set yet. Run with --snapshot 5 first.")
            return 1
        asyncio.run(measure())
    return 0


if __name__ == "__main__":
    sys.exit(main())
