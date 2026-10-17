"""Chat model wrapper for tool calling (Azure OpenAI, or any OpenAI-compatible endpoint).

Tool calling: we send the model a list of tools (name, description, JSON schema of the
arguments). Instead of answering, the model can reply "call run_sql with this SQL".
We run the tool, send the result back, and repeat until the model gives a final answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)

    def as_message(self) -> dict:
        """The assistant message to append to the conversation history."""
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                }
                for c in self.tool_calls
            ]
        return message


class ChatModel(Protocol):
    name: str

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResponse: ...


class OpenAIChatModel:
    """Works with `openai.AzureOpenAI` and `openai.OpenAI` clients (same chat API)."""

    def __init__(self, client: Any, model: str, temperature: float = 0.0) -> None:
        self.client = client
        self.model = model  # on Azure this is the deployment name
        self.temperature = temperature
        self.name = model

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResponse:
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if tools:
            kwargs |= {"tools": tools, "tool_choice": "auto"}
        message = self.client.chat.completions.create(**kwargs).choices[0].message
        calls = []
        for call in message.tool_calls or []:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_invalid_json": call.function.arguments}
            calls.append(ToolCall(call.id, call.function.name, args))
        return ChatResponse(message.content, calls)


def get_chat_model(settings) -> ChatModel | None:
    """LLM_PROVIDER=azure | openai | offline. Offline returns None (rule-based planner)."""
    if settings.llm_provider == "azure":
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key.get_secret_value(),
            api_version=settings.azure_openai_api_version,
        )
        return OpenAIChatModel(client, settings.azure_openai_chat_deployment)
    if settings.llm_provider == "openai":
        from openai import OpenAI

        key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else "none"
        client = OpenAI(base_url=settings.openai_base_url, api_key=key)
        return OpenAIChatModel(client, settings.openai_model)
    return None
