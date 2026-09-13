"""Slack approval transitions and the draft-to-send mid-check.

This module parses verified action payloads, prevents duplicate action work,
rechecks stored receipts against the current GitHub HEAD, and mirrors the
resulting state. It must never send a draft without reusing
``grounding.check.check_staleness`` first.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .config import Settings, get_settings
from .github_client import GitHubClient
from .grounding.check import check_staleness
from .models import Evidence
from .notion_client import NotionClient
from .slack_client import SlackClient
from .store import Store


def _now() -> str:
    """Return a UTC timestamp for state transitions."""

    return datetime.now(timezone.utc).isoformat()


def _payload_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract and validate the fields required by Slack interactivity."""

    action = (payload.get("actions") or [{}])[0] or {}
    user = payload.get("user") or {}
    message = payload.get("message") or {}
    channel = payload.get("channel") or {}
    fields = {
        "action_id": str(action.get("action_id", "")),
        "post_id": str(action.get("value", "")),
        "username": str(user.get("username") or user.get("name") or "user"),
        "message_ts": str(message.get("ts", "")),
        "channel_id": str(channel.get("id", "")),
        "action_ts": str(action.get("action_ts", "")),
    }
    missing = [key for key, value in fields.items() if not value]
    if missing:
        raise ValueError("Slack interaction missing " + ", ".join(missing))
    message_blocks = message.get("blocks")
    fields["message_blocks"] = message_blocks if isinstance(message_blocks, list) and message_blocks else None
    return fields


def _evidence(post: dict[str, Any]) -> list[Evidence]:
    """Convert stored evidence rows back into contract objects."""

    receipts: list[Evidence] = []
    for sentence in post.get("sentences", []):
        for item in sentence.get("evidence", []):
            receipts.append(
                Evidence(
                    entity=str(item["entity"]),
                    kind=item["kind"],
                    path=str(item["path"]),
                    line_no=item.get("line_no"),
                    line_text=item.get("line_text"),
                    commit_sha=item.get("commit_sha"),
                )
            )
    return receipts


def _mark_error(store: Store, post_id: int, component: str, error: str) -> None:
    """Persist an explicit approval failure on the affected post."""

    store.update_post(post_id, status="Errored", error=f"{component}: {error}")
    store.append_event("error", {"component": component, "reason": error}, post_id=post_id)


def _notion_status(notion: Any, store: Store, post: dict[str, Any], post_id: int, status: str) -> bool:
    """Mirror a post status and mark the local row if Notion fails."""

    notion_id = post.get("notion_page_id")
    if not notion_id:
        return True
    try:
        notion.update_post_row(notion_id, status=status)
        return True
    except Exception as exc:
        _mark_error(store, post_id, "notion", str(exc))
        return False


async def handle_interaction(
    payload: dict[str, Any],
    *,
    store: Store | None = None,
    settings: Settings | None = None,
    github: Any | None = None,
    notion: Any | None = None,
    slack: Any | None = None,
) -> None:
    """Handle one verified Slack approve/reject interaction in the background."""

    active_settings = settings or get_settings(strict=False)
    active_store = store or Store(active_settings.database_path)
    try:
        fields = _payload_fields(payload)
        post_id = int(fields["post_id"])
    except (TypeError, ValueError) as exc:
        active_store.append_event("error", {"component": "approval", "reason": str(exc)})
        return
    if not active_store.mark_action(fields["action_ts"]):
        active_store.append_event("approval.duplicate", {"action_ts": fields["action_ts"]}, post_id=post_id)
        return
    post = active_store.get_post(post_id)
    if post is None:
        active_store.append_event("error", {"component": "approval", "reason": f"post {post_id} not found"}, post_id=post_id)
        return
    active_github = github or GitHubClient(active_settings, store=active_store)
    active_notion = notion or NotionClient(active_settings, store=active_store)
    active_slack = slack or SlackClient(active_settings, store=active_store)
    active_store.append_event(
        "approval.received",
        {"action_id": fields["action_id"], "username": fields["username"], "action_ts": fields["action_ts"]},
        post_id=post_id,
    )

    if fields["action_id"] == "reject":
        active_store.update_post(post_id, status="Rejected")
        try:
            active_slack.update_message(
                fields["message_ts"],
                f"❌ Rejected by @{fields['username']}",
                channel=fields["channel_id"],
                blocks=fields["message_blocks"],
                post=post,
            )
        except Exception as exc:
            _mark_error(active_store, post_id, "slack", str(exc))
            return
        if not _notion_status(active_notion, active_store, post, post_id, "Rejected"):
            return
        try:
            active_notion.set_note_status(post["note_page_id"], "Ready")
        except Exception as exc:
            _mark_error(active_store, post_id, "notion", str(exc))
            return
        active_store.append_event("post.rejected", {"username": fields["username"]}, post_id=post_id)
        return

    if fields["action_id"] != "approve":
        active_store.append_event("error", {"component": "approval", "reason": f"unknown action {fields['action_id']}"}, post_id=post_id)
        return

    try:
        current_head = active_github.head_sha("main")
        current_diff = active_github.compare(post["head_sha"], current_head)
        stale = check_staleness(_evidence(post), current_diff)
    except Exception as exc:
        _mark_error(active_store, post_id, "github", str(exc))
        return

    if stale.stale:
        active_store.update_post(post_id, status="Stale", stale_at=_now())
        active_store.append_event("midcheck.stale", {"reason": stale.reason}, post_id=post_id)
        try:
            active_slack.update_message(
                fields["message_ts"],
                f"⚠️ Not sent: evidence changed — {stale.reason}",
                channel=fields["channel_id"],
                blocks=fields["message_blocks"],
                post=post,
            )
        except Exception as exc:
            _mark_error(active_store, post_id, "slack", str(exc))
            return
        _notion_status(active_notion, active_store, post, post_id, "Stale")
        return

    active_store.append_event("midcheck.passed", {"head_sha": current_head}, post_id=post_id)
    active_store.update_post(post_id, status="Sent", sent_at=_now())
    try:
        active_slack.update_message(
            fields["message_ts"],
            f"✅ Approved by @{fields['username']} — copy & post",
            channel=fields["channel_id"],
            blocks=fields["message_blocks"],
            post=post,
        )
    except Exception as exc:
        _mark_error(active_store, post_id, "slack", str(exc))
        return
    if not _notion_status(active_notion, active_store, post, post_id, "Sent"):
        return
    active_store.append_event("post.sent", {"username": fields["username"], "head_sha": current_head}, post_id=post_id)
