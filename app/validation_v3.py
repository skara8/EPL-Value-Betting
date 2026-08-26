from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Iterable, Optional

from independent_model_v24 import HistoricalMatch, LeagueSource
from model_v3 import (
    V3HyperParameters,
    combined_probability,
    component_probabilities,
    new_dynamic_state,
    tune_model,
    update_dynamic_state,
)


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
    baseline_home: Optional[float] = None
    baseline_draw: Optional[float] = None
    baseline_away: Optional[float] = None
    elo_only_home: Optional[float] = None
    elo_only_draw: Optional[float] = None
    elo_only_away: Optional[float] = None
    dynamic_only_home: Optional[float] = None
    dynamic_only_draw: Optional[float] = None
    dynamic_only_away: Optional[float] = None


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
    rps: Optional[float] = None
    league_frequency_log_loss: Optional[float] = None
    elo_only_log_loss: Optional[float] = None
    dynamic_only_log_loss: Optional[float] = None
    delta_log_loss_vs_best_baseline: Optional[float] = None


def _outcome(match: HistoricalMatch) -> int:
    return 0 if match.home_goals > match.away_goals else 1 if match.home_goals == match.away_goals else 2


def _safe_log_loss(probabilities: Iterable[tuple[float, float, float]], outcomes: Iterable[int]) -> Optional[float]:
    values = []
    for probs, outcome in zip(probabilities, outcomes):
        values.append(-math.log(max(1e-12, probs[outcome])))
    return mean(values) if values else None


def multiclass_log_loss(records: Iterable[WalkForwardPrediction]) -> Optional[float]:
    records = list(records)
    return _safe_log_loss(
        ((r.p_home, r.p_draw, r.p_away) for r in records),
        (r.outcome for r in records),
    )


def multiclass_brier(records: Iterable[WalkForwardPrediction]) -> Optional[float]:
    values = []
    for r in records:
        probs = (r.p_home, r.p_draw, r.p_away)
        values.append(sum((probs[k] - (1.0 if r.outcome == k else 0.0)) ** 2 for k in range(3)))
    return mean(values) if values else None


def ranked_probability_score(records: Iterable[WalkForwardPrediction]) -> Optional[float]:
    """Three-outcome RPS; lower is better.

    Home/draw/away is treated as the natural ordered outcome used by the
    football literature. RPS complements log loss without replacing it as the
    primary selection metric.
    """
    values = []
    for r in records:
        probs = (r.p_home, r.p_draw, r.p_away)
        obs = (1.0 if r.outcome == 0 else 0.0, 1.0 if r.outcome == 1 else 0.0, 1.0 if r.outcome == 2 else 0.0)
        cumulative_p = (probs[0], probs[0] + probs[1])
        cumulative_y = (obs[0], obs[0] + obs[1])
        values.append(sum((p - y) ** 2 for p, y in zip(cumulative_p, cumulative_y)) / 2.0)
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


def _league_frequency_baseline(training: list[HistoricalMatch]) -> tuple[float, float, float]:
    counts = [1.0, 1.0, 1.0]  # weak Laplace prior prevents zero-probability logs
    for match in training:
        counts[_outcome(match)] += 1.0
    total = sum(counts)
    return counts[0] / total, counts[1] / total, counts[2] / total


def _date_groups(matches: list[HistoricalMatch]) -> list[list[HistoricalMatch]]:
    by_day: dict[object, list[HistoricalMatch]] = defaultdict(list)
    for match in sorted(matches, key=lambda m: (m.kickoff, m.home_team, m.away_team)):
        by_day[match.kickoff.date()].append(match)
    return [by_day[day] for day in sorted(by_day)]


def _fold_plan(
    history: list[HistoricalMatch],
    min_train_matches: int,
    fold_size: int,
    max_folds: Optional[int],
) -> list[tuple[list[HistoricalMatch], list[HistoricalMatch]]]:
    """Create date-atomic expanding folds.

    A calendar day can never straddle train and test. This closes a subtle
    leakage path that remained when folds were first cut by raw match count and
    only then batched by day inside the test period.
    """
    groups = _date_groups(history)
    cumulative = 0
    train_group_count = 0
    for i, group in enumerate(groups):
        cumulative += len(group)
        if cumulative >= min_train_matches:
            train_group_count = i + 1
            break
    if train_group_count <= 0 or train_group_count >= len(groups):
        return []

    plans: list[tuple[list[HistoricalMatch], list[HistoricalMatch]]] = []
    start_group = train_group_count
    requested = max(20, int(fold_size))
    while start_group < len(groups):
        end_group = start_group
        test_count = 0
        while end_group < len(groups) and test_count < requested:
            test_count += len(groups[end_group])
            end_group += 1
        training = [m for group in groups[:start_group] for m in group]
        test = [m for group in groups[start_group:end_group] for m in group]
        if training and test:
            plans.append((training, test))
        start_group = end_group

    if max_folds is not None and max_folds >= 0:
        plans = plans[-max_folds:] if max_folds else []
    return plans


