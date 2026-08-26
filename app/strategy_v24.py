from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from engine import CombinedMatch


SIDES = ("HOME", "DRAW", "AWAY")


@dataclass
class V24Decision:
    match_name: str
    league: str
    side: str
    selection: str
    quote_source: str
    quote_odds: float
    model_probability: float
    conservative_probability: float
    fair_odds: float
    model_ev_pct: float
    robust_ev_pct: float
    model_spread_pp: Optional[float]
    component_count: int
    confidence: str
    status: str
    reason: str
    market_probability: Optional[float] = None
    market_gap_pp: Optional[float] = None


def selection_name(row: CombinedMatch, side: str) -> str:
    if side == "HOME":
        return row.home_team
    if side == "AWAY":
        return row.away_team
    return "Draw"


def _best_quote(row: CombinedMatch, side: str) -> tuple[str, Optional[float]]:
    """Best observed executable price.

    V2.4's probability is generated independently from football history, so
    Polymarket no longer creates circularity and may be used as an execution
    quote when its effective fee-adjusted price is best.
    """
    shop = getattr(row, "price_shop", None)
    if shop is not None:
        try:
            quotes = [q for q in shop.quotes.get(side, ()) if getattr(q, "decimal_odds", None) and float(q.decimal_odds) > 1]
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
    attr = {
        "HOME": "market_reference_home",
        "DRAW": "market_reference_draw",
        "AWAY": "market_reference_away",
    }.get(side)
    value = getattr(row, attr, None) if attr else None
    return float(value) if value is not None else None


def decision_for_side(row: CombinedMatch, side: str, min_ev_pct: float = 4.0) -> Optional[V24Decision]:
    edge = getattr(row, "edge_outcomes", {}).get(side)
    if edge is None:
        return None
    p = getattr(edge, "model_probability", None)
    c = getattr(edge, "conservative_probability", None)
    if p is None or c is None:
        return None

    source, odds = _best_quote(row, side)
    if odds is None or odds <= 1:
        return None

    p = float(p)
    c = float(c)
    model_ev = (p * odds - 1.0) * 100.0
    robust_ev = (c * odds - 1.0) * 100.0
    spread = getattr(edge, "external_disagreement_pp", None)
    spread = float(spread) if spread is not None else None
    component_count = int(getattr(edge, "source_count", 0) or 0)
    confidence = str(getattr(edge, "confidence", "LOW") or "LOW").upper()
    fair_odds = 1.0 / p
    market_p = _market_probability(row, side)
    market_gap = (p - market_p) * 100.0 if market_p is not None else None

    if robust_ev >= min_ev_pct and component_count >= 3 and confidence in {"HIGH", "MEDIUM"}:
        status = "ROBUST INDEPENDENT +EV"
        reason = (
            f"The football model makes {selection_name(row, side)} about {p * 100:.1f}% likely (fair odds ${fair_odds:.2f}). "
            f"{source} is offering ${odds:.2f}, and even the cautious independent-model estimate still gives about {robust_ev:+.1f}% EV."
        )
    elif model_ev >= min_ev_pct and robust_ev > 0:
        status = "INDEPENDENT +EV — MODEL SPREAD"
        reason = (
            f"The central football model gives about {model_ev:+.1f}% EV at {source}'s ${odds:.2f}, but the model variants disagree enough that the cautious estimate falls to {robust_ev:+.1f}% EV."
        )
    elif model_ev >= min_ev_pct:
        status = "WATCH — INDEPENDENT MODEL DISAGREEMENT"
        reason = (
            f"The central independent estimate shows {model_ev:+.1f}% EV, but at least one football-model variant would make the same price negative ({robust_ev:+.1f}% cautious EV)."
        )
    elif model_ev > 0:
        status = "POSITIVE — BELOW THRESHOLD"
        reason = (
            f"The independent football model sees a small positive edge ({model_ev:+.1f}%), but it does not clear the configured +{min_ev_pct:.1f}% threshold."
        )
    else:
        status = "NEGATIVE EV"
        reason = (
            f"This is the best observed price for comparison, but the independent probability still implies {model_ev:+.1f}% EV."
        )

    return V24Decision(
        match_name=row.match_name,
        league=str(getattr(row, "league", "") or "Unknown league"),
        side=side,
        selection=selection_name(row, side),
        quote_source=source,
        quote_odds=odds,
        model_probability=p,
        conservative_probability=c,
        fair_odds=fair_odds,
        model_ev_pct=model_ev,
        robust_ev_pct=robust_ev,
        model_spread_pp=spread,
        component_count=component_count,
        confidence=confidence,
        status=status,
        reason=reason,
        market_probability=market_p,
        market_gap_pp=market_gap,
    )


def build_v24_decisions(rows: list[CombinedMatch], min_ev_pct: float = 4.0) -> list[V24Decision]:
    decisions: list[V24Decision] = []
    for row in rows:
        for side in SIDES:
            item = decision_for_side(row, side, min_ev_pct)
            if item:
                decisions.append(item)

    status_rank = {
        "ROBUST INDEPENDENT +EV": 5,
        "INDEPENDENT +EV — MODEL SPREAD": 4,
        "WATCH — INDEPENDENT MODEL DISAGREEMENT": 3,
        "POSITIVE — BELOW THRESHOLD": 2,
        "NEGATIVE EV": 1,
    }
    confidence_rank = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}
    decisions.sort(
        key=lambda d: (
            status_rank.get(d.status, 0),
            d.robust_ev_pct,
            confidence_rank.get(d.confidence, 0),
            d.model_ev_pct,
        ),
        reverse=True,
    )
    return decisions


def best_available_v24(rows: list[CombinedMatch], min_ev_pct: float = 4.0) -> Optional[V24Decision]:
    decisions = build_v24_decisions(rows, min_ev_pct)
    if not decisions:
        return None
    robust = [d for d in decisions if d.status == "ROBUST INDEPENDENT +EV"]
    if robust:
        return robust[0]
    # Keep the user's dashboard rule: always show the highest available EV if
    # no positive/robust option exists, but label negative EV explicitly.
    return max(decisions, key=lambda d: (d.model_ev_pct, d.robust_ev_pct, d.confidence))
