from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Optional

import engine
from engine import CombinedMatch


FPL_BOOTSTRAP = "https://fantasy.premierleague.com/api/bootstrap-static/"

# These are research weights, not fitted coefficients. They determine how much
# each contextual judgement contributes to a small capped probability tilt.
# The independent V1.5 market model remains visible and untouched.
FACTOR_WEIGHTS = {
    "player_lineup": 0.35,
    "recent_performance": 0.25,
    "tactical_matchup": 0.20,
    "manager_coaching": 0.10,
    "transfer_squad": 0.05,
    "schedule_rest": 0.05,
}

FACTOR_LABELS = {
    "player_lineup": "Player / expected line-up",
    "recent_performance": "Recent underlying performance",
    "tactical_matchup": "Tactical / style matchup",
    "manager_coaching": "Manager / coaching matchup",
    "transfer_squad": "Transfer / squad-change assessment",
    "schedule_rest": "Schedule / rest / travel",
}

POSITIONS = ("GKP", "DEF", "MID", "FWD")


@dataclass
class FPLPlayerAvailability:
    name: str
    position: str
    status: str
    chance: float
    news: str
    importance: float


@dataclass
class FPLTeamContext:
    team: str
    available_players: int = 0
    doubtful_players: int = 0
    unavailable_players: int = 0
    ignored_departures: int = 0
    availability_penalty: float = 0.0
    position_penalty: dict[str, float] = field(default_factory=lambda: {p: 0.0 for p in POSITIONS})
    strength_home: Optional[float] = None
    strength_away: Optional[float] = None
    attack_home: Optional[float] = None
    attack_away: Optional[float] = None
    defence_home: Optional[float] = None
    defence_away: Optional[float] = None
    unavailable: list[FPLPlayerAvailability] = field(default_factory=list)


@dataclass
class ContextInputs:
    player_lineup: float = 0.0
    recent_performance: float = 0.0
    tactical_matchup: float = 0.0
    manager_coaching: float = 0.0
    transfer_squad: float = 0.0
    schedule_rest: float = 0.0
    home_transfer_spend_m: Optional[float] = None
    away_transfer_spend_m: Optional[float] = None
    home_manager: str = ""
    away_manager: str = ""
    notes: str = ""


@dataclass
class ContextAdjustment:
    home_probability: float
    draw_probability: float
    away_probability: float
    home_shift_pp: float
    draw_shift_pp: float
    away_shift_pp: float
    weighted_score: float
    max_shift_pp: float
    auto_availability_rating: float
    factor_breakdown: dict[str, float]


# ---------------------------------------------------------------------------
# FPL current-player / availability feed
# ---------------------------------------------------------------------------


def _canonical_team(name: str) -> Optional[str]:
    return engine.canonical_epl_club(name)


def _position_name(element_type: Any) -> str:
    return {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}.get(element_type, "UNK")


def _safe_number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _player_chance(player: dict[str, Any]) -> float:
    status = str(player.get("status") or "a").lower()
    if status == "a":
        return 1.0
    raw = player.get("chance_of_playing_next_round")
    if raw is None:
        if status in {"i", "u", "s"}:
            return 0.0
        if status == "d":
            return 0.75
        return 0.5
    return max(0.0, min(1.0, _safe_number(raw) / 100.0))


def _is_departed_player_news(news: str) -> bool:
    """True when FPL news clearly describes a player no longer in the squad.

    FPL can retain transferred or loaned-out players in bootstrap data. Those
    players are not injuries and must not create an availability penalty.
    """
    text = (news or "").strip().lower()
    if not text:
        return False
    phrases = (
        "has joined ",
        "have joined ",
        "joined permanently",
        "joined on loan",
        "on loan for the rest",
        "on loan until",
        "loaned to ",
        "has signed for ",
        "signed permanently",
        "has returned to ",
        "returned to ",
        "has left the club",
        "left the club",
        "transferred to ",
        "permanently transferred",
    )
    return any(phrase in text for phrase in phrases)


