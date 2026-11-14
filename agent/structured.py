"""Structured output: get a Pydantic model back from the LLM, not free text.

The trick: give the model one tool, "final_answer", whose arguments ARE the
Pydantic model, and force it to call that tool (tool_choice="required").
The tool call's arguments are then checked by the same model.

    story = await generate_structured(client, StoryOutput, instructions, article)
    quiz = await generate_structured(client, Quiz, quiz_instructions, story_text)

Build plan 4.25 (make_output_tool), 4.26 (tool_choice="required"),
4.28 (parse_output), 4.29 (generate_structured).
"""

from typing import Any

from pydantic import BaseModel, ValidationError

from agent.datatypes import ExecutionContext, Message, ToolResult
from agent.llm import (
    LlmClient,
    LlmRequest,
    LlmResponse,
    LlmUsage,
    supports_forced_tool_choice,
)
from agent.tools import BaseTool

OUTPUT_TOOL_NAME = "final_answer"


class StructuredOutputError(Exception):
    """The model didn't return a valid answer, even after retrying."""


def make_output_tool(
    model_cls: type[BaseModel],
    name: str = OUTPUT_TOOL_NAME,
    description: str | None = None,
) -> BaseTool:
    """A tool whose arguments are model_cls. Calling it just returns them."""

    class OutputTool(BaseTool):
        args_model = model_cls  # the tool definition's schema comes from here

        async def execute(self, context: ExecutionContext, args: Any) -> Any:
            return args

    return OutputTool(
        name=name,
        description=description
        or (
            f"Give your final answer as a {model_cls.__name__}. "
            "Call this tool exactly once, with every required field filled in."
        ),
    )


def parse_output[T: BaseModel](
    response: LlmResponse, model_cls: type[T], tool_name: str = OUTPUT_TOOL_NAME
) -> T:
    """Turn the output tool call's arguments into model_cls.

    Raises StructuredOutputError if there's no such call, or ValidationError
    if its arguments don't fit the model.
    """
    calls = [c for c in response.tool_calls if c.name == tool_name]
    if not calls:
        raise StructuredOutputError(
            f"The model didn't call {tool_name} (stop reason: {response.stop_reason})."
        )
    return model_cls.model_validate(calls[0].arguments)


def _problems(err: ValidationError) -> list[str]:
    """Field-by-field problems, without echoing the values back."""
    return [
        f"{'.'.join(map(str, e['loc'])) or 'answer'}: {e['msg']}"
        for e in err.errors(include_input=False, include_url=False)
    ]


async def generate_structured[T: BaseModel](
    client: LlmClient,
    model_cls: type[T],
    instructions: str,
    user_content: str,
    *,
    max_tokens: int = 4096,
    max_attempts: int = 2,
    usage: LlmUsage | None = None,
) -> T:
    """Ask the model for a model_cls answer. Retries once with the errors shown.

    Pass an LlmUsage as `usage` to add up tokens and cost across attempts.
    """
    tool = make_output_tool(model_cls)
    # Force the final_answer call where the model allows it; otherwise ask for
    # it in the instructions (a missing call is retried below).
    if supports_forced_tool_choice(client.model):
        tool_choice = "required"
    else:
        tool_choice = "auto"
        instructions += f"\n\nGive your answer by calling the {tool.name} tool."
    contents: list[Any] = [Message(role="user", content=user_content)]
    last_problems: list[str] = []

    for _ in range(max_attempts):
        response = await client.generate(
            LlmRequest(
                instructions=instructions,
                contents=contents,
                tools=[tool],
                tool_choice=tool_choice,
                max_tokens=max_tokens,
            )
        )
        if usage is not None:
            usage.input_tokens += response.usage.input_tokens
            usage.output_tokens += response.usage.output_tokens
            if response.usage.cost_usd is not None:
                usage.cost_usd = (usage.cost_usd or 0) + response.usage.cost_usd

        try:
            return parse_output(response, model_cls)
        except ValidationError as err:
            last_problems = _problems(err)
        except StructuredOutputError as err:
            last_problems = [str(err)]
            continue  # no final_answer call to reply to; just ask again

        # Show the model its mistakes and ask again (build plan task 13).
        call = next(c for c in response.tool_calls if c.name == tool.name)
        contents += response.content
        contents.append(
            ToolResult(
                tool_call_id=call.tool_call_id,
                name=tool.name,
                status="error",
                content=[
                    (
                        "Your answer didn't pass the checks. Fix these and call "
                        f"{tool.name} again:"
                    ),
                    *last_problems,
                ],
            )
        )

    raise StructuredOutputError(
        f"No valid {model_cls.__name__} after {max_attempts} attempts: "
        + "; ".join(last_problems)
    )
