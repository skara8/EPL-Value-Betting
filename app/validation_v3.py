from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Iterable, Optional

from independent_model_v24 import HistoricalMatch, LeagueSource
from model_v3 import V3HyperParameters, combined_probability, component_probabilities, new_dynamic_state, tune_model, update_dynamic_state


@dataclass(frozen=True)
class WalkForwardPrediction:
    kickoff: datetime
    league_key: str
    home_team: str
    away_team: str
    p_home: float
    p_draw: float
    p_away: float
    outcome: int
    fold: int
    training_matches: int
    dynamic_weight: float
    calibration_temperature: float


@dataclass
class CalibrationBin:
    lower: float
    upper: float
    predictions: int = 0
    mean_probability: float = 0.0
    realised_frequency: float = 0.0


@dataclass
class WalkForwardReport:
    league_key: str
    predictions: int
    folds: int
    log_loss: Optional[float]
    brier_score: Optional[float]
    calibration_error: Optional[float]
    home_log_loss: Optional[float]
    draw_log_loss: Optional[float]
    away_log_loss: Optional[float]
    calibration_bins: tuple[CalibrationBin, ...] = ()
    records: tuple[WalkForwardPrediction, ...] = ()
    notes: tuple[str, ...] = ()


def _outcome(match: HistoricalMatch) -> int:
    return 0 if match.home_goals > match.away_goals else 1 if match.home_goals == match.away_goals else 2


def multiclass_log_loss(records: Iterable[WalkForwardPrediction]) -> Optional[float]:
    values = []
    for r in records:
        p = (r.p_home, r.p_draw, r.p_away)[r.outcome]
        values.append(-math.log(max(1e-12, p)))
    return mean(values) if values else None


def multiclass_brier(records: Iterable[WalkForwardPrediction]) -> Optional[float]:
    values = []
    for r in records:
        probs = (r.p_home, r.p_draw, r.p_away)
        values.append(sum((probs[k] - (1.0 if r.outcome == k else 0.0)) ** 2 for k in range(3)))
    return mean(values) if values else None


def _binary_log_loss(records: list[WalkForwardPrediction], outcome: int) -> Optional[float]:
    values = []
    for r in records:
        p = (r.p_home, r.p_draw, r.p_away)[outcome]
        y = 1.0 if r.outcome == outcome else 0.0
        values.append(-(y * math.log(max(1e-12, p)) + (1-y) * math.log(max(1e-12, 1-p))))
    return mean(values) if values else None


def calibration_table(records: Iterable[WalkForwardPrediction], bins: int = 10) -> tuple[CalibrationBin, ...]:
    buckets = [[] for _ in range(bins)]
    for r in records:
        for k, p in enumerate((r.p_home, r.p_draw, r.p_away)):
            buckets[min(bins-1, max(0, int(p*bins)))].append((p, 1.0 if r.outcome == k else 0.0))
    output = []
    for i, items in enumerate(buckets):
        if items:
            output.append(CalibrationBin(i/bins, (i+1)/bins, len(items), mean(x[0] for x in items), mean(x[1] for x in items)))
        else:
            output.append(CalibrationBin(i/bins, (i+1)/bins))
    return tuple(output)


def expected_calibration_error(table: Iterable[CalibrationBin]) -> Optional[float]:
    table = list(table)
    total = sum(item.predictions for item in table)
    if not total:
        return None
    return sum(item.predictions/total * abs(item.mean_probability-item.realised_frequency) for item in table)


def _fit_training_state(source: LeagueSource, training: list[HistoricalMatch], params: V3HyperParameters):
    state = new_dynamic_state(source, params)
    by_day = defaultdict(list)
    for match in training:
        by_day[match.kickoff.date()].append(match)
    for day in sorted(by_day):
        for match in by_day[day]:
            update_dynamic_state(state, match)
    return state


def walk_forward_validate(
    source: LeagueSource,
    matches: list[HistoricalMatch],
    *,
    min_train_matches: int = 140,
    fold_size: int = 80,
    max_folds: Optional[int] = None,
) -> WalkForwardReport:
    """Expanding-window validation with no random shuffle or same-day leakage."""
    history = sorted(matches, key=lambda m: (m.kickoff, m.home_team, m.away_team))
    if len(history) <= min_train_matches + 10:
        return WalkForwardReport(source.key, 0, 0, None, None, None, None, None, None, notes=(f"Need more than {min_train_matches+10} chronological matches; found {len(history)}.",))

    records: list[WalkForwardPrediction] = []
    fold = 0
    start = min_train_matches
    if max_folds is not None:
        start = max(min_train_matches, len(history) - max_folds * max(20, fold_size))
    while start < len(history):
        if max_folds is not None and fold >= max_folds:
            break
        end = min(len(history), start + max(20, fold_size))
        training, test = history[:start], history[start:end]
        params, weight, temperature, _ = tune_model(source, training)
        state = _fit_training_state(source, training, params)
        by_day = defaultdict(list)
        for match in test:
            by_day[match.kickoff.date()].append(match)
        for day in sorted(by_day):
            predicted = []
            for match in by_day[day]:
                dynamic, elo, _, _ = component_probabilities(state, match.home_team, match.away_team, match.kickoff)
                predicted.append((match, combined_probability(dynamic, elo, weight, temperature)))
            for match, p in predicted:
                records.append(WalkForwardPrediction(
                    match.kickoff, source.key, match.home_team, match.away_team,
                    p[0], p[1], p[2], _outcome(match), fold, len(training), weight, temperature,
                ))
            # Every same-day fixture is predicted before any same-day outcome is admitted.
            for match in by_day[day]:
                update_dynamic_state(state, match)
        fold += 1
        start = end

    table = calibration_table(records)
    return WalkForwardReport(
        source.key, len(records), fold, multiclass_log_loss(records), multiclass_brier(records),
        expected_calibration_error(table), _binary_log_loss(records,0), _binary_log_loss(records,1), _binary_log_loss(records,2),
        table, tuple(records), ("Expanding-window validation; same-day fixtures are forecast before any same-day outcome update.",),
    )


def pooled_report(reports: Iterable[WalkForwardReport]) -> WalkForwardReport:
    reports = list(reports)
    records = [record for report in reports for record in report.records]
    table = calibration_table(records)
    return WalkForwardReport(
        "POOLED", len(records), sum(r.folds for r in reports), multiclass_log_loss(records), multiclass_brier(records),
        expected_calibration_error(table), _binary_log_loss(records,0), _binary_log_loss(records,1), _binary_log_loss(records,2),
        table, tuple(records), ("Pooled metrics contain only genuine chronological out-of-fold predictions.",),
    )
