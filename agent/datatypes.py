import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class Message(BaseModel):
    """A LLM message in the conversation with the agent"""

    type: Literal["message"] = "message"
    role: Literal["system", "user", "assistant"]
    content: str


class ToolCall(BaseModel):
    """A LLM message that calls a tool in the agent"""

    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str
    name: str
    arguments: dict


class ToolResult(BaseModel):
    """A LLM message that returns the result of a tool call"""

    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    name: str
    status: Literal["success", "error"]
    content: list


ContentItem = Annotated[Message | ToolCall | ToolResult, Field(discriminator="type")]


class Event(BaseModel):
    """An event in the agent's conversation with the LLM"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    execution_id: str
    timestamp: float = Field(default_factory=lambda: datetime.now().timestamp())
    author: str
    content: list[ContentItem] = Field(default_factory=list)


@dataclass
class ExecutionContext:
    """Storage for all execution context data"""

    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    events: list[Event] = field(default_factory=list)
    current_step: int = 0
    state: dict[str, Any] = field(default_factory=dict)
    final_result: str | BaseModel | None = None

    def add_event(self, event: Event):
        """Add an event to the execution context"""
        self.events.append(event)

    def increment_step(self):
        """Increment the current step of the execution context"""
        self.current_step += 1
