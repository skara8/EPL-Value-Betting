from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean
from typing import Optional, Any

import engine
from engine import CombinedMatch


SIDES = ("HOME", "DRAW", "AWAY")


@dataclass
class PoissonMarketFit:
    source: str
    lambda_home: float
    lambda_away: float
    home_probability: float
    draw_probability: float
    away_probability: float
    fit_error_pct: float


@dataclass
class OutcomeEdge:
    side: str
    name: str
    sportsbet_odds: Optional[float]
    break_even_probability: Optional[float]
    sportsbet_raw_probability: Optional[float]
    sportsbet_devig_probability: Optional[float]
    polymarket_probability: Optional[float]
    pinnacle_probability: Optional[float]
    ah_probability: Optional[float]
    model_probability: Optional[float]
    conservative_probability: Optional[float]
    model_fair_odds: Optional[float]
    price_edge_pp: Optional[float]
    sportsbet_residual_pp: Optional[float]
    model_ev_pct: Optional[float]
    conservative_ev_pct: Optional[float]
    required_odds_for_threshold: Optional[float]
    external_disagreement_pp: Optional[float]
    bias_tags: tuple[str, ...]
    confidence: str
    signal: str
    source_count: int


# ---------------------------------------------------------------------------
# Vig / margin removal
# ---------------------------------------------------------------------------


def raw_implied_probabilities(
    h: Optional[float], d: Optional[float], a: Optional[float]
) -> Optional[tuple[float, float, float]]:
    if any(x is None or x <= 1 for x in (h, d, a)):
        return None
    return 1.0 / float(h), 1.0 / float(d), 1.0 / float(a)


def proportional_devig(
    h: Optional[float], d: Optional[float], a: Optional[float]
) -> Optional[tuple[float, float, float]]:
    raw = raw_implied_probabilities(h, d, a)
    if raw is None:
        return None
    total = sum(raw)
    if total <= 0:
        return None
    return tuple(x / total for x in raw)  # type: ignore[return-value]


def power_devig(
    h: Optional[float], d: Optional[float], a: Optional[float]
) -> Optional[tuple[float, float, float]]:
    """
    Remove a 3-way bookmaker margin with the power method.

    q_i = 1 / odds_i are the raw implied probabilities.  We solve k such that
    sum(q_i ** k) = 1 and return q_i ** k.

    Compared with simple proportional normalisation, k > 1 removes relatively
    more probability from longshots.  That makes it useful as a diagnostic in
    markets where favourite-longshot bias / differential margin allocation is
    plausible.  It is NOT used to calculate EV against Sportsbet's own price.
    """
    raw = raw_implied_probabilities(h, d, a)
    if raw is None:
        return None
    if abs(sum(raw) - 1.0) < 1e-12:
        return raw

    low, high = 0.01, 10.0
    for _ in range(100):
        mid = (low + high) / 2.0
        value = sum(q ** mid for q in raw)
        # q < 1, so increasing k lowers the sum.
        if value > 1.0:
            low = mid
        else:
            high = mid
    k = (low + high) / 2.0
    result = tuple(q ** k for q in raw)
    total = sum(result)
    if total <= 0:
        return None
    return tuple(x / total for x in result)  # type: ignore[return-value]


def two_way_fair_odds(
    first_odds: Optional[float], second_odds: Optional[float]
) -> Optional[tuple[float, float]]:
    if first_odds is None or second_odds is None or first_odds <= 1 or second_odds <= 1:
        return None
    a, b = 1.0 / first_odds, 1.0 / second_odds
    total = a + b
    if total <= 0:
        return None
    p_a, p_b = a / total, b / total
    return 1.0 / p_a, 1.0 / p_b


# ---------------------------------------------------------------------------
# Asian Handicap + total -> Poisson score model
# ---------------------------------------------------------------------------


def _quarter_parts(line: float) -> tuple[float, ...]:
    """Split a .25/.75 Asian line into its two neighbouring half-lines."""
    quarters = round(line * 4)
    if quarters % 2:
        return line - 0.25, line + 0.25
    return (line,)


