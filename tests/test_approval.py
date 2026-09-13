"""Offline tests for Slack approval transitions and mid-check behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path

from cdl.approval import handle_interaction
from cdl.config import Settings
from cdl.models import Claim, DiffContext, Entities, Evidence, FileChange, Sentence, Verdict
from cdl.store import Store


def settings(tmp_path: Path) -> Settings:
    """Return isolated settings for approval tests."""

    return Settings(
        "http://test", "admin", "github", "webhook", "repo", "notion", "notes", "posts",
        "notes-ds", "posts-ds", "slack-token", "signing", "C123", "anthropic", "model", str(tmp_path / "db.sqlite")
    )


class FakeGitHub:
    """GitHub double exposing HEAD and compare responses."""

    def __init__(self, diff):
        self.diff = diff
        self.calls = []

    def head_sha(self, ref):
        self.calls.append(("head", ref))
        return "c" * 40

    def compare(self, base, head):
        self.calls.append(("compare", base, head))
        return self.diff


class FakeNotion:
    """Notion double recording status and note updates."""

    def __init__(self):
        self.post_statuses = []
        self.note_statuses = []

    def update_post_row(self, page_id, **props):
        self.post_statuses.append((page_id, props))

    def set_note_status(self, page_id, status):
        self.note_statuses.append((page_id, status))


class FakeSlack:
    """Slack double recording message updates."""

    def __init__(self):
        self.updates = []

    def update_message(self, *args, **kwargs):
        self.updates.append((args, kwargs))


def payload(action_id: str, post_id: int, action_ts: str) -> dict:
    """Build the fields supplied by a Slack button interaction."""

    return {
        "actions": [{"action_id": action_id, "value": str(post_id), "action_ts": action_ts}],
        "user": {"username": "akash"},
        "message": {"ts": "123.1"},
        "channel": {"id": "C123"},
    }


def create_draft(store: Store) -> int:
    """Persist one Draft post with a symbol receipt."""

    post_id = store.create_post("note-1", "a" * 40, "b" * 40, "Draft", "I updated handle_push.", notion_page_id="notion-1", slack_channel="C123")
    sentence = Sentence(0, "I updated handle_push.", "both")
    claim = Claim(sentence, Entities(symbols=["handle_push"]))
    verdict = Verdict(0, "SUPPORTED", evidence=[])
    # The original evidence is written explicitly to exercise the same read-back path as production.
    verdict.evidence.append(Evidence("handle_push", "symbol", "cdl/app.py", 10, "def handle_push():"))
    store.add_sentences(post_id, [claim], [verdict])
    store.update_post(post_id, slack_ts="123.1")
    return post_id


def test_approve_runs_midcheck_and_marks_sent(tmp_path):
    """An unchanged receipt becomes Sent and removes Slack actions."""

    store = Store(tmp_path / "db.sqlite")
    post_id = create_draft(store)
    github = FakeGitHub(DiffContext("repo", "b" * 40, "c" * 40, files=[FileChange("cdl/app.py", "modified", added=[(11, "# unrelated")])]))
    notion = FakeNotion()
    slack = FakeSlack()
    asyncio.run(handle_interaction(payload("approve", post_id, "a1"), store=store, settings=settings(tmp_path), github=github, notion=notion, slack=slack))
    post = store.get_post(post_id)
    assert post["status"] == "Sent"
    assert github.calls == [("head", "main"), ("compare", "b" * 40, "c" * 40)]
    assert "Approved by @akash" in slack.updates[0][0][1]
    assert notion.post_statuses[-1][1] == {"status": "Sent"}


def test_approve_after_receipt_removal_becomes_stale(tmp_path):
    """A deleted cited symbol is caught before the approval sends it."""

    store = Store(tmp_path / "db.sqlite")
    post_id = create_draft(store)
    github = FakeGitHub(DiffContext("repo", "b" * 40, "c" * 40, files=[FileChange("cdl/app.py", "modified", removed=[(10, "def handle_push():")])]))
    notion = FakeNotion()
    slack = FakeSlack()
    asyncio.run(handle_interaction(payload("approve", post_id, "a2"), store=store, settings=settings(tmp_path), github=github, notion=notion, slack=slack))
    assert store.get_post(post_id)["status"] == "Stale"
    assert "Not sent: evidence changed" in slack.updates[0][0][1]
    assert notion.post_statuses[-1][1] == {"status": "Stale"}


def test_reject_returns_note_to_ready(tmp_path):
    """Reject removes actions, mirrors Rejected, and requeues the note."""

    store = Store(tmp_path / "db.sqlite")
    post_id = create_draft(store)
    notion = FakeNotion()
    slack = FakeSlack()
    asyncio.run(handle_interaction(payload("reject", post_id, "a3"), store=store, settings=settings(tmp_path), notion=notion, slack=slack))
    assert store.get_post(post_id)["status"] == "Rejected"
    assert notion.post_statuses[-1][1] == {"status": "Rejected"}
    assert notion.note_statuses == [("note-1", "Ready")]


def test_duplicate_action_is_ignored(tmp_path):
    """The Slack action timestamp is an idempotency key."""

    store = Store(tmp_path / "db.sqlite")
    post_id = create_draft(store)
    notion = FakeNotion()
    slack = FakeSlack()
    action = payload("reject", post_id, "same")
    asyncio.run(handle_interaction(action, store=store, settings=settings(tmp_path), notion=notion, slack=slack))
    asyncio.run(handle_interaction(action, store=store, settings=settings(tmp_path), notion=notion, slack=slack))
    assert len(slack.updates) == 1
