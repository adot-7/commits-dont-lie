"""Slack approval transition seam for CDL.

This early milestone only acknowledges background dispatch.  The completed
implementation owns approve/reject actions and the mid-check; it must never
send a draft without reusing the staleness checker first.
"""

from __future__ import annotations

from .store import Store


async def handle_interaction(payload: dict, *, store: Store | None = None) -> None:
    """Record a verified interaction until M3 adds the state machine."""

    active_store = store or Store()
    active_store.append_event("approval.queued", {"type": payload.get("type")})
