from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Optional

from context_model import ContextInputs, context_adjustment_for_match, adjusted_ev, biggest_position_gap
from dutch_calc import DutchSelection, calculate_dutch, polymarket_effective_decimal_odds
from engine import CombinedMatch


@dataclass
class SimpleBetIdea:
    match_name: str
    side: str
    selection: str
    sportsbet_odds: float
    base_probability: float
    adjusted_probability: float
    base_ev_pct: float
    decision_ev_pct: float
    conservative_ev_pct: Optional[float]
    confidence: str
    signal: str
    context_shift_pp: float
    short_reason: str
    detail_reason: str


@dataclass
class DutchIdea:
    match_name: str
    labels: tuple[str, ...]
    sources: tuple[str, ...]
    effective_odds: tuple[float, ...]
    expected_ev_pct: float
    equal_profit_pct: float
    arbitrage: bool
    complete_market: bool
    short_reason: str


def _names(row: CombinedMatch) -> dict[str, str]:
    return {"HOME": row.home_team, "DRAW": "Draw", "AWAY": row.away_team}


def _odds(row: CombinedMatch) -> dict[str, Optional[float]]:
    return {"HOME": row.sb_home, "DRAW": row.sb_draw, "AWAY": row.sb_away}


def _base_probs(row: CombinedMatch) -> dict[str, Optional[float]]:
    return {
        "HOME": getattr(row, "model_fair_home", None),
        "DRAW": getattr(row, "model_fair_draw", None),
        "AWAY": getattr(row, "model_fair_away", None),
    }


def _context_probs(row: CombinedMatch, adjustment) -> dict[str, float]:
    return {
        "HOME": adjustment.home_probability,
        "DRAW": adjustment.draw_probability,
        "AWAY": adjustment.away_probability,
    }


def _context_shifts(adjustment) -> dict[str, float]:
    return {
        "HOME": adjustment.home_shift_pp,
        "DRAW": adjustment.draw_shift_pp,
        "AWAY": adjustment.away_shift_pp,
    }


def _market_agreement_text(edge) -> str:
    count = getattr(edge, "source_count", 0)
    disagreement = getattr(edge, "external_disagreement_pp", None)
    if count >= 2 and disagreement is not None:
        if disagreement <= 2.0:
            return "The outside markets are telling a very similar story."
        if disagreement <= 4.0:
            return "The outside markets are reasonably close, but not identical."
        return "The outside markets disagree quite a bit, so the estimate is less certain."
    return "Only one outside reference is available, so confidence is lower."


def _availability_text(row: CombinedMatch, fpl_context: dict) -> str:
    home = fpl_context.get(row.home_team) if fpl_context else None
    away = fpl_context.get(row.away_team) if fpl_context else None
    position, gap = biggest_position_gap(home, away)
    if not position or abs(gap) < 0.15:
        return "Player availability does not create a large extra lean."
    label = {"GKP": "goalkeeper", "DEF": "defence", "MID": "midfield", "FWD": "attack"}.get(position, position)
    if gap > 0:
        return f"Current availability looks better for {row.home_team}, mainly around {label}."
    return f"Current availability looks better for {row.away_team}, mainly around {label}."


def _simple_reason(row: CombinedMatch, selection: str, odds: float, probability: float, ev: float, edge, shift: float, fpl_context: dict) -> tuple[str, str]:
    break_even = 1.0 / odds
    difference = (probability - break_even) * 100.0
    plain = (
        f"Our model gives {selection} about {probability * 100:.1f}% chance. "
        f"At ${odds:.2f}, the price only needs about {break_even * 100:.1f}% to break even. "
        f"That leaves about {difference:+.1f} percentage points of model edge."
    )
    if abs(shift) >= 0.10:
        direction = "helps" if shift > 0 else "hurts"
        plain += f" Current team context {direction} this pick by about {abs(shift):.1f} points."
    else:
        plain += " Current team context barely changes the estimate."

    detail = (
        plain
        + "\n\n"
        + _market_agreement_text(edge)
        + " "
        + _availability_text(row, fpl_context)
        + f" The resulting theoretical EV is {ev:+.1f}%."
    )
    return plain, detail


