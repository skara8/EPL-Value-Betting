from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Optional

from context_model import ContextInputs, adjusted_ev
from decision_model import DutchIdea, SimpleBetIdea, _best_effective_prices
from dutch_calc import calculate_dutch
from engine import CombinedMatch
from football_intelligence import MatchIntelligence, context_adjustment_v18, intelligence_plain_summary


@dataclass
class V18BetIdea(SimpleBetIdea):
    football_quality: str = "LOW"
    football_rating: float = 0.0
    football_reason: str = ""


@dataclass
class V18DutchIdea(DutchIdea):
    football_quality: str = "LOW"


def _key(row: CombinedMatch) -> str:
    return f"{row.kickoff.isoformat()}|{row.home_team}|{row.away_team}"


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


def _context_probs(adjustment) -> dict[str, float]:
    return {
        "HOME": adjustment.home_probability,
        "DRAW": adjustment.draw_probability,
        "AWAY": adjustment.away_probability,
    }


def _shifts(adjustment) -> dict[str, float]:
    return {
        "HOME": adjustment.home_shift_pp,
        "DRAW": adjustment.draw_shift_pp,
        "AWAY": adjustment.away_shift_pp,
    }


def _market_sentence(edge) -> str:
    if edge is None:
        return "The market comparison is incomplete."
    count = int(getattr(edge, "source_count", 0) or 0)
    disagreement = getattr(edge, "external_disagreement_pp", None)
    conservative = getattr(edge, "conservative_ev_pct", None)
    if count >= 2 and disagreement is not None and disagreement <= 2.0 and conservative is not None and conservative > 0:
        return "The independent markets broadly agree and the edge survives the conservative check."
    if count >= 2 and disagreement is not None and disagreement <= 4.0:
        return "The outside markets are reasonably close, although the edge is not equally strong everywhere."
    if count >= 2:
        return "The outside markets disagree, so this estimate deserves extra caution."
    return "Only one independent market reference is available, so confidence is lower."


def _plain_reason(
    row: CombinedMatch,
    side: str,
    selection: str,
    odds: float,
    probability: float,
    base_probability: float,
    base_ev: float,
    decision_ev: float,
    shift: float,
    edge,
    intelligence: Optional[MatchIntelligence],
) -> tuple[str, str]:
    break_even = 1.0 / odds
    price_gap = (probability - break_even) * 100.0
    base_gap = (base_probability - break_even) * 100.0
    short = (
        f"Sportsbet's ${odds:.2f} price needs {break_even * 100:.1f}% to break even. "
        f"The independent market model starts at {base_probability * 100:.1f}% and the full V1.8 model ends near {probability * 100:.1f}%, "
        f"leaving about {price_gap:+.1f} probability points of edge."
    )
    if abs(shift) >= 0.10:
        if shift > 0:
            short += f" Team/player data adds a small {abs(shift):.1f}-point boost."
        else:
            short += f" Team/player data trims the estimate by {abs(shift):.1f} points."
    else:
        short += " Team/player data barely changes the market estimate."

    football = intelligence_plain_summary(intelligence)
    detail = (
        f"{short}\n\n"
        f"Base market edge: {base_gap:+.1f} probability points and {base_ev:+.1f}% EV. "
        f"After the capped football-context layer: {decision_ev:+.1f}% EV.\n\n"
        f"{_market_sentence(edge)}\n\n"
        f"{football}\n\n"
        "The automatic football layer uses expected-XI strength, recent underlying performance, tactical-profile matchup and rest. "
        "It is intentionally capped so it can nudge a market-supported idea but cannot dominate the independent market model."
    )
    return short, detail


