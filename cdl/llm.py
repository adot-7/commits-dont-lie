"""Anthropic drafting, extraction, and substring filtering for CDL.

This module owns tool-use calls and the guard that removes extracted names not
present in the sentence. It must never decide SUPPORTED, UNSUPPORTED, or
UNVERIFIABLE; that decision belongs only to ``grounding.check``.
"""

from __future__ import annotations

from typing import Callable

from .models import Entities, Sentence


def _keeps(sentence_text: str, entity: str, *, file_entity: bool = False) -> bool:
    """Apply the case-insensitive literal substring rule."""

    lowered = sentence_text.lower()
    return entity.lower() in lowered or (
        file_entity and entity.rsplit("/", 1)[-1].lower() in lowered
    )


def filter_entities(
    sentence: Sentence,
    entities: Entities,
    *,
    event_sink: Callable[[str, dict], object] | None = None,
) -> Entities:
    """Drop hallucinated entities and optionally emit one event per drop."""

    kept: dict[str, list[str]] = {"files": [], "symbols": [], "integrations": []}
    for category, values in (
        ("files", entities.files),
        ("symbols", entities.symbols),
        ("integrations", entities.integrations),
    ):
        for value in values:
            if _keeps(sentence.text, value, file_entity=category == "files"):
                kept[category].append(value)
            elif event_sink is not None:
                event_sink(
                    "entity_dropped",
                    {"sentence_idx": sentence.idx, "entity": value, "kind": category[:-1]},
                )
    return Entities(**kept)
