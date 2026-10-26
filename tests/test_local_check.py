"""Offline tests for local git diffs and the zero-setup check flow."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

from cdl.bharatcode_client import ToolResult
from cdl.config import Settings
from cdl.local_check import as_json, check_sentences, render, split_sentences
from cdl.local_git import EMPTY_TREE, LocalGit
from cdl.store import Store


def git(repo: Path, *args: str) -> str:
    """Run git in a temp repo with a fixed identity."""

    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True, capture_output=True, text=True,
    ).stdout


def make_repo(tmp_path: Path) -> Path:
    """Create a repo with an add, a modify, and a rename."""

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "app.py").write_text("def handle_push():\n    return 1\n")
    (repo / "notes.txt").write_text("one\ntwo\nthree\nfour\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "first")
    (repo / "app.py").write_text("def handle_push():\n    return 2\n\n\ndef verify_signature():\n    return True\n")
    git(repo, "mv", "notes.txt", "docs.txt")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "feat: verify webhook signatures")
    return repo


def test_compare_last_commit_has_lines_renames_and_commits(tmp_path):
    local = LocalGit(make_repo(tmp_path))
    diff = local.compare(*local.last_n(1))
    files = {item.path: item for item in diff.files}

    assert [commit.message for commit in diff.commits] == ["feat: verify webhook signatures"]
    assert files["docs.txt"].status == "renamed" and files["docs.txt"].previous_path == "notes.txt"
    assert (5, "def verify_signature():") in files["app.py"].added
    assert (2, "    return 1") in files["app.py"].removed


def test_last_n_beyond_history_uses_empty_tree(tmp_path):
    local = LocalGit(make_repo(tmp_path))
    base, _ = local.last_n(10)
    diff = local.compare(base, "HEAD")
    assert base == EMPTY_TREE
    assert len(diff.commits) == 2
    assert {item.status for item in diff.files} == {"added"}


def test_split_sentences_handles_bullets_and_dotted_paths():
    sentences = split_sentences("- Added verify_signature in app.py. It works!\n* Also docs.txt")
    assert [item.text for item in sentences] == ["Added verify_signature in app.py.", "It works!", "Also docs.txt"]


class FakeExtractor:
    """Return queued entities as forced tool calls."""

    model = "fake-model"

    def __init__(self, payloads):
        self.payloads = list(payloads)

    def call_tool(self, **_kwargs):
        return ToolResult(self.payloads.pop(0))


def test_check_blocks_the_false_claim_and_keeps_receipts(tmp_path):
    repo = make_repo(tmp_path)
    local = LocalGit(repo)
    diff = local.compare(*local.last_n(1))
    settings = replace(
        Settings("", "", "", "", "", "", "", "", "", "", "", "", "", "", "", str(tmp_path / "db.sqlite")),
        bharatcode_api_key="bc",
    )
    fake = FakeExtractor([
        {"files": ["app.py"], "symbols": ["verify_signature"], "integrations": []},
        {"files": [], "symbols": ["rate_limit"], "integrations": []},
    ])
    sentences = split_sentences("app.py now has verify_signature. I also added rate_limit.")
    result = check_sentences(sentences, diff, settings=settings, store=Store(tmp_path / "db.sqlite"), llm_client=fake)

    assert [verdict.status for verdict in result.verdicts] == ["SUPPORTED", "UNSUPPORTED"]
    assert not result.passed
    assert result.honest_text() == "app.py now has verify_signature."
    report = render(result, diff)
    assert "app.py:5 (verify_signature)" in report and "BLOCKED" in report
    assert as_json(result, diff)["failures"][0]["text"] == "I also added rate_limit."
