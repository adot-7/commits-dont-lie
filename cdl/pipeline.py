"""Background orchestration for GitHub push deliveries.

This early milestone only provides the dispatch seam used by the webhook.
Later milestones add monitoring and drafting behind this function; it must use
clients and the grounding module rather than duplicate their logic.
"""

from __future__ import annotations

from .store import Store


async def handle_push(push_id: int, *, store: Store | None = None) -> None:
    """Record that a persisted push was accepted by the background worker."""

    active_store = store or Store()
    active_store.append_event("pipeline.accepted", {"push_id": push_id}, push_id=push_id)
