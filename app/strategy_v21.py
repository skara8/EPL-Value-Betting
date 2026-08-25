from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from engine import CombinedMatch


SIDES = ("HOME", "DRAW", "AWAY")


@dataclass
class V21Decision:
    match_name: str
    league: str
    side: str
    selection: str
    quote_source: str
    quote_odds: float
    model_probability: float
    conservative_probability: float
    model_ev_pct: float
    robust_ev_pct: float
    disagreement_pp: Optional[float]
    source_count: int
    confidence: str
    status: str
    reason: str


def selection_name(row: CombinedMatch, side: str) -> str:
    if side == "HOME":
        return row.home_team
    if side == "AWAY":
        return row.away_team
    return "Draw"


def _best_quote(row: CombinedMatch, side: str) -> tuple[str, Optional[float]]:
    """Return the best non-reference execution price for a V2.1 signal.

    Polymarket helps form the external fair probability, so using the same
    Polymarket quote as the target price would partly let a market assess its own
    value. The general Best Prices/Dutch tools may still show Polymarket, but the
    primary V2.1 signal price is selected from Sportsbet/other fixed-odds books.
    """
    shop = getattr(row, "price_shop", None)
    if shop is not None:
        try:
            quotes = list(shop.quotes.get(side, ()))
        except Exception:
            quotes = []
        eligible = [
            q for q in quotes
            if getattr(q, "decimal_odds", None)
            and str(getattr(q, "source", "")).strip().lower() != "polymarket"
        ]
        if eligible:
            quote = max(eligible, key=lambda q: float(q.decimal_odds))
            return str(getattr(quote, "source", "Best observed")), float(quote.decimal_odds)

    odds = {
        "HOME": getattr(row, "sb_home", None),
        "DRAW": getattr(row, "sb_draw", None),
        "AWAY": getattr(row, "sb_away", None),
    }.get(side)
    return "Sportsbet", float(odds) if odds is not None else None


def decision_for_side(
    row: CombinedMatch,
    side: str,
    min_ev_pct: float = 4.0,
    max_disagreement_pp: float = 4.0,
) -> Optional[V21Decision]:
    edge = getattr(row, "edge_outcomes", {}).get(side)
    if edge is None:
        return None

    model_p = getattr(edge, "model_probability", None)
    conservative_p = getattr(edge, "conservative_probability", None)
    if model_p is None or conservative_p is None:
        return None

    source, odds = _best_quote(row, side)
    if odds is None or odds <= 1:
        return None

    model_p = float(model_p)
    conservative_p = float(conservative_p)
    model_ev = (model_p * odds - 1.0) * 100.0
    robust_ev = (conservative_p * odds - 1.0) * 100.0
    source_count = int(getattr(edge, "source_count", 0) or 0)
    disagreement = getattr(edge, "external_disagreement_pp", None)
    disagreement = float(disagreement) if disagreement is not None else None
    confidence = str(getattr(edge, "confidence", "LOW") or "LOW").upper()

    enough_sources = source_count >= 2
    agreement_ok = disagreement is not None and disagreement <= max_disagreement_pp
    confidence_ok = confidence in {"HIGH", "MEDIUM"}

    if robust_ev >= min_ev_pct and enough_sources and agreement_ok and confidence_ok:
        status = "ROBUST +EV"
        reason = (
            f"Even the less optimistic external reference still values {selection_name(row, side)} highly enough "
            f"for {source}'s ${odds:.2f} price to clear the +{min_ev_pct:.1f}% EV threshold."
        )
    elif model_ev >= min_ev_pct and robust_ev > 0:
        status = "WATCH — EDGE NOT ROBUST"
        reason = (
            f"The average model likes the price, but the less optimistic external reference only gives about {robust_ev:+.1f}% EV. "
            "The apparent edge is therefore sensitive to which reference market is right."
        )
    elif model_ev >= min_ev_pct:
        status = "WATCH — MARKET DISAGREEMENT"
        reason = (
            f"The average model shows {model_ev:+.1f}% EV, but the conservative reference is {robust_ev:+.1f}% EV. "
            "This is not treated as a primary edge."
        )
    elif model_ev > 0:
        status = "POSITIVE — BELOW THRESHOLD"
        reason = "The best observed non-reference price is positive by the point estimate, but it does not clear the configured EV threshold."
    else:
        status = "NEGATIVE EV"
        reason = "This is shown only for comparison; the best observed non-reference price is still negative under the model."

    return V21Decision(
        match_name=row.match_name,
        league=str(getattr(row, "league", "") or "Unknown league"),
        side=side,
        selection=selection_name(row, side),
        quote_source=source,
        quote_odds=odds,
        model_probability=model_p,
        conservative_probability=conservative_p,
        model_ev_pct=model_ev,
        robust_ev_pct=robust_ev,
        disagreement_pp=disagreement,
        source_count=source_count,
        confidence=confidence,
        status=status,
        reason=reason,
    )


def build_v21_decisions(
    rows: list[CombinedMatch],
    min_ev_pct: float = 4.0,
    max_disagreement_pp: float = 4.0,
) -> list[V21Decision]:
    decisions: list[V21Decision] = []
    for row in rows:
        for side in SIDES:
            item = decision_for_side(row, side, min_ev_pct, max_disagreement_pp)
            if item is not None:
                decisions.append(item)

    def rank(item: V21Decision):
        status_rank = {
            "ROBUST +EV": 4,
            "WATCH — EDGE NOT ROBUST": 3,
            "WATCH — MARKET DISAGREEMENT": 2,
            "POSITIVE — BELOW THRESHOLD": 1,
            "NEGATIVE EV": 0,
        }.get(item.status, 0)
        confidence_rank = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}.get(item.confidence, 0)
        return status_rank, item.robust_ev_pct, confidence_rank, item.model_ev_pct

    decisions.sort(key=rank, reverse=True)
    return decisions


def primary_v21_decisions(
    rows: list[CombinedMatch],
    min_ev_pct: float = 4.0,
    max_disagreement_pp: float = 4.0,
) -> list[V21Decision]:
    return [
        item
        for item in build_v21_decisions(rows, min_ev_pct, max_disagreement_pp)
        if item.status == "ROBUST +EV"
    ]


def best_available_v21(
    rows: list[CombinedMatch],
    min_ev_pct: float = 4.0,
    max_disagreement_pp: float = 4.0,
) -> Optional[V21Decision]:
    decisions = build_v21_decisions(rows, min_ev_pct, max_disagreement_pp)
    if not decisions:
        return None
    primary = [d for d in decisions if d.status == "ROBUST +EV"]
    if primary:
        return primary[0]
    # If nothing is robust, honour the UI requirement to show the highest EV
    # option, while keeping its status explicit rather than calling it a bet.
    return max(decisions, key=lambda d: (d.model_ev_pct, d.robust_ev_pct))
