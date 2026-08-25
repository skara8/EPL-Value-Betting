from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Optional
from urllib.parse import quote

import engine
from engine import BRISBANE, CombinedMatch


PINNACLE_BASE = "https://api.pulsescore.net/api/ps3838"
PINNACLE_LEAGUES = (
    "England - Premier League",
    "Premier League",
    "England Premier League",
)


@dataclass
class MarketContext:
    provider: str
    kickoff: datetime
    home_team: str
    away_team: str
    home_odds: Optional[float] = None
    draw_odds: Optional[float] = None
    away_odds: Optional[float] = None
    fair_home: Optional[float] = None
    fair_draw: Optional[float] = None
    fair_away: Optional[float] = None
    ah_home_line: Optional[float] = None
    ah_home_odds: Optional[float] = None
    ah_away_line: Optional[float] = None
    ah_away_odds: Optional[float] = None
    total_line: Optional[float] = None
    total_over: Optional[float] = None
    total_under: Optional[float] = None
    updated: str = "—"


# ---------------------------------------------------------------------------
# Generic market parsing
# ---------------------------------------------------------------------------


def _normalise_market_name(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(value or "").upper()).strip("_")


def _markets(event: dict[str, Any]) -> list[dict[str, Any]]:
    value = event.get("markets") or []
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        return [x for x in value.values() if isinstance(x, dict)]
    return []


def _selections(market: dict[str, Any]) -> list[dict[str, Any]]:
    value = market.get("selections") or []
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        return [x for x in value.values() if isinstance(x, dict)]
    return []


def _is_full_time(market: dict[str, Any]) -> bool:
    period = _normalise_market_name(market.get("period") or market.get("periodName"))
    raw = str(market.get("rawName") or market.get("name") or "").lower()
    if not period:
        return not any(x in raw for x in ("first half", "1st half", "second half", "2nd half"))
    return period in {"FULL_TIME", "FULLTIME", "REGULATION", "REG", "MATCH", "GAME"}


def _decimal(selection: dict[str, Any]) -> Optional[float]:
    for key in ("decimal", "odds", "price"):
        value = engine.safe_float(selection.get(key))
        if value is not None and value > 1:
            return value
    prices = selection.get("prices")
    if isinstance(prices, dict):
        for key in ("decimal", "odds", "price"):
            value = engine.safe_float(prices.get(key))
            if value is not None and value > 1:
                return value
    return None


def _number_from_label(value: str) -> Optional[float]:
    # Prefer a signed handicap where one is printed in the outcome label.
    matches = re.findall(r"(?<!\d)([-+]\d+(?:\.\d+)?)(?!\d)", value or "")
    if matches:
        return engine.safe_float(matches[-1])
    return None


def _selection_line(selection: dict[str, Any]) -> Optional[float]:
    for key in ("line", "handicap", "points", "spread"):
        value = engine.safe_float(selection.get(key))
        if value is not None:
            return value
    label = str(selection.get("name") or selection.get("rawName") or "")
    return _number_from_label(label)


def _two_way_no_vig(a: Optional[float], b: Optional[float]) -> Optional[tuple[float, float]]:
    if a is None or b is None or a <= 1 or b <= 1:
        return None
    raw_a, raw_b = 1.0 / a, 1.0 / b
    total = raw_a + raw_b
    return raw_a / total, raw_b / total


def no_vig_1x2(h: Optional[float], d: Optional[float], a: Optional[float]) -> Optional[tuple[float, float, float]]:
    if any(x is None or x <= 1 for x in (h, d, a)):
        return None
    raw = [1.0 / float(x) for x in (h, d, a)]
    total = sum(raw)
    return raw[0] / total, raw[1] / total, raw[2] / total