def _single_line_profit(value: float, line: float, decimal_odds: float) -> float:
    adjusted = value + line
    if adjusted > 1e-9:
        return decimal_odds - 1.0
    if adjusted < -1e-9:
        return -1.0
    return 0.0


def asian_handicap_profit(goal_difference: int, line: float, decimal_odds: float) -> float:
    parts = _quarter_parts(line)
    return mean(_single_line_profit(float(goal_difference), part, decimal_odds) for part in parts)


def total_over_profit(total_goals: int, line: float, decimal_odds: float) -> float:
    parts = _quarter_parts(line)
    # Over line T is equivalent to (total_goals - T) > 0.
    return mean(_single_line_profit(float(total_goals), -part, decimal_odds) for part in parts)


def _poisson_array(lam: float, max_goals: int = 13) -> list[float]:
    values = [math.exp(-lam)]
    for goals in range(1, max_goals + 1):
        values.append(values[-1] * lam / goals)
    return values


def _score_distribution(lambda_home: float, lambda_away: float, max_goals: int = 13):
    home = _poisson_array(lambda_home, max_goals)
    away = _poisson_array(lambda_away, max_goals)
    mass = sum(home) * sum(away)
    if mass <= 0:
        return []
    output = []
    for h, ph in enumerate(home):
        for a, pa in enumerate(away):
            output.append((h, a, ph * pa / mass))
    return output


def _market_expected_profit(
    lambda_home: float,
    lambda_away: float,
    ah_line: float,
    ah_fair_odds: float,
    total_line: float,
    over_fair_odds: float,
) -> tuple[float, float, tuple[float, float, float]]:
    ah_ev = total_ev = 0.0
    p_home = p_draw = p_away = 0.0
    for h, a, probability in _score_distribution(lambda_home, lambda_away):
        ah_ev += probability * asian_handicap_profit(h - a, ah_line, ah_fair_odds)
        total_ev += probability * total_over_profit(h + a, total_line, over_fair_odds)
        if h > a:
            p_home += probability
        elif h == a:
            p_draw += probability
        else:
            p_away += probability
    return ah_ev, total_ev, (p_home, p_draw, p_away)


def _fit_from_market(
    source: str,
    ah_home_line: Optional[float],
    ah_home_odds: Optional[float],
    ah_away_odds: Optional[float],
    total_line: Optional[float],
    total_over: Optional[float],
    total_under: Optional[float],
) -> Optional[PoissonMarketFit]:
    if any(x is None for x in (ah_home_line, ah_home_odds, ah_away_odds, total_line, total_over, total_under)):
        return None

    ah_fair = two_way_fair_odds(ah_home_odds, ah_away_odds)
    total_fair = two_way_fair_odds(total_over, total_under)
    if ah_fair is None or total_fair is None:
        return None

    ah_line = float(ah_home_line)
    goals_line = float(total_line)
    ah_fair_home = ah_fair[0]
    over_fair = total_fair[0]

    # Search around the quoted total and handicap.  The market lines are not
    # literally the Poisson means, but they are excellent centres for a small
    # numerical calibration.
    total_min = max(1.0, goals_line - 1.15)
    total_max = min(5.5, goals_line + 1.15)
    diff_centre = -ah_line
    diff_min = max(-3.0, diff_centre - 1.5)
    diff_max = min(3.0, diff_centre + 1.5)

    best: Optional[tuple[float, float, float, tuple[float, float, float]]] = None

    def consider(total_lambda: float, diff_lambda: float, current_best):
        lh = (total_lambda + diff_lambda) / 2.0
        la = (total_lambda - diff_lambda) / 2.0
        if lh < 0.08 or la < 0.08:
            return current_best
        ah_ev, over_ev, probs = _market_expected_profit(
            lh, la, ah_line, ah_fair_home, goals_line, over_fair
        )
        loss = ah_ev * ah_ev + over_ev * over_ev
        candidate = (loss, total_lambda, diff_lambda, probs)
        if current_best is None or candidate[0] < current_best[0]:
            return candidate
        return current_best

    t = total_min
    while t <= total_max + 1e-9:
        d = diff_min
        while d <= diff_max + 1e-9:
            best = consider(t, d, best)
            d += 0.10
        t += 0.10

    if best is None:
        return None

    _, best_t, best_d, _ = best
    # Fine local refinement.
    for t_step in (0.04, 0.015):
        new_best = best
        for ti in range(-6, 7):
            total_lambda = best_t + ti * t_step
            for di in range(-6, 7):
                diff_lambda = best_d + di * t_step
                new_best = consider(total_lambda, diff_lambda, new_best)
        best = new_best
        _, best_t, best_d, _ = best

    loss, best_t, best_d, probabilities = best
    lh = (best_t + best_d) / 2.0
    la = (best_t - best_d) / 2.0
    if lh <= 0 or la <= 0:
        return None

    return PoissonMarketFit(
        source=source,
        lambda_home=lh,
        lambda_away=la,
        home_probability=probabilities[0],
        draw_probability=probabilities[1],
        away_probability=probabilities[2],
        fit_error_pct=math.sqrt(loss) * 100.0,
    )


