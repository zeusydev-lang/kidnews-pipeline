"""Structured output (final_answer tool) against the fake Anthropic server."""

import asyncio
import copy

import pytest
from pydantic import ValidationError

from agent.datatypes import Message, ToolCall
from agent.llm import LlmClient, LlmResponse, LlmUsage
from agent.outputs import StoryOutput, to_story_draft
from agent.structured import (
    StructuredOutputError,
    generate_structured,
    make_output_tool,
    parse_output,
)
from core.models import Quiz, StoryDraft
from tests.fake_anthropic import FakeAnthropic, text_reply, tool_reply

STORY = {
    "kid_title": "Why Sea Ice Shrinks",
    "summary": "Sea ice grows in winter and shrinks in summer.",
    "body": "The Arctic Ocean freezes in winter.\n\nIn summer it melts.",
    "topic": "Ice and snow",
    "concepts": [
        {"name": "Sea ice", "explanation": "Frozen ocean water."},
        {"name": "Albedo", "explanation": "How much light bounces off."},
    ],
    "glossary": [{"term": "Arctic", "definition": "The region near the North Pole."}],
}

QUIZ = {
    "questions": [
        {
            "prompt": f"Question {i}?",
            "options": ["Ice", "Rain", "Wind", "Sun"],
            "correct_index": 0,
            "explanation": "The story says so.",
        }
        for i in range(4)
    ]
}


def answer(args: dict, call_id: str = "toolu_1", **kw) -> dict:
    return tool_reply([(call_id, "final_answer", args)], **kw)


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


def run(coro):
    return asyncio.run(coro)


# --- 4.25 make_output_tool ---------------------------------------------------


class TestMakeOutputTool:
    def test_definition_uses_the_model_schema(self):
        definition = make_output_tool(StoryOutput).tool_definition["function"]
        assert definition["name"] == "final_answer"
        assert "StoryOutput" in definition["description"]
        params = definition["parameters"]
        assert {"kid_title", "summary", "body", "concepts"} <= set(params["properties"])
        assert params["additionalProperties"] is False

    def test_custom_name_and_description(self):
        tool = make_output_tool(Quiz, name="submit_quiz", description="Send the quiz")
        assert (tool.name, tool.description) == ("submit_quiz", "Send the quiz")


# --- 4.28 parse_output -------------------------------------------------------


def response_with(*calls: ToolCall) -> LlmResponse:
    return LlmResponse(content=list(calls), stop_reason="tool_calls")


class TestParseOutput:
    def test_valid_arguments_become_the_model(self):
        response = response_with(
            ToolCall(tool_call_id="t", name="final_answer", arguments=STORY)
        )
        story = parse_output(response, StoryOutput)
        assert isinstance(story, StoryOutput)
        assert story.concepts[1].name == "Albedo"

    def test_no_output_call(self):
        response = LlmResponse(
            content=[Message(role="assistant", content="Here you go")],
            stop_reason="stop",
        )
        with pytest.raises(StructuredOutputError, match="didn't call final_answer"):
            parse_output(response, StoryOutput)

    def test_invalid_arguments(self):
        bad = STORY | {"concepts": STORY["concepts"][:1]}  # needs 2-3
        response = response_with(
            ToolCall(tool_call_id="t", name="final_answer", arguments=bad)
        )
        with pytest.raises(ValidationError, match="concepts"):
            parse_output(response, StoryOutput)


# --- 4.26 + 4.29 generate_structured -----------------------------------------


