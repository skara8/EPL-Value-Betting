from __future__ import annotations

"""V3 football probability layer.

The production probability remains bookmaker-independent.  V3 fixes the V2.4
component-count/fallback problem and adds a chronologically tuned dynamic-state
challenger.  The challenger is deliberately *research only* until the
walk-forward engine demonstrates an out-of-sample improvement.
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Callable, Optional

import engine
import independent_model_v24 as v24
from edge_model import power_devig
from engine import CombinedMatch

ProgressCallback = Callable[[int, str, str], None]
SIDES = ("HOME", "DRAW", "AWAY")


@dataclass
class V3IndependentForecast:
    match_name: str
    league_key: str
    league_name: str
    home_team_history: str
    away_team_history: str
    home_probability: float
    draw_probability: float
    away_probability: float
    stress_home: float
    stress_draw: float
    stress_away: float
    fair_home_odds: float
    fair_draw_odds: float
    fair_away_odds: float
    dc_home: float
    dc_draw: float
    dc_away: float
    elo_home: float
    elo_draw: float
    elo_away: float
    short_home: Optional[float]
    short_draw: Optional[float]
    short_away: Optional[float]
    long_home: Optional[float]
    long_draw: Optional[float]
    long_away: Optional[float]
    lambda_home: float
    lambda_away: float
    model_spread_pp: float
    history_matches: int
    home_history_matches: int
    away_history_matches: int
    confidence: str
    components: tuple[str, ...]
    challenger_home: Optional[float] = None
    challenger_draw: Optional[float] = None
    challenger_away: Optional[float] = None
    challenger_eta: Optional[float] = None
    challenger_annual_shrink: Optional[float] = None
    challenger_validation_logloss: Optional[float] = None
    challenger_gap_pp: Optional[float] = None


@dataclass
class V3ModelResult:
    forecasts: dict[str, V3IndependentForecast]
    supported_leagues: tuple[str, ...]
    unavailable_leagues: tuple[str, ...]
    notes: tuple[str, ...]
    downloaded_files: int = 0
    cache_hits: int = 0
    challenger_leagues: int = 0


@dataclass
class _VenueStats:
    home_gf: float = 0.0
    home_ga: float = 0.0
    home_w: float = 0.0
    away_gf: float = 0.0
    away_ga: float = 0.0
    away_w: float = 0.0
    matches: int = 0


@dataclass
class _CleanState:
    source: v24.LeagueSource
    cutoff: datetime
    half_life_days: float
    league_home_goals: float
    league_away_goals: float
    draw_rate: float
    teams: dict[str, _VenueStats]
    ratings: dict[str, float]
    team_names: tuple[str, ...]
    history_matches: int


def _emit(cb: Optional[ProgressCallback], percent: int, stage: str, detail: str) -> None:
    if cb:
        cb(max(0, min(100, int(percent))), stage, detail)


def _normalise(values) -> tuple[float, float, float]:
    vals = [max(1e-12, float(x)) for x in values]
    total = sum(vals)
    return vals[0] / total, vals[1] / total, vals[2] / total


def _build_clean_state(
    source: v24.LeagueSource,
    matches: list[v24.HistoricalMatch],
    cutoff: datetime,
    half_life_days: float,
) -> _CleanState:
    """V2.4-compatible state with two correctness fixes.

    * every unseen Elo team starts at the same 1500 prior (no order effect);
    * no bookmaker information enters any state.
    """
    cutoff = cutoff.astimezone(timezone.utc)
    relevant: list[tuple[v24.HistoricalMatch, float]] = []
    teams: dict[str, _VenueStats] = defaultdict(_VenueStats)
    total_w = home_goals = away_goals = draws = 0.0

    for match in matches:
        if match.kickoff >= cutoff:
            break
        age = (cutoff - match.kickoff).total_seconds() / 86400.0
        if age < 0 or age > 1300.0:
            continue
        weight = 0.5 ** (age / max(30.0, half_life_days))
        relevant.append((match, weight))
        total_w += weight
        home_goals += match.home_goals * weight
        away_goals += match.away_goals * weight
        draws += (1.0 if match.home_goals == match.away_goals else 0.0) * weight

        hs = teams[match.home_team]
        hs.home_gf += match.home_goals * weight
        hs.home_ga += match.away_goals * weight
        hs.home_w += weight
        hs.matches += 1

        ass = teams[match.away_team]
        ass.away_gf += match.away_goals * weight
        ass.away_ga += match.home_goals * weight
        ass.away_w += weight
        ass.matches += 1

    league_home = home_goals / total_w if total_w else 1.45
    league_away = away_goals / total_w if total_w else 1.15
    draw_rate = draws / total_w if total_w else 0.26
    draw_rate = max(0.17, min(0.34, draw_rate))

    ratings: dict[str, float] = {}
    last_season: Optional[str] = None
    for match, _ in relevant:
        if last_season is not None and match.season != last_season:
            for team in list(ratings):
                ratings[team] = 1500.0 + (ratings[team] - 1500.0) * 0.82
        last_season = match.season
        rh = ratings.setdefault(match.home_team, 1500.0)
        ra = ratings.setdefault(match.away_team, 1500.0)
        expected = 1.0 / (1.0 + 10.0 ** ((ra - (rh + 60.0)) / 400.0))
        score = 1.0 if match.home_goals > match.away_goals else 0.0 if match.home_goals < match.away_goals else 0.5
        gd = abs(match.home_goals - match.away_goals)
        k = 22.0 * (1.0 + 0.12 * min(3, gd))
        delta = k * (score - expected)
        ratings[match.home_team] = rh + delta
        ratings[match.away_team] = ra - delta

    return _CleanState(
        source=source,
        cutoff=cutoff,
        half_life_days=half_life_days,
        league_home_goals=league_home,
        league_away_goals=league_away,
        draw_rate=draw_rate,
        teams=dict(teams),
        ratings=ratings,
        team_names=tuple(teams),
        history_matches=len(relevant),
    )


def _resolve_team(name: str, state: _CleanState) -> Optional[str]:
    # V2.4's history resolver is already independent of prices and contains the
    # project's explicit club aliases.  Reuse it by presenting the same fields.
    return v24._resolve_team(name, state)  # type: ignore[arg-type]


def _team_lambdas(state: _CleanState, home: str, away: str, prior_matches: float = 7.0) -> Optional[tuple[float, float]]:
    hs = state.teams.get(home)
    ass = state.teams.get(away)
    if not hs or not ass or hs.matches < 2 or ass.matches < 2:
        return None
    lh, la = state.league_home_goals, state.league_away_goals
    home_gf = (hs.home_gf + prior_matches * lh) / (hs.home_w + prior_matches)
    home_ga = (hs.home_ga + prior_matches * la) / (hs.home_w + prior_matches)
    away_gf = (ass.away_gf + prior_matches * la) / (ass.away_w + prior_matches)
    away_ga = (ass.away_ga + prior_matches * lh) / (ass.away_w + prior_matches)
    lambda_home = lh * (home_gf / max(0.2, lh)) * (away_ga / max(0.2, lh))
    lambda_away = la * (away_gf / max(0.2, la)) * (home_ga / max(0.2, la))
    return max(0.20, min(4.5, lambda_home)), max(0.15, min(4.0, lambda_away))


def _elo_probabilities(state: _CleanState, home: str, away: str) -> tuple[float, float, float]:
    rh = state.ratings.get(home, 1500.0)
    ra = state.ratings.get(away, 1500.0)
    diff = (rh + 60.0) - ra
    decisive_home = 1.0 / (1.0 + 10.0 ** (-diff / 400.0))
    draw = state.draw_rate * math.exp(-abs(diff) / 950.0)
    draw = max(0.14, min(0.34, draw))
    return _normalise(((1.0 - draw) * decisive_home, draw, (1.0 - draw) * (1.0 - decisive_home)))


# ---------------------------------------------------------------------------
# Research-only dynamic-state challenger
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DynamicParams:
    eta: float
    annual_shrink: float
    rho: float = -0.08


@dataclass
class DynamicState:
    attack: dict[str, float]
    defence: dict[str, float]
    last_seen: dict[str, datetime]
    mu_home: float
    mu_away: float
    params: DynamicParams


def _shrink(value: float, days: float, annual_shrink: float) -> float:
    if days <= 0:
        return value
    factor = max(0.0, min(1.0, annual_shrink)) ** (days / 365.25)
    return value * factor


def _touch(state: DynamicState, team: str, at: datetime) -> tuple[float, float]:
    a = state.attack.get(team, 0.0)
    d = state.defence.get(team, 0.0)
    prior = state.last_seen.get(team)
    if prior is not None:
        days = max(0.0, (at - prior).total_seconds() / 86400.0)
        a = _shrink(a, days, state.params.annual_shrink)
        d = _shrink(d, days, state.params.annual_shrink)
    state.attack[team] = a
    state.defence[team] = d
    state.last_seen[team] = at
    return a, d


def _dynamic_lambdas(state: DynamicState, home: str, away: str, at: datetime) -> tuple[float, float]:
    ah, dh = _touch(state, home, at)
    aa, da = _touch(state, away, at)
    lh = math.exp(state.mu_home + ah - da)
    la = math.exp(state.mu_away + aa - dh)
    return max(0.12, min(5.0, lh)), max(0.10, min(4.5, la))


def _dynamic_update(state: DynamicState, match: v24.HistoricalMatch) -> tuple[float, float, tuple[float, float, float]]:
    lh, la = _dynamic_lambdas(state, match.home_team, match.away_team, match.kickoff)
    probs = v24.dixon_coles_probabilities(lh, la, rho=state.params.rho)
    err_h = float(match.home_goals) - lh
    err_a = float(match.away_goals) - la
    eta = state.params.eta
    state.attack[match.home_team] = max(-1.4, min(1.4, state.attack.get(match.home_team, 0.0) + eta * err_h))
    state.defence[match.away_team] = max(-1.4, min(1.4, state.defence.get(match.away_team, 0.0) - eta * err_h))
    state.attack[match.away_team] = max(-1.4, min(1.4, state.attack.get(match.away_team, 0.0) + eta * err_a))
    state.defence[match.home_team] = max(-1.4, min(1.4, state.defence.get(match.home_team, 0.0) - eta * err_a))
    return lh, la, probs


def _dynamic_initial(matches: list[v24.HistoricalMatch], params: DynamicParams) -> DynamicState:
    sample = matches[: max(30, min(len(matches), 380))]
    if sample:
        home_rate = sum(m.home_goals for m in sample) / len(sample)
        away_rate = sum(m.away_goals for m in sample) / len(sample)
    else:
        home_rate, away_rate = 1.45, 1.15
    return DynamicState({}, {}, {}, math.log(max(0.25, home_rate)), math.log(max(0.20, away_rate)), params)


def _outcome_index(match: v24.HistoricalMatch) -> int:
    return 0 if match.home_goals > match.away_goals else 1 if match.home_goals == match.away_goals else 2


def _tune_dynamic(matches: list[v24.HistoricalMatch], cutoff: datetime) -> tuple[DynamicParams, Optional[float]]:
    usable = [m for m in matches if m.kickoff < cutoff]
    if len(usable) < 120:
        return DynamicParams(0.025, 0.90), None
    split = max(80, int(len(usable) * 0.72))
    train, validation = usable[:split], usable[split:]
    candidates = [
        DynamicParams(eta, shrink)
        for eta in (0.015, 0.025, 0.040)
        for shrink in (0.80, 0.90, 0.97)
    ]
    best_params = candidates[0]
    best_loss = float("inf")
    for params in candidates:
        state = _dynamic_initial(train, params)
        for match in train:
            _dynamic_update(state, match)
        loss = 0.0
        n = 0
        for match in validation:
            _, _, probs = _dynamic_update(state, match)
            p = max(1e-12, probs[_outcome_index(match)])
            loss -= math.log(p)
            n += 1
        score = loss / max(1, n)
        if score < best_loss:
            best_loss = score
            best_params = params
    return best_params, best_loss if math.isfinite(best_loss) else None


def _fit_dynamic_live(
    matches: list[v24.HistoricalMatch], cutoff: datetime
) -> tuple[DynamicState, DynamicParams, Optional[float]]:
    params, validation_loss = _tune_dynamic(matches, cutoff)
    usable = [m for m in matches if m.kickoff < cutoff]
    state = _dynamic_initial(usable, params)
    for match in usable:
        _dynamic_update(state, match)
    return state, params, validation_loss


def _dynamic_forecast(state: DynamicState, home: str, away: str, kickoff: datetime) -> tuple[float, float, float]:
    # Work on a shallow state copy because evaluating a future fixture should
    # not mutate the fitted live state used by another fixture.
    clone = DynamicState(
        dict(state.attack), dict(state.defence), dict(state.last_seen),
        state.mu_home, state.mu_away, state.params,
    )
    lh, la = _dynamic_lambdas(clone, home, away, kickoff.astimezone(timezone.utc))
    return v24.dixon_coles_probabilities(lh, la, rho=state.params.rho)


def _forecast_clean(
    row: CombinedMatch,
    source: v24.LeagueSource,
    states: dict[float, _CleanState],
    dynamic: Optional[tuple[DynamicState, DynamicParams, Optional[float]]] = None,
) -> Optional[V3IndependentForecast]:
    main = states[180.0]
    home = _resolve_team(row.home_team, main)
    away = _resolve_team(row.away_team, main)
    if not home or not away or home == away:
        return None

    main_lambdas = _team_lambdas(main, home, away)
    if main_lambdas is None:
        return None
    dc = v24.dixon_coles_probabilities(*main_lambdas)
    elo = _elo_probabilities(main, home, away)
    components: list[tuple[str, tuple[float, float, float]]] = [("DIXON-COLES", dc), ("ELO", elo)]

    short: Optional[tuple[float, float, float]] = None
    short_state = states[90.0]
    sh = _resolve_team(row.home_team, short_state)
    sa = _resolve_team(row.away_team, short_state)
    if sh and sa:
        lambdas = _team_lambdas(short_state, sh, sa)
        if lambdas is not None:
            short = v24.dixon_coles_probabilities(*lambdas)
            components.append(("SHORT-DECAY", short))

    long: Optional[tuple[float, float, float]] = None
    long_state = states[360.0]
    lh_name = _resolve_team(row.home_team, long_state)
    la_name = _resolve_team(row.away_team, long_state)
    if lh_name and la_name:
        lambdas = _team_lambdas(long_state, lh_name, la_name)
        if lambdas is not None:
            long = v24.dixon_coles_probabilities(*lambdas)
            components.append(("LONG-DECAY", long))

    # Missing variants remain missing.  V2.4 copied the main DC prediction and
    # counted it as another vote; V3 explicitly forbids that behaviour.
    central = _normalise(tuple(mean(p[i] for _, p in components) for i in range(3)))
    stress = tuple(min(p[i] for _, p in components) for i in range(3))
    spread = max(
        (max(p[i] for _, p in components) - min(p[i] for _, p in components)) * 100.0
        for i in range(3)
    )
    h_count = main.teams[home].matches
    a_count = main.teams[away].matches
    min_count = min(h_count, a_count)
    unique_count = len(components)
    confidence = (
        "HIGH" if min_count >= 30 and unique_count >= 3 and spread <= 5.0
        else "MEDIUM" if min_count >= 12 and unique_count >= 2 and spread <= 8.0
        else "LOW"
    )

    challenger = None
    eta = shrink = val_loss = gap = None
    if dynamic is not None:
        dyn_state, params, val_loss = dynamic
        # Dynamic team keys use exactly the same canonical history identifiers.
        if home in dyn_state.attack or home in dyn_state.last_seen:
            if away in dyn_state.attack or away in dyn_state.last_seen:
                challenger = _dynamic_forecast(dyn_state, home, away, row.kickoff)
                eta, shrink = params.eta, params.annual_shrink
                gap = max(abs(challenger[i] - central[i]) * 100.0 for i in range(3))

    return V3IndependentForecast(
        match_name=row.match_name,
        league_key=source.key,
        league_name=source.name,
        home_team_history=home,
        away_team_history=away,
        home_probability=central[0], draw_probability=central[1], away_probability=central[2],
        stress_home=stress[0], stress_draw=stress[1], stress_away=stress[2],
        fair_home_odds=1.0 / central[0], fair_draw_odds=1.0 / central[1], fair_away_odds=1.0 / central[2],
        dc_home=dc[0], dc_draw=dc[1], dc_away=dc[2],
        elo_home=elo[0], elo_draw=elo[1], elo_away=elo[2],
        short_home=short[0] if short else None,
        short_draw=short[1] if short else None,
        short_away=short[2] if short else None,
        long_home=long[0] if long else None,
        long_draw=long[1] if long else None,
        long_away=long[2] if long else None,
        lambda_home=main_lambdas[0], lambda_away=main_lambdas[1],
        model_spread_pp=spread,
        history_matches=main.history_matches,
        home_history_matches=h_count,
        away_history_matches=a_count,
        confidence=confidence,
        components=tuple(name for name, _ in components),
        challenger_home=challenger[0] if challenger else None,
        challenger_draw=challenger[1] if challenger else None,
        challenger_away=challenger[2] if challenger else None,
        challenger_eta=eta,
        challenger_annual_shrink=shrink,
        challenger_validation_logloss=val_loss,
        challenger_gap_pp=gap,
    )


def build_v3_forecasts(
    rows: list[CombinedMatch],
    histories: dict[str, list[v24.HistoricalMatch]],
    progress: Optional[ProgressCallback] = None,
) -> dict[str, V3IndependentForecast]:
    grouped: dict[str, list[CombinedMatch]] = defaultdict(list)
    source_by_key = {s.key: s for s in v24.LEAGUE_SOURCES}
    for row in rows:
        source = v24.resolve_league_source(str(getattr(row, "league", "") or ""))
        if source and source.key in histories:
            grouped[source.key].append(row)

    total_rows = sum(len(items) for items in grouped.values())
    done = 0
    forecasts: dict[str, V3IndependentForecast] = {}
    for key, league_rows in grouped.items():
        source = source_by_key[key]
        history = histories[key]
        if not history:
            continue
        cutoff = min(row.kickoff.astimezone(timezone.utc) for row in league_rows)
        states = {half: _build_clean_state(source, history, cutoff, half) for half in (90.0, 180.0, 360.0)}
        dynamic = _fit_dynamic_live(history, cutoff) if len(history) >= 80 else None
        for row in league_rows:
            forecast = _forecast_clean(row, source, states, dynamic)
            if forecast is not None:
                forecasts[row.match_name] = forecast
            done += 1
            if done == total_rows or done % 10 == 0:
                _emit(progress, 80 + int(6 * done / max(1, total_rows)), "V3 independent model", f"Priced {done}/{total_rows} supported fixtures; dynamic challenger remains research-only")
    return forecasts


def _market_reference_triplet(row: CombinedMatch) -> Optional[tuple[float, float, float]]:
    values = (
        getattr(row, "model_fair_home", None),
        getattr(row, "model_fair_draw", None),
        getattr(row, "model_fair_away", None),
    )
    if all(v is not None for v in values):
        return float(values[0]), float(values[1]), float(values[2])
    return None


def apply_v3_forecasts(rows: list[CombinedMatch], forecasts: dict[str, V3IndependentForecast], min_ev_pct: float = 4.0) -> list[CombinedMatch]:
    for row in rows:
        market_ref = _market_reference_triplet(row)
        row.market_reference_home = market_ref[0] if market_ref else None
        row.market_reference_draw = market_ref[1] if market_ref else None
        row.market_reference_away = market_ref[2] if market_ref else None

        forecast = forecasts.get(row.match_name)
        outcomes = getattr(row, "edge_outcomes", {})
        if forecast is None:
            row.model_fair_home = row.model_fair_draw = row.model_fair_away = None
            row.edge_source_names = tuple()
            row.edge_source_count = 0
            row.edge_disagreement_pp = None
            row.edge_best_selection = "—"
            row.edge_best_ev_pct = None
            row.edge_best_conservative_ev_pct = None
            row.edge_signal = "NO INDEPENDENT MODEL"
            row.edge_confidence = "LOW"
            row.reference_tier = "INDEPENDENT MODEL UNAVAILABLE"
            for side in SIDES:
                edge = outcomes.get(side)
                if edge is None:
                    continue
                edge.model_probability = None
                edge.conservative_probability = None
                edge.model_fair_odds = None
                edge.price_edge_pp = None
                edge.model_ev_pct = None
                edge.conservative_ev_pct = None
                edge.required_odds_for_threshold = None
                edge.source_count = 0
                edge.confidence = "LOW"
                edge.signal = "NO INDEPENDENT MODEL"
            continue

        row.independent_v3 = forecast
        # Preserve the old attribute for inherited V2.4 UI/storage compatibility.
        row.independent_v24 = forecast
        row.model_fair_home = forecast.home_probability
        row.model_fair_draw = forecast.draw_probability
        row.model_fair_away = forecast.away_probability
        row.edge_source_names = forecast.components
        row.edge_source_count = len(forecast.components)
        row.edge_disagreement_pp = forecast.model_spread_pp
        row.reference_tier = "V3 — INDEPENDENT FOOTBALL BASELINE"
        row.reference_execution_exclusions = tuple()
        row.v3_challenger_home = forecast.challenger_home
        row.v3_challenger_draw = forecast.challenger_draw
        row.v3_challenger_away = forecast.challenger_away

        probs = (forecast.home_probability, forecast.draw_probability, forecast.away_probability)
        stress = (forecast.stress_home, forecast.stress_draw, forecast.stress_away)
        sb = (row.sb_home, row.sb_draw, row.sb_away)
        sb_devig = (
            getattr(row, "sb_devig_power_home", None),
            getattr(row, "sb_devig_power_draw", None),
            getattr(row, "sb_devig_power_away", None),
        )
        for i, side in enumerate(SIDES):
            edge = outcomes.get(side)
            if edge is None:
                continue
            odds = sb[i]
            p = probs[i]
            edge.model_probability = p
            # Kept solely for inherited tables.  V3 does not call this a
            # confidence bound and does not use it to certify an edge.
            edge.conservative_probability = stress[i]
            edge.model_fair_odds = 1.0 / p
            break_even = 1.0 / odds if odds is not None and odds > 1 else None
            edge.price_edge_pp = (p - break_even) * 100.0 if break_even is not None else None
            edge.sportsbet_residual_pp = (p - sb_devig[i]) * 100.0 if sb_devig[i] is not None else None
            edge.model_ev_pct = engine.expected_value_pct(p, odds)
            edge.conservative_ev_pct = engine.expected_value_pct(stress[i], odds)
            edge.required_odds_for_threshold = (1.0 + min_ev_pct / 100.0) / p
            edge.external_disagreement_pp = forecast.model_spread_pp
            edge.source_count = len(forecast.components)
            edge.confidence = forecast.confidence
            if edge.model_ev_pct is None:
                edge.signal = "NO PRICE"
            elif edge.model_ev_pct >= min_ev_pct:
                edge.signal = "RESEARCH +EV — UNVALIDATED"
            elif edge.model_ev_pct > 0:
                edge.signal = "POSITIVE — BELOW THRESHOLD"
            else:
                edge.signal = "NEGATIVE EV"

        valid = [outcomes.get(side) for side in SIDES if outcomes.get(side) is not None]
        best = max(valid, key=lambda e: e.model_ev_pct if e.model_ev_pct is not None else -99999.0, default=None)
        if best is not None:
            row.edge_best_selection = best.side
            row.edge_best_ev_pct = best.model_ev_pct
            row.edge_best_conservative_ev_pct = best.conservative_ev_pct
            row.edge_signal = best.signal
            row.edge_confidence = best.confidence
    return rows


def build_and_apply_v3_model(
    rows: list[CombinedMatch],
    min_ev_pct: float = 4.0,
    progress: Optional[ProgressCallback] = None,
) -> V3ModelResult:
    histories, initial = v24.load_histories_for_rows(rows, progress=progress)
    forecasts = build_v3_forecasts(rows, histories, progress=progress)
    apply_v3_forecasts(rows, forecasts, min_ev_pct=min_ev_pct)
    challenger_leagues = len({f.league_key for f in forecasts.values() if f.challenger_home is not None})
    notes = list(initial.notes)
    notes.append(
        "V3 rule: missing short/long variants stay missing and do not increase component_count; "
        "the dynamic-state model is a challenger only until walk-forward validation passes."
    )
    return V3ModelResult(
        forecasts=forecasts,
        supported_leagues=initial.supported_leagues,
        unavailable_leagues=initial.unavailable_leagues,
        notes=tuple(notes),
        downloaded_files=initial.downloaded_files,
        cache_hits=initial.cache_hits,
        challenger_leagues=challenger_leagues,
    )