def _classify_selection(selection: dict[str, Any], home: str, away: str) -> Optional[str]:
    outcome = _normalise_market_name(
        selection.get("canonicalOutcome") or selection.get("outcome") or selection.get("side")
    )
    label = str(selection.get("name") or selection.get("rawName") or "").strip()
    normal = engine.normalise_text(label)

    if outcome in {"HOME", "H"} or engine.team_similarity(label, home) >= 0.78:
        return "home"
    if outcome in {"AWAY", "A"} or engine.team_similarity(label, away) >= 0.78:
        return "away"
    if outcome in {"DRAW", "D", "TIE"} or normal in {"draw", "tie"}:
        return "draw"
    if outcome == "OVER" or normal.startswith("over"):
        return "over"
    if outcome == "UNDER" or normal.startswith("under"):
        return "under"
    return None


def extract_1x2(event: dict[str, Any]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    home = str(event.get("home") or event.get("homeTeam") or "")
    away = str(event.get("away") or event.get("awayTeam") or "")

    for market in _markets(event):
        if market.get("isActive") is False or not _is_full_time(market):
            continue
        canonical = _normalise_market_name(market.get("canonicalMarket") or market.get("marketType"))
        raw = str(market.get("rawName") or market.get("name") or "").lower()
        is_result = canonical in {"MATCH_RESULT", "MATCH_WINNER", "MONEYLINE_3WAY", "ML3WAY"}
        is_result = is_result or any(x in raw for x in ("match result", "full time result", "fulltime result", "win-draw-win", "3 way", "3-way"))
        if not is_result:
            continue

        result: dict[str, float] = {}
        for selection in _selections(market):
            if selection.get("isActive") is False:
                continue
            side = _classify_selection(selection, home, away)
            odds = _decimal(selection)
            if side in {"home", "draw", "away"} and odds is not None:
                result[side] = odds
        if result:
            return result.get("home"), result.get("draw"), result.get("away")
    return None, None, None


def extract_main_asian_handicap(
    event: dict[str, Any],
) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """Return home line, home odds, away line, away odds for the main FT AH market."""
    home = str(event.get("home") or event.get("homeTeam") or "")
    away = str(event.get("away") or event.get("awayTeam") or "")
    candidates: list[tuple[float, float, float, float, float]] = []

    for market in _markets(event):
        if market.get("isActive") is False or not _is_full_time(market):
            continue
        canonical = _normalise_market_name(market.get("canonicalMarket") or market.get("marketType"))
        raw = str(market.get("rawName") or market.get("name") or "").lower()
        if canonical not in {"ASIAN_HANDICAP", "HANDICAP"} and "asian handicap" not in raw:
            continue

        market_line = engine.safe_float(market.get("line") if market.get("line") is not None else market.get("handicap"))
        if market_line is None:
            raw_numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", raw)
            if raw_numbers:
                market_line = engine.safe_float(raw_numbers[-1])

        home_odds = away_odds = None
        home_line = away_line = None
        for selection in _selections(market):
            if selection.get("isActive") is False:
                continue
            side = _classify_selection(selection, home, away)
            odds = _decimal(selection)
            line = _selection_line(selection)
            if side == "home" and odds is not None:
                home_odds, home_line = odds, line
            elif side == "away" and odds is not None:
                away_odds, away_line = odds, line

        if home_line is None and market_line is not None:
            home_line = market_line
        if away_line is None and home_line is not None:
            away_line = -home_line
        if home_line is None and away_line is not None:
            home_line = -away_line

        if home_odds is not None and away_odds is not None and home_line is not None and away_line is not None:
            # Main line is normally the pair nearest even money.
            score = abs(home_odds - 2.0) + abs(away_odds - 2.0)
            candidates.append((score, home_line, home_odds, away_line, away_odds))

    if not candidates:
        return None, None, None, None
    candidates.sort(key=lambda x: x[0])
    _, home_line, home_odds, away_line, away_odds = candidates[0]
    return home_line, home_odds, away_line, away_odds


def extract_main_total(
    event: dict[str, Any],
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return total-goals line, Over odds and Under odds for the main FT total."""
    candidates: list[tuple[float, float, float, float]] = []

    for market in _markets(event):
        if market.get("isActive") is False or not _is_full_time(market):
            continue
        canonical = _normalise_market_name(market.get("canonicalMarket") or market.get("marketType"))
        raw = str(market.get("rawName") or market.get("name") or "").lower()
        is_total = canonical in {"OVER_UNDER", "TOTAL", "TOTAL_GOALS", "GOAL_TOTAL"}
        is_total = is_total or "total goals" in raw or "over/under" in raw or "goals over under" in raw
        if not is_total:
            continue

        line = engine.safe_float(market.get("line"))
        over = under = None
        selection_line = None
        for selection in _selections(market):
            if selection.get("isActive") is False:
                continue
            side = _classify_selection(selection, "", "")
            odds = _decimal(selection)
            if side == "over" and odds is not None:
                over = odds
                selection_line = _selection_line(selection) or selection_line
            elif side == "under" and odds is not None:
                under = odds
                selection_line = _selection_line(selection) or selection_line

        if line is None:
            line = selection_line
        if line is None:
            raw_numbers = re.findall(r"\d+(?:\.\d+)?", raw)
            if raw_numbers:
                line = engine.safe_float(raw_numbers[-1])

        if line is not None and over is not None and under is not None:
            score = abs(over - 2.0) + abs(under - 2.0)
            candidates.append((score, line, over, under))

    if not candidates:
        return None, None, None
    candidates.sort(key=lambda x: x[0])
    _, line, over, under = candidates[0]
    return line, over, under


def _context_from_event(provider: str, event: dict[str, Any]) -> Optional[MarketContext]:
    kickoff = engine.parse_datetime(event.get("startTime") or event.get("startsAt") or event.get("startDate"))
    if kickoff is None:
        return None
    home_raw = str(event.get("home") or event.get("homeTeam") or "").strip()
    away_raw = str(event.get("away") or event.get("awayTeam") or "").strip()
    home = engine.canonical_epl_club(home_raw) or home_raw
    away = engine.canonical_epl_club(away_raw) or away_raw
    if not engine.is_current_epl_fixture(home, away):
        return None

    h, d, a = extract_1x2(event)
    fair = no_vig_1x2(h, d, a)
    ah_h_line, ah_h, ah_a_line, ah_a = extract_main_asian_handicap(event)
    total_line, total_over, total_under = extract_main_total(event)

    return MarketContext(
        provider=provider,
        kickoff=kickoff,
        home_team=home,
        away_team=away,
        home_odds=h,
        draw_odds=d,
        away_odds=a,
        fair_home=fair[0] if fair else None,
        fair_draw=fair[1] if fair else None,
        fair_away=fair[2] if fair else None,
        ah_home_line=ah_h_line,
        ah_home_odds=ah_h,
        ah_away_line=ah_a_line,
        ah_away_odds=ah_a,
        total_line=total_line,
        total_over=total_over,
        total_under=total_under,
        updated="—",
    )


# ---------------------------------------------------------------------------
# Sportsbet and Pinnacle context sources
# ---------------------------------------------------------------------------


def sportsbet_context_from_cache(start_date: date, end_date: date) -> list[MarketContext]:
    """Reuse the raw Sportsbet event cache populated by engine.fetch_sportsbet_epl."""
    cached = getattr(engine, "_sportsbet_cache", {}).get("events")
    if not isinstance(cached, list):
        return []
    rows: list[MarketContext] = []
    for event in cached:
        if not isinstance(event, dict) or not engine._sportsbet_epl(event):
            continue
        context = _context_from_event("Sportsbet", event)
        if context is None or not engine.in_range(context.kickoff, start_date, end_date):
            continue
        rows.append(context)
    return sorted(rows, key=lambda x: x.kickoff)


def _events(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("events", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def fetch_pinnacle_context(api_key: str, start_date: date, end_date: date) -> list[MarketContext]:
    if not api_key.strip():
        return []

    events: list[dict[str, Any]] = []
    last_error: Optional[Exception] = None

    for league in PINNACLE_LEAGUES:
        try:
            payload = engine.get_json(
                f"{PINNACLE_BASE}/soccer/leagues/{quote(league, safe='')}/events",
                headers={"X-Secret": api_key.strip()},
                provider_name="PulseScore / Pinnacle",
            )
            events = _events(payload)
            if events:
                break
        except Exception as exc:
            last_error = exc

    if not events:
        # Fallback to a small paginated catalogue and filter locally.
        try:
            for page in range(1, 8):
                payload = engine.get_json(
                    f"{PINNACLE_BASE}/soccer/events",
                    headers={"X-Secret": api_key.strip()},
                    params={"page": page, "limit": 100},
                    provider_name="PulseScore / Pinnacle",
                )
                batch = _events(payload)
                events.extend(batch)
                if not batch or not (payload.get("hasNextPage") if isinstance(payload, dict) else False):
                    break
        except Exception as exc:
            last_error = exc

    if not events and last_error is not None:
        raise RuntimeError(str(last_error))

    rows: list[MarketContext] = []
    for event in events:
        if event.get("live") is True or event.get("isInPlay") is True:
            continue
        context = _context_from_event("Pinnacle", event)
        if context is None or not engine.in_range(context.kickoff, start_date, end_date):
            continue
        rows.append(context)
    return sorted(rows, key=lambda x: x.kickoff)


def _match_context(row: CombinedMatch, contexts: list[MarketContext]) -> Optional[MarketContext]:
    best: Optional[MarketContext] = None
    best_score = 0.0
    for ctx in contexts:
        hours = abs((row.kickoff - ctx.kickoff.astimezone(BRISBANE)).total_seconds()) / 3600.0
        if hours > 8:
            continue
        score = engine.teams_match(row.home_team, row.away_team, ctx.home_team, ctx.away_team) - min(hours / 100.0, 0.05)
        if score > best_score:
            best, best_score = ctx, score
    return best if best_score >= 0.72 else None


# ---------------------------------------------------------------------------
# Row enrichment and analysis
# ---------------------------------------------------------------------------


def _set_defaults(row: CombinedMatch) -> None:
    defaults = {
        "sb_ah_home_line": None,
        "sb_ah_home_odds": None,
        "sb_ah_away_line": None,
        "sb_ah_away_odds": None,
        "sb_total_line": None,
        "sb_total_over": None,
        "sb_total_under": None,
        "pin_home": None,
        "pin_draw": None,
        "pin_away": None,
        "pin_fair_home": None,
        "pin_fair_draw": None,
        "pin_fair_away": None,
        "pin_ev_home_pct": None,
        "pin_ev_draw_pct": None,
        "pin_ev_away_pct": None,
        "pin_ah_home_line": None,
        "pin_ah_home_odds": None,
        "pin_ah_away_line": None,
        "pin_ah_away_odds": None,
        "pin_total_line": None,
        "pin_total_over": None,
        "pin_total_under": None,
        "consensus_home": None,
        "consensus_draw": None,
        "consensus_away": None,
        "consensus_ev_home_pct": None,
        "consensus_ev_draw_pct": None,
        "consensus_ev_away_pct": None,
        "reference_max_diff_pp": None,
        "reference_quality": "PM ONLY",
        "sharp_check": "NO PINNACLE",
    }
    for key, value in defaults.items():
        setattr(row, key, value)


def enrich_rows(
    rows: list[CombinedMatch],
    api_key: str,
    start_date: date,
    end_date: date,
) -> tuple[list[CombinedMatch], list[str]]:
    warnings: list[str] = []
    sb_context = sportsbet_context_from_cache(start_date, end_date)

    try:
        pin_context = fetch_pinnacle_context(api_key, start_date, end_date)
    except Exception as exc:
        pin_context = []
        text = str(exc)
        if "403" in text:
            warnings.append("INFO: Pinnacle/PS3838 is not available on the current PulseScore access tier. Sportsbet AH/totals and Polymarket analysis still work.")
        else:
            warnings.append(f"INFO: Pinnacle sharp-market cross-check unavailable: {text}")

    for row in rows:
        _set_defaults(row)
        sb = _match_context(row, sb_context)
        pin = _match_context(row, pin_context)

        if sb:
            row.sb_ah_home_line = sb.ah_home_line
            row.sb_ah_home_odds = sb.ah_home_odds
            row.sb_ah_away_line = sb.ah_away_line
            row.sb_ah_away_odds = sb.ah_away_odds
            row.sb_total_line = sb.total_line
            row.sb_total_over = sb.total_over
            row.sb_total_under = sb.total_under

        if pin:
            row.pin_home = pin.home_odds
            row.pin_draw = pin.draw_odds
            row.pin_away = pin.away_odds
            row.pin_fair_home = pin.fair_home
            row.pin_fair_draw = pin.fair_draw
            row.pin_fair_away = pin.fair_away
            row.pin_ev_home_pct = engine.expected_value_pct(pin.fair_home, row.sb_home)
            row.pin_ev_draw_pct = engine.expected_value_pct(pin.fair_draw, row.sb_draw)
            row.pin_ev_away_pct = engine.expected_value_pct(pin.fair_away, row.sb_away)
            row.pin_ah_home_line = pin.ah_home_line
            row.pin_ah_home_odds = pin.ah_home_odds
            row.pin_ah_away_line = pin.ah_away_line
            row.pin_ah_away_odds = pin.ah_away_odds
            row.pin_total_line = pin.total_line
            row.pin_total_over = pin.total_over
            row.pin_total_under = pin.total_under

            if all(x is not None for x in (row.pm_fair_home, row.pm_fair_draw, row.pm_fair_away, pin.fair_home, pin.fair_draw, pin.fair_away)):
                pm = (row.pm_fair_home, row.pm_fair_draw, row.pm_fair_away)
                pf = (pin.fair_home, pin.fair_draw, pin.fair_away)
                row.consensus_home = (pm[0] + pf[0]) / 2.0
                row.consensus_draw = (pm[1] + pf[1]) / 2.0
                row.consensus_away = (pm[2] + pf[2]) / 2.0
                row.consensus_ev_home_pct = engine.expected_value_pct(row.consensus_home, row.sb_home)
                row.consensus_ev_draw_pct = engine.expected_value_pct(row.consensus_draw, row.sb_draw)
                row.consensus_ev_away_pct = engine.expected_value_pct(row.consensus_away, row.sb_away)
                diffs = [abs(pm[i] - pf[i]) * 100.0 for i in range(3)]
                row.reference_max_diff_pp = max(diffs)
                if row.reference_max_diff_pp <= 1.5:
                    row.reference_quality = "STRONG AGREEMENT"
                elif row.reference_max_diff_pp <= 3.0:
                    row.reference_quality = "MODERATE AGREEMENT"
                else:
                    row.reference_quality = "DIVERGENT"

            pin_ev = {
                "HOME": row.pin_ev_home_pct,
                "DRAW": row.pin_ev_draw_pct,
                "AWAY": row.pin_ev_away_pct,
            }.get(row.best_selection)
            if row.best_selection in {"HOME", "DRAW", "AWAY"}:
                if pin_ev is None:
                    row.sharp_check = "NO PIN PRICE"
                elif pin_ev > 0:
                    row.sharp_check = "PINNACLE CONFIRMS"
                else:
                    row.sharp_check = "PINNACLE DISAGREES"

    return rows, warnings


def outcome_analysis(row: CombinedMatch, min_ev_pct: float) -> list[dict[str, Any]]:
    names = {
        "HOME": row.home_team,
        "DRAW": "Draw",
        "AWAY": row.away_team,
    }
    odds_map = {"HOME": row.sb_home, "DRAW": row.sb_draw, "AWAY": row.sb_away}
    pm_map = {"HOME": row.pm_fair_home, "DRAW": row.pm_fair_draw, "AWAY": row.pm_fair_away}
    pin_map = {
        "HOME": getattr(row, "pin_fair_home", None),
        "DRAW": getattr(row, "pin_fair_draw", None),
        "AWAY": getattr(row, "pin_fair_away", None),
    }
    ev_map = {"HOME": row.ev_home_pct, "DRAW": row.ev_draw_pct, "AWAY": row.ev_away_pct}
    pin_ev_map = {
        "HOME": getattr(row, "pin_ev_home_pct", None),
        "DRAW": getattr(row, "pin_ev_draw_pct", None),
        "AWAY": getattr(row, "pin_ev_away_pct", None),
    }
    consensus_map = {
        "HOME": getattr(row, "consensus_home", None),
        "DRAW": getattr(row, "consensus_draw", None),
        "AWAY": getattr(row, "consensus_away", None),
    }
    consensus_ev_map = {
        "HOME": getattr(row, "consensus_ev_home_pct", None),
        "DRAW": getattr(row, "consensus_ev_draw_pct", None),
        "AWAY": getattr(row, "consensus_ev_away_pct", None),
    }

    output: list[dict[str, Any]] = []
    for side in ("HOME", "DRAW", "AWAY"):
        odds = odds_map[side]
        pm_p = pm_map[side]
        pin_p = pin_map[side]
        break_even = 1.0 / odds if odds is not None and odds > 1 else None
        fair_odds = 1.0 / pm_p if pm_p is not None and pm_p > 0 else None
        edge_pp = (pm_p - break_even) * 100.0 if pm_p is not None and break_even is not None else None
        threshold_odds = (1.0 + min_ev_pct / 100.0) / pm_p if pm_p is not None and pm_p > 0 else None
        diff_pp = abs(pm_p - pin_p) * 100.0 if pm_p is not None and pin_p is not None else None
        output.append(
            {
                "side": side,
                "name": names[side],
                "sportsbet_odds": odds,
                "break_even": break_even,
                "pm_probability": pm_p,
                "pm_fair_odds": fair_odds,
                "edge_pp": edge_pp,
                "pm_ev_pct": ev_map[side],
                "threshold_odds": threshold_odds,
                "pinnacle_probability": pin_p,
                "pinnacle_ev_pct": pin_ev_map[side],
                "reference_diff_pp": diff_pp,
                "consensus_probability": consensus_map[side],
                "consensus_ev_pct": consensus_ev_map[side],
            }
        )
    return output


def _fmt_line(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if abs(value) < 1e-9:
        return "0"
    return f"{value:+g}"


def handicap_summary(row: CombinedMatch) -> str:
    parts: list[str] = []
    if getattr(row, "sb_ah_home_line", None) is not None:
        parts.append(
            f"Sportsbet AH: {row.home_team} {_fmt_line(row.sb_ah_home_line)} @ {engine.fmt_odds(row.sb_ah_home_odds)} / "
            f"{row.away_team} {_fmt_line(row.sb_ah_away_line)} @ {engine.fmt_odds(row.sb_ah_away_odds)}"
        )
    else:
        parts.append("Sportsbet AH: unavailable")

    if getattr(row, "sb_total_line", None) is not None:
        parts.append(
            f"Sportsbet total: {row.sb_total_line:g} goals — Over {engine.fmt_odds(row.sb_total_over)} / Under {engine.fmt_odds(row.sb_total_under)}"
        )
    else:
        parts.append("Sportsbet total: unavailable")

    if getattr(row, "pin_ah_home_line", None) is not None:
        parts.append(
            f"Pinnacle AH: {row.home_team} {_fmt_line(row.pin_ah_home_line)} @ {engine.fmt_odds(row.pin_ah_home_odds)} / "
            f"{row.away_team} {_fmt_line(row.pin_ah_away_line)} @ {engine.fmt_odds(row.pin_ah_away_odds)}"
        )
    else:
        parts.append("Pinnacle AH: unavailable")

    if getattr(row, "pin_total_line", None) is not None:
        parts.append(
            f"Pinnacle total: {row.pin_total_line:g} goals — Over {engine.fmt_odds(row.pin_total_over)} / Under {engine.fmt_odds(row.pin_total_under)}"
        )
    else:
        parts.append("Pinnacle total: unavailable")

    return "\n".join(parts)


def plain_english_summary(row: CombinedMatch, min_ev_pct: float) -> str:
    analyses = outcome_analysis(row, min_ev_pct)
    best = next((x for x in analyses if x["side"] == row.best_selection), None)

    lines = [
        f"{row.home_team} v {row.away_team}",
        f"Kick-off: {row.kickoff.strftime('%d/%m/%y %H:%M')} Brisbane time",
        "",
    ]

    if best is None or best["pm_ev_pct"] is None:
        lines.append("There is not enough matched Sportsbet + Polymarket 1X2 data to calculate EV for this fixture.")
        return "\n".join(lines)

    decision = "QUALIFIES" if best["pm_ev_pct"] >= min_ev_pct else "PASS"
    lines.append(
        f"Decision: {decision}. The best current PM-based option is {best['name']} at Sportsbet {engine.fmt_odds(best['sportsbet_odds'])}, "
        f"with estimated EV {engine.fmt_pct(best['pm_ev_pct'])}. Your screening threshold is +{min_ev_pct:.2f}%."
    )

    if row.away_favourite == "YES":
        if row.best_selection == "AWAY" and best["pm_ev_pct"] >= min_ev_pct:
            lines.append("Away-favourite flag: YES — the away favourite is also the qualifying value selection. This is a research flag only, not an extra probability boost.")
        else:
            lines.append("Away-favourite flag: YES, but away-favourite status alone does not create a bet.")

    lines.append("")
    if getattr(row, "pin_fair_home", None) is not None:
        diff = getattr(row, "reference_max_diff_pp", None)
        diff_text = engine.fmt_pct(diff) if diff is not None else "—"
        lines.append(
            f"Reference check: {getattr(row, 'reference_quality', 'PM ONLY')} between Polymarket and Pinnacle; largest H/D/A probability difference {diff_text}. "
            f"For the PM-best selection, the sharp check is {getattr(row, 'sharp_check', 'NO PINNACLE')}."
        )
    else:
        lines.append("Reference check: Polymarket only. Pinnacle is unavailable, so there is no independent sharp 1X2 confirmation in this snapshot.")

    lines.append("")
    lines.append("How the EV is calculated:")
    for item in analyses:
        if item["sportsbet_odds"] is None or item["pm_probability"] is None:
            continue
        status = "meets threshold" if item["pm_ev_pct"] is not None and item["pm_ev_pct"] >= min_ev_pct else "below threshold"
        lines.append(
            f"• {item['name']}: Sportsbet {item['sportsbet_odds']:.2f} needs {item['break_even']*100:.2f}% to break even. "
            f"PM baseline is {item['pm_probability']*100:.2f}% (fair odds {item['pm_fair_odds']:.2f}). "
            f"EV = {item['pm_probability']*100:.2f}% × {item['sportsbet_odds']:.2f} − 1 = {item['pm_ev_pct']:.2f}% — {status}. "
            f"At the same PM probability, Sportsbet would need to offer about {item['threshold_odds']:.2f}+ to reach +{min_ev_pct:.2f}% EV."
        )

    return "\n".join(lines)
