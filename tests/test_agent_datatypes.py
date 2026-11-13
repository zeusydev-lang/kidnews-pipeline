"""Agent data types: messages, tool calls, events and the execution context.

No database needed.
"""

import time
import uuid

import pytest
from pydantic import BaseModel, ValidationError

from agent.datatypes import Event, ExecutionContext, Message, ToolCall, ToolResult

# --- Fixtures ----------------------------------------------------------------


@pytest.fixture
def message():
    return Message(role="user", content="Hello")


@pytest.fixture
def tool_call():
    return ToolCall(
        tool_call_id="call-1",
        name="fetch_article",
        arguments={"url": "https://example.com", "options": {"timeout": 30}},
    )


@pytest.fixture
def tool_result():
    return ToolResult(
        tool_call_id="call-1",
        name="fetch_article",
        status="success",
        content=[{"title": "Melting Ice"}],
    )


def make_event(execution_id="run-1", author="agent", content=None):
    return Event(execution_id=execution_id, author=author, content=content or [])


# --- Message -----------------------------------------------------------------


class TestMessage:
    def test_fields_and_default_type(self, message):
        data = message.model_dump()
        assert data == {"type": "message", "role": "user", "content": "Hello"}

    @pytest.mark.parametrize("role", ["system", "user", "assistant"])
    def test_allowed_roles(self, role):
        assert Message(role=role, content="Hi").role == role

    def test_unknown_role_is_rejected(self):
        with pytest.raises(ValidationError, match="role"):
            Message(role="robot", content="Hello")

    def test_content_is_required(self):
        with pytest.raises(ValidationError, match="content"):
            Message(role="user")


# --- ToolCall ----------------------------------------------------------------


class TestToolCall:
    def test_default_type(self, tool_call):
        assert tool_call.type == "tool_call"

    def test_nested_arguments_are_kept(self, tool_call):
        assert tool_call.arguments["options"]["timeout"] == 30

    @pytest.mark.parametrize("missing", ["tool_call_id", "name", "arguments"])
    def test_required_fields(self, missing):
        data = {"tool_call_id": "call-1", "name": "fetch_article", "arguments": {}}
        del data[missing]
        with pytest.raises(ValidationError, match=missing):
            ToolCall(**data)


# --- ToolResult --------------------------------------------------------------


class TestToolResult:
    def test_default_type(self, tool_result):
        assert tool_result.type == "tool_result"

    @pytest.mark.parametrize("status", ["success", "error"])
    def test_allowed_statuses(self, status):
        result = ToolResult(tool_call_id="c", name="n", status=status, content=[])
        assert result.status == status

    def test_unknown_status_is_rejected(self):
        with pytest.raises(ValidationError, match="status"):
            ToolResult(tool_call_id="c", name="n", status="maybe", content=[])

    def test_content_must_be_a_list(self):
        with pytest.raises(ValidationError, match="content"):
            ToolResult(tool_call_id="c", name="n", status="success", content="text")


# --- Event -------------------------------------------------------------------


class TestEvent:
    def test_each_event_gets_its_own_id(self):
        a, b = make_event(), make_event()
        assert a.id != b.id
        uuid.UUID(a.id)  # raises if it isn't a valid UUID

    def test_timestamp_is_now(self):
        before = time.time()
        event = make_event()
        assert before - 1 <= event.timestamp <= time.time() + 1

    def test_content_defaults_to_empty_list(self):
        assert Event(execution_id="run-1", author="agent").content == []

    def test_events_do_not_share_content(self, message):
        a = Event(execution_id="run-1", author="agent")
        b = Event(execution_id="run-1", author="agent")
        a.content.append(message)
        assert b.content == []

    def test_mixed_content_keeps_types(self, message, tool_call, tool_result):
        event = make_event(content=[message, tool_call, tool_result])
        assert [type(c) for c in event.content] == [Message, ToolCall, ToolResult]

    def test_json_round_trip_keeps_types(self, message, tool_call, tool_result):
        event = make_event(content=[message, tool_call, tool_result])
        restored = Event.model_validate_json(event.model_dump_json())
        assert restored == event
        assert [type(c) for c in restored.content] == [Message, ToolCall, ToolResult]

    def test_dicts_are_turned_into_the_right_class(self):
        event = make_event(
            content=[{"type": "message", "role": "user", "content": "hi"}]
        )
        assert isinstance(event.content[0], Message)

    def test_unknown_content_type_is_rejected(self):
        with pytest.raises(ValidationError):
            make_event(content=[{"type": "picture", "url": "x"}])

    @pytest.mark.parametrize("missing", ["execution_id", "author"])
    def test_required_fields(self, missing):
        data = {"execution_id": "run-1", "author": "agent"}
        del data[missing]
        with pytest.raises(ValidationError, match=missing):
            Event(**data)


# --- ExecutionContext --------------------------------------------------------


class TestExecutionContext:
    def test_starts_empty(self):
        context = ExecutionContext()
        assert context.events == []
        assert context.current_step == 0
        assert context.state == {}
        assert context.final_result is None

    def test_each_context_gets_its_own_id(self):
        a, b = ExecutionContext(), ExecutionContext()
        assert a.execution_id != b.execution_id
        uuid.UUID(a.execution_id)

    def test_custom_id_is_kept(self):
        assert ExecutionContext(execution_id="run-1").execution_id == "run-1"

    def test_contexts_do_not_share_events_or_state(self):
        a, b = ExecutionContext(), ExecutionContext()
        a.add_event(make_event(a.execution_id))
        a.state["article_url"] = "https://example.com"
        assert b.events == []
        assert b.state == {}

    def test_add_event_keeps_order(self):
        context = ExecutionContext()
        events = [make_event(author=name) for name in ("user", "agent", "tool")]
        for event in events:
            context.add_event(event)
        assert [e.author for e in context.events] == ["user", "agent", "tool"]

    def test_increment_step(self):
        context = ExecutionContext()
        for _ in range(3):
            context.increment_step()
        assert context.current_step == 3

    def test_state_holds_any_value(self):
        context = ExecutionContext()
        values = {"text": "hi", "count": 2, "items": [1, 2], "nested": {"a": 1}}
        context.state.update(values)
        assert context.state == values

    def test_final_result_can_be_text(self):
        context = ExecutionContext()
        context.final_result = "done"
        assert context.final_result == "done"

    def test_final_result_can_be_a_model(self):
        class Answer(BaseModel):
            title: str

        context = ExecutionContext()
        context.final_result = Answer(title="Melting Ice")
        assert context.final_result.title == "Melting Ice"
