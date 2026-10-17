"""Chat model wrapper for tool calling: OpenRouter (default), Azure OpenAI, or any
OpenAI-compatible endpoint.

OpenRouter is one API in front of many models (OpenAI, Google, DeepSeek...). It speaks the
OpenAI chat API, so the official `openai` SDK works with base_url=https://openrouter.ai/api/v1
and switching model is just a different LLM_MODEL string.

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
class Usage:
    """Tokens and cost of one or more LLM calls. cost_usd is None if the API did not say."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None
    calls: int = 0

    def __add__(self, other: Usage) -> Usage:
        costs = [c for c in (self.cost_usd, other.cost_usd) if c is not None]
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            sum(costs) if costs else None,
            self.calls + other.calls,
        )


@dataclass
class ChatResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)

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
    """Works with `openai.OpenAI` (OpenRouter and others) and `openai.AzureOpenAI` clients."""

    def __init__(
        self,
        client: Any,
        model: str,
        temperature: float | None = 0.0,
        *,
        extra_body: dict | None = None,
    ) -> None:
        self.client = client
        self.model = model  # on Azure this is the deployment name
        self.temperature = temperature
        self.extra_body = extra_body
        self.name = model

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResponse:
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if tools:
            kwargs |= {"tools": tools, "tool_choice": "auto"}
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        response = self.client.chat.completions.create(**kwargs)
        if not getattr(response, "choices", None):
            # OpenRouter reports provider errors in the body with HTTP 200.
            error = getattr(response, "error", None) or "no choices returned"
            raise RuntimeError(f"LLM call failed: {error}")
        message = response.choices[0].message
        calls = []
        for call in message.tool_calls or []:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_invalid_json": call.function.arguments}
            calls.append(ToolCall(call.id, call.function.name, args))
        return ChatResponse(message.content, calls, _usage(response))


def _usage(response: Any) -> Usage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return Usage(calls=1)
    cost = getattr(usage, "cost", None)  # OpenRouter adds the cost in USD
    return Usage(
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
        float(cost) if cost is not None else None,
        1,
    )


def openrouter_client(settings) -> Any | None:
    """An OpenAI SDK client for LLM_BASE_URL (OpenRouter by default), or None without a key."""
    key = settings.secret("openrouter_api_key")
    if not key:
        return None
    from openai import OpenAI

    return OpenAI(
        base_url=settings.llm_base_url,
        api_key=key,
        timeout=settings.llm_timeout_seconds,
        max_retries=3,
        # Optional OpenRouter headers that name the app in its dashboard.
        default_headers={
            "HTTP-Referer": "https://github.com/rapuriteja/lakehouse-copilot-mcp",
            "X-Title": "Lakehouse Copilot",
        },
    )


def get_chat_model(settings, model: str | None = None) -> ChatModel | None:
    """LLM_PROVIDER=openrouter (default) | azure | offline.

    Returns None for offline mode, or when OpenRouter has no API key: the copilot then
    uses the rule-based planner, so the project still runs for free.
    """
    if settings.llm_provider == "azure":
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.secret("azure_openai_api_key"),
            api_version=settings.azure_openai_api_version,
        )
        return OpenAIChatModel(
            client, model or settings.azure_openai_chat_deployment, settings.llm_temperature
        )
    if settings.llm_provider == "openrouter":
        client = openrouter_client(settings)
        if client is None:
            return None
        return OpenAIChatModel(
            client,
            model or settings.llm_model,
            settings.llm_temperature,
            extra_body={"usage": {"include": True}},  # ask OpenRouter to report the cost
        )
    return None