def fetch_fpl_team_context() -> dict[str, FPLTeamContext]:
    """Fetch free public FPL bootstrap data and summarise current availability.

    FPL player prices/performance are only a rough proxy for player importance.
    Clear transfer/loan departures are ignored so stale squad records cannot be
    mistaken for injuries.
    """
    payload = engine.get_json(FPL_BOOTSTRAP, provider_name="Fantasy Premier League")
    teams = payload.get("teams") if isinstance(payload, dict) else None
    players = payload.get("elements") if isinstance(payload, dict) else None
    if not isinstance(teams, list) or not isinstance(players, list):
        raise RuntimeError("Fantasy Premier League: unexpected bootstrap response.")

    team_id_map: dict[int, str] = {}
    contexts: dict[str, FPLTeamContext] = {}
    for team in teams:
        if not isinstance(team, dict):
            continue
        canonical = _canonical_team(str(team.get("name") or team.get("short_name") or ""))
        if not canonical:
            continue
        team_id = int(team.get("id"))
        team_id_map[team_id] = canonical
        contexts[canonical] = FPLTeamContext(
            team=canonical,
            strength_home=engine.safe_float(team.get("strength_overall_home")),
            strength_away=engine.safe_float(team.get("strength_overall_away")),
            attack_home=engine.safe_float(team.get("strength_attack_home")),
            attack_away=engine.safe_float(team.get("strength_attack_away")),
            defence_home=engine.safe_float(team.get("strength_defence_home")),
            defence_away=engine.safe_float(team.get("strength_defence_away")),
        )

    by_position_prices: dict[str, list[float]] = {"GKP": [], "DEF": [], "MID": [], "FWD": []}
    for player in players:
        if not isinstance(player, dict):
            continue
        news = str(player.get("news") or "").strip()
        if _is_departed_player_news(news):
            continue
        position = _position_name(player.get("element_type"))
        price = _safe_number(player.get("now_cost")) / 10.0
        if position in by_position_prices and price > 0:
            by_position_prices[position].append(price)
    medians = {pos: (median(values) if values else 5.0) for pos, values in by_position_prices.items()}

    for player in players:
        if not isinstance(player, dict):
            continue
        canonical = team_id_map.get(int(player.get("team") or 0))
        if not canonical or canonical not in contexts:
            continue
        ctx = contexts[canonical]
        news = str(player.get("news") or "").strip()
        if _is_departed_player_news(news):
            ctx.ignored_departures += 1
            continue

        chance = _player_chance(player)
        status = str(player.get("status") or "a").lower()
        position = _position_name(player.get("element_type"))
        price = _safe_number(player.get("now_cost")) / 10.0
        price_ratio = price / max(0.1, medians.get(position, 5.0))
        price_ratio = max(0.5, min(2.25, price_ratio))

        minutes = _safe_number(player.get("minutes"))
        ppg = _safe_number(player.get("points_per_game"))
        xgi90 = _safe_number(player.get("expected_goal_involvements_per_90"))
        performance = min(1.5, max(0.0, ppg / 5.5))
        if position in {"MID", "FWD"}:
            performance = max(performance, min(1.5, xgi90 / 0.65))
        sample = min(1.0, minutes / 360.0) if minutes > 0 else 0.0
        importance = 0.72 * price_ratio + 0.28 * (sample * performance + (1 - sample) * 0.5)
        importance = max(0.35, min(2.5, importance))

        if chance >= 0.999:
            ctx.available_players += 1
        elif chance > 0:
            ctx.doubtful_players += 1
        else:
            ctx.unavailable_players += 1

        loss = importance * (1.0 - chance)
        ctx.availability_penalty += loss
        if position in ctx.position_penalty:
            ctx.position_penalty[position] += loss
        if chance < 0.999:
            ctx.unavailable.append(
                FPLPlayerAvailability(
                    name=str(player.get("web_name") or player.get("second_name") or "Unknown"),
                    position=position,
                    status=status,
                    chance=chance,
                    news=news,
                    importance=importance,
                )
            )

    for ctx in contexts.values():
        ctx.unavailable.sort(key=lambda x: x.importance * (1.0 - x.chance), reverse=True)
    return contexts


def availability_rating(home: Optional[FPLTeamContext], away: Optional[FPLTeamContext]) -> float:
    """Return -3..+3; positive means relative player availability favours home."""
    if home is None or away is None:
        return 0.0
    difference = away.availability_penalty - home.availability_penalty
    return max(-3.0, min(3.0, difference * 1.25))


def biggest_position_gap(home: Optional[FPLTeamContext], away: Optional[FPLTeamContext]) -> tuple[str, float]:
    """Return the position with the largest penalty gap; positive favours home."""
    if home is None or away is None:
        return "", 0.0
    gaps = {
        position: away.position_penalty.get(position, 0.0) - home.position_penalty.get(position, 0.0)
        for position in POSITIONS
    }
    position = max(gaps, key=lambda p: abs(gaps[p]))
    return position, gaps[position]


# ---------------------------------------------------------------------------
# Context probability tilt
# ---------------------------------------------------------------------------


def _clamp_rating(value: float) -> float:
    return max(-3.0, min(3.0, float(value)))


def weighted_context_score(inputs: ContextInputs, auto_availability: float = 0.0) -> tuple[float, dict[str, float]]:
    values = {
        "player_lineup": _clamp_rating(inputs.player_lineup + auto_availability),
        "recent_performance": _clamp_rating(inputs.recent_performance),
        "tactical_matchup": _clamp_rating(inputs.tactical_matchup),
        "manager_coaching": _clamp_rating(inputs.manager_coaching),
        "transfer_squad": _clamp_rating(inputs.transfer_squad),
        "schedule_rest": _clamp_rating(inputs.schedule_rest),
    }
    contributions = {key: values[key] * FACTOR_WEIGHTS[key] for key in FACTOR_WEIGHTS}
    return sum(contributions.values()), contributions


