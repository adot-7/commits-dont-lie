"""Slack Web API client for CDL drafts, decisions, and corrections.

This module owns Block Kit message construction, updates, threaded replies,
and Slack API error logging. It must not decide verdicts or perform post state
transitions; those belong to drafter, approval, and monitor orchestration.
"""

from __future__ import annotations

from typing import Any

try:
    from slack_sdk import WebClient
except ImportError:  # pragma: no cover - dependency is listed in requirements
    WebClient = None

from .config import Settings, get_settings
from .store import Store


class SlackError(RuntimeError):
    """Raised when a Slack API call fails."""


def _response_value(response: Any, key: str, default: Any = None) -> Any:
    """Read a value from Slack's response object or a test dictionary."""

    if isinstance(response, dict):
        return response.get(key, default)
    try:
        return response[key]
    except (KeyError, TypeError):
        return getattr(response, key, default)


class SlackClient:
    """Synchronous, injectable Slack Web API client."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        store: Store | None = None,
        web_client: Any | None = None,
    ):
        self.settings = settings or get_settings(strict=False)
        self.store = store or Store(self.settings.database_path)
        if web_client is not None:
            self.client = web_client
        elif WebClient is not None:
            self.client = WebClient(token=self.settings.slack_bot_token)
        else:
            self.client = None

    def _call(self, method: str, **kwargs: Any) -> Any:
        """Call Slack and convert API failures into a logged SlackError."""

        if self.client is None:
            error = SlackError("slack_sdk is not installed")
            self.store.append_event("slack.failed", {"method": method, "reason": str(error)})
            raise error
        try:
            response = getattr(self.client, method)(**kwargs)
            if _response_value(response, "ok", True) is False:
                raise SlackError(str(_response_value(response, "error", "unknown Slack error")))
            return response
        except Exception as exc:
            if not isinstance(exc, SlackError):
                exc = SlackError(str(exc))
            self.store.append_event("slack.failed", {"method": method, "reason": str(exc)})
            raise exc

    def _evidence_context(self, evidence_lines: list[str]) -> dict[str, Any]:
        """Build a compact context block for receipts in Slack."""

        text = "\n".join(evidence_lines) or "No evidence receipts"
        return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}

    def post_draft(self, post_id: int, text: str, evidence_lines: list[str]) -> str:
        """Post a draft with receipt context and Approve/Reject buttons."""

        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            self._evidence_context(evidence_lines),
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "action_id": "approve",
                        "value": str(post_id),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "style": "danger",
                        "action_id": "reject",
                        "value": str(post_id),
                    },
                ],
            },
        ]
        response = self._call("chat_postMessage", channel=self.settings.slack_channel_id, text=text, blocks=blocks)
        ts = str(_response_value(response, "ts", ""))
        if not ts:
            raise SlackError("Slack draft response did not include ts")
        self.store.append_event("slack.posted", {"post_id": post_id, "kind": "draft", "ts": ts}, post_id=post_id)
        return ts

    def post_blocked(self, post_id: int, blocked: list[tuple[str, str]]) -> str:
        """Post a blocked notice without interactive actions."""

        fallback = f"⛔ Blocked — {len(blocked)} sentence(s) failed the gate"
        blocks: list[dict[str, Any]] = [{"type": "section", "text": {"type": "mrkdwn", "text": fallback}}]
        for sentence, reason in blocked:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"⛔ {sentence}\n{reason}"}})
        response = self._call("chat_postMessage", channel=self.settings.slack_channel_id, text=fallback, blocks=blocks)
        ts = str(_response_value(response, "ts", ""))
        if not ts:
            raise SlackError("Slack blocked response did not include ts")
        self.store.append_event("slack.posted", {"post_id": post_id, "kind": "blocked", "ts": ts}, post_id=post_id)
        return ts

    def update_message(self, ts: str, text: str, blocks: list[dict[str, Any]] | None = None, *, channel: str | None = None) -> None:
        """Update a draft message, normally removing its action buttons."""

        self._call(
            "chat_update",
            channel=channel or self.settings.slack_channel_id,
            ts=ts,
            text=text,
            blocks=blocks or [{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
        )

    def post_thread_reply(self, thread_ts: str, text: str, *, channel: str | None = None) -> str:
        """Reply to the original draft thread with a stale correction."""

        response = self._call(
            "chat_postMessage",
            channel=channel or self.settings.slack_channel_id,
            thread_ts=thread_ts,
            text=text,
        )
        ts = str(_response_value(response, "ts", ""))
        self.store.append_event("correction.threaded", {"thread_ts": thread_ts, "ts": ts})
        return ts
