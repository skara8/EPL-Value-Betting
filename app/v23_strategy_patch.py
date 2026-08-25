from __future__ import annotations

import strategy_v21


_ORIGINAL_DECISION = strategy_v21.decision_for_side


def _best_quote_v23(row, side: str):
    exclusions = {str(x).strip().lower() for x in getattr(row, "reference_execution_exclusions", ()) or ()}
    exclusions.add("polymarket")

    shop = getattr(row, "price_shop", None)
    if shop is not None:
        try:
            quotes = list(shop.quotes.get(side, ()))
        except Exception:
            quotes = []
        eligible = [
            q for q in quotes
            if getattr(q, "decimal_odds", None)
            and str(getattr(q, "source", "")).strip().lower() not in exclusions
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


def decision_for_side_v23(row, side: str, min_ev_pct: float = 4.0, max_disagreement_pp: float = 4.0):
    item = _ORIGINAL_DECISION(row, side, min_ev_pct=min_ev_pct, max_disagreement_pp=max_disagreement_pp)
    if item is None:
        return None

    tier = str(getattr(row, "reference_tier", "") or "")
    if tier.startswith("TIER 2") and item.status == "ROBUST +EV":
        item.status = "CONSENSUS +EV"
        item.reason = (
            f"Several independent non-Sportsbet bookmakers agree that {item.selection} is priced above their de-vigged consensus. "
            "Pinnacle/Polymarket confirmation is unavailable, so this is deliberately lower confidence than a primary robust edge."
        )
    elif tier.startswith("TIER 1B") and item.status == "ROBUST +EV":
        item.reason = (
            f"One primary external reference plus a multi-book consensus still values {item.selection} highly enough for "
            f"{item.quote_source}'s ${item.quote_odds:.2f} price to clear the threshold."
        )
    return item


strategy_v21._best_quote = _best_quote_v23
strategy_v21.decision_for_side = decision_for_side_v23
