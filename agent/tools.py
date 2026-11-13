"""Base class for tools the agent can call.

A tool declares its arguments as a Pydantic model. That model is used twice:
  1. to build the tool definition sent to the LLM (JSON Schema), and
  2. to check the LLM's arguments before the tool runs.

The LLM's arguments are untrusted input, just like fetched web text, so they
are always validated. Tools must not read secrets or write to the database;
the run script saves results itself after the checks.

Example:

    class FetchArgs(ToolArgs):
        url: str

    class FetchArticleTool(BaseTool):
        \"\"\"Fetch a NASA Earth Observatory article by URL.\"\"\"

        args_model = FetchArgs

        async def execute(self, context, args: FetchArgs):
            ...

    tool = FetchArticleTool()        # name "fetch_article", description from docstring
    tool.tool_definition             # sent to the LLM
    await tool.run(context, call)    # always returns a ToolResult
"""

import inspect
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from agent.datatypes import ExecutionContext, ToolCall, ToolResult

# Tool names the LLM APIs accept: letters, numbers, underscores, hyphens.
NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class ToolArgs(BaseModel):
    """Base for tool argument models. Unknown arguments are refused."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ToolError(Exception):
    """A tool failed in an expected way. The message is shown to the LLM."""


def default_name(cls: type) -> str:
    """FetchArticleTool -> fetch_article"""
    base = cls.__name__.removesuffix("Tool") or cls.__name__
    return re.sub(r"(?<!^)(?=[A-Z])", "_", base).lower()


class BaseTool(ABC):
    """Base class for tools that can be used by agents."""

    # Override in each tool. ToolArgs itself means "no arguments".
    args_model: ClassVar[type[ToolArgs]] = ToolArgs

    def __init__(
        self,
        name: str | None = None,
        description: str | None = None,
        tool_definition: dict[str, Any] | None = None,
    ):
        self.name = name or default_name(type(self))
        if not NAME_PATTERN.fullmatch(self.name):
            raise ValueError(
                f"Invalid tool name {self.name!r}: use letters, numbers, _ or -"
            )
        # type(self).__doc__ is this tool's own docstring (not inherited).
        # An explicit "" is kept, so a subclass can say "no description".
        if description is None:
            description = inspect.cleandoc(type(self).__doc__ or "")
        self.description = description
        self._tool_definition = tool_definition

    @property
    def tool_definition(self) -> dict[str, Any]:
        """The definition sent to the LLM (OpenAI-style format, used by litellm)."""
        if self._tool_definition is not None:
            return self._tool_definition
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_model.model_json_schema(),
            },
        }

    @abstractmethod
    async def execute(self, context: ExecutionContext, args: ToolArgs) -> Any:
        """Do the tool's work with already-validated arguments."""

    async def __call__(self, context: ExecutionContext, **kwargs) -> Any:
        """Validate the arguments, then run the tool. Errors are raised."""
        args = self.args_model.model_validate(kwargs)
        return await self.execute(context, args)

    async def run(self, context: ExecutionContext, call: ToolCall) -> ToolResult:
        """Run a tool call from the LLM. Never raises: failures become error results."""

        def result(status: str, content: list) -> ToolResult:
            return ToolResult(
                tool_call_id=call.tool_call_id,
                name=self.name,
                status=status,
                content=content,
            )

        try:
            # Validate the raw dict (not **kwargs), so an argument the LLM
            # names "context" is reported as unknown instead of crashing.
            args = self.args_model.model_validate(call.arguments)
            output = await self.execute(context, args)
        except ValidationError as err:
            problems = [
                f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}"
                for e in err.errors(include_input=False, include_url=False)
            ]
            return result("error", [f"Invalid arguments for {self.name}.", *problems])
        except ToolError as err:
            return result("error", [str(err)])
        except Exception as err:  # noqa: BLE001 - the agent loop must keep going
            # Only the error type goes to the LLM; details may contain private data.
            return result("error", [f"{self.name} failed ({type(err).__name__})."])

        return result("success", output if isinstance(output, list) else [output])


class FunctionTool(BaseTool):
    """A tool that wraps a plain function (async or regular).

    The function's parameters become the tool's arguments: type hints set the
    types, defaults make them optional, and the docstring is the description.
    A parameter named `context` gets the ExecutionContext and is hidden from
    the LLM.

        async def reading_level(text: str) -> float:
            \"\"\"Flesch-Kincaid grade level of a text.\"\"\"
            ...

        tool = FunctionTool(reading_level)   # name "reading_level"
    """

    def __init__(
        self,
        func: Callable[..., Any],
        name: str | None = None,
        description: str | None = None,
        tool_definition: dict[str, Any] | None = None,
    ):
        self.func = func
        self.needs_context = "context" in inspect.signature(func).parameters
        self.args_model = self._build_args_model(func)
        super().__init__(
            name=name or func.__name__,
            description=description
            if description is not None
            else inspect.getdoc(func) or "",
            tool_definition=tool_definition,  # None: built from args_model
        )

    async def execute(self, context: ExecutionContext, args: ToolArgs) -> Any:
        """Call the wrapped function with the validated arguments."""
        kwargs = {field: getattr(args, field) for field in type(args).model_fields}
        if self.needs_context:
            kwargs["context"] = context
        result = self.func(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    @staticmethod
    def _build_args_model(func: Callable[..., Any]) -> type[ToolArgs]:
        """Turn the function's parameters (except `context`) into a ToolArgs model."""
        fields: dict[str, Any] = {}
        for param in inspect.signature(func).parameters.values():
            if param.name == "context":
                continue
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                raise ValueError(
                    f"{func.__name__}: *args and **kwargs can't be described to the LLM"
                )
            annotation = Any if param.annotation is param.empty else param.annotation
            default = ... if param.default is param.empty else param.default
            fields[param.name] = (annotation, default)
        model_name = "".join(part.title() for part in func.__name__.split("_")) + "Args"
        return create_model(model_name, __base__=ToolArgs, **fields)
