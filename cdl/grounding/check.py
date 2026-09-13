"""Deterministic claim grounding and post-publish staleness checks.

This module owns the only lexical matcher used by both the publish gate and
the monitor. It must remain pure and offline, importing only stdlib modules
and ``cdl.models``; it never calls an API or asks an LLM for a verdict.
"""

from __future__ import annotations

import re

from ..models import Claim, DiffContext, Entities, Evidence, FileChange, StaleVerdict, Verdict


INTEGRATION_ALIASES: dict[str, list[str]] = {
    "slack": ["slack", "slack_sdk", "xo" + "xb"],
    "notion": ["notion", "nt" + "n_", "data_source"],
    "github": ["github", "x-hub-signature", "x-github"],
    "webhook": ["webhook", "x-hub-signature", "x-github-delivery"],
    "anthropic": ["anthropic", "claude"],
    "sqlite": ["sqlite", "sqlite3"],
    "caddy": ["caddy", "caddyfile"],
    "fastapi": ["fastapi", "uvicorn"],
}


NO_ENTITIES_REASON = "Names no file, function, or integration that can be checked against the diff."


def _basename(path: str) -> str:
    """Return a repository path's basename without importing pathlib."""

    return path.rsplit("/", 1)[-1]


def _file_evidence(entity: str, files: list[FileChange]) -> list[Evidence]:
    """Match a full path or a unique basename against non-removed files."""

    active = [file for file in files if file.status != "removed"]
    exact = [file for file in active if file.path == entity]
    if exact:
        return [Evidence(entity=entity, kind="file", path=exact[0].path)]
    basename_matches = [file for file in active if _basename(file.path) == entity]
    if len(basename_matches) == 1:
        return [Evidence(entity=entity, kind="file", path=basename_matches[0].path)]
    return []


def match_file(entity: str, diff: DiffContext) -> list[Evidence]:
    """Return file receipts according to the full-path/basename rule."""

    return _file_evidence(entity, diff.files)


def _symbol_pattern(entity: str) -> re.Pattern[str]:
    """Compile the case-sensitive word-boundary pattern for a symbol."""

    return re.compile(r"\b" + re.escape(entity) + r"\b")


def match_symbol(entity: str, diff: DiffContext) -> list[Evidence]:
    """Find the first added occurrence per changed file for a symbol."""

    pattern = _symbol_pattern(entity)
    matches: list[Evidence] = []
    for file in diff.files:
        for line_no, line_text in file.added:
            if pattern.search(line_text):
                matches.append(
                    Evidence(
                        entity=entity,
                        kind="symbol",
                        path=file.path,
                        line_no=line_no,
                        line_text=line_text,
                    )
                )
                break
    return matches


def _integration_tokens(entity: str) -> list[str]:
    """Return the raw entity and aliases for matching integration keys.

    Compound names such as ``GitHub webhooks`` can refer to more than one
    configured integration, so each key contained in the entity contributes
    its aliases in deterministic table order.
    """

    lowered = entity.lower()
    tokens = [lowered]
    for key, aliases in INTEGRATION_ALIASES.items():
        if key in lowered:
            tokens.extend(aliases)
    return list(dict.fromkeys(tokens))


def match_integration(entity: str, diff: DiffContext) -> list[Evidence]:
    """Find the first added-line or changed-path receipt for an integration."""

    tokens = _integration_tokens(entity)
    for file in diff.files:
        for line_no, line_text in file.added:
            if any(token in line_text.lower() for token in tokens):
                return [
                    Evidence(
                        entity=entity,
                        kind="integration",
                        path=file.path,
                        line_no=line_no,
                        line_text=line_text,
                    )
                ]
    for file in diff.files:
        if any(token in file.path.lower() for token in tokens):
            return [Evidence(entity=entity, kind="integration", path=file.path)]
    return []


def _entities(claim: Claim) -> list[tuple[str, str]]:
    """Flatten entity categories while preserving their deterministic order."""

    return [
        *[(entity, "file") for entity in claim.entities.files],
        *[(entity, "symbol") for entity in claim.entities.symbols],
        *[(entity, "integration") for entity in claim.entities.integrations],
    ]