def _optional_component_log_loss(records: list[WalkForwardPrediction], prefix: str) -> Optional[float]:
    triples = []
    outcomes = []
    names = (f"{prefix}_home", f"{prefix}_draw", f"{prefix}_away")
    for record in records:
        values = tuple(getattr(record, name) for name in names)
        if any(value is None for value in values):
            continue
        triples.append((float(values[0]), float(values[1]), float(values[2])))
        outcomes.append(record.outcome)
    return _safe_log_loss(triples, outcomes)


def _build_report(source_key: str, records: list[WalkForwardPrediction], folds: int, notes: tuple[str, ...]) -> WalkForwardReport:
    table = calibration_table(records)
    ll = multiclass_log_loss(records)
    league_ll = _optional_component_log_loss(records, "baseline")
    elo_ll = _optional_component_log_loss(records, "elo_only")
    dynamic_ll = _optional_component_log_loss(records, "dynamic_only")
    baseline_values = [value for value in (league_ll, elo_ll, dynamic_ll) if value is not None]
    delta = None if ll is None or not baseline_values else ll - min(baseline_values)
    return WalkForwardReport(
        source_key,
        len(records),
        folds,
        ll,
        multiclass_brier(records),
        expected_calibration_error(table),
        _binary_log_loss(records, 0),
        _binary_log_loss(records, 1),
        _binary_log_loss(records, 2),
        table,
        tuple(records),
        notes,
        ranked_probability_score(records),
        league_ll,
        elo_ll,
        dynamic_ll,
        delta,
    )


def walk_forward_validate(
    source: LeagueSource,
    matches: list[HistoricalMatch],
    *,
    min_train_matches: int = 140,
    fold_size: int = 80,
    max_folds: Optional[int] = None,
) -> WalkForwardReport:
    """Expanding-window validation with date-atomic folds and no same-day leakage."""
    history = sorted(matches, key=lambda m: (m.kickoff, m.home_team, m.away_team))
    if len(history) <= min_train_matches + 10:
        return WalkForwardReport(
            source.key, 0, 0, None, None, None, None, None, None,
            notes=(f"Need more than {min_train_matches+10} chronological matches; found {len(history)}.",),
        )

    plans = _fold_plan(history, min_train_matches, fold_size, max_folds)
    if not plans:
        return WalkForwardReport(
            source.key, 0, 0, None, None, None, None, None, None,
            notes=("No complete date-atomic walk-forward fold could be created.",),
        )

    records: list[WalkForwardPrediction] = []
    for fold, (training, test) in enumerate(plans):
        params, weight, temperature, _ = tune_model(source, training)
        state = _fit_training_state(source, training, params)
        baseline = _league_frequency_baseline(training)
        by_day = defaultdict(list)
        for match in test:
            by_day[match.kickoff.date()].append(match)

        for day in sorted(by_day):
            predicted = []
            for match in by_day[day]:
                dynamic, elo, _, _ = component_probabilities(state, match.home_team, match.away_team, match.kickoff)
                combined = combined_probability(dynamic, elo, weight, temperature)
                predicted.append((match, combined, dynamic, elo))
            for match, p, dynamic, elo in predicted:
                records.append(WalkForwardPrediction(
                    match.kickoff,
                    source.key,
                    match.home_team,
                    match.away_team,
                    p[0], p[1], p[2],
                    _outcome(match),
                    fold,
                    len(training),
                    weight,
                    temperature,
                    baseline[0], baseline[1], baseline[2],
                    elo[0], elo[1], elo[2],
                    dynamic[0], dynamic[1], dynamic[2],
                ))
            # Every same-day fixture is predicted before any same-day outcome is admitted.
            for match in by_day[day]:
                update_dynamic_state(state, match)

    return _build_report(
        source.key,
        records,
        len(plans),
        (
            "Date-atomic expanding-window validation; a calendar day never straddles train/test and same-day fixtures are forecast before any same-day outcome update.",
            "Primary model is compared against league-frequency, Elo-only and dynamic-score-only chronological baselines.",
        ),
    )


def pooled_report(reports: Iterable[WalkForwardReport]) -> WalkForwardReport:
    reports = list(reports)
    records = [record for report in reports for record in report.records]
    return _build_report(
        "POOLED",
        records,
        sum(r.folds for r in reports),
        ("Pooled metrics contain only genuine date-atomic chronological out-of-fold predictions.",),
    )
