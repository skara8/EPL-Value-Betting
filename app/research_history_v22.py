from __future__ import annotations

from dataclasses import replace

import engine
from research_models_v22 import HistoricalMatch, fetch_recent_epl_history as _fetch_recent_epl_history


def fetch_recent_epl_history(target) -> list[HistoricalMatch]:
    """Return Football-Data history using the same canonical club names as live feeds."""
    rows = _fetch_recent_epl_history(target)
    output = []
    for row in rows:
        home = engine.canonical_epl_club(row.home) or row.home
        away = engine.canonical_epl_club(row.away) or row.away
        output.append(replace(row, home=home, away=away))
    return output
