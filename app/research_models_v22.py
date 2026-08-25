from __future__ import annotations

import csv
import io
import math
import statistics
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import requests

import engine
from config import DATA_DIR
from edge_model import power_devig
from engine import CombinedMatch


CACHE_DIR = DATA_DIR / "cache" / "historical_models"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_SECONDS = 12 * 60 * 60
HALF_LIFE_DAYS = 180.0


@dataclass(frozen=True)
class HistoricalMatch:
    kickoff: datetime
    home: str
    away: str
    home_goals: int
    away_goals: int
    market_home: Optional[float] = None
    market_draw: Optional[float] = None
    market_away: Optional[float] = None


@dataclass
class ResearchModelResult:
    match_name: str
    side: str
    selection: str
    market_probability: Optional[float]
    elo_probability: Optional[float]
    poisson_probability: Optional[float]
    football_shift_pp: float
    experimental_probability: Optional[float]
    residual_pp: Optional[float]
    dispersion_pp: Optional[float]
    agreement: str
    history_matches: int
    note: str


@dataclass
class HistoricalValidation:
    samples: int = 0
    market_brier: Optional[float] = None
    elo_brier: Optional[float] = None
    poisson_brier: Optional[float] = None
    blend_brier: Optional[float] = None
    selected_blend_weight: float = 0.0
    holdout_improvement_pct: Optional[float] = None


def _season_start_year(target: date) -> int:
    return target.year if target.month >= 7 else target.year - 1


def _season_code(start_year: int) -> str:
    return f"{str(start_year)[-2:]}{str(start_year + 1)[-2:]}"


def _cache_path(code: str) -> Path:
    return CACHE_DIR / f"football_data_E0_{code}.csv"


def _load_csv(code: str) -> str:
    path = _cache_path(code)
    if path.exists() and time.time() - path.stat().st_mtime <= CACHE_SECONDS:
        try:
            return path.read_text(encoding="utf-8-sig")
        except Exception:
            pass
    url = f"https://www.football-data.co.uk/mmz4281/{code}/E0.csv"
    response = requests.get(
        url,
        timeout=15,
        headers={"User-Agent": "FootballValueBetting/2.2 research-model validation"},
    )
    response.raise_for_status()
    text = response.content.decode("utf-8-sig", errors="replace")
    try:
        path.write_text(text, encoding="utf-8")
    except Exception:
        pass
    return text


