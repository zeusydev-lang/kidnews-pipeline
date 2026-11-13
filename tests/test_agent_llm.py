"""LLM layer against a fake Anthropic server on localhost.

No API key, no network, no cost. The fake server records the exact request
body litellm sends, so we can check what Claude would really receive.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from agent.datatypes import Message, ToolCall, ToolResult
from agent.llm import LlmClient, LlmError, LlmRequest, LlmResponse
from agent.tools import FunctionTool

# --- Fake Anthropic Messages API --------------------------------------------


class FakeAnthropic:
    """Answers POST /v1/messages with a canned reply and records each request."""

    def __init__(self):
        self.requests: list[dict] = []
        self.reply: dict = text_reply("Hello!")
        self.status = 200
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                fake.requests.append(
                    {
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": json.loads(self.rfile.read(length)),
                    }
                )
                body = json.dumps(fake.reply).encode()
                self.send_response(fake.status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def last_body(self) -> dict:
        return self.requests[-1]["body"]


def text_reply(text: str, input_tokens=100, output_tokens=20) -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def tool_reply(text: str | None, calls: list[tuple[str, str, dict]]) -> dict:
    content = [{"type": "text", "text": text}] if text else []
    content += [
        {"type": "tool_use", "id": cid, "name": name, "input": args}
        for cid, name, args in calls
    ]
    reply = text_reply("")
    reply.update(content=content, stop_reason="tool_use")
    return reply


@pytest.fixture(scope="module")
def fake():
    server = FakeAnthropic()
    yield server
    server.server.shutdown()


@pytest.fixture
def client(fake, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    fake.reply, fake.status = text_reply("Hello!"), 200
    return LlmClient("anthropic/claude-haiku-5-5", api_base=fake.url, num_retries=0)


def generate(client, request) -> LlmResponse:
    return asyncio.run(client.generate(request))


async def reading_level(text: str) -> float:
    """Flesch-Kincaid grade level of a text."""
    return 6.5


# --- _build_messages: pure conversion, no server -----------------------------


class TestBuildMessages:
    def build(self, **kwargs):
        return LlmClient("anthropic/x")._build_messages(LlmRequest(**kwargs))

    def test_instructions_become_system_message(self):
        messages = self.build(
            instructions="Be kind.", contents=[Message(role="user", content="Hi")]
        )
        assert messages == [
            {"role": "system", "content": "Be kind."},
            {"role": "user", "content": "Hi"},
        ]

    def test_no_instructions_no_system_message(self):
        messages = self.build(contents=[Message(role="user", content="Hi")])
        assert [m["role"] for m in messages] == ["user"]

    def test_text_and_tool_calls_share_one_assistant_message(self):
        messages = self.build(
            contents=[
                Message(role="user", content="Check it"),
                Message(role="assistant", content="Let me check."),
                ToolCall(tool_call_id="t1", name="a", arguments={"x": 1}),
                ToolCall(tool_call_id="t2", name="b", arguments={}),
            ]
        )
        assistant = messages[-1]
        assert assistant["role"] == "assistant"
        assert assistant["content"] == "Let me check."
        assert [c["id"] for c in assistant["tool_calls"]] == ["t1", "t2"]
        assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {
            "x": 1
        }

    def test_tool_call_without_text(self):
        messages = self.build(
            contents=[
                Message(role="user", content="Go"),
                ToolCall(tool_call_id="t1", name="a", arguments={}),
            ]
        )
        assert messages[-1]["content"] is None
        assert messages[-1]["tool_calls"][0]["function"]["name"] == "a"

    def test_tool_results(self):
        messages = self.build(
            contents=[
                ToolResult(
                    tool_call_id="t1",
                    name="a",
                    status="success",
                    content=["line", {"k": 1}],
                ),
                ToolResult(tool_call_id="t2", name="b", status="error", content=["no"]),
            ]
        )
        assert messages[0] == {
            "role": "tool",
            "tool_call_id": "t1",
            "content": 'line\n{"k": 1}',
        }
        assert messages[1]["content"] == "Error: no"


# --- Full round trip through litellm to the fake server ----------------------


class TestGenerate:
    def test_text_reply(self, client, fake):
        response = generate(
            client,
            LlmRequest(
                instructions="Be kind.",
                contents=[Message(role="user", content="Hi")],
            ),
        )
        assert response.text == "Hello!"
        assert response.tool_calls == []
        assert response.stop_reason == "stop"

    def test_request_sent_to_claude(self, client, fake):
        generate(
            client,
            LlmRequest(
                instructions="Be kind.",
                contents=[Message(role="user", content="Hi")],
                max_tokens=500,
            ),
        )
        request = fake.requests[-1]
        body = request["body"]
        assert request["path"].endswith("/v1/messages")
        assert body["model"] == "claude-haiku-5-5"
        assert body["max_tokens"] == 500
        assert "Be kind." in json.dumps(body["system"])  # system is separate
        assert [m["role"] for m in body["messages"]] == ["user"]
        assert body["thinking"] == {"type": "disabled"}
        for banned in ("temperature", "top_p", "top_k"):
            assert banned not in body
        assert request["headers"].get("x-api-key") == "sk-ant-test-not-real"

    def test_tools_are_sent(self, client, fake):
        tool = FunctionTool(reading_level)
        generate(
            client,
            LlmRequest(contents=[Message(role="user", content="Hi")], tools=[tool]),
        )
        sent = fake.last_body["tools"]
        assert [t["name"] for t in sent] == ["reading_level"]
        assert sent[0]["input_schema"]["properties"]["text"]["type"] == "string"
        assert fake.last_body["tool_choice"]["type"] == "auto"

    def test_no_tools_no_tool_fields(self, client, fake):
        generate(client, LlmRequest(contents=[Message(role="user", content="Hi")]))
        assert "tools" not in fake.last_body

    def test_tool_call_reply(self, client, fake):
        fake.reply = tool_reply(
            "Checking.", [("toolu_1", "reading_level", {"text": "Ice melts."})]
        )
        response = generate(
            client, LlmRequest(contents=[Message(role="user", content="Grade it")])
        )
        assert response.text == "Checking."
        assert response.stop_reason == "tool_calls"
        [call] = response.tool_calls
        assert (call.tool_call_id, call.name) == ("toolu_1", "reading_level")
        assert call.arguments == {"text": "Ice melts."}

    def test_tool_loop_history_reaches_claude_correctly(self, client, fake):
        contents = [
            Message(role="user", content="Grade it"),
            Message(role="assistant", content="Checking."),
            ToolCall(
                tool_call_id="toolu_1",
                name="reading_level",
                arguments={"text": "Ice"},
            ),
            ToolResult(
                tool_call_id="toolu_1",
                name="reading_level",
                status="success",
                content=[6.5],
            ),
        ]
        generate(client, LlmRequest(contents=contents))
        messages = fake.last_body["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant", "user"]
        assistant_types = [b["type"] for b in messages[1]["content"]]
        assert assistant_types == ["text", "tool_use"]
        result = messages[2]["content"][0]
        assert result["type"] == "tool_result"
        assert result["tool_use_id"] == "toolu_1"

    def test_usage_and_cost(self, client, fake):
        fake.reply = text_reply("ok", input_tokens=1_000_000, output_tokens=200_000)
        usage = generate(
            client, LlmRequest(contents=[Message(role="user", content="Hi")])
        ).usage
        assert (usage.input_tokens, usage.output_tokens) == (1_000_000, 200_000)
        assert usage.cost_usd == pytest.approx(0.10 + 0.10)  # $0.10 in + $0.10 out

    def test_unknown_model_has_no_cost(self):
        assert LlmClient("openai/some-model").cost(1000, 1000) is None

    def test_api_error_raises_llm_error_without_details(self, client, fake):
        fake.status = 400
        fake.reply = {
            "type": "error",
            "error": {"type": "invalid_request_error", "message": "secret detail"},
        }
        with pytest.raises(LlmError) as err:
            generate(client, LlmRequest(contents=[Message(role="user", content="Hi")]))
        assert "secret detail" not in str(err.value)
        assert "sk-ant" not in str(err.value)


# --- _parse_response edge cases ----------------------------------------------


class TestParseResponse:
    def parse(self, message: dict, finish_reason="stop"):
        from litellm import ModelResponse

        raw = ModelResponse(
            model="claude-haiku-5-5",
            choices=[{"message": message, "finish_reason": finish_reason}],
            usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        )
        return LlmClient("anthropic/claude-haiku-5-5")._parse_response(raw)

    def call(self, arguments: str) -> dict:
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "t1",
                    "type": "function",
                    "function": {"name": "a", "arguments": arguments},
                }
            ],
        }

    def test_invalid_json_arguments_are_kept_for_the_tool_to_refuse(self):
        [call] = self.parse(self.call("{not json")).tool_calls
        assert call.arguments == {"_invalid_json": "{not json"}

    def test_non_object_arguments_are_refused_the_same_way(self):
        [call] = self.parse(self.call("[1, 2]")).tool_calls
        assert call.arguments == {"_invalid_json": "[1, 2]"}

    def test_empty_arguments_mean_no_arguments(self):
        [call] = self.parse(self.call("")).tool_calls
        assert call.arguments == {}

    def test_empty_reply(self):
        response = self.parse({"role": "assistant", "content": None})
        assert response.content == []
        assert response.text == ""
