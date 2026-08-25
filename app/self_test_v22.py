from __future__ import annotations

from datetime import datetime

import engine
from edge_parallel_v22 import enrich_edge_model_parallel


def run() -> int:
    rows = []
    for i in range(12):
        row = engine.CombinedMatch(
            kickoff=datetime(2026, 8, 29, 20, 0, tzinfo=engine.BRISBANE),
            home_team=f"Home {i}",
            away_team=f"Away {i}",
            sb_home=2.00,
            sb_draw=3.40,
            sb_away=3.80,
            pm_fair_home=0.51,
            pm_fair_draw=0.26,
            pm_fair_away=0.23,
        )
        row.pin_home = 1.98
        row.pin_draw = 3.55
        row.pin_away = 4.05
        rows.append(row)

    calculated, stats = enrich_edge_model_parallel(rows, min_ev_pct=4.0, workers=2)
    if len(calculated) != len(rows):
        return 2
    if not stats.parallel or stats.workers != 2:
        return 3
    if any(not hasattr(row, "edge_outcomes") for row in calculated):
        return 4
    return 0
