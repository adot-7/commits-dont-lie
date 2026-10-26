"""Offline tests for the BharatCode Chat Completions client and provider config."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from cdl.bharatcode_client import BharatCodeClient, BharatCodeError
from cdl.config import ConfigError, Settings, load_settings
from cdl.llm import ENTITIES_TOOL, draft, extract_entities
from cdl.models import DiffContext, FileChange, Sentence
from cdl.store import Store


def completion(arguments=None, *, content=None, finish_reason="tool_calls", name="extract_entities"):
    """Build an OpenAI-shaped chat completion body."""

    message: dict = {"role": "assistant", "content": content}
    if arguments is not None:
        message["tool_calls"] = [
            {"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
        ]
    return {
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
    }


def mock_client(responses):
    """Return a BharatCode client whose HTTP layer replays queued responses."""

    queue = list(responses)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, body = queue.pop(0)
        return httpx.Response(status, json=body, headers={"x-request-id": f"req-{len(seen)}"})

    client = BharatCodeClient(
        "bc_test_key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        busy_wait_seconds=0,
    )
    return client, seen


def settings(tmp_path: Path) -> Settings:
    """Return settings that select the BharatCode provider."""

    base = Settings(
        "http://test", "admin", "github", "webhook", "adot-7/commits-dont-lie", "notion", "notes", "posts",
        "notes-ds", "posts-ds", "slack", "signing", "C1", "", "unused", str(tmp_path / "db.sqlite")
    )
    return replace(base, llm_provider="bharatcode", bharatcode_api_key="bc_test_key")


def test_forced_function_tool_payload_and_bearer_header():
    client, seen = mock_client([(200, completion({"files": [], "symbols": [], "integrations": []}))])
    result = client.call_tool(system="sys", messages=[{"role": "user", "content": "hi"}], tool=ENTITIES_TOOL, temperature=0.0)

    body = json.loads(seen[0].content)
    assert seen[0].url == "https://bharatcode.ai/api/model/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer bc_test_key"
    assert body["model"] == "qwen-3.8-27b"
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["tools"][0]["function"]["parameters"] == ENTITIES_TOOL["input_schema"]
    assert body["tool_choice"] == {"type": "function", "function": {"name": "extract_entities"}}
    assert body["reasoning_effort"] == "none"
    assert json.loads(result.raw_input) == {"files": [], "symbols": [], "integrations": []}
    assert (result.input_tokens, result.output_tokens, result.request_id) == (120, 30, "req-1")


def test_length_finish_retries_with_double_budget_and_never_uses_partial_call():
    partial = completion({"files": ["cdl/app.py"]}, finish_reason="length")
    client, seen = mock_client([(200, partial), (200, completion({"files": [], "symbols": [], "integrations": []}))])
    client.call_tool(system="s", messages=[], tool=ENTITIES_TOOL, temperature=0.0, max_tokens=1000)

    assert [json.loads(request.content)["max_tokens"] for request in seen] == [1000, 2000]


def test_second_length_finish_raises():
    partial = completion({"files": []}, finish_reason="length")
    client, _ = mock_client([(200, partial), (200, partial)])
    with pytest.raises(BharatCodeError, match="budget"):
        client.call_tool(system="s", messages=[], tool=ENTITIES_TOOL, temperature=0.0)


def test_busy_model_is_retried():
    client, seen = mock_client([(503, {"error": "busy"}), (200, completion({"files": [], "symbols": [], "integrations": []}))])
    client.call_tool(system="s", messages=[], tool=ENTITIES_TOOL, temperature=0.0)
    assert len(seen) == 2


def test_auth_error_names_status_and_request_id():
    client, _ = mock_client([(401, {"error": {"message": "Missing BharatCode bearer token."}})])
    with pytest.raises(BharatCodeError, match="HTTP 401 request_id=req-1"):
        client.call_tool(system="s", messages=[], tool=ENTITIES_TOOL, temperature=0.0)


def test_json_in_content_is_accepted_when_no_tool_call():
    content = '```json\n{"files": [], "symbols": ["claim_vs_diff"], "integrations": []}\n```'
    client, _ = mock_client([(200, completion(None, content=content, finish_reason="stop"))])
    result = client.call_tool(system="s", messages=[], tool=ENTITIES_TOOL, temperature=0.0)
    assert result.raw_input["symbols"] == ["claim_vs_diff"]
    assert result.via == "content_json"


def test_extraction_through_bharatcode_logs_usage_and_keeps_substring_guard(tmp_path):
    client, _ = mock_client([(200, completion({"files": ["cdl/app.py"], "symbols": ["handle_push"], "integrations": []}))])
    store = Store(tmp_path / "db.sqlite")
    entities = extract_entities(
        Sentence(0, "cdl/app.py now calls handle_push", "commits"), ["cdl/app.py"],
        llm_client=client, store=store, settings=settings(tmp_path),
    )
    rows = store._connect().execute("SELECT data_json FROM events WHERE kind='llm.call'").fetchall()
    logged = json.loads(rows[-1]["data_json"])
    assert entities.files == ["cdl/app.py"]
    assert logged["model"] == "qwen-3.8-27b"
    assert logged["request_id"] == "req-1"


def test_draft_through_bharatcode(tmp_path):
    sentences = [{"text": f"I changed cdl/app.py, part {idx}.", "source": "commits"} for idx in range(3)]
    client, seen = mock_client([(200, completion({"sentences": sentences}, name="draft_sentences"))])
    diff = DiffContext("r", "a" * 40, "b" * 40, files=[FileChange("cdl/app.py", "modified")])
    result = draft("Title", "Body", diff, llm_client=client, store=Store(tmp_path / "db.sqlite"), settings=settings(tmp_path))
    assert len(result) == 3
    assert json.loads(seen[0].content)["tool_choice"]["function"]["name"] == "draft_sentences"


REQUIRED = {
    "ADMIN_TOKEN": "a", "GITHUB_TOKEN": "g", "GITHUB_WEBHOOK_SECRET": "w", "GITHUB_REPO": "o/r",
    "NOTION_TOKEN": "n", "NOTION_NOTES_DB_ID": "1", "NOTION_POSTS_DB_ID": "2", "NOTION_NOTES_DS_ID": "3",
    "NOTION_POSTS_DS_ID": "4", "SLACK_BOT_TOKEN": "s", "SLACK_SIGNING_SECRET": "x", "SLACK_CHANNEL_ID": "C",
}


def test_default_provider_requires_bharatcode_key(monkeypatch, tmp_path):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    for name in ("LLM_PROVIDER", "BHARATCODE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigError, match="BHARATCODE_API_KEY"):
        load_settings(strict=True, dotenv_path=tmp_path / "missing.env")
    monkeypatch.setenv("BHARATCODE_API_KEY", "bc")
    loaded = load_settings(strict=True, dotenv_path=tmp_path / "missing.env")
    assert (loaded.llm_provider, loaded.llm_model) == ("bharatcode", "qwen-3.8-27b")


def test_anthropic_provider_requires_anthropic_key(monkeypatch, tmp_path):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        load_settings(strict=True, dotenv_path=tmp_path / "missing.env")


def test_unknown_provider_fails_loudly(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(ConfigError, match="LLM_PROVIDER"):
        load_settings(strict=False, dotenv_path=tmp_path / "missing.env")
