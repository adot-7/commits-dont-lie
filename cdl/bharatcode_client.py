"""BharatCode model API client for CDL's forced tool calls.

This module owns raw HTTPS calls to BharatCode's OpenAI-compatible Chat
Completions endpoint: headers, forced function tools, busy retries, and output
budget handling. It returns tool arguments only; it must never decide a verdict
or read the process environment.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://bharatcode.ai/api/model/v1"
DEFAULT_MODEL = "qwen-3.8-27b"
MAX_OUTPUT_TOKENS = 32000
BUSY_STATUSES = {429, 503}


class BharatCodeError(RuntimeError):
    """Raised when BharatCode refuses a call or the output budget runs out.

    Malformed tool output raises ``ValueError`` instead, so callers can retry it.
    """


@dataclass
class ToolResult:
    """Provider-neutral result of one forced tool call."""

    raw_input: Any
    input_tokens: int = 0
    output_tokens: int = 0
    request_id: str = ""
    via: str = "tool_call"


def openai_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert CDL's Anthropic-style tool definition to an OpenAI function tool."""

    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool["input_schema"],
        },
    }


def _content_json(content: str | None) -> Any | None:
    """Return a JSON object written as plain content, with or without a fence."""

    if not content:
        return None
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


class BharatCodeClient:
    """Synchronous BharatCode client with one forced function tool per call."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        reasoning_effort: str = "none",
        http_client: httpx.Client | Any | None = None,
        busy_wait_seconds: float = 2.0,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.reasoning_effort = reasoning_effort
        self.client = http_client or httpx.Client(timeout=120)
        self._owns_client = http_client is None
        self.busy_wait_seconds = busy_wait_seconds

    @property
    def headers(self) -> dict[str, str]:
        """Return bearer authentication headers for the model API."""

        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def close(self) -> None:
        """Close the internally-owned HTTP client."""

        if self._owns_client:
            self.client.close()

    def _post(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """POST one completion, retrying twice when the model is busy."""

        last_error = "unknown BharatCode error"
        for attempt in range(3):
            response = self.client.post(f"{self.base_url}/chat/completions", headers=self.headers, json=payload)
            request_id = response.headers.get("x-request-id", "")
            if response.status_code in BUSY_STATUSES and attempt < 2:
                time.sleep(self.busy_wait_seconds * (attempt + 1))
                continue
            if response.status_code >= 400:
                last_error = f"HTTP {response.status_code} request_id={request_id or '-'}: {response.text[:500]}"
                raise BharatCodeError(last_error)
            return response.json(), request_id
        raise BharatCodeError(last_error)

    def call_tool(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tool: dict[str, Any],
        temperature: float,
        max_tokens: int = 4096,
    ) -> ToolResult:
        """Force one function call and return its raw arguments.

        A ``length`` finish means the output budget (which includes reasoning)
        ran out. Incomplete tool calls are never used; the call is repeated
        once with double the budget instead of the same exhausted one.
        """

        budget = max_tokens
        for attempt in range(2):
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": [{"role": "system", "content": system}, *messages],
                "tools": [openai_tool(tool)],
                "tool_choice": {"type": "function", "function": {"name": tool["name"]}},
                "temperature": temperature,
                "max_tokens": budget,
            }
            if self.reasoning_effort:
                payload["reasoning_effort"] = self.reasoning_effort
            body, request_id = self._post(payload)
            choices = body.get("choices") or []
            if not choices:
                raise ValueError(f"response has no choices request_id={request_id or '-'}")
            choice = choices[0]
            usage = body.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens", 0) or 0)
            output_tokens = int(usage.get("completion_tokens", 0) or 0)
            if choice.get("finish_reason") == "length":
                if attempt == 0 and budget < MAX_OUTPUT_TOKENS:
                    budget = min(budget * 2, MAX_OUTPUT_TOKENS)
                    continue
                raise BharatCodeError(f"output budget of {budget} tokens exhausted request_id={request_id or '-'}")
            message = choice.get("message") or {}
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                if function.get("name") == tool["name"]:
                    return ToolResult(function.get("arguments"), input_tokens, output_tokens, request_id)
            fallback = _content_json(message.get("content"))
            if fallback is not None:
                return ToolResult(fallback, input_tokens, output_tokens, request_id, via="content_json")
            raise ValueError(f"response has no {tool['name']} tool call request_id={request_id or '-'}")
        raise BharatCodeError("tool call failed")

    def list_models(self) -> list[str]:
        """Return model IDs from the public catalog endpoint."""

        response = self.client.get(f"{self.base_url}/models", headers=self.headers)
        if response.status_code >= 400:
            raise BharatCodeError(f"HTTP {response.status_code}: {response.text[:500]}")
        return [str(item.get("id")) for item in response.json().get("data", []) if item.get("id")]
