"""Background orchestration for GitHub push deliveries.

This module loads persisted pushes, fetches their compare range, runs the
monitor before drafting, and contains the outer failure boundary. It must use
clients and the grounding module rather than duplicate their logic.
"""

from __future__ import annotations

from typing import Any

from .config import Settings, get_settings
from .github_client import GitHubClient
from .notion_client import NotionClient
from .slack_client import SlackClient
from .store import Store


async def handle_push(
    push_id: int,
    *,
    store: Store | None = None,
    settings: Settings | None = None,
    github: Any | None = None,
    notion: Any | None = None,
    slack: Any | None = None,
) -> None:
    """Run compare → monitor → draft for one persisted push."""

    active_settings = settings or get_settings(strict=False)
    active_store = store or Store(active_settings.database_path)
    push = active_store.get_push(push_id)
    if push is None:
        active_store.append_event(
            "error",
            {"component": "pipeline", "reason": f"push {push_id} not found"},
            push_id=push_id,
        )
        return
    active_github = github or GitHubClient(active_settings, store=active_store)
    active_notion = notion or NotionClient(active_settings, store=active_store)
    active_slack = slack or SlackClient(active_settings, store=active_store)
    try:
        diff = active_github.compare(push.before_sha, push.after_sha)
    except Exception as exc:
        active_store.append_event("compare.failed", {"push_id": push_id, "reason": str(exc)}, push_id=push_id)
        active_store.append_event("error", {"component": "github", "reason": str(exc)}, push_id=push_id)
        return
    try:
        from . import monitor

        monitor.run(
            diff,
            store=active_store,
            settings=active_settings,
            notion=active_notion,
            slack=active_slack,
        )
        from . import drafter

        drafter.maybe_draft(
            push.after_sha,
            diff=diff,
            settings=active_settings,
            store=active_store,
            github=active_github,
            notion=active_notion,
            slack=active_slack,
        )
    except Exception as exc:
        active_store.append_event("error", {"component": "pipeline", "reason": str(exc)}, push_id=push_id)
