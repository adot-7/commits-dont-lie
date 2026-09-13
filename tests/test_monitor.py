"""Offline tests for Sent-post staleness monitoring and corrections."""

from __future__ import annotations

from pathlib import Path

from cdl.config import Settings
from cdl.models import Claim, DiffContext, Entities, Evidence, FileChange, Sentence, Verdict
from cdl.monitor import run
from cdl.store import Store


def settings(tmp_path: Path) -> Settings:
    """Return isolated monitor settings."""

    return Settings(
        "http://test", "admin", "github", "webhook", "repo", "notion", "notes", "posts",
        "notes-ds", "posts-ds", "slack-token", "signing", "C123", "anthropic", "model", str(tmp_path / "db.sqlite")
    )


class FakeNotion:
    """Notion mirror double."""

    def __init__(self):
        self.calls = []

    def update_post_row(self, *args, **kwargs):
        self.calls.append(("update", args, kwargs))

    def create_post_row(self, *args, **kwargs):
        self.calls.append(("create", args, kwargs))
        return "notion-correction"


class FakeSlack:
    """Slack thread double."""

    def __init__(self):
        self.calls = []

    def post_thread_reply(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "123.2"


def sent_post(store: Store) -> int:
    """Persist one Sent post citing a function."""

    post_id = store.create_post("note-1", "a" * 40, "b" * 40, "Sent", "I updated handle_push.", notion_page_id="notion-1", slack_channel="C123")
    sentence = Sentence(0, "I updated handle_push.", "both")
    claim = Claim(sentence, Entities(symbols=["handle_push"]))
    verdict = Verdict(0, "SUPPORTED", evidence=[Evidence("handle_push", "symbol", "cdl/app.py", 10, "def handle_push():")])
    store.add_sentences(post_id, [claim], [verdict])
    store.update_post(post_id, slack_ts="123.1")
    return post_id


def test_removed_receipt_creates_stale_correction_and_thread(tmp_path):
    """A removed symbol flips the original and creates all correction links."""

    store = Store(tmp_path / "db.sqlite")
    post_id = sent_post(store)
    notion = FakeNotion()
    slack = FakeSlack()
    diff = DiffContext("repo", "b" * 40, "c" * 40, files=[FileChange("cdl/app.py", "modified", removed=[(10, "def handle_push():")])])
    corrections = run(diff, settings=settings(tmp_path), store=store, notion=notion, slack=slack)
    original = store.get_post(post_id)
    correction = store.get_post(corrections[0])
    assert original["status"] == "Stale"
    assert original["superseded_by"] == corrections[0]
    assert correction["status"] == "Correction"
    assert correction["text"].startswith("Correction to post #")
    assert slack.calls[0][1]["channel"] == "C123"
    assert any(call[0] == "update" and call[2]["status"] == "Stale" for call in notion.calls)


def test_unchanged_receipt_is_only_checked(tmp_path):
    """A later unrelated change does not create a correction."""

    store = Store(tmp_path / "db.sqlite")
    post_id = sent_post(store)
    diff = DiffContext("repo", "b" * 40, "c" * 40, files=[FileChange("cdl/other.py", "modified", added=[(1, "x")])])
    assert run(diff, settings=settings(tmp_path), store=store, notion=FakeNotion(), slack=FakeSlack()) == []
    assert store.get_post(post_id)["status"] == "Sent"
