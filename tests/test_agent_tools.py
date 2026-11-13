"""Agent tool base class. No database or network needed."""

import asyncio

import pytest
from pydantic import ValidationError

from agent.datatypes import ExecutionContext, ToolCall
from agent.tools import BaseTool, FunctionTool, ToolArgs, ToolError, default_name

# --- Example tools -----------------------------------------------------------


class EchoArgs(ToolArgs):
    text: str
    times: int = 1


class EchoTool(BaseTool):
    """Repeat some text.

    Used only in tests.
    """

    args_model = EchoArgs

    async def execute(self, context, args: EchoArgs):
        context.state["echoed"] = True
        return [args.text] * args.times


class PingTool(BaseTool):
    """Answer pong. Takes no arguments."""

    async def execute(self, context, args):
        return "pong"


class ExpectedFailureTool(BaseTool):
    """Fails in an expected way."""

    async def execute(self, context, args):
        raise ToolError("Article not found")


class CrashingTool(BaseTool):
    """Crashes with a message that must not reach the LLM."""

    async def execute(self, context, args):
        raise RuntimeError("password=hunter2")


class NoDocTool(BaseTool):
    async def execute(self, context, args):
        return None


def call(name="echo", **arguments) -> ToolCall:
    return ToolCall(tool_call_id="call-1", name=name, arguments=arguments)


def run(tool, tool_call, context=None):
    return asyncio.run(tool.run(context or ExecutionContext(), tool_call))


# --- Names and descriptions --------------------------------------------------


class TestNaming:
    @pytest.mark.parametrize(
        ("cls", "expected"),
        [
            (EchoTool, "echo"),
            (ExpectedFailureTool, "expected_failure"),
            (NoDocTool, "no_doc"),
        ],
    )
    def test_default_name_from_class(self, cls, expected):
        assert default_name(cls) == expected
        assert cls().name == expected

    def test_custom_name_and_description(self):
        tool = EchoTool(name="say", description="Say it")
        assert (tool.name, tool.description) == ("say", "Say it")

    def test_description_from_docstring(self):
        assert EchoTool().description == "Repeat some text.\n\nUsed only in tests."

    def test_no_docstring_gives_empty_description(self):
        assert NoDocTool().description == ""

    @pytest.mark.parametrize("bad", ["has space", "dot.name", "x" * 65])
    def test_invalid_name_is_refused(self, bad):
        with pytest.raises(ValueError, match="Invalid tool name"):
            EchoTool(name=bad)

    def test_base_class_cannot_be_used_directly(self):
        with pytest.raises(TypeError):
            BaseTool()


# --- Tool definition ---------------------------------------------------------


class TestToolDefinition:
    def test_built_from_args_model(self):
        definition = EchoTool().tool_definition
        assert definition["type"] == "function"
        function = definition["function"]
        assert function["name"] == "echo"
        assert function["description"].startswith("Repeat some text.")
        params = function["parameters"]
        assert params["type"] == "object"
        assert set(params["properties"]) == {"text", "times"}
        assert params["required"] == ["text"]
        assert params["additionalProperties"] is False

    def test_tool_without_arguments(self):
        params = PingTool().tool_definition["function"]["parameters"]
        assert params["type"] == "object"
        assert params["properties"] == {}

    def test_custom_definition_is_used_as_is(self):
        custom = {"type": "function", "function": {"name": "x"}}
        assert EchoTool(tool_definition=custom).tool_definition is custom


# --- Calling a tool directly -------------------------------------------------


class TestCall:
    def test_valid_arguments(self):
        context = ExecutionContext()
        assert asyncio.run(EchoTool()(context, text="hi", times=2)) == ["hi", "hi"]
        assert context.state["echoed"] is True

    def test_invalid_arguments_raise(self):
        with pytest.raises(ValidationError):
            asyncio.run(EchoTool()(ExecutionContext(), times=2))


# --- run(): what the agent loop uses -----------------------------------------


