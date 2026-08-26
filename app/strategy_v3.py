from __future__ import annotations

"""Decision layer for V3.

V3 deliberately stops calling the minimum component probability a confidence
bound.  A displayed positive EV remains a *research signal* until the
walk-forward validation gate has enough evidence to certify the model family.
"""

from dataclasses import dataclass
from typing import Optional

from engine import CombinedMatch

SIDES = ("HOME", "DRAW", "AWAY")


@dataclass
class V3Decision:
    match_name: str
    league: str
    side: str
    selection: str
    quote_source: str
    quote_odds: float
    model_probability: float
    fair_odds: float
    model_ev_pct: float
    stress_probability: Optional[float]
    stress_ev_pct: Optional[float]
    component_count: int
    model_spread_pp: Optional[float]
    confidence: str
    status: str
    reason: str
    validation_grade: str = "UNVALIDATED"
    market_probability: Optional[float] = None
    market_gap_pp: Optional[float] = None
    challenger_probability: Optional[float] = None
    challenger_gap_pp: Optional[float] = None


def selection_name(row: CombinedMatch, side: str) -> str:
    if side == "HOME":
        return row.home_team
    if side == "AWAY":
        return row.away_team
    return "Draw"


def _best_quote(row: CombinedMatch, side: str) -> tuple[str, Optional[float]]:
    shop = getattr(row, "price_shop", None)
    if shop is not None:
        try:
            quotes = [
                q for q in shop.quotes.get(side, ())
                if getattr(q, "decimal_odds", None) is not None and float(q.decimal_odds) > 1
            ]
        except Exception:
            quotes = []
        if quotes:
            quote = max(quotes, key=lambda q: float(q.decimal_odds))
            return str(getattr(quote, "source", "Best observed")), float(quote.decimal_odds)
    odds = {
        "HOME": getattr(row, "sb_home", None),
        "DRAW": getattr(row, "sb_draw", None),
        "AWAY": getattr(row, "sb_away", None),
    }.get(side)
    return "Sportsbet", float(odds) if odds is not None else None


def _market_probability(row: CombinedMatch, side: str) -> Optional[float]:
    attr = {"HOME": "market_reference_home", "DRAW": "market_reference_draw", "AWAY": "market_reference_away"}[side]
    value = getattr(row, attr, None)
    return float(value) if value is not None else None


def _challenger_probability(row: CombinedMatch, side: str) -> Optional[float]:
    attr = {"HOME": "v3_challenger_home", "DRAW": "v3_challenger_draw", "AWAY": "v3_challenger_away"}[side]
    value = getattr(row, attr, None)
    return float(value) if value is not None else None


def decision_for_side(
    row: CombinedMatch,
    side: str,
    min_ev_pct: float = 4.0,
    validation_grade: str = "UNVALIDATED",
) -> Optional[V3Decision]:
    edge = getattr(row, "edge_outcomes", {}).get(side)
    if edge is None or getattr(edge, "model_probability", None) is None:
        return None
    source, odds = _best_quote(row, side)
    if odds is None or odds <= 1:
        return None

    p = float(edge.model_probability)
    stress = getattr(edge, "conservative_probability", None)
    stress = float(stress) if stress is not None else None
    model_ev = (p * odds - 1.0) * 100.0
    stress_ev = (stress * odds - 1.0) * 100.0 if stress is not None else None
    fair = 1.0 / p
    component_count = int(getattr(edge, "source_count", 0) or 0)
    spread = getattr(edge, "external_disagreement_pp", None)
    spread = float(spread) if spread is not None else None
    confidence = str(getattr(edge, "confidence", "LOW") or "LOW").upper()
    market_p = _market_probability(row, side)
    market_gap = (p - market_p) * 100.0 if market_p is not None else None
    challenger = _challenger_probability(row, side)
    challenger_gap = (challenger - p) * 100.0 if challenger is not None else None

    grade = str(validation_grade or "UNVALIDATED").upper()
    if model_ev >= min_ev_pct and grade == "VALIDATED":
        status = "VALIDATED +EV"
        reason = (
            f"The validated independent model estimates {selection_name(row, side)} at {p * 100:.1f}% "
            f"(fair ${fair:.2f}); {source} is offering ${odds:.2f}, producing {model_ev:+.1f}% model EV."
        )
    elif model_ev >= min_ev_pct:
        status = "RESEARCH +EV — UNVALIDATED"
        reason = (
            f"The independent football baseline estimates {selection_name(row, side)} at {p * 100:.1f}% "
            f"(fair ${fair:.2f}); {source} is offering ${odds:.2f}, which implies {model_ev:+.1f}% EV. "
            "V3 does not label this a proven edge until chronological validation passes."
        )
    elif model_ev > 0:
        status = "POSITIVE — BELOW THRESHOLD"
        reason = f"Independent EV is {model_ev:+.1f}% at {source}'s ${odds:.2f}, below the configured +{min_ev_pct:.1f}% research threshold."
    else:
        status = "NEGATIVE EV"
        reason = f"This is the highest observed price for comparison, but the independent probability still implies {model_ev:+.1f}% EV."

    return V3Decision(
        match_name=row.match_name,
        league=str(getattr(row, "league", "") or "Unknown league"),
        side=side,
        selection=selection_name(row, side),
        quote_source=source,
        quote_odds=odds,
        model_probability=p,
        fair_odds=fair,
        model_ev_pct=model_ev,
        stress_probability=stress,
        stress_ev_pct=stress_ev,
        component_count=component_count,
        model_spread_pp=spread,
        confidence=confidence,
        status=status,
        reason=reason,
        validation_grade=grade,
        market_probability=market_p,
        market_gap_pp=market_gap,
        challenger_probability=challenger,
        challenger_gap_pp=challenger_gap,
    )


def build_v3_decisions(
    rows: list[CombinedMatch],
    min_ev_pct: float = 4.0,
    validation_grade: str = "UNVALIDATED",
) -> list[V3Decision]:
    decisions: list[V3Decision] = []
    for row in rows:
        for side in SIDES:
            item = decision_for_side(row, side, min_ev_pct=min_ev_pct, validation_grade=validation_grade)
            if item is not None:
                decisions.append(item)

    status_rank = {
        "VALIDATED +EV": 5,
        "RESEARCH +EV — UNVALIDATED": 4,
        "POSITIVE — BELOW THRESHOLD": 2,
        "NEGATIVE EV": 1,
    }
    confidence_rank = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}
    decisions.sort(
        key=lambda d: (
            status_rank.get(d.status, 0),
            d.model_ev_pct,
            confidence_rank.get(d.confidence, 0),
            -abs(d.model_spread_pp or 99.0),
        ),
        reverse=True,
    )
    return decisions


def best_available_v3(
    rows: list[CombinedMatch],
    min_ev_pct: float = 4.0,
    validation_grade: str = "UNVALIDATED",
) -> Optional[V3Decision]:
    decisions = build_v3_decisions(rows, min_ev_pct=min_ev_pct, validation_grade=validation_grade)
    if not decisions:
        return None
    validated = [d for d in decisions if d.status == "VALIDATED +EV"]
    if validated:
        return validated[0]
    research = [d for d in decisions if d.status == "RESEARCH +EV — UNVALIDATED"]
    if research:
        return max(research, key=lambda d: (d.model_ev_pct, d.confidence))
    return max(decisions, key=lambda d: (d.model_ev_pct, d.confidence))
