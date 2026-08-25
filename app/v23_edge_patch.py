from __future__ import annotations

from statistics import mean

import edge_model
import engine


_ORIGINAL = edge_model.calculate_match_edge


def _normalise(values):
    total = sum(values)
    if total <= 0:
        return tuple(values)
    return tuple(v / total for v in values)


def _avg(items):
    return _normalise(tuple(mean(item[i] for item in items) for i in range(3)))


def _disagreement(items):
    if len(items) < 2:
        return None
    return max((max(item[i] for item in items) - min(item[i] for item in items)) * 100.0 for i in range(3))


def _triplet_from_edges(outcomes, attr):
    try:
        values = tuple(getattr(outcomes[side], attr) for side in edge_model.SIDES)
    except Exception:
        return None
    return values if all(v is not None for v in values) else None


def calculate_match_edge_v23(row, min_ev_pct: float = 4.0):
    outcomes = _ORIGINAL(row, min_ev_pct=min_ev_pct)

    pm = _triplet_from_edges(outcomes, "polymarket_probability")
    pin = _triplet_from_edges(outcomes, "pinnacle_probability")
    raw_consensus = list(getattr(row, "consensus_components", ()) or ())
    consensus = [(str(x[0]), (float(x[1]), float(x[2]), float(x[3]))) for x in raw_consensus if len(x) >= 4]

    primary = []
    primary_names = []
    if pm is not None:
        primary.append(pm)
        primary_names.append("POLYMARKET")
    if pin is not None:
        primary.append(pin)
        primary_names.append("PINNACLE")

    # The sharp two-reference model remains unchanged whenever both primary
    # references exist. Consensus is a coverage fallback, not a third vote.
    if len(primary) >= 2:
        row.reference_tier = "TIER 1 — SHARP REFERENCES"
        row.reference_execution_exclusions = tuple()
        return outcomes

    components = list(primary)
    names = list(primary_names)
    exclusions = []
    tier = "SINGLE REFERENCE"

    if consensus:
        if len(primary) == 1:
            # Treat several correlated fixed-odds books as one consensus vote so
            # they do not overwhelm the surviving sharp/exchange reference.
            components.append(_avg([item[1] for item in consensus]))
            names.append("BOOKMAKER CONSENSUS")
            exclusions.extend(item[0] for item in consensus)
            tier = "TIER 1B — PRIMARY + CONSENSUS"
        elif len(consensus) >= 2:
            # When sharp/exchange references are absent, each de-vigged book is
            # an independent fallback component. Require at least two.
            components.extend(item[1] for item in consensus)
            names.extend(f"CONSENSUS:{item[0]}" for item in consensus)
            exclusions.extend(item[0] for item in consensus)
            tier = "TIER 2 — BOOKMAKER CONSENSUS"

    if not components:
        row.reference_tier = "NO INDEPENDENT REFERENCE"
        row.reference_execution_exclusions = tuple()
        return outcomes

    model = _avg(components)
    disagreement = _disagreement(components)
    source_count = len(components)
    consensus_only = tier.startswith("TIER 2")

    if consensus_only:
        confidence = "MEDIUM" if source_count >= 3 and disagreement is not None and disagreement <= 2.5 else "LOW"
    elif tier.startswith("TIER 1B"):
        confidence = "MEDIUM" if disagreement is not None and disagreement <= 4.0 else "LOW"
    else:
        confidence = "LOW"

    sb_odds = {"HOME": row.sb_home, "DRAW": row.sb_draw, "AWAY": row.sb_away}
    sb_devig = _triplet_from_edges(outcomes, "sportsbet_devig_probability")

    for i, side in enumerate(edge_model.SIDES):
        edge = outcomes[side]
        odds = sb_odds[side]
        p = model[i]
        conservative = min(item[i] for item in components)
        break_even = 1.0 / odds if odds is not None and odds > 1 else None
        edge.model_probability = p
        edge.conservative_probability = conservative
        edge.model_fair_odds = 1.0 / p if p > 0 else None
        edge.price_edge_pp = (p - break_even) * 100.0 if break_even is not None else None
        edge.sportsbet_residual_pp = (p - sb_devig[i]) * 100.0 if sb_devig is not None else None
        edge.model_ev_pct = engine.expected_value_pct(p, odds)
        edge.conservative_ev_pct = engine.expected_value_pct(conservative, odds)
        edge.required_odds_for_threshold = (1.0 + min_ev_pct / 100.0) / p if p > 0 else None
        edge.external_disagreement_pp = disagreement
        edge.source_count = source_count
        edge.confidence = confidence
        edge.bias_tags = edge_model._bias_tags(row, side, odds, edge.sportsbet_residual_pp)
        if edge.model_ev_pct is None:
            edge.signal = "NO MODEL"
        elif edge.model_ev_pct < min_ev_pct:
            edge.signal = "PASS"
        elif consensus_only:
            edge.signal = "CONSENSUS EDGE" if confidence == "MEDIUM" and edge.conservative_ev_pct is not None and edge.conservative_ev_pct > 0 else "CONSENSUS WATCH"
        elif tier.startswith("TIER 1B"):
            if edge.conservative_ev_pct is not None and edge.conservative_ev_pct > 0 and disagreement is not None and disagreement <= 4.0:
                edge.signal = "MIXED ROBUST EDGE"
            else:
                edge.signal = "EDGE - REFERENCE DISAGREEMENT"

    row.edge_source_names = tuple(names)
    row.edge_source_count = source_count
    row.edge_disagreement_pp = disagreement
    row.model_fair_home, row.model_fair_draw, row.model_fair_away = model
    row.reference_tier = tier
    row.reference_execution_exclusions = tuple(sorted(set(exclusions)))

    best = max(outcomes.values(), key=lambda x: x.model_ev_pct if x.model_ev_pct is not None else -99999.0)
    row.edge_best_selection = best.side
    row.edge_best_ev_pct = best.model_ev_pct
    row.edge_best_conservative_ev_pct = best.conservative_ev_pct
    row.edge_signal = best.signal
    row.edge_confidence = best.confidence
    row.edge_bias_tags = best.bias_tags
    return outcomes


edge_model.calculate_match_edge = calculate_match_edge_v23
