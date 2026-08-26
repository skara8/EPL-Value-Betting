from __future__ import annotations

"""Chronological walk-forward validation for V3.

The engine intentionally evaluates forecasts by date.  Football-Data history
is frequently date-only, so every match on a calendar date is predicted before
*any* result from that date is admitted to the state.  This is conservative and
prevents same-day look-ahead leakage.
"""

import math
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Callable, Iterable, Optional

import independent_model_v24 as v24
import independent_model_v3 as v3

ProgressCallback = Callable[[int, str, str], None]


@dataclass(frozen=True)
class PredictionRecord:
    league_key: str
    kickoff: datetime
    season: str
    home_team: str
    away_team: str
    actual_index: int
    baseline: tuple[float, float, float]
    challenger: tuple[float, float, float]


@dataclass
class ModelMetrics:
    n: int
    log_loss: float
    brier: float
    rps: float
    ece: float


@dataclass
class WalkForwardResult:
    records: list[PredictionRecord]
    baseline: ModelMetrics
    challenger: ModelMetrics
    delta_log_loss: float
    delta_log_loss_ci_low: float
    delta_log_loss_ci_high: float
    challenger_better_fraction: float
    leagues: tuple[str, ...]
    periods: int
    forecast_grade: str
    notes: tuple[str, ...]


def _emit(cb: Optional[ProgressCallback], pct: int, stage: str, detail: str) -> None:
    if cb:
        cb(max(0, min(100, int(pct))), stage, detail)


def _actual_index(match: v24.HistoricalMatch) -> int:
    return 0 if match.home_goals > match.away_goals else 1 if match.home_goals == match.away_goals else 2


def _clip_probs(values: Iterable[float]) -> tuple[float, float, float]:
    vals = [max(1e-8, min(1.0 - 1e-8, float(v))) for v in values]
    total = sum(vals)
    return vals[0] / total, vals[1] / total, vals[2] / total


def _metrics(records: list[PredictionRecord], field: str) -> ModelMetrics:
    if not records:
        return ModelMetrics(0, float("nan"), float("nan"), float("nan"), float("nan"))
    log_loss = brier = rps = 0.0
    calibration_rows: list[tuple[float, int]] = []
    for record in records:
        probs = _clip_probs(getattr(record, field))
        y = record.actual_index
        log_loss -= math.log(max(1e-12, probs[y]))
        for k in range(3):
            truth = 1.0 if k == y else 0.0
            brier += (probs[k] - truth) ** 2
        # H/D/A can be represented on the ordered result axis home -> draw -> away.
        cum_p1 = probs[0]
        cum_p2 = probs[0] + probs[1]
        cum_y1 = 1.0 if y == 0 else 0.0
        cum_y2 = 1.0 if y in (0, 1) else 0.0
        rps += ((cum_p1 - cum_y1) ** 2 + (cum_p2 - cum_y2) ** 2) / 2.0
        for k in range(3):
            calibration_rows.append((probs[k], 1 if k == y else 0))

    # Simple pooled multiclass one-vs-all expected calibration error.
    ece = 0.0
    total_points = len(calibration_rows)
    for b in range(10):
        lo, hi = b / 10.0, (b + 1) / 10.0
        bucket = [x for x in calibration_rows if lo <= x[0] < hi or (b == 9 and x[0] == 1.0)]
        if not bucket:
            continue
        avg_p = mean(x[0] for x in bucket)
        avg_y = mean(x[1] for x in bucket)
        ece += len(bucket) / total_points * abs(avg_p - avg_y)

    n = len(records)
    return ModelMetrics(n, log_loss / n, brier / n, rps / n, ece)


def _logloss_delta(record: PredictionRecord) -> float:
    b = max(1e-12, record.baseline[record.actual_index])
    c = max(1e-12, record.challenger[record.actual_index])
    # Challenger loss minus baseline loss; negative is better.
    return -math.log(c) + math.log(b)


def _cluster_key(record: PredictionRecord) -> str:
    dt = record.kickoff.astimezone(timezone.utc)
    return f"{record.league_key}:{dt.year:04d}-{dt.month:02d}"


def _cluster_bootstrap_ci(records: list[PredictionRecord], samples: int = 500) -> tuple[float, float, float]:
    if not records:
        return float("nan"), float("nan"), float("nan")
    clusters: dict[str, list[float]] = defaultdict(list)
    for record in records:
        clusters[_cluster_key(record)].append(_logloss_delta(record))
    keys = sorted(clusters)
    if len(keys) < 3:
        values = [_logloss_delta(r) for r in records]
        avg = mean(values)
        return avg, avg, 1.0 if avg < 0 else 0.0

    rng = random.Random(20260826)
    means: list[float] = []
    for _ in range(max(100, samples)):
        selected = [rng.choice(keys) for _ in keys]
        vals = [value for key in selected for value in clusters[key]]
        means.append(mean(vals))
    means.sort()
    low = means[int(0.025 * (len(means) - 1))]
    high = means[int(0.975 * (len(means) - 1))]
    better = sum(1 for value in means if value < 0) / len(means)
    return low, high, better


def _baseline_for_match(
    source: v24.LeagueSource,
    prior: list[v24.HistoricalMatch],
    target: v24.HistoricalMatch,
) -> Optional[tuple[float, float, float]]:
    # Build all states at midnight of the target date. This excludes every
    # result on that date and therefore cannot leak a later same-day fixture.
    cutoff = target.kickoff.astimezone(timezone.utc)
    states = {half: v3._build_clean_state(source, prior, cutoff, half) for half in (90.0, 180.0, 360.0)}

    class _Row:
        home_team = target.home_team
        away_team = target.away_team
        kickoff = target.kickoff
        match_name = f"{target.home_team} v {target.away_team}"

    forecast = v3._forecast_clean(_Row(), source, states, dynamic=None)  # type: ignore[arg-type]
    if forecast is None:
        return None
    return forecast.home_probability, forecast.draw_probability, forecast.away_probability


