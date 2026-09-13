"""Post-publish staleness monitor seam for CDL.

M3 keeps this pass empty so drafting can ship first. M4 implements the Sent
post sweep here; it must reuse ``grounding.check.check_staleness`` and must not
draft new copy or implement a second matcher.
"""

from __future__ import annotations

from .models import DiffContext


def run(diff: DiffContext, **_kwargs) -> list[int]:
    """Return no monitored posts until the M4 monitor implementation lands."""

    return []
