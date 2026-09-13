"""Pure data contracts shared by the CDL modules.

The module owns dataclasses for GitHub diffs, extracted claims, receipts, and
verdicts.  It must remain standard-library-only and must not perform I/O,
read environment variables, or import application modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class CommitMeta:
    """Metadata for one commit in a compare response."""

    sha: str
    message: str
    author: str | None = None
    timestamp: str | None = None
    url: str | None = None


@dataclass
class FileChange:
    """One changed file and the parsed added/removed lines."""

    path: str
    status: Literal["added", "modified", "removed", "renamed"]
    previous_path: str | None = None
    patch: str | None = None
    added: list[tuple[int, str]] = field(default_factory=list)
    removed: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class DiffContext:
    """The deterministic input to drafting and grounding."""

    repo: str
    base_sha: str
    head_sha: str
    commits: list[CommitMeta] = field(default_factory=list)
    files: list[FileChange] = field(default_factory=list)
    truncated: bool = False


Source = Literal["notes", "commits", "both"]


@dataclass
class Sentence:
    """A drafted post sentence and its declared source."""

    idx: int
    text: str
    source: Source


@dataclass
class Entities:
    """Concrete names extracted from one sentence."""

    files: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    integrations: list[str] = field(default_factory=list)


@dataclass
class Claim:
    """A sentence paired with substring-filtered entities."""

    sentence: Sentence
    entities: Entities


Status = Literal["SUPPORTED", "UNSUPPORTED", "UNVERIFIABLE"]


@dataclass
class Evidence:
    """A reproducible receipt tying an entity to a changed line or path."""

    entity: str
    kind: Literal["file", "symbol", "integration"]
    path: str
    line_no: int | None = None
    line_text: str | None = None
    commit_sha: str | None = None


@dataclass
class Verdict:
    """The deterministic result for one claim."""

    sentence_idx: int
    status: Status
    evidence: list[Evidence] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class StaleVerdict:
    """Whether a previously stored receipt no longer holds."""

    stale: bool
    removed: list[Evidence] = field(default_factory=list)
    reason: str = ""


@dataclass
class Note:
    """A Notion note selected for drafting."""

    page_id: str
    title: str
    body: str = ""


@dataclass
class PushRecord:
    """A persisted GitHub push delivery."""

    id: int
    delivery_id: str
    before_sha: str
    after_sha: str
    ref: str
    received_at: str
    payload_json: str


def dataclass_dict(value: Any) -> Any:
    """Convert nested contract dataclasses to JSON-compatible primitives."""

    if hasattr(value, "__dataclass_fields__"):
        return {name: dataclass_dict(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, list):
        return [dataclass_dict(item) for item in value]
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return {key: dataclass_dict(item) for key, item in value.items()}
    return value
