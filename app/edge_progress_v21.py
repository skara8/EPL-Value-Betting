from __future__ import annotations

from typing import Callable, Optional

from edge_model import calculate_match_edge
from engine import CombinedMatch


ProgressCallback = Callable[[int, str, str], None]


def enrich_edge_model_progressive(
    rows: list[CombinedMatch],
    min_ev_pct: float = 4.0,
    progress: Optional[ProgressCallback] = None,
    start_pct: int = 73,
    end_pct: int = 80,
) -> list[CombinedMatch]:
    """Run the unchanged edge calculation while reporting real row progress."""
    total = max(1, len(rows))
    if progress:
        progress(start_pct, "Probability model", f"Calculating fair probabilities and EV for {len(rows):,} fixtures")
    for index, row in enumerate(rows, start=1):
        calculate_match_edge(row, min_ev_pct=min_ev_pct)
        if progress and (index == total or index % max(10, total // 14) == 0):
            pct = start_pct + int((end_pct - start_pct) * index / total)
            progress(
                min(end_pct, pct),
                "Probability model",
                f"Calculated {index:,}/{total:,} fixtures · de-vig, external fair probability, conservative EV",
            )
    return rows
