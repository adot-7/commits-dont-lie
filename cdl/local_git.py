"""Local git diffs as CDL ``DiffContext`` values.

This module owns turning a commit range in a local repository into the same
``DiffContext`` the GitHub compare path produces, so the one matcher can run
without any GitHub token. It shells out to ``git`` read-only and must never
modify the repository or decide a verdict.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .github_client import parse_patch
from .models import CommitMeta, DiffContext, FileChange

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_STATUS = {"A": "added", "M": "modified", "D": "removed", "R": "renamed", "C": "added", "T": "modified"}


class LocalGitError(RuntimeError):
    """Raised when git cannot resolve a range or read a diff."""


class LocalGit:
    """Read commit ranges from a local repository with the ``compare`` shape."""

    def __init__(self, repo_dir: str | Path = "."):
        self.repo_dir = Path(repo_dir)

    def _git(self, *args: str) -> str:
        """Run one read-only git command and return stdout."""

        result = subprocess.run(
            ["git", "--literal-pathspecs", "-C", str(self.repo_dir), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise LocalGitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    def git_dir(self) -> Path:
        """Return the absolute .git directory, which also proves this is a repo."""

        return Path(self._git("rev-parse", "--absolute-git-dir").strip())

    def repo_name(self) -> str:
        """Name the repository by its origin remote, falling back to the folder."""

        try:
            url = self._git("remote", "get-url", "origin").strip()
        except LocalGitError:
            return Path(self._git("rev-parse", "--show-toplevel").strip()).name
        if "://" in url:
            rest = re.sub(r"^[^/@]+@", "", url.split("://", 1)[1])  # drop user:token@
        else:
            host, _, path = url.partition(":")  # scp form: git@host:owner/repo
            rest = f"{host.rsplit('@', 1)[-1]}/{path}"
        rest = rest.rstrip("/").removesuffix(".git")
        return rest.removeprefix("github.com/")

    def resolve(self, ref: str) -> str:
        """Resolve a ref to a full commit SHA."""

        return self._git("rev-parse", "--verify", f"{ref}^{{commit}}").strip()

    def last_n(self, count: int, head: str = "HEAD") -> tuple[str, str]:
        """Return a (base, head) range covering the last ``count`` first-parent commits."""

        if count < 1:
            raise LocalGitError("the number of commits must be at least 1")
        head_sha = self.resolve(head)
        shas = self._git("rev-list", "--first-parent", f"--max-count={count + 1}", head_sha).split()
        base = shas[count] if len(shas) > count else EMPTY_TREE
        return base, head_sha

    def parse_range(self, spec: str) -> tuple[str, str]:
        """Resolve ``A..B`` or ``A...B`` (or a single ref meaning its parent..ref)."""

        if ".." in spec:
            base, head = spec.split("...", 1) if "..." in spec else spec.split("..", 1)
            return self.resolve(base or "HEAD"), self.resolve(head or "HEAD")
        return self.last_n(1, spec)

    def _merge_base(self, base: str, head: str) -> str:
        """Diff from where the branches split, like GitHub's ``base...head`` compare."""

        try:
            return self._git("merge-base", base, head).strip()
        except LocalGitError:
            return base

    def _commits(self, base: str, head: str) -> list[CommitMeta]:
        """Return commits in the range, oldest first, like GitHub compare."""

        revs = head if base == EMPTY_TREE else f"{base}..{head}"
        raw = self._git("log", "--reverse", "--format=%H%x1f%an%x1f%aI%x1f%B%x1e", revs)
        commits: list[CommitMeta] = []
        for record in raw.split("\x1e"):
            parts = record.strip("\n").split("\x1f")
            if len(parts) == 4:
                commits.append(CommitMeta(sha=parts[0], author=parts[1], timestamp=parts[2], message=parts[3].strip()))
        return commits

    def _patch(self, base: str, head: str, paths: list[str]) -> str | None:
        """Return the hunks for one file without headers, or None for binaries."""

        raw = self._git("diff", "--no-color", "--no-ext-diff", "-M", base, head, "--", *paths)
        lines = raw.splitlines()
        start = next((idx for idx, line in enumerate(lines) if line.startswith("@@")), None)
        if start is None:
            return None
        return "\n".join(lines[start:])

    def compare(self, base: str, head: str) -> DiffContext:
        """Build a ``DiffContext`` for ``base..head`` from the local repository."""

        head_sha = self.resolve(head)
        base_sha = base if base == EMPTY_TREE else self._merge_base(self.resolve(base), head_sha)
        raw = self._git("diff", "--name-status", "-z", "-M", base_sha, head_sha)
        tokens = raw.split("\0")
        files: list[FileChange] = []
        idx = 0
        while idx < len(tokens) and tokens[idx]:
            code = tokens[idx]
            kind = code[0]
            if kind in {"R", "C"}:
                previous_path, path = tokens[idx + 1], tokens[idx + 2]
                idx += 3
            else:
                previous_path, path = None, tokens[idx + 1]
                idx += 2
            patch = self._patch(base_sha, head_sha, [previous_path, path] if previous_path else [path])
            added, removed = parse_patch(patch)
            files.append(
                FileChange(
                    path=path,
                    status=_STATUS.get(kind, "modified"),  # type: ignore[arg-type]
                    previous_path=previous_path if kind == "R" else None,
                    patch=patch,
                    added=added,
                    removed=removed,
                )
            )
        return DiffContext(
            repo=self.repo_name(),
            base_sha=base_sha,
            head_sha=head_sha,
            commits=self._commits(base_sha, head_sha),
            files=files,
        )
