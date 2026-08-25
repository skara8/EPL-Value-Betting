from __future__ import annotations

from datetime import datetime

import edge_model
import engine
from self_test_v22 import run as run_v22


def run() -> int:
    base = run_v22()
    if base != 0:
        return base

    row = engine.CombinedMatch(
        kickoff=datetime(2026, 8, 29, 20, 0, tzinfo=engine.BRISBANE),
        home_team="Example Home",
        away_team="Example Away",
        sb_home=2.40,
        sb_draw=3.20,
        sb_away=3.00,
    )
    row.consensus_components = (
        ("Bet365", 0.43, 0.29, 0.28),
        ("TAB", 0.42, 0.30, 0.28),
        ("Ladbrokes", 0.425, 0.295, 0.28),
    )
    outcomes = edge_model.calculate_match_edge(row, 4.0)
    if getattr(row, "reference_tier", "") != "TIER 2 — BOOKMAKER CONSENSUS":
        return 5
    if getattr(row, "model_fair_home", None) is None:
        return 6
    if outcomes["HOME"].source_count < 2:
        return 7
    return 0
