"""Notion REST client for CDL notes and post mirrors.

This module owns API-versioned data-source queries, page/block parsing, schema
assertion, and mirror writes. It must not contain deterministic verdict logic
or Slack behavior.
"""

from __future__ import annotations

from typing import Any, Iterable

import httpx

from .config import Settings, get_settings
from .store import Store


API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"


class NotionError(RuntimeError):
    """Raised when a Notion request fails or returns an unexpected shape."""


class NotionSchemaError(NotionError):
    """Raised when a configured data source is missing a required property."""


NOTES_SCHEMA = {"Name": "title", "Status": "select", "Created": "created_time"}
POSTS_SCHEMA = {
    "Name": "title",
    "Status": "select",
    "Post Text": "rich_text",
    "Evidence": "rich_text",
    "Commit Range": "rich_text",
    "Head SHA": "rich_text",
    "Slack TS": "rich_text",
    "Superseded By": "relation",
    "Note": "relation",
    "Dashboard": "url",
}


def chunks(text: str, size: int = 2000) -> list[str]:
    """Split rich text at Notion's 2000-character object limit."""

    if not text:
        return []
    return [text[offset : offset + size] for offset in range(0, len(text), size)]


def rich_text(content: str) -> list[dict[str, Any]]:
    """Build chunked Notion rich-text objects from plain text."""

    return [{"text": {"content": part}} for part in chunks(content)]


def _plain_text(items: Iterable[dict[str, Any]] | None) -> str:
    """Join Notion rich-text plain text fields."""

    return "".join(str(item.get("plain_text", "")) for item in (items or []))