class TestGenerateStructured:
    def test_story(self, client, fake):
        fake.reset(answer(STORY))
        story = run(generate_structured(client, StoryOutput, "Write.", "Article"))
        assert story.kid_title == "Why Sea Ice Shrinks"

    def test_quiz(self, client, fake):
        fake.reset(answer(QUIZ))
        quiz = run(generate_structured(client, Quiz, "Quiz.", "Story"))
        assert len(quiz.questions) == 4

    def test_request_forces_the_output_tool(self, client, fake):
        fake.reset(answer(STORY))
        run(generate_structured(client, StoryOutput, "Write.", "Article text"))
        body = fake.requests[-1]
        assert body["tool_choice"]["type"] == "any"  # litellm's "required"
        assert [t["name"] for t in body["tools"]] == ["final_answer"]
        assert "kid_title" in body["tools"][0]["input_schema"]["properties"]
        assert body["messages"][0]["content"][0]["text"] == "Article text"

    def test_nested_model_schema_is_sent(self, client, fake):
        fake.reset(answer(QUIZ))
        run(generate_structured(client, Quiz, "Quiz.", "Story"))
        schema = fake.requests[-1]["tools"][0]["input_schema"]
        assert "QuizQuestion" in str(schema)  # nested model travels via $defs

    def test_retry_shows_the_model_its_mistakes(self, client, fake):
        bad = STORY | {"concepts": STORY["concepts"][:1]}
        fake.reset(answer(bad, call_id="toolu_bad"), answer(STORY, call_id="toolu_ok"))
        story = run(generate_structured(client, StoryOutput, "Write.", "Article"))
        assert story.kid_title == "Why Sea Ice Shrinks"
        assert len(fake.requests) == 2

        retry_messages = fake.requests[1]["messages"]
        assert [m["role"] for m in retry_messages] == ["user", "assistant", "user"]
        result = retry_messages[2]["content"][0]
        assert result["type"] == "tool_result"
        assert result["tool_use_id"] == "toolu_bad"
        feedback = str(result["content"])
        assert "concepts" in feedback
        assert "Frozen ocean water" not in feedback  # values aren't echoed back

    def test_retry_when_the_tool_was_not_called(self, client, fake):
        fake.reset(text_reply("I'd rather chat."), answer(QUIZ))
        quiz = run(generate_structured(client, Quiz, "Quiz.", "Story"))
        assert len(quiz.questions) == 4
        assert len(fake.requests) == 2

    def test_gives_up_after_max_attempts(self, client, fake):
        bad = copy.deepcopy(QUIZ)
        bad["questions"][0]["correct_index"] = 9
        fake.reset(answer(bad))
        with pytest.raises(StructuredOutputError, match="after 2 attempts"):
            run(generate_structured(client, Quiz, "Quiz.", "Story"))
        assert len(fake.requests) == 2

    def test_usage_adds_up_across_attempts(self, client, fake):
        bad = STORY | {"topic": ""}
        fake.reset(
            answer(bad, input_tokens=1000, output_tokens=100),
            answer(STORY, input_tokens=2000, output_tokens=300),
        )
        usage = LlmUsage()
        run(generate_structured(client, StoryOutput, "W.", "A", usage=usage))
        assert (usage.input_tokens, usage.output_tokens) == (3000, 400)
        assert usage.cost_usd == pytest.approx((3000 * 0.10 + 400 * 0.50) / 1e6)


# --- StoryOutput -> StoryDraft -----------------------------------------------


class TestToStoryDraft:
    def test_builds_a_valid_draft(self):
        draft = to_story_draft(
            StoryOutput(**STORY),
            Quiz(**QUIZ),
            publisher_id=1,
            source_url="https://earthobservatory.nasa.gov/images/1",
            title="Arctic Sea Ice Minimum",
            text_hash="abc",
            model="anthropic/claude-haiku-5-5",
            prompt_version="v1",
        )
        assert isinstance(draft, StoryDraft)
        assert draft.slug == "why-sea-ice-shrinks"
        assert draft.concepts[0] == {
            "name": "Sea ice",
            "explanation": "Frozen ocean water.",
        }
        assert draft.source_url.startswith("https://earthobservatory")

    def test_model_cannot_set_source_fields(self):
        with pytest.raises(ValidationError, match="source_url"):
            StoryOutput(**STORY, source_url="https://evil.example")