def build_bet_ideas(
    rows: list[CombinedMatch],
    fpl_context: Optional[dict] = None,
    context_inputs_by_key: Optional[dict[str, ContextInputs]] = None,
    max_context_shift_pp: float = 1.50,
    min_ev_pct: float = 4.0,
) -> list[SimpleBetIdea]:
    """Create a user-facing shortlist while keeping the V1.5 model as anchor.

    Context is allowed to nudge a positive or near-positive market idea, but it
    cannot turn a clearly negative base EV into a dashboard recommendation.
    """
    fpl_context = fpl_context or {}
    context_inputs_by_key = context_inputs_by_key or {}
    ideas: list[SimpleBetIdea] = []

    for row in rows:
        key = f"{row.kickoff.isoformat()}|{row.home_team}|{row.away_team}"
        inputs = context_inputs_by_key.get(key, ContextInputs())
        adjustment = context_adjustment_for_match(row, inputs, fpl_context, max_shift_pp=max_context_shift_pp)
        if adjustment is None:
            continue
        context_evs = adjusted_ev(row, adjustment)
        context_probs = _context_probs(row, adjustment)
        shifts = _context_shifts(adjustment)
        names = _names(row)
        odds_map = _odds(row)
        edges = getattr(row, "edge_outcomes", {})

        for side in ("HOME", "DRAW", "AWAY"):
            edge = edges.get(side)
            odds = odds_map[side]
            base_p = _base_probs(row)[side]
            context_p = context_probs[side]
            base_ev = getattr(edge, "model_ev_pct", None) if edge is not None else None
            context_ev = context_evs.get(side)
            if None in (odds, base_p, base_ev, context_ev):
                continue

            # Context should confirm/nudge an existing market idea, not create a
            # recommendation out of a clearly negative independent-market view.
            if float(base_ev) < 0.0:
                continue
            if float(context_ev) < min_ev_pct:
                continue

            short, detail = _simple_reason(
                row, names[side], float(odds), float(context_p), float(context_ev),
                edge, shifts[side], fpl_context,
            )
            conservative = getattr(edge, "conservative_ev_pct", None) if edge is not None else None
            confidence = getattr(edge, "confidence", "LOW") if edge is not None else "LOW"
            signal = getattr(edge, "signal", "MODEL EDGE") if edge is not None else "MODEL EDGE"
            ideas.append(
                SimpleBetIdea(
                    match_name=row.match_name,
                    side=side,
                    selection=names[side],
                    sportsbet_odds=float(odds),
                    base_probability=float(base_p),
                    adjusted_probability=float(context_p),
                    base_ev_pct=float(base_ev),
                    decision_ev_pct=float(context_ev),
                    conservative_ev_pct=conservative,
                    confidence=confidence,
                    signal=signal,
                    context_shift_pp=shifts[side],
                    short_reason=short,
                    detail_reason=detail,
                )
            )

    # Robustness first, then decision EV.
    def rank(idea: SimpleBetIdea):
        conservative = idea.conservative_ev_pct if idea.conservative_ev_pct is not None else -999.0
        confidence_score = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}.get(idea.confidence, 0)
        return (conservative > 0, confidence_score, conservative, idea.decision_ev_pct)

    ideas.sort(key=rank, reverse=True)
    return ideas


