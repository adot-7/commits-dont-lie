"""Post-publish staleness monitor for CDL Sent posts.

This module checks every Sent post on every push, creates correction records,
and mirrors stale state to Notion and Slack. It must reuse
``grounding.check.check_staleness`` and must not draft new copy or implement a
second matcher.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .config import Settings, get_settings
from .grounding.check import check_staleness
from .models import DiffContext, Evidence
from .notion_client import NotionClient
from .slack_client import SlackClient
from .store import Store


def _now() -> str:
    """Return a UTC timestamp for stale transitions."""

    return datetime.now(timezone.utc).isoformat()


def _evidence(post: dict[str, Any]) -> list[Evidence]:
    """Convert stored child evidence rows into contract receipts."""

    return [
        Evidence(
            entity=str(item["entity"]),
            kind=item["kind"],
            path=str(item["path"]),
            line_no=item.get("line_no"),
            line_text=item.get("line_text"),
            commit_sha=item.get("commit_sha"),
        )
        for sentence in post.get("sentences", [])
        for item in sentence.get("evidence", [])
    ]


def _mark_error(store: Store, post_id: int, component: str, error: str) -> None:
    """Persist a visible external-service failure on the affected post."""

    store.update_post(post_id, status="Errored", error=f"{component}: {error}")
    store.append_event("error", {"component": component, "reason": error}, post_id=post_id)


def run(
    diff: DiffContext,
    *,
    settings: Settings | None = None,
    store: Store | None = None,
    notion: Any | None = None,
    slack: Any | None = None,
) -> list[int]:
    """Check all Sent posts and return local IDs of newly-created corrections."""

    active_settings = settings or get_settings(strict=False)
    active_store = store or Store(active_settings.database_path)
    active_notion = notion or NotionClient(active_settings, store=active_store)
    active_slack = slack or SlackClient(active_settings, store=active_store)
    corrections: list[int] = []
    for post in active_store.list_sent_posts():
        post_id = int(post["id"])
        stale = check_staleness(_evidence(post), diff)
        active_store.append_event(
            "monitor.checked",
            {"head_sha": diff.head_sha, "stale": stale.stale, "removed": [item.entity for item in stale.removed]},
            post_id=post_id,
        )
        if not stale.stale:
            continue
        first = stale.removed[0]
        correction_text = f"Correction to post #{post_id}: {first.entity} was removed in {diff.head_sha[:7]} ({first.path})."
        active_store.update_post(post_id, status="Stale", stale_at=_now())
        correction_id = active_store.create_post(
            post.get("note_page_id") or "",
            diff.base_sha,
            diff.head_sha,
            "Correction",
            correction_text,
        )
        corrections.append(correction_id)
        active_store.update_post(post_id, superseded_by=correction_id)
        active_store.append_event(
            "post.stale",
            {"reason": stale.reason, "correction_id": correction_id},
            post_id=post_id,
        )
        notion_id = post.get("notion_page_id")
        try:
            if notion_id:
                active_notion.update_post_row(notion_id, status="Stale", superseded_by=str(correction_id))
            correction_notion_id = active_notion.create_post_row(
                correction_text,
                "Correction",
                [f"⚠️ {stale.reason}"],
                diff.base_sha,
                diff.head_sha,
                post.get("note_page_id") or "",
                dashboard_url=f"{active_settings.app_base_url.rstrip('/')}/posts/{correction_id}",
                name=f"Correction: {first.entity}"[:60],
            )
            active_store.update_post(correction_id, notion_page_id=correction_notion_id)
        except Exception as exc:
            _mark_error(active_store, post_id, "notion", str(exc))
            continue
        try:
            if post.get("slack_ts"):
                thread_text = f"⚠️ Stale: {stale.reason}. Correction: {active_settings.app_base_url.rstrip('/')}/posts/{correction_id}"
                active_slack.post_thread_reply(
                    post["slack_ts"],
                    thread_text,
                    channel=post.get("slack_channel") or active_settings.slack_channel_id,
                )
            active_store.append_event(
                "correction.threaded",
                {"correction_id": correction_id, "reason": stale.reason},
                post_id=post_id,
            )
        except Exception as exc:
            _mark_error(active_store, post_id, "slack", str(exc))
    return corrections
