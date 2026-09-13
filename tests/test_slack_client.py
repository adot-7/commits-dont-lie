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


def test_update_preserves_post_text_and_evidence_context(tmp_path):
    """Terminal updates replace actions without hiding copyable draft content."""

    fake = FakeSlack()
    client = SlackClient(settings(tmp_path), store=Store(tmp_path / "db.sqlite"), web_client=fake)
    post_text = "I changed cdl/app.py and kept the receipt."
    evidence = ['✅ "I changed cdl/app.py and kept the receipt." → cdl/app.py (cdl/app.py)']
    ts = client.post_draft(7, post_text, evidence)
    original_blocks = fake.calls[0][1]["blocks"]
    client.update_message(ts, "✅ Approved by @akash — copy & post", blocks=original_blocks)
    updated = fake.calls[1][1]["blocks"]
    assert updated[0] == original_blocks[0]
    assert updated[1] == original_blocks[1]
    assert updated[2]["type"] == "context"
    assert updated[2]["elements"][0]["text"] == "✅ Approved by @akash — copy & post"
    assert all(block["type"] != "actions" for block in updated)


def test_update_rebuilds_post_text_and_evidence_when_blocks_are_missing(tmp_path):
    """Approval payloads without blocks still get readable stored content."""

    fake = FakeSlack()
    client = SlackClient(settings(tmp_path), store=Store(tmp_path / "db.sqlite"), web_client=fake)
    post = {
        "text": "I changed cdl/app.py.",
        "sentences": [{
            "text": "I changed cdl/app.py.",
            "status": "SUPPORTED",
            "reason": "supported",
            "evidence": [{"path": "cdl/app.py", "line_no": 4, "entity": "cdl/app.py"}],
        }],
    }
    client.update_message("123.1", "❌ Rejected by @akash", post=post)
    updated = fake.calls[0][1]["blocks"]
    assert updated[0]["text"]["text"] == post["text"]
    assert "cdl/app.py:4 (cdl/app.py)" in updated[1]["elements"][0]["text"]
    assert updated[2]["elements"][0]["text"] == "❌ Rejected by @akash"


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
