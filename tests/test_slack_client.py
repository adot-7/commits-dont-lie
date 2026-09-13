"""Offline tests for Slack Block Kit messages and receipt events."""

from __future__ import annotations

from pathlib import Path

from cdl.config import Settings
from cdl.slack_client import SlackClient
from cdl.store import Store


def settings(tmp_path: Path) -> Settings:
    """Return isolated Slack settings."""

    return Settings(
        "http://test", "admin", "github", "webhook", "repo", "notion", "notes", "posts",
        "notes-ds", "posts-ds", "slack-token", "signing", "C123", "anthropic", "model", str(tmp_path / "db.sqlite")
    )


class FakeSlack:
    """Record Slack API method calls and return successful responses."""

    def __init__(self):
        self.calls = []

    def chat_postMessage(self, **kwargs):
        self.calls.append(("chat_postMessage", kwargs))
        return {"ok": True, "ts": "123.456"}

    def chat_update(self, **kwargs):
        self.calls.append(("chat_update", kwargs))
        return {"ok": True}


def test_draft_blocks_and_updates_remove_actions(tmp_path):
    """Draft messages carry receipts and both action buttons."""

    fake = FakeSlack()
    client = SlackClient(settings(tmp_path), store=Store(tmp_path / "db.sqlite"), web_client=fake)
    ts = client.post_draft(7, "I changed the app.", ["✅ \"I changed the app.\" → cdl/app.py"])
    client.update_message(ts, "✅ Approved by @akash — copy & post")
    method, call = fake.calls[0]
    assert method == "chat_postMessage"
    assert call["channel"] == "C123"
    assert call["blocks"][-1]["type"] == "actions"
    assert [button["action_id"] for button in call["blocks"][-1]["elements"]] == ["approve", "reject"]
    assert fake.calls[1][1]["blocks"][0]["type"] == "section"


def test_blocked_message_has_no_actions_and_thread_reply_uses_thread_ts(tmp_path):
    """Blocked notices and correction replies use the documented shapes."""

    fake = FakeSlack()
    client = SlackClient(settings(tmp_path), store=Store(tmp_path / "db.sqlite"), web_client=fake)
    client.post_blocked(8, [("I changed missing_fn.", "missing_fn does not appear")])
    client.post_thread_reply("123.456", "⚠️ Stale: missing_fn was removed")
    blocked = fake.calls[0][1]
    assert all(block["type"] != "actions" for block in blocked["blocks"])
    thread = fake.calls[1][1]
    assert thread["thread_ts"] == "123.456"