class NotionClient:
    """Synchronous, injectable Notion client with structured event logging."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        store: Store | None = None,
        http_client: httpx.Client | Any | None = None,
    ):
        self.settings = settings or get_settings(strict=False)
        self.store = store or Store(self.settings.database_path)
        self.client = http_client or httpx.Client(timeout=20)
        self._owns_client = http_client is None

    @property
    def headers(self) -> dict[str, str]:
        """Return the required Notion authorization and API-version headers."""

        return {
            "Authorization": f"Bearer {self.settings.notion_token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def close(self) -> None:
        """Close the internally-owned HTTP client."""

        if self._owns_client:
            self.client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        operation: str,
        write: bool = False,
    ) -> dict[str, Any]:
        """Perform one Notion request and log its outcome without leaking headers."""

        url = f"{API_BASE}/{path.lstrip('/')}"
        try:
            response = self.client.request(method, url, headers=self.headers, json=body, params=params)
            status_code = int(getattr(response, "status_code", 0))
            if not 200 <= status_code < 300:
                raw = getattr(response, "text", "")
                try:
                    parsed = response.json()
                    raw = parsed if parsed else raw
                except (TypeError, ValueError):
                    pass
                raise NotionError(f"HTTP {status_code}: {raw}")
            payload = response.json()
        except (httpx.HTTPError, OSError, NotionError, ValueError) as exc:
            data = {"operation": operation, "reason": str(exc)}
            if "response" in locals():
                data["status_code"] = getattr(response, "status_code", None)
                data["raw_response"] = getattr(response, "text", "")
            self.store.append_event("notion.failed", data)
            raise
        self.store.append_event(
            "notion.written" if write else "notion.request",
            {"operation": operation, "status_code": status_code},
        )
        return payload

    def resolve_data_source_id(self, database_id: str) -> str:
        """Resolve the first data-source ID for one legacy database ID."""

        payload = self._request("GET", f"databases/{database_id}", operation="resolve_data_source")
        sources = payload.get("data_sources") or []
        if not sources or not sources[0].get("id"):
            raise NotionError(f"No data source returned for database {database_id}")
        return str(sources[0]["id"])

    def resolve_data_source_ids(self) -> dict[str, str]:
        """Resolve and return Notes/Posts data-source IDs for the CLI."""

        return {
            "NOTION_NOTES_DS_ID": self.resolve_data_source_id(self.settings.notion_notes_db_id),
            "NOTION_POSTS_DS_ID": self.resolve_data_source_id(self.settings.notion_posts_db_id),
        }

    def ready_notes(self) -> list[tuple[str, str]]:
        """Return ready note page IDs and titles in created-time order."""

        body = {
            "filter": {"property": "Status", "select": {"equals": "Ready"}},
            "sorts": [{"timestamp": "created_time", "direction": "ascending"}],
        }
        payload = self._request(
            "POST",
            f"data_sources/{self.settings.notion_notes_ds_id}/query",
            body=body,
            operation="ready_notes",
        )
        notes: list[tuple[str, str]] = []
        for page in payload.get("results", []):
            properties = page.get("properties") or {}
            title_items = (properties.get("Name") or {}).get("title") or []
            notes.append((str(page.get("id", "")), _plain_text(title_items)))
        self.store.append_event("notes.ready", {"count": len(notes)})
        return notes

    def read_note_body(self, page_id: str) -> str:
        """Read paragraph, list, and heading blocks and join their text."""

        payload = self._request(
            "GET",
            f"blocks/{page_id}/children",
            params={"page_size": 100},
            operation="read_note_body",
        )
        lines: list[str] = []
        for block in payload.get("results", []):
            block_type = block.get("type", "")
            if block_type in {"paragraph", "bulleted_list_item", "heading_1", "heading_2", "heading_3"}:
                lines.append(_plain_text((block.get(block_type) or {}).get("rich_text")))
        return "\n".join(line for line in lines if line)

    def _assert_data_source_schema(self, data_source_id: str, expected: dict[str, str], label: str) -> None:
        """Verify required property names and Notion property types."""

        payload = self._request("GET", f"data_sources/{data_source_id}", operation=f"assert_schema:{label}")
        properties = payload.get("properties") or {}
        missing: list[str] = []
        for name, expected_type in expected.items():
            actual_type = (properties.get(name) or {}).get("type")
            if actual_type != expected_type:
                missing.append(f"{name} (expected {expected_type}, got {actual_type or 'missing'})")
        if missing:
            raise NotionSchemaError(f"{label} data source schema invalid: {', '.join(missing)}")

    def assert_schema(self) -> None:
        """Fail loudly if either configured data source lacks the required schema."""

        self._assert_data_source_schema(self.settings.notion_notes_ds_id, NOTES_SCHEMA, "Notes")
        self._assert_data_source_schema(self.settings.notion_posts_ds_id, POSTS_SCHEMA, "Posts")

    def set_note_status(self, page_id: str, status: str) -> None:
        """Update a Notes page select status."""

        self._request(
            "PATCH",
            f"pages/{page_id}",
            body={"properties": {"Status": {"select": {"name": status}}}},
            operation="set_note_status",
            write=True,
        )

    def create_post_row(
        self,
        text: str,
        status: str,
        evidence_lines: list[str],
        base_sha: str,
        head_sha: str,
        note_page_id: str,
        *,
        dashboard_url: str = "",
        name: str | None = None,
    ) -> str:
        """Create a mirrored Posts row and return its Notion page ID."""

        properties: dict[str, Any] = {
            "Name": {"title": [{"text": {"content": (name or text[:60] or "Build update")[:60]}}]},
            "Status": {"select": {"name": status}},
            "Post Text": {"rich_text": rich_text(text)},
            "Evidence": {"rich_text": [item for line in evidence_lines for item in rich_text(line)]},
            "Commit Range": {"rich_text": rich_text(f"{base_sha[:7]}..{head_sha[:7]}")},
            "Head SHA": {"rich_text": rich_text(head_sha)},
            "Dashboard": {"url": dashboard_url or None},
            "Note": {"relation": [{"id": note_page_id}]},
        }
        payload = self._request(
            "POST",
            "pages",
            body={"parent": {"type": "data_source_id", "data_source_id": self.settings.notion_posts_ds_id}, "properties": properties},
            operation="create_post_row",
            write=True,
        )
        page_id = str(payload.get("id", ""))
        if not page_id:
            raise NotionError("Notion create post response did not include an id")
        return page_id

    def update_post_row(self, page_id: str, **props: Any) -> None:
        """Update status, Slack receipt, relation, or mirror fields on a post."""

        property_map: dict[str, Any] = {}
        for key, value in props.items():
            if key == "status":
                property_map["Status"] = {"select": {"name": value}}
            elif key in {"slack_ts", "head_sha", "commit_range"}:
                name = {"slack_ts": "Slack TS", "head_sha": "Head SHA", "commit_range": "Commit Range"}[key]
                property_map[name] = {"rich_text": rich_text(str(value))}
            elif key == "superseded_by":
                property_map["Superseded By"] = {"relation": [{"id": str(value)}] if value else []}
            elif key == "note_page_id":
                property_map["Note"] = {"relation": [{"id": str(value)}] if value else []}
            elif key == "dashboard_url":
                property_map["Dashboard"] = {"url": value or None}
            elif key == "text":
                property_map["Post Text"] = {"rich_text": rich_text(str(value))}
            elif key == "evidence_lines":
                property_map["Evidence"] = {"rich_text": [item for line in value for item in rich_text(line)]}
            else:
                raise ValueError(f"Unknown Notion post property: {key}")
        if property_map:
            self._request(
                "PATCH",
                f"pages/{page_id}",
                body={"properties": property_map},
                operation="update_post_row",
                write=True,
            )
