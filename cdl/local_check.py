"""Zero-setup check and draft for a local repository.

This module owns the ``cdl check`` and ``cdl draft`` flows: split a post into
sentences, extract entities with the model, apply the substring guard, and run
the same ``claim_vs_diff`` and publish gate the server uses. It needs only a
model key; it must not decide support outside ``grounding.check``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import llm
from .drafter import _gate_failures, evidence_line
from .grounding.check import claim_vs_diff
from .models import Claim, DiffContext, Sentence, Verdict, dataclass_dict
from .store import Store

_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


@dataclass
class CheckResult:
    """Sentences, their verdicts, and the gate outcome for one post."""

    sentences: list[Sentence]
    verdicts: list[Verdict]
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True when the publish gate would let this post through."""

        return not self.failures

    def honest_text(self) -> str:
        """Return the post with every gate-failing sentence removed."""

        failing = {text for text, _ in self.failures}
        return " ".join(sentence.text for sentence in self.sentences if sentence.text not in failing)


def split_sentences(text: str) -> list[Sentence]:
    """Split a post deterministically on sentence ends and line breaks."""

    parts = (_BULLET.sub("", part).strip() for part in _SPLIT.split(text))
    return [Sentence(idx, part, "both") for idx, part in enumerate(part for part in parts if part)]


def check_sentences(
    sentences: list[Sentence],
    diff: DiffContext,
    *,
    settings: Any,
    store: Store,
    llm_client: Any | None = None,
) -> CheckResult:
    """Extract, guard, and match every sentence, then apply the publish gate."""

    paths = [item.path for item in diff.files]
    verdicts: list[Verdict] = []
    for sentence in sentences:
        extracted = llm.extract_entities(sentence, paths, llm_client=llm_client, store=store, settings=settings)
        filtered = llm.filter_entities(sentence, extracted, event_sink=lambda kind, data: store.append_event(kind, data))
        verdict = claim_vs_diff(Claim(sentence, filtered), diff)
        store.append_event("verdict", {"sentence_idx": sentence.idx, **dataclass_dict(verdict)})
        verdicts.append(verdict)
    return CheckResult(sentences, verdicts, _gate_failures(sentences, verdicts))


def draft_and_check(
    note: str,
    diff: DiffContext,
    *,
    settings: Any,
    store: Store,
    llm_client: Any | None = None,
) -> CheckResult:
    """Draft a post from the range and the author's note, then check every sentence."""

    sentences = llm.draft("Build update", note or "(no note; write from the commits)", diff, llm_client=llm_client, store=store, settings=settings)
    return check_sentences(sentences, diff, settings=settings, store=store, llm_client=llm_client)


def render(result: CheckResult, diff: DiffContext) -> str:
    """Render a terminal report with one receipt line per sentence."""

    lines = [
        f"{diff.repo} {diff.base_sha[:7]}..{diff.head_sha[:7]} · {len(diff.commits)} commits · {len(diff.files)} files",
        "",
    ]
    lines.extend(evidence_line(sentence, verdict) for sentence, verdict in zip(result.sentences, result.verdicts))
    lines.append("")
    if result.passed:
        lines.append("PASS — every checkable claim has a receipt in the diff.")
    else:
        lines.append(f"BLOCKED — {len(result.failures)} sentence(s) would not ship:")
        lines.extend(f"  • {text} ({reason})" for text, reason in result.failures)
        honest = result.honest_text()
        if honest:
            lines.extend(["", "What the diff does support:", f"  {honest}"])
    return "\n".join(lines)


def as_json(result: CheckResult, diff: DiffContext) -> dict[str, Any]:
    """Return a machine-readable report for CI."""

    return {
        "repo": diff.repo,
        "base": diff.base_sha,
        "head": diff.head_sha,
        "passed": result.passed,
        "sentences": [
            {"text": sentence.text, **dataclass_dict(verdict)}
            for sentence, verdict in zip(result.sentences, result.verdicts)
        ],
        "failures": [{"text": text, "reason": reason} for text, reason in result.failures],
    }
