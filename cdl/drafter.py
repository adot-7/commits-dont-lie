"""Drafting, grounding, and publish-gate orchestration for CDL.

This module joins Notion notes, GitHub diffs, LLM output, and the deterministic
matcher, then mirrors the resulting state to SQLite, Notion, and Slack. It
must not decide support outside ``grounding.check.claim_vs_diff``.
"""

from __future__ import annotations

from typing import Any

from . import llm
from .config import Settings, get_settings
from .github_client import GitHubClient
from .grounding.check import claim_vs_diff
from .models import Claim, DiffContext, Sentence, Verdict
from .notion_client import NotionClient
from .slack_client import SlackClient
from .store import Store


def evidence_line(sentence: Sentence, verdict: Verdict) -> str:
    """Render one compact receipt line for Notion and Slack."""

    marker = {"SUPPORTED": "✅", "UNSUPPORTED": "⛔", "UNVERIFIABLE": "❔"}[verdict.status]
    receipts = []
    for receipt in verdict.evidence:
        location = receipt.path if receipt.line_no is None else f"{receipt.path}:{receipt.line_no}"
        receipts.append(f"{location} ({receipt.entity})")
    if verdict.missing:
        receipts.append("missing: " + ", ".join(verdict.missing))
    suffix = ", ".join(receipts) if receipts else verdict.reason
    return f'{marker} "{sentence.text}" → {suffix}'


def _error_post(
    store: Store,
    *,
    note_page_id: str,
    base_sha: str,
    head_sha: str,
    component: str,
    error: str,
) -> int:
    """Persist a visible Errored row for failures before a normal post exists."""

    post_id = store.create_post(note_page_id, base_sha, head_sha, "Errored", "", error=f"{component}: {error}")
    store.append_event("error", {"component": component, "reason": error}, post_id=post_id)
    return post_id


def _mark_error(store: Store, post_id: int, component: str, error: str) -> None:
    """Mark a local post Errored and retain the component/reason."""

    store.update_post(post_id, status="Errored", error=f"{component}: {error}")
    store.append_event("error", {"component": component, "reason": error}, post_id=post_id)


def _try_notion_create(
    notion: NotionClient,
    store: Store,
    post_id: int,
    *,
    text: str,
    status: str,
    lines: list[str],
    base_sha: str,
    head_sha: str,
    note_page_id: str,
    settings: Settings,
) -> str | None:
    """Create a Notion mirror and mark the local post on failure."""

    try:
        notion_id = notion.create_post_row(
            text,
            status,
            lines,
            base_sha,
            head_sha,
            note_page_id,
            dashboard_url=f"{settings.app_base_url.rstrip('/')}/posts/{post_id}",
        )
        store.update_post(post_id, notion_page_id=notion_id)
        return notion_id
    except Exception as exc:
        _mark_error(store, post_id, "notion", str(exc))
        return None


