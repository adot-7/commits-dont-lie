"""GitHub REST client and unified-diff parser for CDL.

This module owns compare/commit HTTP calls, retry behavior, and parsing of
``+``/``-`` lines into ``DiffContext``. It must not make verdict decisions or
parse claim semantics beyond the line-oriented patch contract.
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx

from .config import Settings, get_settings
from .models import CommitMeta, DiffContext, FileChange
from .store import Store


HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @")
ZERO_SHA = "0" * 40


class GitHubError(RuntimeError):
    """Raised when GitHub cannot provide a compare or commit response."""


def parse_patch(patch: str | None) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Parse hunk line numbers and added/removed text from a patch."""

    if patch is None:
        return [], []
    added: list[tuple[int, str]] = []
    removed: list[tuple[int, str]] = []
    old_line: int | None = None
    new_line: int | None = None
    for line in patch.splitlines():
        hunk = HUNK_RE.match(line)
        if hunk:
            old_line = int(hunk.group(1))
            new_line = int(hunk.group(3))
            continue
        if old_line is None or new_line is None or line.startswith("\\"):
            continue
        if line.startswith("+"):
            added.append((new_line, line[1:]))
            new_line += 1
        elif line.startswith("-"):
            removed.append((old_line, line[1:]))
            old_line += 1
        elif line.startswith(" "):
            old_line += 1
            new_line += 1
    return added, removed


def _status(raw_status: str, previous_path: str | None) -> str:
    """Normalize GitHub file statuses to the four CDL contract values."""

    if raw_status == "renamed" or previous_path:
        return "renamed"
    if raw_status in {"added", "modified", "removed"}:
        return raw_status
    return "modified"


class GitHubClient:
    """Synchronous, injectable GitHub client used by the pipeline."""

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
        self.base_url = "https://api.github.com"

    @property
    def headers(self) -> dict[str, str]:
        """Return the exact GitHub headers required by the integration spec."""

        return {
            "Authorization": f"Bearer {self.settings.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def close(self) -> None:
        """Close the internally-owned HTTP client."""

        if self._owns_client:
            self.client.close()

    def _url(self, path: str) -> str:
        """Build a URL using the configured owner/repository pair."""

        return f"{self.base_url}/repos/{self.settings.github_repo}/{path.lstrip('/')}"

    def _get_json(self, path: str, *, operation: str) -> dict[str, Any]:
        """GET JSON with two retries for transient failures."""

        del operation
        last_error = "unknown GitHub error"
        for attempt in range(3):
            try:
                response = self.client.get(self._url(path), headers=self.headers)
                status_code = int(getattr(response, "status_code", 0))
                if 200 <= status_code < 300:
                    return response.json()
                raw = getattr(response, "text", "")
                last_error = f"HTTP {status_code}: {raw}"
                if status_code < 500:
                    break
            except (httpx.HTTPError, OSError) as exc:
                last_error = str(exc)
            if attempt < 2:
                time.sleep(0.25 * (2**attempt))
        raise GitHubError(last_error)

    def _commit_payload(self, sha: str) -> dict[str, Any]:
        """Fetch one commit payload for zero-base fallback and HEAD lookup."""

        try:
            payload = self._get_json(f"commits/{sha}", operation="commit")
        except GitHubError as exc:
            self.store.append_event("compare.failed", {"operation": "commit", "sha": sha, "reason": str(exc)})
            raise
        self.store.append_event("github.request", {"operation": "commit", "sha": sha})
        return payload

    def head_sha(self, ref: str = "main") -> str:
        """Return the current SHA for a branch or ref."""

        payload = self._commit_payload(ref)
        return str(payload.get("sha", ""))

    def compare(self, base: str, head: str) -> DiffContext:
        """Fetch and parse a GitHub compare range into a ``DiffContext``."""

        requested_base = base
        if base == ZERO_SHA or (base and set(base) == {"0"}):
            payload = self._commit_payload(head)
            parents = payload.get("parents") or []
            if not parents:
                raise GitHubError(f"commit {head} has no parent for zero-base compare")
            base = str(parents[0].get("sha", ""))

        try:
            payload = self._get_json(f"compare/{base}...{head}", operation="compare")
        except GitHubError as exc:
            self.store.append_event(
                "compare.failed",
                {"base": requested_base, "resolved_base": base, "head": head, "reason": str(exc)},
            )
            raise

        commits = [
            CommitMeta(
                sha=str(item.get("sha", "")),
                message=str((item.get("commit") or {}).get("message", "")),
                author=((item.get("commit") or {}).get("author") or {}).get("name"),
                timestamp=((item.get("commit") or {}).get("author") or {}).get("date"),
                url=item.get("html_url"),
            )
            for item in payload.get("commits", [])
        ]
        files: list[FileChange] = []
        omitted_patch = False
        raw_files = payload.get("files", [])
        for item in raw_files:
            patch = item.get("patch")
            previous_path = item.get("previous_filename")
            added, removed = parse_patch(patch)
            omitted_patch = omitted_patch or patch is None
            files.append(
                FileChange(
                    path=str(item.get("filename", "")),
                    status=_status(str(item.get("status", "modified")), previous_path),
                    previous_path=previous_path,
                    patch=patch,
                    added=added,
                    removed=removed,
                )
            )

        context = DiffContext(
            repo=self.settings.github_repo,
            base_sha=base,
            head_sha=str(payload.get("sha", head)),
            commits=commits,
            files=files,
            truncated=bool(payload.get("truncated", False)) or len(raw_files) >= 300 or omitted_patch,
        )
        self.store.append_event(
            "compare.fetched",
            {
                "base": context.base_sha,
                "head": context.head_sha,
                "files": len(context.files),
                "commits": len(context.commits),
                "truncated": context.truncated,
            },
        )
        return context
