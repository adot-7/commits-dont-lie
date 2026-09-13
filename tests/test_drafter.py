"""Offline acceptance tests for the draft-to-gate orchestration."""

from __future__ import annotations

from pathlib import Path

import pytest

from cdl.config import Settings
from cdl.drafter import evidence_line, maybe_draft
from cdl.models import DiffContext, Entities, FileChange, Sentence, Verdict
from cdl.store import Store


def settings(tmp_path: Path) -> Settings:
    """Return isolated integration settings."""

    return Settings(
        "http://test", "admin", "github", "webhook", "repo", "notion", "notes", "posts",
        "notes-ds", "posts-ds", "slack-token", "signing", "C123", "anthropic", "model", str(tmp_path / "db.sqlite")
    )


def diff():
    """Build a range with one file and one named symbol."""

    return DiffContext(
        "repo", "a" * 40, "b" * 40,
        files=[FileChange("cdl/app.py", "modified", added=[(10, "def handle_push(): pass")])],
    )


class FakeNotion:
    """Minimal Notion facade for drafter tests."""

    def __init__(self):
        self.calls = []

    def ready_notes(self):
        self.calls.append("ready_notes")
        return [("note-1", "Build update")]

    def read_note_body(self, page_id):
        self.calls.append(("read", page_id))
        return "I worked on the webhook."

    def create_post_row(self, *args, **kwargs):
        self.calls.append(("create", args, kwargs))
        return "notion-post-1"

    def update_post_row(self, *args, **kwargs):
        self.calls.append(("update", args, kwargs))

    def set_note_status(self, *args):
        self.calls.append(("note_status", args))


class FakeSlack:
    """Minimal Slack facade for drafter tests."""

    def __init__(self):
        self.calls = []

    def post_draft(self, *args):
        self.calls.append(("draft", args))
        return "123.1"

    def post_blocked(self, *args):
        self.calls.append(("blocked", args))
        return "123.2"


def patch_llm(monkeypatch, *, missing=False, unverifiable_indexes=()):
    """Patch LLM calls with deterministic extracted entities."""

    sentences = [
        Sentence(0, "I changed cdl/app.py.", "commits"),
        Sentence(1, "I updated missing_fn." if missing else "I updated handle_push.", "both"),
        Sentence(2, "I refreshed cdl/app.py.", "notes"),
    ]
    monkeypatch.setattr("cdl.drafter.llm.draft", lambda *args, **kwargs: sentences)

    def extract(sentence, *args, **kwargs):
        if sentence.idx in unverifiable_indexes:
            return Entities()
        if missing and sentence.idx == 1:
            return Entities(symbols=["missing_fn"])
        if sentence.idx == 0:
            return Entities(files=["cdl/app.py"])
        if sentence.idx == 1:
            return Entities(symbols=["handle_push"])
        return Entities(files=["cdl/app.py"])

    monkeypatch.setattr("cdl.drafter.llm.extract_entities", extract)


def test_unverifiable_evidence_line_is_labelled_for_mirrors():
    sentence = Sentence(1, "I kept going through the weird failure.", "notes")
    verdict = Verdict(1, "UNVERIFIABLE", reason="Names no file, function, or integration that can be checked against the diff.")
    assert evidence_line(sentence, verdict) == '❔ "I kept going through the weird failure." → no checkable claim'


def test_supported_draft_mirrors_to_notion_and_slack(tmp_path, monkeypatch):
    """All supported sentences produce a Draft with buttons and a drafted note."""

    patch_llm(monkeypatch)
    store = Store(tmp_path / "db.sqlite")
    notion = FakeNotion()
    slack = FakeSlack()
    post_ids = maybe_draft("b" * 40, diff=diff(), settings=settings(tmp_path), store=store, notion=notion, slack=slack)
    assert len(post_ids) == 1
    post = store.get_post(post_ids[0])
    assert post["status"] == "Draft"
    assert post["notion_page_id"] == "notion-post-1"
    assert post["slack_ts"] == "123.1"
    assert all(sentence["status"] == "SUPPORTED" for sentence in post["sentences"])
    assert slack.calls[0][0] == "draft"
    assert any(call[0] == "note_status" for call in notion.calls)


def test_unsupported_draft_is_blocked_without_note_transition(tmp_path, monkeypatch):
    """A missing entity blocks the post and leaves the Ready note unchanged."""

    patch_llm(monkeypatch, missing=True)
    store = Store(tmp_path / "db.sqlite")
    notion = FakeNotion()
    slack = FakeSlack()
    post_ids = maybe_draft("b" * 40, diff=diff(), settings=settings(tmp_path), store=store, notion=notion, slack=slack)
    post = store.get_post(post_ids[0])
    assert post["status"] == "Blocked"
    assert post["sentences"][1]["status"] == "UNSUPPORTED"
    assert slack.calls[0][0] == "blocked"
    assert not any(call[0] == "note_status" for call in notion.calls)


@pytest.mark.parametrize(
    ("unverifiable_indexes", "expected_status"),
    [((1,), "Draft"), ((1, 2), "Blocked")],
)
def test_unverifiable_gate_allows_one_and_blocks_two(tmp_path, monkeypatch, unverifiable_indexes, expected_status):
    """One pure-voice sentence passes; two fail with the explicit gate reason."""

    patch_llm(monkeypatch, unverifiable_indexes=unverifiable_indexes)
    store = Store(tmp_path / "db.sqlite")
    notion = FakeNotion()
    slack = FakeSlack()
    post_ids = maybe_draft("b" * 40, diff=diff(), settings=settings(tmp_path), store=store, notion=notion, slack=slack)
    post = store.get_post(post_ids[0])
    assert post["status"] == expected_status
    assert post["sentences"][1]["status"] == "UNVERIFIABLE"
    if expected_status == "Draft":
        assert slack.calls[0][0] == "draft"
        assert "❔" in slack.calls[0][1][2][1]
        assert "no checkable claim" in slack.calls[0][1][2][1]
    else:
        assert slack.calls[0][0] == "blocked"
        blocked = slack.calls[0][1][1]
        assert all(reason == "more than one sentence makes no checkable claim" for _, reason in blocked)


def test_note_head_idempotency_prevents_duplicate_post(tmp_path, monkeypatch):
    """The same note/head pair is a no-op on the second draft attempt."""

    patch_llm(monkeypatch)
    store = Store(tmp_path / "db.sqlite")
    notion = FakeNotion()
    slack = FakeSlack()
    first = maybe_draft("b" * 40, diff=diff(), settings=settings(tmp_path), store=store, notion=notion, slack=slack)
    second = maybe_draft("b" * 40, diff=diff(), settings=settings(tmp_path), store=store, notion=notion, slack=slack)
    assert len(first) == 1
    assert second == []
    assert len(store.list_posts()) == 1
