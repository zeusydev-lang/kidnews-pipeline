"""LLM layer: one place that talks to the model, through litellm.

litellm lets us switch providers by changing one string (LLM_MODEL). Only this
module imports litellm, so the rest of the project stays fast to import.

    client = LlmClient()                                   # model from LLM_MODEL
    request = LlmRequest(
        instructions="...", contents=[Message(role="user", content="...")]
    )
    response = await client.generate(request)

Notes for Claude Haiku 5.5 (the default):
  - No temperature / top_p / top_k: the model rejects non-default values.
  - Thinking is off by default here. With thinking on, Claude's thinking blocks
    must be sent back on every tool-calling turn, and our Event types don't
    store them. Pass thinking={"type": "adaptive"} to turn it on for
    single-turn calls.
"""

import json
import os
from typing import Any, Literal

import litellm
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from agent.datatypes import ContentItem, Message, ToolCall, ToolResult
from agent.tools import BaseTool

load_dotenv(override=False)  # ANTHROPIC_API_KEY and LLM_MODEL from .env

DEFAULT_MODEL = "anthropic/claude-haiku-5-5"

# US dollars per million tokens (input, output). Checked 2026-11; prices change,
# so verify against the provider's pricing page. Unknown models cost None.
PRICES_PER_MILLION: dict[str, tuple[float, float]] = {
    "anthropic/claude-haiku-5-5": (0.10, 0.50),  # prompts up to 100K tokens
    "anthropic/claude-sonnet-5-5": (2.00, 10.00),
}

# Claude models that reject a forced tool call (tool_choice "required").
# For these, structured output uses "auto" plus an instruction instead.
_NO_FORCED_TOOL_CHOICE = (
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "claude-fable-5-1",
    "claude-fable-5-2",
)


def supports_forced_tool_choice(model: str) -> bool:
    return not any(name in model for name in _NO_FORCED_TOOL_CHOICE)


def default_thinking(model: str) -> dict[str, Any] | None:
    """How to turn thinking off for each Claude model, or None to leave it alone.

    With thinking on, Claude's thinking blocks must be sent back on every tool
    turn, and our Event types don't store them, so we keep it off where we can.
    """
    if not model.startswith("anthropic/"):
        return None
    if "claude-sonnet-5-5" in model:
        return {"type": "between_tools"}  # Sonnet 5.5 rejects "disabled"
    if "claude-opus-5-5" in model or "claude-fable-5" in model:
        return None  # thinking can't be turned off on these
    return {"type": "disabled"}


class LlmError(Exception):
    """The LLM call failed. The message never contains the API key."""


# --- Request and response ----------------------------------------------------


