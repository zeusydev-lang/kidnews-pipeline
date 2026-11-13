"""Live test call to the real model (build plan 4.14). Costs a fraction of a cent.

Needs ANTHROPIC_API_KEY in .env. Makes three checks:
  1. a plain question,
  2. a question that makes the model call a tool, then answer with the result,
  3. a structured Quiz through the final_answer tool.

Usage:
    uv run python -m scripts.llm_smoke
"""

import asyncio
import os
import sys

from agent.datatypes import ExecutionContext, Message
from agent.llm import LlmClient, LlmError, LlmRequest, LlmUsage
from agent.structured import generate_structured
from agent.tools import FunctionTool
from core.models import Quiz


def word_count(text: str) -> int:
    """Count the words in a piece of text."""
    return len(text.split())


async def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Add it to .env first.")
        return 1

    client = LlmClient()
    print(f"Model: {client.model}\n")
    total_cost = 0.0

    # 1. Plain question
    request = LlmRequest(
        instructions="You explain Earth science to middle-schoolers in one sentence.",
        contents=[Message(role="user", content="Why is the sky blue?")],
        max_tokens=200,
    )
    try:
        response = await client.generate(request)
    except LlmError as err:
        print(err)
        return 1
    print("1. Plain call")
    print(f"   Answer: {response.text}")
    usage = response.usage
    print(f"   Tokens: {usage.input_tokens} in / {usage.output_tokens} out")
    total_cost += response.usage.cost_usd or 0

    # 2. Tool call round trip
    tool = FunctionTool(word_count)
    contents = [
        Message(
            role="user",
            content=(
                'Use the word_count tool on "Sea ice grows in winter", '
                "then tell me the number."
            ),
        )
    ]
    request = LlmRequest(contents=contents, tools=[tool], max_tokens=300)
    response = await client.generate(request)
    total_cost += response.usage.cost_usd or 0
    print("\n2. Tool call")
    if not response.tool_calls:
        print(f"   The model didn't call the tool. It said: {response.text!r}")
        return 1

    contents += response.content
    for call in response.tool_calls:
        print(f"   Model called {call.name}({call.arguments})")
        result = await tool.run(ExecutionContext(), call)
        print(f"   Tool returned {result.status}: {result.content}")
        contents.append(result)

    final = await client.generate(
        LlmRequest(contents=contents, tools=[tool], max_tokens=300)
    )
    total_cost += final.usage.cost_usd or 0
    print(f"   Final answer: {final.text}")

    # 3. Structured output: a Quiz via the final_answer tool
    usage = LlmUsage()
    quiz = await generate_structured(
        client,
        Quiz,
        "Write a 4-question multiple-choice quiz for 6th graders. "
        "Each question has 4 options and exactly one correct answer.",
        "Story: Sea ice grows in winter and melts in summer. Satellites measure it.",
        usage=usage,
    )
    total_cost += usage.cost_usd or 0
    print("\n3. Structured output (Quiz)")
    for i, q in enumerate(quiz.questions, 1):
        print(f"   {i}. {q.prompt}  ->  {q.options[q.correct_index]}")

    print(f"\nTotal cost: ${total_cost:.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