def evaluate_league(
    source: v24.LeagueSource,
    matches: list[v24.HistoricalMatch],
    min_train_matches: int = 180,
    max_predictions: Optional[int] = None,
    progress: Optional[ProgressCallback] = None,
) -> list[PredictionRecord]:
    matches = sorted(matches, key=lambda m: m.kickoff)
    if len(matches) <= min_train_matches + 20:
        return []

    # Evaluate the most recent portion while retaining enough training history.
    start_index = min_train_matches
    if max_predictions is not None:
        start_index = max(start_index, len(matches) - max_predictions)
    evaluation = matches[start_index:]
    by_date: dict[str, list[v24.HistoricalMatch]] = defaultdict(list)
    for match in evaluation:
        by_date[match.kickoff.astimezone(timezone.utc).date().isoformat()].append(match)

    # The dynamic challenger is trained only on matches strictly before the
    # first evaluation day. It is then updated after all predictions on a day.
    first_date = min(match.kickoff for match in evaluation)
    pretrain = [m for m in matches if m.kickoff < first_date]
    if len(pretrain) < 80:
        return []
    params, _ = v3._tune_dynamic(pretrain, first_date)
    dynamic_state = v3._dynamic_initial(pretrain, params)
    for match in pretrain:
        v3._dynamic_update(dynamic_state, match)

    records: list[PredictionRecord] = []
    dates = sorted(by_date)
    for date_idx, day in enumerate(dates):
        day_matches = sorted(by_date[day], key=lambda m: (m.home_team, m.away_team))
        cutoff = min(m.kickoff for m in day_matches)
        prior = [m for m in matches if m.kickoff < cutoff]
        # Build three baseline states once for the whole date.
        states = {half: v3._build_clean_state(source, prior, cutoff, half) for half in (90.0, 180.0, 360.0)}
        for target in day_matches:
            class _Row:
                home_team = target.home_team
                away_team = target.away_team
                kickoff = target.kickoff
                match_name = f"{target.home_team} v {target.away_team}"

            baseline_f = v3._forecast_clean(_Row(), source, states, dynamic=None)  # type: ignore[arg-type]
            if baseline_f is None:
                continue
            baseline = (baseline_f.home_probability, baseline_f.draw_probability, baseline_f.away_probability)
            challenger = v3._dynamic_forecast(dynamic_state, target.home_team, target.away_team, target.kickoff)
            records.append(PredictionRecord(
                league_key=source.key,
                kickoff=target.kickoff,
                season=target.season,
                home_team=target.home_team,
                away_team=target.away_team,
                actual_index=_actual_index(target),
                baseline=_clip_probs(baseline),
                challenger=_clip_probs(challenger),
            ))
        # Only now may same-day results enter the challenger state.
        for target in day_matches:
            v3._dynamic_update(dynamic_state, target)
        if date_idx % 20 == 0:
            _emit(progress, 5 + int(85 * (date_idx + 1) / len(dates)), "Walk-forward validation", f"{source.name}: completed {date_idx + 1}/{len(dates)} evaluation dates")
    return records


def run_walk_forward(
    histories: dict[str, list[v24.HistoricalMatch]],
    league_keys: Optional[Iterable[str]] = None,
    max_predictions_per_league: int = 900,
    bootstrap_samples: int = 500,
    progress: Optional[ProgressCallback] = None,
) -> WalkForwardResult:
    source_by_key = {source.key: source for source in v24.LEAGUE_SOURCES}
    keys = [key for key in (league_keys or histories.keys()) if key in histories and key in source_by_key]
    records: list[PredictionRecord] = []
    notes: list[str] = []
    for idx, key in enumerate(keys):
        source = source_by_key[key]
        _emit(progress, int(90 * idx / max(1, len(keys))), "Walk-forward validation", f"Testing {source.name} using only earlier dates")
        league_records = evaluate_league(
            source,
            histories[key],
            min_train_matches=180,
            max_predictions=max_predictions_per_league,
            progress=None,
        )
        records.extend(league_records)
        notes.append(f"{source.name}: {len(league_records)} chronological predictions")

    baseline = _metrics(records, "baseline")
    challenger = _metrics(records, "challenger")
    delta = challenger.log_loss - baseline.log_loss if records else float("nan")
    low, high, better = _cluster_bootstrap_ci(records, samples=bootstrap_samples)
    periods = len({_cluster_key(record) for record in records})

    # This gate is a *forecast* gate only. It never certifies betting edge.
    if len(records) >= 1000 and high < 0 and challenger.brier <= baseline.brier + 0.001 and challenger.ece <= baseline.ece + 0.01:
        grade = "FORECAST_VALIDATED"
    elif len(records) >= 500:
        grade = "RESEARCH_ONLY"
    else:
        grade = "INSUFFICIENT_SAMPLE"

    _emit(progress, 100, "Walk-forward validation", f"Completed {len(records)} predictions · challenger Δ log loss {delta:+.5f}")
    return WalkForwardResult(
        records=records,
        baseline=baseline,
        challenger=challenger,
        delta_log_loss=delta,
        delta_log_loss_ci_low=low,
        delta_log_loss_ci_high=high,
        challenger_better_fraction=better,
        leagues=tuple(keys),
        periods=periods,
        forecast_grade=grade,
        notes=tuple(notes),
    )
