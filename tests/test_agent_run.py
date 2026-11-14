"""The daily run, end to end: sample article -> fake Claude -> Neon test branch."""

import asyncio
import copy
from datetime import date

import pytest

from agent import run as agent_run
from agent.llm import LlmClient
from agent.nasa_eo import Article
from agent.run import RunError, run
from core import stories
from tests.fake_anthropic import FakeAnthropic, tool_reply

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_db")]

ARTICLE = Article(
    title="A Global Rebound for Mangroves",
    url="https://science.nasa.gov/earth/earth-observatory/a-global-rebound-for-mangroves/",
    author="Michala Garrison",
    published_on=date(2026, 11, 13),
    text="Mangrove forests are growing again.\n\nLandsat images show the change.",
    image_url="https://assets.science.nasa.gov/content/dam/mangroves.jpg",
    image_alt="Green mangrove forest along a coast.",
    image_credit="NASA Earth Observatory images by Michala Garrison.",
)

STORY = {
    "kid_title": "Mangrove Forests Are Coming Back",
    "summary": "Mangrove forests grow along warm coasts. They are growing again.",
    "body": (
        "Mangroves are trees that grow in salty water near the coast. "
        "For many years, people cut them down.\n\n"
        "Now satellites show that some mangrove forests are growing back. "
        "That is good news for fish and birds."
    ),
    "topic": "Forests",
    "concepts": [
        {"name": "Mangroves", "explanation": "Trees that grow in salty coastal water."},
        {"name": "Satellites", "explanation": "Machines in space that take pictures."},
    ],
    "glossary": [{"term": "Coast", "definition": "Land next to the sea."}],
}

QUIZ = {  # QuizOutput: the model writes answers as text, code places them
    "questions": [
        {
            "prompt": f"Question {i}?",
            "correct_answer": f"Right {i}",
            "wrong_answers": [f"Wrong {i}a", f"Wrong {i}b", f"Wrong {i}c"],
            "explanation": "The story says so.",
        }
        for i in range(4)
    ]
}

VERDICT = {  # JudgeVerdict: everything checks out
    "claims": [
        {
            "claim": "Mangrove forests are growing again.",
            "supported": True,
            "evidence": "Mangrove forests are growing again.",
        }
    ],
    "questions": [
        {"question_number": n, "answer_supported": True, "one_correct_answer": True}
        for n in range(1, 5)
    ],
    "safe_for_kids": True,
    "tone_ok": True,
}


def answer(args, call_id="toolu_1", input_tokens=2000, output_tokens=500):
    return tool_reply(
        [(call_id, "final_answer", args)], None, input_tokens, output_tokens
    )


@pytest.fixture(scope="module")
def fake():
    server = FakeAnthropic()
    yield server
    server.close()