def _parse_date(value: str) -> Optional[datetime]:
    text = str(value or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _odds(row: dict[str, str], names: Iterable[str]) -> Optional[float]:
    for name in names:
        try:
            value = float(row.get(name, "") or 0)
            if value > 1.0:
                return value
        except Exception:
            continue
    return None


def parse_history_csv(text: str) -> list[HistoricalMatch]:
    output: list[HistoricalMatch] = []
    for row in csv.DictReader(io.StringIO(text)):
        kickoff = _parse_date(row.get("Date", ""))
        home = str(row.get("HomeTeam", "") or "").strip()
        away = str(row.get("AwayTeam", "") or "").strip()
        try:
            hg = int(float(row.get("FTHG", "")))
            ag = int(float(row.get("FTAG", "")))
        except Exception:
            continue
        if kickoff is None or not home or not away:
            continue
        h = _odds(row, ("PSCH", "PSH", "B365CH", "B365H"))
        d = _odds(row, ("PSCD", "PSD", "B365CD", "B365D"))
        a = _odds(row, ("PSCA", "PSA", "B365CA", "B365A"))
        fair = power_devig(h, d, a) if h and d and a else None
        output.append(
            HistoricalMatch(
                kickoff=kickoff,
                home=home,
                away=away,
                home_goals=hg,
                away_goals=ag,
                market_home=fair[0] if fair else None,
                market_draw=fair[1] if fair else None,
                market_away=fair[2] if fair else None,
            )
        )
    return output


def fetch_recent_epl_history(target: date) -> list[HistoricalMatch]:
    start = _season_start_year(target)
    matches: list[HistoricalMatch] = []
    # Two complete-ish seasons gives time-decay enough history without turning a
    # refresh into a large historical download.
    for season_start in (start - 2, start - 1, start):
        code = _season_code(season_start)
        try:
            matches.extend(parse_history_csv(_load_csv(code)))
        except Exception:
            continue
    unique = {(m.kickoff.date(), engine.normalise_text(m.home), engine.normalise_text(m.away)): m for m in matches}
    return sorted(unique.values(), key=lambda m: m.kickoff)


def _poisson_array(lam: float, max_goals: int = 10) -> list[float]:
    lam = max(0.08, min(6.0, float(lam)))
    values = [math.exp(-lam)]
    for goals in range(1, max_goals + 1):
        values.append(values[-1] * lam / goals)
    total = sum(values)
    return [v / total for v in values]


def _score_probs(lambda_home: float, lambda_away: float, rho: float = -0.08) -> tuple[float, float, float]:
    hp = _poisson_array(lambda_home)
    ap = _poisson_array(lambda_away)
    h = d = a = 0.0
    mass = 0.0
    for hg, ph in enumerate(hp):
        for ag, pa in enumerate(ap):
            p = ph * pa
            # Small Dixon-Coles-style low-score correlation correction.  This
            # is deliberately bounded; the attack/defence rates do the heavy
            # lifting and the component remains experimental until validated.
            if hg == 0 and ag == 0:
                p *= max(0.75, 1.0 - lambda_home * lambda_away * rho)
            elif hg == 0 and ag == 1:
                p *= max(0.75, 1.0 + lambda_home * rho)
            elif hg == 1 and ag == 0:
                p *= max(0.75, 1.0 + lambda_away * rho)
            elif hg == 1 and ag == 1:
                p *= max(0.75, 1.0 - rho)
            mass += p
            if hg > ag:
                h += p
            elif hg == ag:
                d += p
            else:
                a += p
    if mass <= 0:
        return 1 / 3, 1 / 3, 1 / 3
    return h / mass, d / mass, a / mass


def _decay_weight(kickoff: datetime, cutoff: datetime, half_life_days: float = HALF_LIFE_DAYS) -> float:
    age_days = max(0.0, (cutoff - kickoff).total_seconds() / 86400.0)
    return math.exp(-math.log(2.0) * age_days / half_life_days)


def time_decayed_poisson(
    history: list[HistoricalMatch], home_team: str, away_team: str, cutoff: datetime
) -> Optional[tuple[float, float, float]]:
    prior = [m for m in history if m.kickoff < cutoff]
    if len(prior) < 80:
        return None

    league_for = league_against = league_weight = 0.0
    for m in prior:
        w = _decay_weight(m.kickoff, cutoff)
        league_for += w * (m.home_goals + m.away_goals)
        league_against += 2.0 * w
        league_weight += w
    if league_weight <= 0 or league_against <= 0:
        return None
    avg_goal = league_for / league_against

    def team_rates(team: str) -> tuple[float, float, float]:
        attack_num = defence_num = weight = 0.0
        key = engine.normalise_text(team)
        for m in prior:
            w = _decay_weight(m.kickoff, cutoff)
            if engine.normalise_text(m.home) == key:
                attack_num += w * m.home_goals
                defence_num += w * m.away_goals
                weight += w
            elif engine.normalise_text(m.away) == key:
                attack_num += w * m.away_goals
                defence_num += w * m.home_goals
                weight += w
        # Empirical-Bayes shrinkage towards league average prevents promoted or
        # sparsely observed teams from producing absurd rates.
        prior_weight = 5.0
        attack = (attack_num + prior_weight * avg_goal) / (weight + prior_weight)
        defence = (defence_num + prior_weight * avg_goal) / (weight + prior_weight)
        return attack / avg_goal, defence / avg_goal, weight

    h_att, h_def, h_weight = team_rates(home_team)
    a_att, a_def, a_weight = team_rates(away_team)
    if min(h_weight, a_weight) < 2.0:
        return None

    # Estimate time-decayed home advantage from the same pre-cutoff data.
    home_goals = away_goals = weight = 0.0
    for m in prior:
        w = _decay_weight(m.kickoff, cutoff)
        home_goals += w * m.home_goals
        away_goals += w * m.away_goals
        weight += w
    home_avg = home_goals / weight if weight else avg_goal
    away_avg = away_goals / weight if weight else avg_goal
    lambda_home = max(0.15, home_avg * h_att * a_def)
    lambda_away = max(0.15, away_avg * a_att * h_def)
    return _score_probs(lambda_home, lambda_away)


def elo_probabilities(
    history: list[HistoricalMatch], home_team: str, away_team: str, cutoff: datetime
) -> Optional[tuple[float, float, float]]:
    prior = [m for m in history if m.kickoff < cutoff]
    if len(prior) < 80:
        return None
    ratings: dict[str, float] = {}
    total_home = total_away = 0.0
    for m in prior:
        hkey = engine.normalise_text(m.home)
        akey = engine.normalise_text(m.away)
        rh = ratings.get(hkey, 1500.0)
        ra = ratings.get(akey, 1500.0)
        expected = 1.0 / (1.0 + 10.0 ** (-(rh + 65.0 - ra) / 400.0))
        actual = 1.0 if m.home_goals > m.away_goals else 0.0 if m.home_goals < m.away_goals else 0.5
        margin = abs(m.home_goals - m.away_goals)
        multiplier = 1.0 + 0.35 * math.log1p(margin)
        change = 22.0 * multiplier * (actual - expected)
        ratings[hkey] = rh + change
        ratings[akey] = ra - change
        total_home += m.home_goals
        total_away += m.away_goals

    hkey = engine.normalise_text(home_team)
    akey = engine.normalise_text(away_team)
    if hkey not in ratings or akey not in ratings:
        return None
    avg_home = total_home / len(prior)
    avg_away = total_away / len(prior)
    diff = ratings[hkey] + 65.0 - ratings[akey]
    scale = math.exp(max(-0.8, min(0.8, diff / 800.0)))
    lambda_home = max(0.15, avg_home * scale)
    lambda_away = max(0.15, avg_away / scale)
    return _score_probs(lambda_home, lambda_away, rho=0.0)


def _football_shift(intel) -> float:
    """Small research-only context shift from xG quality and XI continuity."""
    if intel is None:
        return 0.0
    shift = 0.0
    try:
        shift += max(-0.45, min(0.45, float(intel.recent_form_rating) * 0.35))
        shift += max(-0.30, min(0.30, float(intel.xi_rating) * 0.25))
        shift += max(-0.20, min(0.20, float(intel.rest_rating) * 0.15))
    except Exception:
        return 0.0

    # Lineup continuity: reward a side whose likely XI has both high start
    # probability and recent-start continuity.  This is kept tiny until the
    # stored research data proves incremental value.
    def continuity(team) -> Optional[float]:
        if team is None or not getattr(team, "expected_xi", None):
            return None
        vals = []
        for player in team.expected_xi:
            vals.append(float(player.start_probability) * float(player.availability) * min(1.0, float(player.recent_starts) / 3.0))
        return statistics.mean(vals) if vals else None

    hc = continuity(getattr(intel, "home", None))
    ac = continuity(getattr(intel, "away", None))
    if hc is not None and ac is not None:
        shift += max(-0.25, min(0.25, (hc - ac) * 0.8))
    return max(-0.75, min(0.75, shift))


def _side_values(probs: tuple[float, float, float], side: str) -> float:
    return probs[{"HOME": 0, "DRAW": 1, "AWAY": 2}[side]]


def build_research_results(
    rows: list[CombinedMatch],
    history: list[HistoricalMatch],
    intelligence_by_match: Optional[dict] = None,
) -> list[ResearchModelResult]:
    intelligence_by_match = intelligence_by_match or {}
    output: list[ResearchModelResult] = []
    for row in rows:
        league = str(getattr(row, "league", "") or "").lower()
        country = str(getattr(row, "country", "") or "").lower()
        if "premier league" not in league or (country and "england" not in country and "united kingdom" not in country):
            continue
        cutoff = row.kickoff.astimezone(timezone.utc)
        elo = elo_probabilities(history, row.home_team, row.away_team, cutoff)
        dc = time_decayed_poisson(history, row.home_team, row.away_team, cutoff)
        intel = intelligence_by_match.get(row.match_name)
        home_context_shift = _football_shift(intel)
        for side in ("HOME", "DRAW", "AWAY"):
            edge = getattr(row, "edge_outcomes", {}).get(side)
            market = getattr(edge, "model_probability", None) if edge else None
            market = float(market) if market is not None else None
            ep = _side_values(elo, side) if elo else None
            dp = _side_values(dc, side) if dc else None
            components = [p for p in (ep, dp) if p is not None]
            residual = None
            dispersion = None
            experimental = None
            context_shift = 0.0 if side == "DRAW" else home_context_shift * (1.0 if side == "HOME" else -1.0)
            if market is not None and components:
                model_mean = statistics.mean(components)
                residual = (model_mean - market) * 100.0
                dispersion = statistics.pstdev([market] + components) * 100.0 if len(components) >= 2 else abs(residual)
                # Research-only blend: 20% historical-model residual plus a tiny
                # context shift, both capped. It is displayed/stored but does not
                # alter V2.1's primary robust signal until validation earns that.
                move_pp = max(-1.25, min(1.25, residual * 0.20 + context_shift))
                experimental = max(0.01, min(0.98, market + move_pp / 100.0))
                if len(components) >= 2 and all((p - market) * residual > 0 for p in components) and abs(residual) >= 0.75:
                    agreement = "MODELS AGREE"
                elif len(components) >= 2 and (ep - market) * (dp - market) < 0:
                    agreement = "MODELS DISAGREE"
                else:
                    agreement = "WEAK/MIXED"
            else:
                agreement = "INSUFFICIENT DATA"
            selection = row.home_team if side == "HOME" else row.away_team if side == "AWAY" else "Draw"
            note = (
                "Experimental only: time-decayed Elo and low-score-corrected Poisson are compared with the live independent-market probability. "
                "The residual does not create a green betting signal until out-of-sample/CLV validation supports it."
            )
            output.append(
                ResearchModelResult(
                    match_name=row.match_name,
                    side=side,
                    selection=selection,
                    market_probability=market,
                    elo_probability=ep,
                    poisson_probability=dp,
                    football_shift_pp=context_shift,
                    experimental_probability=experimental,
                    residual_pp=residual,
                    dispersion_pp=dispersion,
                    agreement=agreement,
                    history_matches=len([m for m in history if m.kickoff < cutoff]),
                    note=note,
                )
            )
    output.sort(key=lambda r: abs(r.residual_pp or 0.0), reverse=True)
    return output


def _brier(probabilities: tuple[float, float, float], result: int) -> float:
    return sum((p - (1.0 if i == result else 0.0)) ** 2 for i, p in enumerate(probabilities))


def validate_historical_models(history: list[HistoricalMatch], max_samples: int = 180) -> HistoricalValidation:
    eligible = [m for m in history if m.market_home is not None and m.market_draw is not None and m.market_away is not None]
    if len(eligible) < 100:
        return HistoricalValidation(samples=0)
    targets = eligible[-max_samples:]
    records = []
    for target in targets:
        elo = elo_probabilities(history, target.home, target.away, target.kickoff)
        dc = time_decayed_poisson(history, target.home, target.away, target.kickoff)
        if elo is None or dc is None:
            continue
        market = (float(target.market_home), float(target.market_draw), float(target.market_away))
        result = 0 if target.home_goals > target.away_goals else 2 if target.home_goals < target.away_goals else 1
        records.append((market, elo, dc, result))
    if len(records) < 60:
        return HistoricalValidation(samples=len(records))

    split = max(30, int(len(records) * 0.70))
    train, holdout = records[:split], records[split:]
    weights = (0.0, 0.05, 0.10, 0.15, 0.20, 0.25)

    def blend(market, elo, dc, weight):
        model = tuple((elo[i] + dc[i]) / 2.0 for i in range(3))
        values = tuple((1.0 - weight) * market[i] + weight * model[i] for i in range(3))
        total = sum(values)
        return tuple(v / total for v in values)

    def avg_brier(items, kind, weight=0.0):
        scores = []
        for market, elo, dc, result in items:
            probs = market if kind == "market" else elo if kind == "elo" else dc if kind == "dc" else blend(market, elo, dc, weight)
            scores.append(_brier(probs, result))
        return statistics.mean(scores)

    best_weight = min(weights, key=lambda w: avg_brier(train, "blend", w))
    market_brier = avg_brier(holdout, "market")
    elo_brier = avg_brier(holdout, "elo")
    dc_brier = avg_brier(holdout, "dc")
    blend_brier = avg_brier(holdout, "blend", best_weight)
    improvement = (market_brier - blend_brier) / market_brier * 100.0 if market_brier > 0 else None
    return HistoricalValidation(
        samples=len(holdout),
        market_brier=market_brier,
        elo_brier=elo_brier,
        poisson_brier=dc_brier,
        blend_brier=blend_brier,
        selected_blend_weight=best_weight,
        holdout_improvement_pct=improvement,
    )
