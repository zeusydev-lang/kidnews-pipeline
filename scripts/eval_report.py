"""Pass rates for every check, and how often the judge agrees with you.

Reads eval_runs and your review decisions (admin login). Writes nothing.
Prints Markdown tables you can paste into the README.

Usage:
    uv run python -m scripts.eval_report                               # dev
    uv run --env-file .env.production python -m scripts.eval_report    # production
"""

import sys

from core.db import connect

JUDGE_CHECKS = ("grounding", "quiz_answerable", "safety", "tone", "judge")


def main() -> int:
    with connect("admin") as conn:
        rates = conn.execute(
            """
            select check_name,
                   count(*) as runs,
                   count(*) filter (where passed) as passed
              from eval_runs
             group by check_name
             order by min(id)
            """
        ).fetchall()
        stories = conn.execute(
            """
            select s.id, s.status,
                   bool_or(not e.passed) filter (
                       where e.check_name = any(%s)
                   ) as judge_flagged
              from stories s
              join eval_runs e on e.story_id = s.id
             group by s.id, s.status
            """,
            [list(JUDGE_CHECKS)],
        ).fetchall()

    if not rates:
        print("No eval results yet. They are saved with each new draft.")
        return 0

    print(f"## Check pass rates ({len(stories)} stories)\n")
    print("| Check | Passed | Rate |")
    print("|---|---|---|")
    for r in rates:
        pct = 100 * r["passed"] / r["runs"]
        print(f"| {r['check_name']} | {r['passed']}/{r['runs']} | {pct:.0f}% |")

    reviewed = [
        s for s in stories if s["status"] in ("approved", "published", "rejected")
    ]
    print(f"\n## Judge vs your review ({len(reviewed)} reviewed stories)\n")
    if not reviewed:
        print("No reviewed stories with eval results yet.")
        return 0
    counts = {"both_ok": 0, "both_flag": 0, "judge_only": 0, "you_only": 0}
    for s in reviewed:
        rejected = s["status"] == "rejected"
        flagged = bool(s["judge_flagged"])
        key = (
            "both_flag"
            if flagged and rejected
            else "judge_only"
            if flagged
            else "you_only"
            if rejected
            else "both_ok"
        )
        counts[key] += 1
    agree = counts["both_ok"] + counts["both_flag"]
    print("| | You approved | You rejected |")
    print("|---|---|---|")
    print(f"| **Judge passed** | {counts['both_ok']} | {counts['you_only']} |")
    print(f"| **Judge flagged** | {counts['judge_only']} | {counts['both_flag']} |")
    print(f"\nAgreement: {agree}/{len(reviewed)} ({100 * agree / len(reviewed):.0f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
