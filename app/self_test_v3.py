from __future__ import annotations

from datetime import datetime, timedelta, timezone

import edge_model
import engine
from execution_v3 import eligible_rows
from independent_model_v24 import HistoricalMatch, LEAGUE_SOURCES
from model_v3 import (
    V3HyperParameters,
    apply_v3_forecasts,
    build_v3_forecasts,
    component_probabilities,
    fit_state,
    tune_model,
)
from strategy_v3 import decision_for_side
from validation_v3 import walk_forward_validate


def _source(key: str):
    return next(s for s in LEAGUE_SOURCES if s.key == key)


def _history(count: int = 90) -> list[HistoricalMatch]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    teams = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta")
    output: list[HistoricalMatch] = []
    for i in range(count):
        home = teams[i % len(teams)]
        away = teams[(i * 3 + 1) % len(teams)]
        if home == away:
            away = teams[(i + 2) % len(teams)]
        hs = len(teams) - teams.index(home)
        as_ = len(teams) - teams.index(away)
        hg = max(0, 1 + int(hs > as_) + int(i % 4 == 0))
        ag = max(0, int(as_ >= hs) + int(i % 7 == 0))
        output.append(
            HistoricalMatch(
                start + timedelta(days=i * 3),
                "2526",
                "ENG-PL",
                home,
                away,
                hg,
                ag,
            )
        )
    return output


def run() -> int:
    """Fast frozen-executable V3 smoke test.

    The full unit suite runs before packaging. This path therefore verifies the
    frozen executable can import and execute the V3 model, uncertainty,
    decision and walk-forward layers without re-running legacy multiprocessing
    self-tests inside the one-file executable.
    """
    source = _source("ENG-PL")
    history = _history()
    cutoff = datetime(2026, 8, 28, tzinfo=timezone.utc)

    params, weight, temp, _ = tune_model(source, history)
    if not isinstance(params, V3HyperParameters):
        return 30
    state = fit_state(source, history, cutoff, params)
    dynamic, elo, _, _ = component_probabilities(state, "alpha", "beta", cutoff)
    if abs(sum(dynamic) - 1.0) > 1e-8 or abs(sum(elo) - 1.0) > 1e-8:
        return 31
    if not (0.0 <= weight <= 1.0 and 0.5 <= temp <= 2.0):
        return 32

    first = fit_state(source, history[:1], history[0].kickoff + timedelta(seconds=1), params)
    if abs(first.elo[history[0].home_team] + first.elo[history[0].away_team] - 3000.0) > 1e-8:
        return 33

    row = engine.CombinedMatch(
        kickoff=datetime(2026, 8, 29, 20, 0, tzinfo=engine.BRISBANE),
        home_team="Alpha",
        away_team="Beta",
        sb_home=1.70,
        sb_draw=3.80,
        sb_away=5.00,
        pm_fair_home=.60,
        pm_fair_draw=.23,
        pm_fair_away=.17,
    )
    row.league = "England Premier League"

    forecast = build_v3_forecasts(
        [row],
        {"ENG-PL": history},
        bootstrap_samples=3,
    )[row.match_name]
    if forecast.components != ("DYNAMIC-POISSON", "ELO"):
        return 34
    if not (forecast.lower_home <= forecast.home_probability <= forecast.upper_home):
        return 35

    edge_model.calculate_match_edge(row, 4.0)
    apply_v3_forecasts([row], {row.match_name: forecast}, 4.0)
    if len(eligible_rows([row])) != 1:
        return 36
    decision = decision_for_side(row, "HOME", 4.0)
    if decision is None or abs(decision.model_probability - forecast.home_probability) > 1e-12:
        return 37

    report = walk_forward_validate(
        source,
        history,
        min_train_matches=60,
        fold_size=20,
        max_folds=1,
    )
    if report.predictions <= 0 or report.log_loss is None or report.brier_score is None:
        return 38
    return 0