@pytest.fixture
def client(fake, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    fake.reset()
    return LlmClient("anthropic/claude-haiku-5-5", api_base=fake.url, num_retries=0)


def go(client, **kwargs):
    kwargs.setdefault("articles", [ARTICLE])
    kwargs.setdefault("judge", client)  # judge on the same fake server
    return asyncio.run(run(client=client, **kwargs))


def test_saves_a_draft(client, fake):
    fake.reset(answer(STORY), answer(QUIZ), answer(VERDICT))
    draft = go(client)

    story_id = stories.find_existing(draft.publisher_id, ARTICLE.url)
    saved = stories.get_story(story_id)
    assert saved["status"] == "draft"
    assert saved["kid_title"] == "Mangrove Forests Are Coming Back"
    assert saved["slug"] == "mangrove-forests-are-coming-back"
    assert saved["image_alt"] == ARTICLE.image_alt
    assert saved["image_credit"] == ARTICLE.image_credit
    assert saved["source_text"] == ARTICLE.text
    assert saved["model"] == "anthropic/claude-haiku-5-5"
    assert saved["prompt_version"] == "v4"
    assert saved["reading_level"] is not None
    assert len(saved["questions"]) == 4


def test_article_goes_in_as_untrusted_source(client, fake):
    fake.reset(answer(STORY), answer(QUIZ), answer(VERDICT))
    go(client, dry_run=True)
    story_request, quiz_request, _judge_request = fake.requests
    user_text = story_request["messages"][0]["content"][0]["text"]
    assert user_text.startswith("<article>") and user_text.endswith("</article>")
    assert "ignore them" in str(story_request["system"])
    quiz_text = quiz_request["messages"][0]["content"][0]["text"]
    assert quiz_text.startswith("<story>") and "Mangrove Forests" in quiz_text


def test_dry_run_saves_nothing(client, fake):
    fake.reset(answer(STORY), answer(QUIZ), answer(VERDICT))
    draft = go(client, dry_run=True)
    assert draft.kid_title == STORY["kid_title"]
    assert stories.find_existing(draft.publisher_id, ARTICLE.url) is None


def test_skips_articles_already_stored(client, fake):
    fake.reset(answer(STORY), answer(QUIZ), answer(VERDICT))
    go(client)
    requests_before = len(fake.requests)
    assert go(client) is None
    assert len(fake.requests) == requests_before  # no model calls for repeats


def test_attribution_gets_one_retry(client, fake):
    bad = copy.deepcopy(STORY)
    bad["summary"] = "According to NASA, mangroves are growing."
    fake.reset(answer(bad), answer(STORY), answer(QUIZ), answer(VERDICT))
    draft = go(client, dry_run=True)
    assert "NASA" not in draft.summary
    retry_text = fake.requests[1]["messages"][0]["content"][0]["text"]
    assert "according to nasa" in retry_text.lower()


def test_attribution_twice_stops_without_saving(client, fake):
    bad = copy.deepcopy(STORY)
    bad["body"] += " NASA says this is true."
    fake.reset(answer(bad))
    with pytest.raises(RunError, match="still fails checks"):
        go(client)
    publisher_id = stories.get_publisher_id("NASA Earth Observatory")
    assert stories.find_existing(publisher_id, ARTICLE.url) is None


def test_spending_cap_stops_the_run(client, fake):
    huge = answer(STORY, input_tokens=5_000_000, output_tokens=0)  # $0.50
    fake.reset(huge, answer(QUIZ), answer(VERDICT))
    with pytest.raises(agent_run.BudgetExceeded):
        go(client, cap=0.25)


def test_url_must_be_in_the_feed(client):
    with pytest.raises(RunError, match="isn't in the current feed"):
        go(client, url="https://science.nasa.gov/earth/earth-observatory/other/")


def test_main_returns_1_on_failure(monkeypatch):
    async def broken(**kwargs):
        raise RunError("boom")

    monkeypatch.setattr(agent_run, "run", broken)
    assert agent_run.main([]) == 1


def test_too_hard_story_gets_one_retry(client, fake):
    hard = copy.deepcopy(STORY)
    hard["body"] = (
        "Substantial anthropogenic perturbations of biogeochemical cycles have "
        "precipitated unprecedented transformations in coastal ecosystems."
    )
    fake.reset(answer(hard), answer(STORY), answer(QUIZ), answer(VERDICT))
    draft = go(client, dry_run=True)
    assert draft.body == STORY["body"]
    retry_text = fake.requests[1]["messages"][0]["content"][0]["text"]
    assert "Reading level is grade" in retry_text


def test_still_too_hard_is_saved_with_a_warning(client, fake, capsys):
    hard = copy.deepcopy(STORY)
    hard["body"] = (
        "Substantial anthropogenic perturbations of biogeochemical cycles have "
        "precipitated unprecedented transformations in coastal ecosystems."
    )
    fake.reset(answer(hard), answer(hard), answer(QUIZ), answer(VERDICT))
    draft = go(client)
    assert draft.reading_level == 20.0
    assert "warning: Reading level is grade" in capsys.readouterr().out


def test_saved_quiz_marks_the_right_answers(client, fake):
    fake.reset(answer(STORY), answer(QUIZ), answer(VERDICT))
    draft = go(client)
    saved = stories.get_story(stories.find_existing(draft.publisher_id, ARTICLE.url))
    for i, q in enumerate(saved["questions"]):
        assert q["options"][q["correct_index"]] == f"Right {i}"
    # Spread across positions: 4 questions use A, B, C and D once each.
    assert sorted(q["correct_index"] for q in saved["questions"]) == [0, 1, 2, 3]


def test_unused_glossary_word_gets_one_retry(client, fake):
    loose = copy.deepcopy(STORY)
    loose["glossary"] = [{"term": "Canopy", "definition": "The top of a forest."}]
    fake.reset(answer(loose), answer(STORY), answer(QUIZ), answer(VERDICT))
    draft = go(client, dry_run=True)
    assert [g["term"] for g in draft.glossary] == ["Coast"]
    retry_text = fake.requests[1]["messages"][0]["content"][0]["text"]
    assert "Canopy" in retry_text


def stored_evals(draft):
    story_id = stories.find_existing(draft.publisher_id, ARTICLE.url)
    return {e["check_name"]: e for e in stories.get_eval_results(story_id)}


def test_eval_results_are_saved_with_the_draft(client, fake):
    fake.reset(answer(STORY), answer(QUIZ), answer(VERDICT))
    evals = stored_evals(go(client))
    assert list(evals) == [
        "reading_level",
        "attribution",
        "glossary_used",
        "grounding",
        "quiz_answerable",
        "safety",
        "tone",
    ]
    # The sample story is very simple text (about grade 3), so the reading
    # level check fails it; everything else passes.
    assert {name for name, e in evals.items() if not e["passed"]} == {"reading_level"}
    assert evals["grounding"]["judge_model"] == "anthropic/claude-haiku-5-5"
    assert evals["reading_level"]["judge_model"] is None  # code check


def test_judge_sees_article_story_and_marked_answers(client, fake):
    fake.reset(answer(STORY), answer(QUIZ), answer(VERDICT))
    go(client, dry_run=True)
    judge_text = fake.requests[2]["messages"][0]["content"][0]["text"]
    assert ARTICLE.text in judge_text
    assert "<story>" in judge_text and STORY["kid_title"] in judge_text
    assert judge_text.count("(marked correct)") == 4


def test_unsupported_claim_is_flagged_but_saved(client, fake):
    verdict = copy.deepcopy(VERDICT)
    verdict["claims"].append(
        {
            "claim": "Scientists think El Nino caused it.",
            "supported": False,
            "evidence": "The article only says it is often associated.",
        }
    )
    fake.reset(answer(STORY), answer(QUIZ), answer(verdict))
    evals = stored_evals(go(client))
    assert evals["grounding"]["passed"] is False
    assert evals["grounding"]["details"]["unsupported"][0]["claim"].startswith(
        "Scientists think"
    )


def test_unsafe_story_is_not_saved(client, fake):
    verdict = copy.deepcopy(VERDICT) | {
        "safe_for_kids": False,
        "safety_notes": "Graphic injury details.",
    }
    fake.reset(answer(STORY), answer(QUIZ), answer(verdict))
    with pytest.raises(RunError, match="unsafe for kids"):
        go(client)
    publisher_id = stories.get_publisher_id("NASA Earth Observatory")
    assert stories.find_existing(publisher_id, ARTICLE.url) is None


def test_judge_failure_is_flagged_not_fatal(client, fake):
    from tests.fake_anthropic import text_reply

    fake.reset(answer(STORY), answer(QUIZ), text_reply("I won't use the tool."))
    evals = stored_evals(go(client))
    assert evals["judge"]["passed"] is False
    assert "didn't call final_answer" in evals["judge"]["details"]["error"]
