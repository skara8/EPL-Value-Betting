from __future__ import annotations

from datetime import datetime, timedelta, timezone

import edge_model
import engine
from independent_model_v24 import (
    HistoricalMatch,
    LEAGUE_SOURCES,
    _build_state,
    _forecast_from_states,
    apply_independent_forecasts,
    dixon_coles_probabilities,
    resolve_league_source,
)
from self_test_v22 import run as run_v22
from updater import external_installer_environment


def _source(key: str):
    return next(s for s in LEAGUE_SOURCES if s.key == key)


def _history() -> list[HistoricalMatch]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    teams = ("alpha", "beta", "gamma", "delta")
    matches: list[HistoricalMatch] = []
    for i in range(96):
        home = teams[i % 4]
        away = teams[(i + 1 + (i // 4) % 2) % 4]
        if home == away:
            away = teams[(i + 2) % 4]
        # Alpha is deliberately the strongest synthetic team; the exact values
        # are unimportant, only that the model can produce a stable independent
        # probability without reading bookmaker odds.
        hg = 2 if home == "alpha" else 1
        ag = 0 if away == "alpha" else 1
        if away == "alpha":
            ag = 2
        matches.append(HistoricalMatch(start + timedelta(days=i * 4), "2526", "ENG-PL", home, away, hg, ag))
    return matches


def run() -> int:
    # Preserve the V2.2 frozen multiprocessing gate.
    base = run_v22()
    if base != 0:
        return base

    if resolve_league_source("England Premier League") is None:
        return 10
    if resolve_league_source("Brazil Serie A") is None:
        return 11

    probs = dixon_coles_probabilities(1.6, 1.1)
    if abs(sum(probs) - 1.0) > 1e-8:
        return 12

    source = _source("ENG-PL")
    history = _history()
    cutoff = datetime(2026, 8, 28, tzinfo=timezone.utc)
    states = {half: _build_state(source, history, cutoff, half) for half in (90.0, 180.0, 360.0)}

    row_a = engine.CombinedMatch(
        kickoff=datetime(2026, 8, 29, 20, 0, tzinfo=engine.BRISBANE),
        home_team="Alpha",
        away_team="Beta",
        sb_home=1.55,
        sb_draw=4.20,
        sb_away=6.20,
        pm_fair_home=0.70,
        pm_fair_draw=0.18,
        pm_fair_away=0.12,
    )
    row_b = engine.CombinedMatch(
        kickoff=row_a.kickoff,
        home_team="Alpha",
        away_team="Beta",
        sb_home=3.10,
        sb_draw=2.40,
        sb_away=2.20,
        pm_fair_home=0.30,
        pm_fair_draw=0.30,
        pm_fair_away=0.40,
    )
    row_a.league = row_b.league = "England Premier League"

    fa = _forecast_from_states(row_a, source, states)
    fb = _forecast_from_states(row_b, source, states)
    if fa is None or fb is None:
        return 13
    if max(abs(x - y) for x, y in zip(
        (fa.home_probability, fa.draw_probability, fa.away_probability),
        (fb.home_probability, fb.draw_probability, fb.away_probability),
    )) > 1e-12:
        # Current Sportsbet/Polymarket prices must not alter V2.4 probabilities.
        return 14

    # Create legacy edge containers and verify V2.4 replaces their probability
    # while retaining the market-derived number only as a diagnostic.
    edge_model.calculate_match_edge(row_a, 4.0)
    old_market = getattr(row_a, "model_fair_home", None)
    apply_independent_forecasts([row_a], {row_a.match_name: fa}, 4.0)
    if abs(float(row_a.model_fair_home) - fa.home_probability) > 1e-12:
        return 15
    if old_market is not None and getattr(row_a, "market_reference_home", None) is None:
        return 16

    # Keep the updater-handoff regression gate from V2.3.1.
    env = external_installer_environment(
        {
            "PATH": r"C:\Windows\System32",
            "_PYI_PARENT_PROCESS_LEVEL": "1",
            "_PYI_APPLICATION_HOME_DIR": r"C:\Temp\_MEI12345",
        }
    )
    if any(key.upper().startswith("_PYI_") for key in env):
        return 17
    if env.get("PYINSTALLER_RESET_ENVIRONMENT") != "1":
        return 18
    return 0
