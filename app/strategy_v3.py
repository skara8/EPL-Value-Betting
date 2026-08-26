from __future__ import annotations

from dataclasses import dataclass
from statistics import NormalDist
from typing import Optional

from engine import CombinedMatch
from model_v3 import V3Forecast

SIDES = ("HOME", "DRAW", "AWAY")


@dataclass
class V3Decision:
    match_name: str
    league: str
    side: str
    selection: str
    quote_source: str
    quote_odds: float
    quote_match_confidence: float
    model_probability: float
    lower_probability: float
    probability_stdev: float
    fair_odds: float
    model_ev_pct: float
    lower_ev_pct: float
    probability_ev_positive: float
    component_spread_pp: float
    component_count: int
    confidence: str
    status: str
    reason: str
    market_probability: Optional[float] = None
    market_gap_pp: Optional[float] = None


def selection_name(row: CombinedMatch, side: str) -> str:
    return row.home_team if side == "HOME" else row.away_team if side == "AWAY" else "Draw"


def _best_quote(row: CombinedMatch, side: str):
    shop = getattr(row, "price_shop", None)
    if shop is not None:
        quotes = [q for q in shop.quotes.get(side, ()) if getattr(q, "decimal_odds", None) and float(q.decimal_odds) > 1]
        if quotes:
            return max(quotes, key=lambda q: float(q.decimal_odds))
    odds = {
        "HOME": getattr(row, "sb_home", None),
        "DRAW": getattr(row, "sb_draw", None),
        "AWAY": getattr(row, "sb_away", None),
    }.get(side)
    if odds is None or odds <= 1:
        return None

    class BaseQuote:
        source = "Sportsbet"
        decimal_odds = float(odds)
        match_confidence = 1.0

    return BaseQuote()


def _market_probability(row: CombinedMatch, side: str) -> Optional[float]:
    attr = {"HOME": "market_reference_home", "DRAW": "market_reference_draw", "AWAY": "market_reference_away"}[side]
    value = getattr(row, attr, None)
    return float(value) if value is not None else None


def _probability_above_break_even(mean: float, stdev: float, odds: float, lower: float) -> float:
    threshold = 1.0 / odds
    if stdev <= 1e-8:
        return 1.0 if mean > threshold else 0.0
    value = 1.0 - NormalDist(mu=mean, sigma=stdev).cdf(threshold)
    if lower > threshold:
        value = max(value, 0.95)
    return max(0.0, min(1.0, value))


def decision_for_side(row: CombinedMatch, side: str, min_ev_pct: float = 4.0) -> Optional[V3Decision]:
    forecast: Optional[V3Forecast] = getattr(row, "independent_v3", None)
    if forecast is None:
        return None
    quote = _best_quote(row, side)
    if quote is None:
        return None

    odds = float(quote.decimal_odds)
    p = forecast.probability(side)
    lower = forecast.lower_probability(side)
    sd = forecast.stdev(side)
    fair = 1.0 / p
    model_ev = (p * odds - 1.0) * 100.0
    lower_ev = (lower * odds - 1.0) * 100.0
    p_positive = _probability_above_break_even(p, sd, odds, lower)
    quote_confidence = float(getattr(quote, "match_confidence", 1.0) or 0.0)
    market_p = _market_probability(row, side)
    market_gap = (p - market_p) * 100.0 if market_p is not None else None

    # Candidates are deliberately not labelled validated bets. Forecast-quality
    # and betting-edge gates require accumulated chronological OOS/CLV evidence.
    if (
        model_ev >= min_ev_pct
        and lower_ev > 0.0
        and p_positive >= 0.90
        and forecast.confidence in {"HIGH", "MEDIUM"}
        and quote_confidence >= 0.94
    ):
        status = "V3 HIGH-CONFIDENCE CANDIDATE"
        reason = (
            f"The independent V3 model prices {selection_name(row, side)} at {p * 100:.1f}% (fair ${fair:.2f}). "
            f"{quote.source} offers ${odds:.2f}; central EV is {model_ev:+.1f}%, the block-bootstrap 5th-percentile EV is {lower_ev:+.1f}%, "
            f"and the estimated probability EV is positive is {p_positive * 100:.0f}%. This remains a research candidate until OOS/CLV validation clears the betting-edge gate."
        )
    elif model_ev >= min_ev_pct and p_positive >= 0.65:
        status = "V3 +EV CANDIDATE — UNCERTAINTY"
        reason = (
            f"Central EV is {model_ev:+.1f}% at ${odds:.2f}, but uncertainty is material: 5th-percentile EV {lower_ev:+.1f}% and P(EV>0) about {p_positive * 100:.0f}%."
        )
    elif model_ev > 0:
        status = "POSITIVE — BELOW V3 THRESHOLD"
        reason = f"The independent point estimate is positive ({model_ev:+.1f}%) but does not satisfy the current V3 research-selection gate."
    else:
        status = "NEGATIVE EV"
        reason = f"The best observed price still implies {model_ev:+.1f}% EV under the independent V3 probability."

    return V3Decision(
        match_name=row.match_name,
        league=str(getattr(row, "league", "") or "Unknown league"),
        side=side,
        selection=selection_name(row, side),
        quote_source=str(quote.source),
        quote_odds=odds,
        quote_match_confidence=quote_confidence,
        model_probability=p,
        lower_probability=lower,
        probability_stdev=sd,
        fair_odds=fair,
        model_ev_pct=model_ev,
        lower_ev_pct=lower_ev,
        probability_ev_positive=p_positive,
        component_spread_pp=forecast.component_spread_pp,
        component_count=len(forecast.components),
        confidence=forecast.confidence,
        status=status,
        reason=reason,
        market_probability=market_p,
        market_gap_pp=market_gap,
    )


def build_v3_decisions(rows: list[CombinedMatch], min_ev_pct: float = 4.0) -> list[V3Decision]:
    decisions = []
    for row in rows:
        for side in SIDES:
            item = decision_for_side(row, side, min_ev_pct=min_ev_pct)
            if item is not None:
                decisions.append(item)
    rank = {
        "V3 HIGH-CONFIDENCE CANDIDATE": 4,
        "V3 +EV CANDIDATE — UNCERTAINTY": 3,
        "POSITIVE — BELOW V3 THRESHOLD": 2,
        "NEGATIVE EV": 1,
    }
    decisions.sort(
        key=lambda d: (rank.get(d.status, 0), d.lower_ev_pct, d.probability_ev_positive, d.model_ev_pct),
        reverse=True,
    )
    return decisions


def best_available_v3(rows: list[CombinedMatch], min_ev_pct: float = 4.0) -> Optional[V3Decision]:
    decisions = build_v3_decisions(rows, min_ev_pct=min_ev_pct)
    if not decisions:
        return None
    high = [d for d in decisions if d.status == "V3 HIGH-CONFIDENCE CANDIDATE"]
    return high[0] if high else max(decisions, key=lambda d: (d.model_ev_pct, d.lower_ev_pct))
