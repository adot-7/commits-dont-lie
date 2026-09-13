"""Tests for GitHub compare parsing and DiffContext construction."""

from __future__ import annotations

from pathlib import Path

from cdl.config import Settings
from cdl.github_client import GitHubClient, parse_patch
from cdl.store import Store


class FakeResponse:
    """Minimal httpx response double."""

    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.text = "fake"

    def json(self):
        return self.payload


class FakeHTTP:
    """Minimal synchronous client double."""

    def __init__(self, response):
        self.response = response
        self.urls = []

    def get(self, url, headers):
        self.urls.append((url, headers))
        return self.response


def settings(tmp_path: Path) -> Settings:
    """Return settings pointed at an isolated SQLite database."""

    return Settings(
        "http://test",
        "admin",
        "github-token",
        "webhook",
        "adot-7/commits-dont-lie",
        "notion",
        "notes-db",
        "posts-db",
        "notes-ds",
        "posts-ds",
        "slack",
        "signing",
        "C1",
        "anthropic",
        "model",
        str(tmp_path / "cdl.sqlite"),
    )


def test_parse_patch_tracks_two_hunks_exactly():
    """Both hunk starts and all line-number increments are preserved."""

    patch = "@@ -2,3 +2,4 @@ def first():\n keep\n-old line\n+new line\n+extra\n tail\n@@ -10 +11,2 @@ def second():\n-old second\n+new second\n+another\n"
    added, removed = parse_patch(patch)
    assert added == [(3, "new line"), (4, "extra"), (11, "new second"), (12, "another")]
    assert removed == [(3, "old line"), (10, "old second")]


def test_compare_builds_context_and_marks_missing_patch_truncated(tmp_path):
    """The compare response maps files and treats omitted patches as truncated."""

    patch = "@@ -1 +1,2 @@\n-old\n+new\n+added\n"
    payload = {
        "sha": "b" * 40,
        "commits": [
            {
                "sha": "b" * 40,
                "html_url": "https://github.com/example/commit/b",
                "commit": {"message": "change", "author": {"name": "Akash", "date": "2026-09-13"}},
            }
        ],
        "files": [
            {"filename": "cdl/a.py", "status": "modified", "patch": patch},
            {"filename": "binary.dat", "status": "modified", "patch": None},
        ],
    }
    http = FakeHTTP(FakeResponse(payload))
    client = GitHubClient(settings(tmp_path), store=Store(tmp_path / "cdl.sqlite"), http_client=http)
    diff = client.compare("a" * 40, "b" * 40)
    assert diff.base_sha == "a" * 40
    assert diff.head_sha == "b" * 40
    assert diff.files[0].added == [(1, "new"), (2, "added")]
    assert diff.files[0].removed == [(1, "old")]
    assert diff.files[1].added == []
    assert diff.truncated is True
    assert http.urls[0][0].endswith(f"compare/{'a' * 40}...{'b' * 40}")