def claim_vs_diff(claim: Claim, diff: DiffContext) -> Verdict:
    """Return a reproducible verdict by matching every entity against the diff."""

    entity_list = _entities(claim)
    if not entity_list:
        return Verdict(
            sentence_idx=claim.sentence.idx,
            status="UNVERIFIABLE",
            reason=NO_ENTITIES_REASON,
        )

    evidence: list[Evidence] = []
    missing: list[str] = []
    for entity, kind in entity_list:
        if kind == "file":
            found = match_file(entity, diff)
        elif kind == "symbol":
            found = match_symbol(entity, diff)
        else:
            found = match_integration(entity, diff)
        if found:
            evidence.extend(found)
        else:
            missing.append(entity)

    if missing:
        reason = (
            f"'{missing[0]}' does not appear in the {len(diff.files)} files changed in "
            f"{diff.base_sha[:7]}..{diff.head_sha[:7]}."
        )
        if diff.truncated and any(kind == "symbol" and entity in missing for entity, kind in entity_list):
            reason += " (some patches unavailable)"
        return Verdict(
            sentence_idx=claim.sentence.idx,
            status="UNSUPPORTED",
            evidence=evidence,
            missing=missing,
            reason=reason,
        )

    return Verdict(
        sentence_idx=claim.sentence.idx,
        status="SUPPORTED",
        evidence=evidence,
        reason="Every named file, function, and integration appears in the diff.",
    )


def _removed_paths(diff: DiffContext) -> set[str]:
    """Return both explicit removed paths and old sides of renames."""

    paths: set[str] = set()
    for file in diff.files:
        if file.status == "removed":
            paths.add(file.path)
        if file.status == "renamed" and file.previous_path:
            paths.add(file.previous_path)
    return paths


def _symbol_stale(receipt: Evidence, diff: DiffContext) -> bool:
    """Check whether a receipt's symbol was removed without a replacement."""

    pattern = _symbol_pattern(receipt.entity)
    removed_here = any(
        pattern.search(line_text)
        for file in diff.files
        if file.path == receipt.path
        for _line_no, line_text in file.removed
    )
    added_anywhere = any(
        pattern.search(line_text)
        for file in diff.files
        for _line_no, line_text in file.added
    )
    return removed_here and not added_anywhere


def _integration_stale(receipt: Evidence, all_evidence: list[Evidence], diff: DiffContext) -> bool:
    """Conservatively stale-check all receipts for one integration entity."""

    receipts = [item for item in all_evidence if item.kind == "integration" and item.entity == receipt.entity]
    removed_paths = _removed_paths(diff)
    if not receipts or not all(item.path in removed_paths for item in receipts):
        return False
    tokens = _integration_tokens(receipt.entity)
    replacement = any(
        any(token in line_text.lower() for token in tokens)
        for file in diff.files
        for _line_no, line_text in file.added
    ) or any(
        file.status != "removed" and any(token in file.path.lower() for token in tokens)
        for file in diff.files
    )
    return not replacement


def check_staleness(evidence: list[Evidence], diff: DiffContext) -> StaleVerdict:
    """Re-check stored receipts against a later diff using the same match rules."""

    removed: list[Evidence] = []
    removed_paths = _removed_paths(diff)
    for receipt in evidence:
        stale = False
        if receipt.kind == "file":
            stale = receipt.path in removed_paths
        elif receipt.kind == "symbol":
            stale = _symbol_stale(receipt, diff)
        elif receipt.kind == "integration":
            stale = _integration_stale(receipt, evidence, diff)
        if stale:
            removed.append(receipt)

    if not removed:
        return StaleVerdict(stale=False, reason="All stored evidence still holds in the diff.")
    first = removed[0]
    return StaleVerdict(
        stale=True,
        removed=removed,
        reason=f"{first.entity} was removed in {diff.head_sha[:7]} ({first.path})",
    )