class LlmRequest(BaseModel):
    """Everything needed for one model call."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    instructions: str = ""  # the system prompt
    contents: list[ContentItem] = Field(default_factory=list)  # the conversation
    tools: list[BaseTool] = Field(default_factory=list)
    # "required" forces a tool call (structured output). Claude Haiku 5.5
    # accepts it; Claude Opus 5.5 and Sonnet 5.5 reject it with a 400
    # (see supports_forced_tool_choice).
    tool_choice: Literal["auto", "none", "required"] = "auto"
    max_tokens: int = Field(default=4096, gt=0)


class LlmUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None  # None when the model's price is unknown


class LlmResponse(BaseModel):
    """What came back: text and/or tool calls, plus usage for cost tracking."""

    content: list[ContentItem] = Field(default_factory=list)
    stop_reason: str | None = None  # "stop", "tool_calls", "length", ...
    model: str = ""
    usage: LlmUsage = Field(default_factory=LlmUsage)

    @property
    def text(self) -> str:
        """All assistant text joined together."""
        return "\n".join(c.content for c in self.content if isinstance(c, Message))

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [c for c in self.content if isinstance(c, ToolCall)]


# --- Client ------------------------------------------------------------------


class LlmClient:
    """Calls the model through litellm and converts to and from our types."""

    def __init__(
        self,
        model: str | None = None,
        *,
        thinking: dict[str, Any] | None = None,
        timeout: float = 120.0,
        num_retries: int = 2,
        api_base: str | None = None,  # only for tests or proxies
    ):
        self.model = model or os.environ.get("LLM_MODEL") or DEFAULT_MODEL
        self.thinking = (
            thinking if thinking is not None else default_thinking(self.model)
        )
        self.timeout = timeout
        self.num_retries = num_retries
        self.api_base = api_base

    async def generate(self, request: LlmRequest) -> LlmResponse:
        """Send one request to the model and return the parsed response."""
        params: dict[str, Any] = {
            "model": self.model,
            "messages": self._build_messages(request),
            "max_tokens": request.max_tokens,
            "timeout": self.timeout,
            "num_retries": self.num_retries,
        }
        if request.tools:
            params["tools"] = [tool.tool_definition for tool in request.tools]
            params["tool_choice"] = request.tool_choice
        if self.thinking is not None:
            params["thinking"] = self.thinking
        if self.api_base:
            params["api_base"] = self.api_base

        try:
            raw = await litellm.acompletion(**params)
        except Exception as err:
            # Type and status only: provider messages can echo request details.
            status = getattr(err, "status_code", None)
            detail = f" (HTTP {status})" if status else ""
            raise LlmError(f"LLM call failed: {type(err).__name__}{detail}") from err
        return self._parse_response(raw)

    # --- Our types -> litellm (OpenAI-style) messages --------------------------

    @staticmethod
    def _build_messages(request: LlmRequest) -> list[dict[str, Any]]:
        """Convert instructions + contents into litellm's message list.

        - Assistant text followed by tool calls becomes ONE assistant message,
          which is how Claude expects a turn that both talks and calls tools.
        - Each tool result becomes a "tool" message; litellm groups them into
          one Claude user turn.
        """
        messages: list[dict[str, Any]] = []
        if request.instructions:
            messages.append({"role": "system", "content": request.instructions})

        for item in request.contents:
            if isinstance(item, Message):
                messages.append({"role": item.role, "content": item.content})

            elif isinstance(item, ToolCall):
                call = {
                    "id": item.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": item.name,
                        "arguments": json.dumps(item.arguments),
                    },
                }
                last = messages[-1] if messages else None
                if last and last["role"] == "assistant":
                    last.setdefault("tool_calls", []).append(call)
                else:
                    messages.append(
                        {"role": "assistant", "content": None, "tool_calls": [call]}
                    )

            elif isinstance(item, ToolResult):
                text = "\n".join(
                    c if isinstance(c, str) else json.dumps(c, default=str)
                    for c in item.content
                )
                if item.status == "error":
                    text = f"Error: {text}"
                messages.append(
                    {"role": "tool", "tool_call_id": item.tool_call_id, "content": text}
                )
        return messages

    # --- litellm response -> our types -----------------------------------------

    def _parse_response(self, raw: Any) -> LlmResponse:
        """Convert litellm's response into an LlmResponse."""
        choice = raw.choices[0]
        message = choice.message
        content: list[ContentItem] = []

        if message.content:
            content.append(Message(role="assistant", content=message.content))

        for call in message.tool_calls or []:
            raw_args = call.function.arguments or "{}"
            try:
                arguments = json.loads(raw_args)
                if not isinstance(arguments, dict):
                    raise TypeError("arguments must be a JSON object")
            except (ValueError, TypeError):
                # Keep the run going: the tool's argument check will refuse
                # this unknown key and tell the model what went wrong.
                arguments = {"_invalid_json": raw_args}
            content.append(
                ToolCall(
                    tool_call_id=call.id, name=call.function.name, arguments=arguments
                )
            )

        usage = getattr(raw, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        return LlmResponse(
            content=content,
            stop_reason=choice.finish_reason,
            model=getattr(raw, "model", "") or self.model,
            usage=LlmUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=self.cost(input_tokens, output_tokens),
            ),
        )

    def cost(self, input_tokens: int, output_tokens: int) -> float | None:
        prices = PRICES_PER_MILLION.get(self.model)
        if prices is None:
            return None
        return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000