def fit_ah_total_model(row: CombinedMatch) -> Optional[PoissonMarketFit]:
    """
    Prefer Pinnacle AH + totals because it is external to the Sportsbet price.
    Fall back to Sportsbet AH + totals only as a same-bookmaker diagnostic.
    """
    pin = _fit_from_market(
        "PINNACLE",
        getattr(row, "pin_ah_home_line", None),
        getattr(row, "pin_ah_home_odds", None),
        getattr(row, "pin_ah_away_odds", None),
        getattr(row, "pin_total_line", None),
        getattr(row, "pin_total_over", None),
        getattr(row, "pin_total_under", None),
    )
    if pin is not None:
        return pin

    return _fit_from_market(
        "SPORTSBET-DIAGNOSTIC",
        getattr(row, "sb_ah_home_line", None),
        getattr(row, "sb_ah_home_odds", None),
        getattr(row, "sb_ah_away_odds", None),
        getattr(row, "sb_total_line", None),
        getattr(row, "sb_total_over", None),
        getattr(row, "sb_total_under", None),
    )


# ---------------------------------------------------------------------------
# External fair model and bias diagnostics
# ---------------------------------------------------------------------------


def _normalise(values: tuple[float, float, float]) -> tuple[float, float, float]:
    total = sum(values)
    if total <= 0:
        return values
    return values[0] / total, values[1] / total, values[2] / total


def _average_probabilities(items: list[tuple[float, float, float]]) -> Optional[tuple[float, float, float]]:
    if not items:
        return None
    values = tuple(mean(item[i] for item in items) for i in range(3))
    return _normalise(values)  # type: ignore[arg-type]


def _selection_is_sportsbet_favourite(row: CombinedMatch, side: str) -> bool:
    if side == "HOME":
        return engine.normalise_text(row.sportsbet_favourite) == engine.normalise_text(row.home_team)
    if side == "AWAY":
        return engine.normalise_text(row.sportsbet_favourite) == engine.normalise_text(row.away_team)
    return False


def _bias_tags(
    row: CombinedMatch,
    side: str,
    sportsbet_odds: Optional[float],
    market_residual_pp: Optional[float],
) -> tuple[str, ...]:
    tags: list[str] = []
    favourite = _selection_is_sportsbet_favourite(row, side)

    if favourite:
        tags.append("FAVOURITE")
        if market_residual_pp is not None and market_residual_pp > 0.50:
            tags.append("FAVOURITE-LONGSHOT ALIGNMENT")

    if side == "AWAY" and row.away_favourite == "YES":
        tags.append("AWAY-FAVOURITE")
        if market_residual_pp is not None and market_residual_pp > 1.00:
            tags.append("POSSIBLE HOME-ADVANTAGE OVERPRICE")

    if not favourite and sportsbet_odds is not None and sportsbet_odds >= 5.0:
        tags.append("LONGSHOT CAUTION")

    return tuple(tags)


def _confidence(source_count: int, disagreement_pp: Optional[float], ah_fit: Optional[PoissonMarketFit]) -> str:
    if source_count >= 2 and disagreement_pp is not None and disagreement_pp <= 2.0:
        if ah_fit is None or ah_fit.source != "PINNACLE" or ah_fit.fit_error_pct <= 1.0:
            return "HIGH"
    if source_count >= 2 and disagreement_pp is not None and disagreement_pp <= 4.0:
        return "MEDIUM"
    return "LOW"