def _softmax_tilt(probs: tuple[float, float, float], theta: float) -> tuple[float, float, float]:
    h, d, a = probs
    raw = (h * math.exp(theta), d, a * math.exp(-theta))
    total = sum(raw)
    return raw[0] / total, raw[1] / total, raw[2] / total


def apply_context_tilt(
    base_probs: tuple[float, float, float],
    weighted_score: float,
    max_shift_pp: float = 1.50,
) -> tuple[float, float, float]:
    total = sum(base_probs)
    if total <= 0:
        raise ValueError("Base probabilities must have positive mass.")
    probs = tuple(max(1e-8, p / total) for p in base_probs)
    strength = min(1.0, abs(weighted_score) / 3.0)
    target_shift = max(0.0, float(max_shift_pp)) / 100.0 * strength
    if target_shift <= 1e-12:
        return probs  # type: ignore[return-value]

    direction = 1.0 if weighted_score > 0 else -1.0
    low, high = 0.0, 1.5
    best = probs
    for _ in range(70):
        mid = (low + high) / 2.0
        candidate = _softmax_tilt(probs, direction * mid)
        largest = max(abs(candidate[i] - probs[i]) for i in range(3))
        best = candidate
        if largest < target_shift:
            low = mid
        else:
            high = mid
    return best


def context_adjustment_for_match(
    row: CombinedMatch,
    inputs: ContextInputs,
    fpl_context: Optional[dict[str, FPLTeamContext]] = None,
    max_shift_pp: float = 1.50,
) -> Optional[ContextAdjustment]:
    base = (
        getattr(row, "model_fair_home", None),
        getattr(row, "model_fair_draw", None),
        getattr(row, "model_fair_away", None),
    )
    if any(p is None for p in base):
        return None

    auto = 0.0
    if fpl_context:
        auto = availability_rating(fpl_context.get(row.home_team), fpl_context.get(row.away_team))
    score, breakdown = weighted_context_score(inputs, auto)
    adjusted = apply_context_tilt((float(base[0]), float(base[1]), float(base[2])), score, max_shift_pp=max_shift_pp)
    return ContextAdjustment(
        home_probability=adjusted[0],
        draw_probability=adjusted[1],
        away_probability=adjusted[2],
        home_shift_pp=(adjusted[0] - float(base[0])) * 100.0,
        draw_shift_pp=(adjusted[1] - float(base[1])) * 100.0,
        away_shift_pp=(adjusted[2] - float(base[2])) * 100.0,
        weighted_score=score,
        max_shift_pp=max_shift_pp,
        auto_availability_rating=auto,
        factor_breakdown=breakdown,
    )


def adjusted_ev(row: CombinedMatch, adjustment: ContextAdjustment) -> dict[str, Optional[float]]:
    return {
        "HOME": engine.expected_value_pct(adjustment.home_probability, row.sb_home),
        "DRAW": engine.expected_value_pct(adjustment.draw_probability, row.sb_draw),
        "AWAY": engine.expected_value_pct(adjustment.away_probability, row.sb_away),
    }


def _position_gap_text(home: Optional[FPLTeamContext], away: Optional[FPLTeamContext]) -> str:
    position, gap = biggest_position_gap(home, away)
    if not position or abs(gap) < 0.15:
        return "No large position-group availability difference."
    label = {"GKP": "goalkeeper", "DEF": "defence", "MID": "midfield", "FWD": "attack"}.get(position, position)
    if gap > 0:
        return f"The biggest availability difference is in {label}, favouring the home side."
    return f"The biggest availability difference is in {label}, favouring the away side."


def fpl_context_summary(row: CombinedMatch, contexts: dict[str, FPLTeamContext]) -> str:
    def describe(ctx: Optional[FPLTeamContext]) -> str:
        if ctx is None:
            return "FPL data unavailable"
        top = []
        for p in ctx.unavailable[:4]:
            chance = int(round(p.chance * 100))
            detail = f"{p.name} ({p.position}, {chance}% chance)"
            if p.news:
                detail += f" — {p.news}"
            top.append(detail)
        player_text = "; ".join(top) if top else "no current flagged availability issues"
        return (
            f"current availability penalty {ctx.availability_penalty:.2f}; "
            f"unavailable {ctx.unavailable_players}; doubtful {ctx.doubtful_players}; "
            f"ignored stale transfer/loan records {ctx.ignored_departures}; {player_text}"
        )

    home = contexts.get(row.home_team)
    away = contexts.get(row.away_team)
    return (
        f"{row.home_team}: {describe(home)}\n\n"
        f"{row.away_team}: {describe(away)}\n\n"
        f"{_position_gap_text(home, away)}"
    )