def maybe_draft(
    head: str,
    *,
    diff: DiffContext | None = None,
    settings: Settings | None = None,
    store: Store | None = None,
    github: Any | None = None,
    notion: Any | None = None,
    slack: Any | None = None,
) -> list[int]:
    """Draft every Ready note against the requested HEAD and apply the gate."""

    active_settings = settings or get_settings(strict=False)
    active_store = store or Store(active_settings.database_path)
    active_github = github or GitHubClient(active_settings, store=active_store)
    active_notion = notion or NotionClient(active_settings, store=active_store)
    active_slack = slack or SlackClient(active_settings, store=active_store)
    created_posts: list[int] = []

    try:
        ready = active_notion.ready_notes()
    except Exception as exc:
        active_store.append_event("error", {"component": "notion", "reason": str(exc)})
        return created_posts
    if not ready:
        active_store.append_event("notes.ready", {"count": 0})
        return created_posts

    last_posted = active_store.last_posted_sha()
    initial_base = last_posted or active_settings.first_sha or (diff.base_sha if diff else "")
    if not initial_base:
        active_store.append_event("error", {"component": "github", "reason": "no base SHA available for draft"})
        return created_posts

    for note_page_id, note_title in ready:
        active_store.mark_note_seen(note_page_id, note_title, "Ready")
        if active_store.post_exists(note_page_id, head):
            active_store.append_event("draft.skipped", {"note_page_id": note_page_id, "head_sha": head})
            continue
        base = last_posted or active_settings.first_sha or initial_base
        try:
            note_body = active_notion.read_note_body(note_page_id)
            range_diff = diff if diff is not None and diff.base_sha == base and diff.head_sha == head else active_github.compare(base, head)
            sentences = llm.draft(note_title, note_body, range_diff, store=active_store, settings=active_settings)
            text = "\n".join(sentence.text for sentence in sentences)
            post_id = active_store.create_post(note_page_id, base, range_diff.head_sha, "Draft", text)
            created_posts.append(post_id)
            claims: list[Claim] = []
            verdicts: list[Verdict] = []
            changed_paths = [item.path for item in range_diff.files]
            for sentence in sentences:
                extracted = llm.extract_entities(sentence, changed_paths, store=active_store, settings=active_settings)
                filtered = llm.filter_entities(
                    sentence,
                    extracted,
                    event_sink=lambda kind, data: active_store.append_event(kind, data, post_id=post_id),
                )
                current_claim = Claim(sentence, filtered)
                verdict = claim_vs_diff(current_claim, range_diff)
                claims.append(current_claim)
                verdicts.append(verdict)
                active_store.append_event(
                    "verdict",
                    {
                        "sentence_idx": sentence.idx,
                        "status": verdict.status,
                        "missing": verdict.missing,
                        "reason": verdict.reason,
                        "evidence": [item.__dict__ for item in verdict.evidence],
                    },
                    post_id=post_id,
                )
            active_store.add_sentences(post_id, claims, verdicts)
            lines = [evidence_line(sentence, verdict) for sentence, verdict in zip(sentences, verdicts)]
            blocked = [
                (sentence.text, verdict.reason)
                for sentence, verdict in zip(sentences, verdicts)
                if verdict.status != "SUPPORTED"
            ]
            if blocked:
                active_store.update_post(post_id, status="Blocked")
                active_store.append_event("post.blocked", {"sentences": len(blocked)}, post_id=post_id)
                notion_id = _try_notion_create(
                    active_notion,
                    active_store,
                    post_id,
                    text=text,
                    status="Blocked",
                    lines=lines,
                    base_sha=base,
                    head_sha=range_diff.head_sha,
                    note_page_id=note_page_id,
                    settings=active_settings,
                )
                if notion_id:
                    try:
                        ts = active_slack.post_blocked(post_id, blocked)
                        active_store.update_post(post_id, slack_ts=ts, slack_channel=active_settings.slack_channel_id)
                        active_notion.update_post_row(notion_id, slack_ts=ts)
                    except Exception as exc:
                        _mark_error(active_store, post_id, "slack", str(exc))
                continue

            notion_id = _try_notion_create(
                active_notion,
                active_store,
                post_id,
                text=text,
                status="Draft",
                lines=lines,
                base_sha=base,
                head_sha=range_diff.head_sha,
                note_page_id=note_page_id,
                settings=active_settings,
            )
            if not notion_id:
                continue
            try:
                ts = active_slack.post_draft(post_id, text, lines)
                active_store.update_post(post_id, slack_ts=ts, slack_channel=active_settings.slack_channel_id)
                try:
                    active_notion.update_post_row(notion_id, slack_ts=ts)
                    active_notion.set_note_status(note_page_id, "Drafted")
                except Exception as exc:
                    _mark_error(active_store, post_id, "notion", str(exc))
                    continue
                active_store.append_event("post.drafted", {"notion_page_id": notion_id, "slack_ts": ts}, post_id=post_id)
            except Exception as exc:
                _mark_error(active_store, post_id, "slack", str(exc))
        except Exception as exc:
            component = "llm" if exc.__class__.__module__.startswith("cdl.llm") else "pipeline"
            post_id = _error_post(
                active_store,
                note_page_id=note_page_id,
                base_sha=base,
                head_sha=head,
                component=component,
                error=str(exc),
            )
            created_posts.append(post_id)
    return created_posts