def _best_effective_prices(row: CombinedMatch, fee_rate: float = 0.05) -> list[DutchSelection]:
    labels = (row.home_team, "Draw", row.away_team)
    sb = (row.sb_home, row.sb_draw, row.sb_away)
    pm = (row.pm_home, row.pm_draw, row.pm_away)
    output: list[DutchSelection] = []
    for label, sb_odds, pm_odds in zip(labels, sb, pm):
        best_source = None
        best_odds = 0.0
        best_pm_price = None
        if sb_odds is not None and sb_odds > 1:
            best_source = "Sportsbet"
            best_odds = float(sb_odds)
        if pm_odds is not None and pm_odds > 1:
            price = 1.0 / float(pm_odds)
            effective = polymarket_effective_decimal_odds(price, fee_rate=fee_rate, maker=False)
            if effective > best_odds:
                best_source = "Polymarket"
                best_odds = effective
                best_pm_price = price
        if best_source is None:
            return []
        if best_source == "Polymarket":
            output.append(DutchSelection(label=label, source=best_source, polymarket_price=best_pm_price, fee_rate=fee_rate, maker=False))
        else:
            output.append(DutchSelection(label=label, source=best_source, decimal_odds=best_odds))
    return output


def find_dutch_ideas(
    rows: list[CombinedMatch],
    fpl_context: Optional[dict] = None,
    context_inputs_by_key: Optional[dict[str, ContextInputs]] = None,
    max_context_shift_pp: float = 1.50,
    fee_rate: float = 0.05,
) -> list[DutchIdea]:
    """Find full-market arbitrage and positive-model-EV two-outcome Dutch ideas."""
    fpl_context = fpl_context or {}
    context_inputs_by_key = context_inputs_by_key or {}
    ideas: list[DutchIdea] = []

    for row in rows:
        selections = _best_effective_prices(row, fee_rate=fee_rate)
        if len(selections) != 3:
            continue
        full = calculate_dutch(selections, 100.0, complete_market=True)
        if full.arbitrage:
            ideas.append(
                DutchIdea(
                    match_name=row.match_name,
                    labels=tuple(r.label for r in full.rows),
                    sources=tuple(r.source for r in full.rows),
                    effective_odds=tuple(r.effective_odds for r in full.rows),
                    expected_ev_pct=full.return_on_stake_pct,
                    equal_profit_pct=full.return_on_stake_pct,
                    arbitrage=True,
                    complete_market=True,
                    short_reason=f"Best prices across both markets lock in about {full.return_on_stake_pct:.2f}% before slippage/price movement.",
                )
            )

        key = f"{row.kickoff.isoformat()}|{row.home_team}|{row.away_team}"
        inputs = context_inputs_by_key.get(key, ContextInputs())
        adjustment = context_adjustment_for_match(row, inputs, fpl_context, max_shift_pp=max_context_shift_pp)
        if adjustment is None:
            continue
        probs = (adjustment.home_probability, adjustment.draw_probability, adjustment.away_probability)

        for pair in combinations(range(3), 2):
            pair_selections = [selections[i] for i in pair]
            partial = calculate_dutch(pair_selections, 100.0, complete_market=False)
            covered_probability = sum(probs[i] for i in pair)
            expected_profit = covered_probability * partial.equal_profit + (1.0 - covered_probability) * -100.0
            expected_ev = expected_profit
            if expected_ev < 4.0:
                continue
            rows_out = partial.rows
            labels = tuple(r.label for r in rows_out)
            sources = tuple(r.source for r in rows_out)
            ideas.append(
                DutchIdea(
                    match_name=row.match_name,
                    labels=labels,
                    sources=sources,
                    effective_odds=tuple(r.effective_odds for r in rows_out),
                    expected_ev_pct=expected_ev,
                    equal_profit_pct=partial.return_on_stake_pct,
                    arbitrage=False,
                    complete_market=False,
                    short_reason=(
                        f"A two-outcome Dutch on {' + '.join(labels)} has about {expected_ev:.1f}% model EV, "
                        "but the uncovered third result still loses the full outlay."
                    ),
                )
            )

    ideas.sort(key=lambda x: (x.arbitrage, x.expected_ev_pct), reverse=True)
    return ideas