def _signal(
    model_ev: Optional[float],
    conservative_ev: Optional[float],
    source_count: int,
    disagreement_pp: Optional[float],
    bias_tags: tuple[str, ...],
    min_ev_pct: float,
) -> str:
    if model_ev is None:
        return "NO MODEL"
    if model_ev < min_ev_pct:
        return "PASS"
    if source_count < 2:
        return "EDGE - SINGLE REFERENCE"
    if conservative_ev is None or conservative_ev <= 0:
        return "EDGE - REFERENCE DISAGREEMENT"
    if disagreement_pp is not None and disagreement_pp > 4.0:
        return "EDGE - DIVERGENT MARKETS"
    if any(tag in bias_tags for tag in ("AWAY-FAVOURITE", "FAVOURITE-LONGSHOT ALIGNMENT")):
        return "BIAS-ALIGNED ROBUST EDGE"
    return "ROBUST EDGE"


def calculate_match_edge(row: CombinedMatch, min_ev_pct: float = 4.0) -> dict[str, OutcomeEdge]:
    # Sportsbet is explicitly excluded from the external fair model.  We still
    # de-vig it to measure how the bookmaker allocates its margin.
    sb_raw = raw_implied_probabilities(row.sb_home, row.sb_draw, row.sb_away)
    sb_devig = power_devig(row.sb_home, row.sb_draw, row.sb_away)

    # Polymarket's executable YES asks are already normalised in the base app.
    pm = None
    if all(x is not None for x in (row.pm_fair_home, row.pm_fair_draw, row.pm_fair_away)):
        pm = (float(row.pm_fair_home), float(row.pm_fair_draw), float(row.pm_fair_away))

    pin = power_devig(
        getattr(row, "pin_home", None),
        getattr(row, "pin_draw", None),
        getattr(row, "pin_away", None),
    )

    ah_fit = fit_ah_total_model(row)
    ah_probs = None
    if ah_fit is not None:
        ah_probs = (ah_fit.home_probability, ah_fit.draw_probability, ah_fit.away_probability)

    # Treat Pinnacle as one provider even when it gives us both 1X2 and AH.
    # This avoids accidentally giving the same bookmaker two full votes.
    pin_parts: list[tuple[float, float, float]] = []
    if pin is not None:
        pin_parts.append(pin)
    if ah_fit is not None and ah_fit.source == "PINNACLE" and ah_probs is not None:
        pin_parts.append(ah_probs)
    pin_component = _average_probabilities(pin_parts)

    provider_components: list[tuple[float, float, float]] = []
    provider_names: list[str] = []
    if pm is not None:
        provider_components.append(pm)
        provider_names.append("POLYMARKET")
    if pin_component is not None:
        provider_components.append(pin_component)
        provider_names.append("PINNACLE")

    model = _average_probabilities(provider_components)
    source_count = len(provider_components)

    disagreement_pp: Optional[float] = None
    if len(provider_components) >= 2:
        disagreement_pp = max(
            abs(provider_components[0][i] - provider_components[1][i]) * 100.0
            for i in range(3)
        )

    names = {"HOME": row.home_team, "DRAW": "Draw", "AWAY": row.away_team}
    sb_odds = {"HOME": row.sb_home, "DRAW": row.sb_draw, "AWAY": row.sb_away}
    output: dict[str, OutcomeEdge] = {}

    for i, side in enumerate(SIDES):
        odds = sb_odds[side]
        break_even = 1.0 / odds if odds is not None and odds > 1 else None
        model_p = model[i] if model is not None else None
        conservative_p = min(x[i] for x in provider_components) if provider_components else None
        sb_raw_p = sb_raw[i] if sb_raw is not None else None
        sb_devig_p = sb_devig[i] if sb_devig is not None else None
        pm_p = pm[i] if pm is not None else None
        pin_p = pin_component[i] if pin_component is not None else None
        ah_p = ah_probs[i] if ah_probs is not None else None

        model_ev = engine.expected_value_pct(model_p, odds)
        conservative_ev = engine.expected_value_pct(conservative_p, odds)
        price_edge_pp = (
            (model_p - break_even) * 100.0
            if model_p is not None and break_even is not None else None
        )
        residual_pp = (
            (model_p - sb_devig_p) * 100.0
            if model_p is not None and sb_devig_p is not None else None
        )
        fair_odds = 1.0 / model_p if model_p is not None and model_p > 0 else None
        required = (
            (1.0 + min_ev_pct / 100.0) / model_p
            if model_p is not None and model_p > 0 else None
        )
        tags = _bias_tags(row, side, odds, residual_pp)
        conf = _confidence(source_count, disagreement_pp, ah_fit)
        sig = _signal(model_ev, conservative_ev, source_count, disagreement_pp, tags, min_ev_pct)

        output[side] = OutcomeEdge(
            side=side,
            name=names[side],
            sportsbet_odds=odds,
            break_even_probability=break_even,
            sportsbet_raw_probability=sb_raw_p,
            sportsbet_devig_probability=sb_devig_p,
            polymarket_probability=pm_p,
            pinnacle_probability=pin_p,
            ah_probability=ah_p,
            model_probability=model_p,
            conservative_probability=conservative_p,
            model_fair_odds=fair_odds,
            price_edge_pp=price_edge_pp,
            sportsbet_residual_pp=residual_pp,
            model_ev_pct=model_ev,
            conservative_ev_pct=conservative_ev,
            required_odds_for_threshold=required,
            external_disagreement_pp=disagreement_pp,
            bias_tags=tags,
            confidence=conf,
            signal=sig,
            source_count=source_count,
        )

    # Attach useful summary fields directly to the live row for the GUI and DB.
    setattr(row, "edge_outcomes", output)
    setattr(row, "edge_source_names", tuple(provider_names))
    setattr(row, "edge_source_count", source_count)
    setattr(row, "edge_disagreement_pp", disagreement_pp)
    setattr(row, "sb_devig_power_home", sb_devig[0] if sb_devig else None)
    setattr(row, "sb_devig_power_draw", sb_devig[1] if sb_devig else None)
    setattr(row, "sb_devig_power_away", sb_devig[2] if sb_devig else None)
    setattr(row, "pin_power_home", pin[0] if pin else None)
    setattr(row, "pin_power_draw", pin[1] if pin else None)
    setattr(row, "pin_power_away", pin[2] if pin else None)
    setattr(row, "ah_model_source", ah_fit.source if ah_fit else "NONE")
    setattr(row, "ah_model_fit_error_pct", ah_fit.fit_error_pct if ah_fit else None)
    setattr(row, "ah_model_lambda_home", ah_fit.lambda_home if ah_fit else None)
    setattr(row, "ah_model_lambda_away", ah_fit.lambda_away if ah_fit else None)
    setattr(row, "ah_model_home", ah_probs[0] if ah_probs else None)
    setattr(row, "ah_model_draw", ah_probs[1] if ah_probs else None)
    setattr(row, "ah_model_away", ah_probs[2] if ah_probs else None)
    setattr(row, "model_fair_home", model[0] if model else None)
    setattr(row, "model_fair_draw", model[1] if model else None)
    setattr(row, "model_fair_away", model[2] if model else None)

    best = max(
        output.values(),
        key=lambda x: x.model_ev_pct if x.model_ev_pct is not None else -99999,
    )
    setattr(row, "edge_best_selection", best.side)
    setattr(row, "edge_best_ev_pct", best.model_ev_pct)
    setattr(row, "edge_best_conservative_ev_pct", best.conservative_ev_pct)
    setattr(row, "edge_signal", best.signal)
    setattr(row, "edge_confidence", best.confidence)
    setattr(row, "edge_bias_tags", best.bias_tags)

    return output