class TestRun:
    def test_success_wraps_result(self):
        result = run(EchoTool(), call(text="hi", times=2))
        assert result.status == "success"
        assert result.content == ["hi", "hi"]
        assert (result.tool_call_id, result.name) == ("call-1", "echo")

    def test_single_value_becomes_a_list(self):
        assert run(PingTool(), call("ping")).content == ["pong"]

    def test_missing_argument_is_an_error_result(self):
        result = run(EchoTool(), call(times=2))
        assert result.status == "error"
        assert "Invalid arguments for echo." in result.content
        assert any(line.startswith("text:") for line in result.content)

    def test_wrong_type_is_an_error_result(self):
        result = run(EchoTool(), call(text="hi", times="many"))
        assert result.status == "error"
        assert any(line.startswith("times:") for line in result.content)

    def test_unknown_argument_is_refused(self):
        result = run(EchoTool(), call(text="hi", sql="drop table stories"))
        assert result.status == "error"
        assert any(line.startswith("sql:") for line in result.content)

    def test_argument_values_are_not_echoed_back(self):
        result = run(EchoTool(), call(text="hi", times="SECRET-VALUE"))
        assert "SECRET-VALUE" not in " ".join(result.content)

    def test_expected_failure_message_is_shown(self):
        result = run(ExpectedFailureTool(), call("expected_failure"))
        assert (result.status, result.content) == ("error", ["Article not found"])

    def test_crash_details_are_hidden(self):
        result = run(CrashingTool(), call("crashing"))
        assert result.status == "error"
        assert result.content == ["crashing failed (RuntimeError)."]
        assert "hunter2" not in " ".join(result.content)


# --- FunctionTool ------------------------------------------------------------


async def add_numbers(a: int, b: int = 10) -> int:
    """Add two numbers.

    Returns the sum.
    """
    return a + b


def shout(text: str) -> str:
    """Make text loud."""
    return text.upper()


async def remember(key: str, context) -> str:
    """Save a key in the run's state."""
    context.state["remembered"] = key
    return f"saved {key}"


def no_hints(value, flag=False):
    return [value, flag]


def explode() -> None:
    raise RuntimeError("secret-path=/etc/passwd")


class TestFunctionTool:
    def test_name_and_description_from_function(self):
        tool = FunctionTool(add_numbers)
        assert tool.name == "add_numbers"
        assert tool.description == "Add two numbers.\n\nReturns the sum."

    def test_custom_name_and_description(self):
        tool = FunctionTool(shout, name="yell", description="Yell it")
        assert (tool.name, tool.description) == ("yell", "Yell it")

    def test_no_docstring_gives_empty_description(self):
        # Must not fall back to FunctionTool's own docstring.
        assert FunctionTool(no_hints).description == ""

    def test_definition_from_signature(self):
        params = FunctionTool(add_numbers).tool_definition["function"]["parameters"]
        assert set(params["properties"]) == {"a", "b"}
        assert params["properties"]["a"]["type"] == "integer"
        assert params["properties"]["b"]["default"] == 10
        assert params["required"] == ["a"]
        assert params["additionalProperties"] is False

    def test_context_is_hidden_from_the_llm(self):
        params = FunctionTool(remember).tool_definition["function"]["parameters"]
        assert set(params["properties"]) == {"key"}

    def test_async_function(self):
        result = run(FunctionTool(add_numbers), call("add_numbers", a=1, b=2))
        assert (result.status, result.content) == ("success", [3])

    def test_default_is_used(self):
        assert run(FunctionTool(add_numbers), call("add_numbers", a=1)).content == [11]

    def test_regular_function(self):
        assert run(FunctionTool(shout), call("shout", text="hi")).content == ["HI"]

    def test_context_is_passed_in(self):
        context = ExecutionContext()
        result = run(FunctionTool(remember), call("remember", key="nasa"), context)
        assert result.content == ["saved nasa"]
        assert context.state["remembered"] == "nasa"

    def test_llm_cannot_set_context(self):
        result = run(FunctionTool(remember), call("remember", key="k", context="fake"))
        assert result.status == "error"
        assert any(line.startswith("context:") for line in result.content)

    def test_parameters_without_type_hints_accept_anything(self):
        result = run(FunctionTool(no_hints), call("no_hints", value={"a": 1}))
        assert result.content == [{"a": 1}, False]

    def test_wrong_type_is_an_error_result(self):
        result = run(FunctionTool(add_numbers), call("add_numbers", a="lots"))
        assert result.status == "error"
        assert any(line.startswith("a:") for line in result.content)

    def test_crash_details_are_hidden(self):
        result = run(FunctionTool(explode), call("explode"))
        assert result.content == ["explode failed (RuntimeError)."]

    def test_star_args_are_refused(self):
        def flexible(*args, **kwargs):
            return None

        with pytest.raises(ValueError, match=r"\*args"):
            FunctionTool(flexible)

    def test_lambda_needs_a_name(self):
        with pytest.raises(ValueError, match="Invalid tool name"):
            FunctionTool(lambda x: x)
        assert FunctionTool(lambda x: x, name="identity").name == "identity"
