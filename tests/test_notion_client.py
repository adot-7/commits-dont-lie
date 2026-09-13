"""Offline contract tests for the Notion client."""

from __future__ import annotations

from pathlib import Path

from cdl.config import Settings
from cdl.notion_client import NotionClient
from cdl.store import Store


class Response:
    """Minimal response double with JSON and status metadata."""

    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.text = "notion response"

    def json(self):
        return self.payload


class HTTP:
    """Queue-backed request double."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def settings(tmp_path: Path) -> Settings:
    """Return isolated settings for Notion tests."""

    return Settings(
        "http://test", "admin", "github", "webhook", "repo", "notion-token", "notes-db", "posts-db",
        "notes-ds", "posts-ds", "slack", "signing", "C1", "anthropic", "model", str(tmp_path / "db.sqlite")
    )


def test_ready_notes_and_body_use_data_sources_and_blocks(tmp_path):
    """Queries use the current endpoint and body readers join supported blocks."""

    http = HTTP([
        Response({"results": [{"id": "page-1", "properties": {"Name": {"title": [{"plain_text": "Build"}]}}}]}),
        Response({"results": [
            {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Why"}]}},
            {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "What"}]}},
            {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Next"}]}},
        ]}),
    ])
    client = NotionClient(settings(tmp_path), store=Store(tmp_path / "db.sqlite"), http_client=http)
    assert client.ready_notes() == [("page-1", "Build")]
    assert client.read_note_body("page-1") == "Why\nWhat\nNext"
    assert "/data_sources/notes-ds/query" in http.calls[0][1]
    assert http.calls[0][2]["headers"]["Notion-Version"] == "2025-09-03"
    assert http.calls[0][2]["json"]["filter"]["select"]["equals"] == "Ready"


def test_create_and_update_chunk_rich_text(tmp_path):
    """Post text and mirror fields are sent with <=2000-character objects."""

    http = HTTP([Response({"id": "notion-post"}), Response({})])
    client = NotionClient(settings(tmp_path), store=Store(tmp_path / "db.sqlite"), http_client=http)
    client.create_post_row("x" * 2105, "Draft", ["e" * 2001], "a" * 40, "b" * 40, "note")
    client.update_post_row("notion-post", status="Sent", slack_ts="123.4")
    body = http.calls[0][2]["json"]["properties"]
    assert all(len(item["text"]["content"]) <= 2000 for item in body["Post Text"]["rich_text"])
    assert all(len(item["text"]["content"]) <= 2000 for item in body["Evidence"]["rich_text"])
    assert http.calls[1][2]["json"]["properties"]["Status"] == {"select": {"name": "Sent"}}