def enrich_edge_model(rows: list[CombinedMatch], min_ev_pct: float = 4.0) -> list[CombinedMatch]:
    for row in rows:
        calculate_match_edge(row, min_ev_pct)
    return rows


def edge_rank_key(row: CombinedMatch) -> tuple[float, float]:
    conservative = getattr(row, "edge_best_conservative_ev_pct", None)
    model = getattr(row, "edge_best_ev_pct", None)
    return (
        conservative if conservative is not None else -99999.0,
        model if model is not None else -99999.0,
    )


def edge_summary(row: CombinedMatch, min_ev_pct: float = 4.0) -> str:
    outcomes: dict[str, OutcomeEdge] = getattr(row, "edge_outcomes", {})
    best_side = getattr(row, "edge_best_selection", "—")
    best = outcomes.get(best_side)
    if best is None:
        return f"{row.match_name}\n\nNo independent fair-probability model could be calculated."

    sources = ", ".join(getattr(row, "edge_source_names", ())) or "none"
    tags = ", ".join(best.bias_tags) if best.bias_tags else "none"

    lines = [
        row.match_name,
        "",
        "WHAT V1.5 IS DOING",
        "Sportsbet is the price being tested, not the fair-probability baseline. Its 1X2 margin is removed separately so we can see how Sportsbet itself shapes the market.",
        f"Independent fair model sources: {sources}. Pinnacle AH/totals are used when available; Sportsbet AH/totals are diagnostic only and are not fed back into Sportsbet EV.",
        "",
        f"BEST MODEL OUTCOME: {best.name}",
        f"Sportsbet price: {engine.fmt_odds(best.sportsbet_odds)}",
        f"Break-even probability at that price: {engine.fmt_probability(best.break_even_probability)}",
        f"V1.5 independent fair probability: {engine.fmt_probability(best.model_probability)}",
        f"Model fair odds: {engine.fmt_odds(best.model_fair_odds)}",
        f"Probability edge over the offered price: {engine.fmt_pct(best.price_edge_pp)} points",
        f"Model EV: {engine.fmt_pct(best.model_ev_pct)}",
        f"Conservative EV (worst external reference): {engine.fmt_pct(best.conservative_ev_pct)}",
        f"Minimum Sportsbet odds needed for +{min_ev_pct:.2f}% EV: {engine.fmt_odds(best.required_odds_for_threshold)}",
        f"Signal: {best.signal}",
        f"Confidence: {best.confidence}",
        f"Bias research tags: {tags}",
        "",
        "WHY THE SPORTSBET DE-VIG NUMBER IS DIFFERENT",
        f"Sportsbet raw implied probability: {engine.fmt_probability(best.sportsbet_raw_probability)}",
        f"Sportsbet power-method de-vig probability: {engine.fmt_probability(best.sportsbet_devig_probability)}",
        f"External model minus Sportsbet de-vig: {engine.fmt_pct(best.sportsbet_residual_pp)} percentage points.",
        "That residual helps identify bookmaker shading / favourite-longshot or home-field patterns, but it is not itself an EV calculation.",
        "",
        "REFERENCE INPUTS",
        f"Polymarket ask-normalised probability: {engine.fmt_probability(best.polymarket_probability)}",
        f"Pinnacle provider probability: {engine.fmt_probability(best.pinnacle_probability)}",
        f"AH + totals Poisson probability: {engine.fmt_probability(best.ah_probability)}",
        f"External market disagreement: {engine.fmt_pct(best.external_disagreement_pp)} percentage points",
    ]

    ah_source = getattr(row, "ah_model_source", "NONE")
    if ah_source != "NONE":
        lines.extend([
            f"AH model source: {ah_source}",
            f"Implied expected goals: {row.home_team} {getattr(row, 'ah_model_lambda_home', 0):.2f}, {row.away_team} {getattr(row, 'ah_model_lambda_away', 0):.2f}",
            f"AH/totals calibration error: {engine.fmt_pct(getattr(row, 'ah_model_fit_error_pct', None))}",
        ])

    lines.extend([
        "",
        "IMPORTANT",
        "The favourite-longshot and away-favourite research does not receive a hard-coded probability bonus. V1.5 marks when the independent model and the historical bias hypothesis point in the same direction. A numerical bias adjustment should only be learned after results and closing prices are collected and backtested out-of-sample.",
    ])
    return "\n".join(lines)
