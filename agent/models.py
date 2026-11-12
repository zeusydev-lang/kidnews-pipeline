from pydantic import BaseModel, Field
from typing import Annotated, Literal, Union
import uuid
from datetime import datetime

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