def build_bet_ideas_v18(
    rows: list[CombinedMatch],
    fpl_context: Optional[dict] = None,
    context_inputs_by_key: Optional[dict[str, ContextInputs]] = None,
    intelligence_by_match: Optional[dict[str, MatchIntelligence]] = None,
    max_context_shift_pp: float = 1.50,
    min_ev_pct: float = 4.0,
) -> list[V18BetIdea]:
    fpl_context = fpl_context or {}
    context_inputs_by_key = context_inputs_by_key or {}
    intelligence_by_match = intelligence_by_match or {}
    ideas: list[V18BetIdea] = []

    for row in rows:
        manual = context_inputs_by_key.get(_key(row), ContextInputs())
        intel = intelligence_by_match.get(row.match_name)
        adjustment = context_adjustment_v18(
            row, manual, fpl_context, intel, max_shift_pp=max_context_shift_pp
        )
        if adjustment is None:
            continue
        context_evs = adjusted_ev(row, adjustment)
        context_probs = _context_probs(adjustment)
        shifts = _shifts(adjustment)
        names = _names(row)
        odds_map = _odds(row)
        base_probs = _base_probs(row)
        edges = getattr(row, "edge_outcomes", {})

        for side in ("HOME", "DRAW", "AWAY"):
            edge = edges.get(side)
            odds = odds_map[side]
            base_p = base_probs[side]
            context_p = context_probs[side]
            base_ev = getattr(edge, "model_ev_pct", None) if edge is not None else None
            context_ev = context_evs.get(side)
            if None in (odds, base_p, base_ev, context_ev):
                continue

            # Keep the V1.7 guardrail. Football information may strengthen or
            # weaken a market-supported idea, but cannot create a dashboard bet
            # from a negative independent-market EV.
            if float(base_ev) < 0.0 or float(context_ev) < float(min_ev_pct):
                continue

            short, detail = _plain_reason(
                row=row,
                side=side,
                selection=names[side],
                odds=float(odds),
                probability=float(context_p),
                base_probability=float(base_p),
                base_ev=float(base_ev),
                decision_ev=float(context_ev),
                shift=float(shifts[side]),
                edge=edge,
                intelligence=intel,
            )
            ideas.append(
                V18BetIdea(
                    match_name=row.match_name,
                    side=side,
                    selection=names[side],
                    sportsbet_odds=float(odds),
                    base_probability=float(base_p),
                    adjusted_probability=float(context_p),
                    base_ev_pct=float(base_ev),
                    decision_ev_pct=float(context_ev),
                    conservative_ev_pct=getattr(edge, "conservative_ev_pct", None) if edge else None,
                    confidence=getattr(edge, "confidence", "LOW") if edge else "LOW",
                    signal=getattr(edge, "signal", "MODEL EDGE") if edge else "MODEL EDGE",
                    context_shift_pp=float(shifts[side]),
                    short_reason=short,
                    detail_reason=detail,
                    football_quality=getattr(intel, "data_quality", "LOW") if intel else "LOW",
                    football_rating=getattr(intel, "overall_rating", 0.0) if intel else 0.0,
                    football_reason=intelligence_plain_summary(intel),
                )
            )

    def rank(idea: V18BetIdea):
        conservative = idea.conservative_ev_pct if idea.conservative_ev_pct is not None else -999.0
        confidence = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}.get(idea.confidence, 0)
        football_quality = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}.get(idea.football_quality, 0)
        # Market robustness remains dominant. Football data quality only breaks
        # close ties rather than overruling the market evidence.
        return (
            conservative > 0,
            confidence,
            conservative,
            football_quality,
            idea.decision_ev_pct,
        )

    ideas.sort(key=rank, reverse=True)
    return ideas


def find_dutch_ideas_v18(
    rows: list[CombinedMatch],
    fpl_context: Optional[dict] = None,
    context_inputs_by_key: Optional[dict[str, ContextInputs]] = None,
    intelligence_by_match: Optional[dict[str, MatchIntelligence]] = None,
    max_context_shift_pp: float = 1.50,
    fee_rate: float = 0.05,
    min_partial_ev_pct: float = 4.0,
) -> list[V18DutchIdea]:
    fpl_context = fpl_context or {}
    context_inputs_by_key = context_inputs_by_key or {}
    intelligence_by_match = intelligence_by_match or {}
    ideas: list[V18DutchIdea] = []

    for row in rows:
        selections = _best_effective_prices(row, fee_rate=fee_rate)
        if len(selections) != 3:
            continue
        full = calculate_dutch(selections, 100.0, complete_market=True)
        intel = intelligence_by_match.get(row.match_name)
        if full.arbitrage:
            ideas.append(
                V18DutchIdea(
                    match_name=row.match_name,
                    labels=tuple(r.label for r in full.rows),
                    sources=tuple(r.source for r in full.rows),
                    effective_odds=tuple(r.effective_odds for r in full.rows),
                    expected_ev_pct=full.return_on_stake_pct,
                    equal_profit_pct=full.return_on_stake_pct,
                    arbitrage=True,
                    complete_market=True,
                    short_reason=(
                        f"The best executable prices across Sportsbet and Polymarket imply about {full.return_on_stake_pct:.2f}% equal-return arbitrage "
                        "before slippage or price movement."
                    ),
                    football_quality=getattr(intel, "data_quality", "LOW") if intel else "LOW",
                )
            )

        manual = context_inputs_by_key.get(_key(row), ContextInputs())
        adjustment = context_adjustment_v18(row, manual, fpl_context, intel, max_shift_pp=max_context_shift_pp)
        if adjustment is None:
            continue
        probs = (adjustment.home_probability, adjustment.draw_probability, adjustment.away_probability)

        for pair in combinations(range(3), 2):
            pair_selections = [selections[i] for i in pair]
            partial = calculate_dutch(pair_selections, 100.0, complete_market=False)
            covered_probability = sum(probs[i] for i in pair)
            expected_ev = covered_probability * partial.equal_profit + (1.0 - covered_probability) * -100.0
            if expected_ev < min_partial_ev_pct:
                continue
            labels = tuple(r.label for r in partial.rows)
            ideas.append(
                V18DutchIdea(
                    match_name=row.match_name,
                    labels=labels,
                    sources=tuple(r.source for r in partial.rows),
                    effective_odds=tuple(r.effective_odds for r in partial.rows),
                    expected_ev_pct=expected_ev,
                    equal_profit_pct=partial.return_on_stake_pct,
                    arbitrage=False,
                    complete_market=False,
                    short_reason=(
                        f"The full V1.8 probability model estimates about {expected_ev:.1f}% EV for a two-result Dutch on {' + '.join(labels)}. "
                        "The uncovered third outcome still loses the full outlay, so this is not arbitrage."
                    ),
                    football_quality=getattr(intel, "data_quality", "LOW") if intel else "LOW",
                )
            )

    ideas.sort(key=lambda x: (x.arbitrage, x.expected_ev_pct), reverse=True)
    return ideas
