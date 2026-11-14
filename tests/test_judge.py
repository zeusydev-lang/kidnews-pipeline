"""The judge and the per-model call settings. Fake Claude server, no database."""

import asyncio

import pytest

from agent.judge import (
    JudgeVerdict,
    judge_input,
    judge_story,
    verdict_to_results,
)
from agent.llm import LlmClient, default_thinking, supports_forced_tool_choice
from agent.outputs import StoryOutput
from core.models import Quiz
from tests.fake_anthropic import FakeAnthropic, text_reply, tool_reply

STORY = StoryOutput(
    kid_title="Rain Fills a Reservoir",
    summary="Heavy rain filled a lake.",
    body="Storms soaked the Four Corners.\n\nThe lake rose nearly 60 feet.",
    topic="Weather",
    concepts=[
        {"name": "Reservoir", "explanation": "A lake that stores water."},
        {"name": "Drought", "explanation": "A long dry time."},
    ],
    glossary=[{"term": "Storm", "definition": "Strong wind and rain."}],
)
QUIZ = Quiz(
    questions=[
        {
            "prompt": f"Question {i}?",
            "options": ["A1", "B1", "C1", "D1"],
            "correct_index": i,
            "explanation": "The story says so.",
        }
        for i in range(4)
    ]
)
GOOD = {
    "claims": [{"claim": "The lake rose 60 feet.", "supported": True, "evidence": "q"}],
    "questions": [
        {"question_number": n, "answer_supported": True, "one_correct_answer": True}
        for n in range(1, 5)
    ],
    "safe_for_kids": True,
    "tone_ok": True,
}


def verdict(**changes) -> JudgeVerdict:
    return JudgeVerdict(**(GOOD | changes))


class TestVerdictToResults:
    def test_all_good(self):
        results = verdict_to_results(verdict(), "m")
        assert [r.check_name for r in results] == [
            "grounding",
            "quiz_answerable",
            "safety",
            "tone",
        ]
        assert all(r.passed for r in results)
        assert all(r.judge_model == "m" for r in results)

    def test_unsupported_claim_fails_grounding(self):
        claims = GOOD["claims"] + [
            {"claim": "El Nino caused it.", "supported": False, "evidence": "no"}
        ]
        grounding = verdict_to_results(verdict(claims=claims), "m")[0]
        assert not grounding.passed
        assert grounding.details["claims_checked"] == 2
        assert grounding.details["unsupported"][0]["claim"] == "El Nino caused it."

    @pytest.mark.parametrize("field", ["answer_supported", "one_correct_answer"])
    def test_bad_question_fails_quiz_check(self, field):
        questions = [dict(q) for q in GOOD["questions"]]
        questions[2][field] = False
        quiz = verdict_to_results(verdict(questions=questions), "m")[1]
        assert not quiz.passed
        assert quiz.details["problems"][0]["question_number"] == 3

    def test_safety_and_tone_are_separate(self):
        results = verdict_to_results(verdict(safe_for_kids=True, tone_ok=False), "m")
        assert results[2].passed and not results[3].passed


def test_judge_input_marks_the_correct_answers():
    text = judge_input("SOURCE ARTICLE", STORY, QUIZ)
    assert text.startswith("<article>\nSOURCE ARTICLE\n</article>")
    assert text.count("(marked correct)") == 4
    assert "1. Question 0?\n   A. A1 (marked correct)" in text


class TestModelSettings:
    def test_haiku(self):
        assert default_thinking("anthropic/claude-haiku-5-5") == {"type": "disabled"}
        assert supports_forced_tool_choice("anthropic/claude-haiku-5-5")

    def test_sonnet(self):
        assert default_thinking("anthropic/claude-sonnet-5-5") == {
            "type": "between_tools"
        }
        assert not supports_forced_tool_choice("anthropic/claude-sonnet-5-5")

    def test_other_providers_untouched(self):
        assert default_thinking("openai/some-model") is None


@pytest.fixture(scope="module")
def fake():
    server = FakeAnthropic()
    yield server
    server.close()


@pytest.fixture
def sonnet(fake, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    fake.reset()
    return LlmClient("anthropic/claude-sonnet-5-5", api_base=fake.url, num_retries=0)


def answer(args):
    return tool_reply([("toolu_1", "final_answer", args)])


def test_sonnet_judge_request(sonnet, fake):
    fake.reset(answer(GOOD))
    _, results = asyncio.run(judge_story(sonnet, "SOURCE", STORY, QUIZ))
    body = fake.requests[0]
    assert body["model"] == "claude-sonnet-5-5"
    assert body["tool_choice"]["type"] == "auto"  # Sonnet 5.5 rejects forced
    assert body["thinking"] == {"type": "between_tools"}
    assert "calling the final_answer tool" in str(body["system"])
    assert all(r.passed for r in results)


def test_sonnet_missing_tool_call_is_retried(sonnet, fake):
    fake.reset(text_reply("Looks fine to me!"), answer(GOOD))
    _, results = asyncio.run(judge_story(sonnet, "SOURCE", STORY, QUIZ))
    assert len(fake.requests) == 2
    assert all(r.passed for r in results)


def test_sonnet_cost_is_counted(sonnet, fake):
    from agent.llm import LlmUsage

    reply = answer(GOOD)
    reply["usage"] = {"input_tokens": 1_000_000, "output_tokens": 100_000}
    fake.reset(reply)
    usage = LlmUsage()
    asyncio.run(judge_story(sonnet, "SOURCE", STORY, QUIZ, usage))
    assert usage.cost_usd == pytest.approx(2.00 + 1.00)
