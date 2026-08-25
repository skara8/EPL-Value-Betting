from __future__ import annotations

import time as time_module
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

import engine
from engine import BRISBANE, CombinedMatch, ProviderMatch
from advanced_market import extract_1x2, extract_main_asian_handicap, extract_main_total


SPORTSBET_BASE = "https://api.pulsescore.net/api/sportsbet-com-au"
POLYMARKET_BASE = "https://api.pulsescore.net/api/polymarket"
PINNACLE_BASE = "https://api.pulsescore.net/api/ps3838"
PAGE_LIMIT = 100
CACHE_SECONDS = 180


@dataclass(frozen=True)
class LeagueInfo:
    name: str
    event_count: int = 0


@dataclass
class ProviderBundle:
    provider: str
    matches: list[ProviderMatch]
    raw_events: list[dict[str, Any]]
    request_count: int


_cache: dict[str, dict[str, Any]] = {
    "sportsbet_leagues": {"time": 0.0, "value": None},
    "sportsbet_events": {"time": 0.0, "value": None},
    "polymarket_events": {"time": 0.0, "value": None},
    "pinnacle_events": {"time": 0.0, "value": None},
}


def _cache_get(key: str):
    slot = _cache[key]
    if slot["value"] is not None and time_module.time() - float(slot["time"] or 0) < CACHE_SECONDS:
        return slot["value"]
    return None


def _cache_put(key: str, value):
    _cache[key]["value"] = value
    _cache[key]["time"] = time_module.time()


def _items(payload: Any, names: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for name in names:
            value = payload.get(name)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def _has_next(payload: Any, page: int, batch_size: int) -> bool:
    if isinstance(payload, dict):
        if "hasNextPage" in payload:
            return bool(payload.get("hasNextPage"))
        total_pages = engine.safe_float(payload.get("totalPages"))
        if total_pages is not None:
            return page < int(total_pages)
    return batch_size >= PAGE_LIMIT


def fetch_sportsbet_soccer_leagues(api_key: str) -> tuple[list[LeagueInfo], int]:
    """
    Ask Sportsbet/PulseScore which soccer leagues Sportsbet actually offers.

    This is the authoritative inclusion gate for V1.9. A competition is never
    analysed merely because another provider has it.
    """
    cached = _cache_get("sportsbet_leagues")
    if cached is not None:
        return list(cached), 0

    leagues: dict[str, LeagueInfo] = {}
    requests = 0
    for page in range(1, 30):
        payload = engine.get_json(
            f"{SPORTSBET_BASE}/soccer/leagues",
            headers={"X-Secret": api_key.strip()},
            params={"page": page, "limit": PAGE_LIMIT},
            provider_name="PulseScore / Sportsbet leagues",
        )
        requests += 1
        batch = _items(payload, ("leagues", "data", "results"))
        for item in batch:
            name = str(item.get("league") or item.get("name") or "").strip()
            if not name:
                continue
            count = int(engine.safe_float(item.get("eventCount")) or 0)
            # Keep zero-count entries only if the API omits counts entirely.
            key = engine.normalise_text(name)
            prior = leagues.get(key)
            if prior is None or count > prior.event_count:
                leagues[key] = LeagueInfo(name=name, event_count=count)
        if not batch or not _has_next(payload, page, len(batch)):
            break

    result = sorted(leagues.values(), key=lambda x: x.name.lower())
    if not result:
        raise RuntimeError("Sportsbet returned no soccer leagues, so V1.9 cannot determine which competitions are eligible.")
    _cache_put("sportsbet_leagues", result)
    return result, requests


def _fetch_all_provider_events(
    base_url: str,
    provider_name: str,
    api_key: str,
    cache_key: str,
) -> tuple[list[dict[str, Any]], int]:
    cached = _cache_get(cache_key)
    if cached is not None:
        return list(cached), 0

    events: list[dict[str, Any]] = []
    requests = 0
    for page in range(1, 60):
        payload = engine.get_json(
            f"{base_url}/soccer/events",
            headers={"X-Secret": api_key.strip()},
            params={"page": page, "limit": PAGE_LIMIT},
            provider_name=provider_name,
        )
        requests += 1
        batch = _items(payload, ("events", "data", "results"))
        events.extend(batch)
        if not batch or not _has_next(payload, page, len(batch)):
            break
        # Be polite to the free API and avoid bursts across a large catalogue.
        time_module.sleep(0.12)

    _cache_put(cache_key, events)
    return events, requests


def _event_match(event: dict[str, Any], provider: str, start_date: date, end_date: date) -> Optional[ProviderMatch]:
    if event.get("live") is True or event.get("isInPlay") is True:
        return None
    kickoff = engine.parse_datetime(event.get("startTime") or event.get("startsAt") or event.get("startDate"))
    if kickoff is None or not engine.in_range(kickoff, start_date, end_date):
        return None

    home = str(event.get("home") or event.get("homeTeam") or "").strip()
    away = str(event.get("away") or event.get("awayTeam") or "").strip()
    if not home or not away:
        return None

    h, d, a = extract_1x2(event)
    if any(x is None for x in (h, d, a)):
        return None

    row = ProviderMatch(provider, kickoff, home, away, h, d, a)
    setattr(row, "league", str(event.get("league") or event.get("leagueName") or "").strip())
    setattr(row, "country", str(event.get("country") or event.get("region") or "").strip())
    setattr(row, "event_id", str(event.get("eventId") or event.get("id") or ""))
    return row


def fetch_multileague_sources(api_key: str, start_date: date, end_date: date):
    """
    Fetch the full current soccer catalogue for Sportsbet, Polymarket and
    Pinnacle/PS3838 through PulseScore.

    Sportsbet league discovery happens first. Only Sportsbet events whose
    competition is in that current league catalogue are eligible for analysis.
    External providers are reference markets only.
    """
    if not api_key.strip():
        raise ValueError("A PulseScore API key is required.")

    leagues, league_requests = fetch_sportsbet_soccer_leagues(api_key)
    allowed = {engine.normalise_text(item.name) for item in leagues}

    sb_raw, sb_requests = _fetch_all_provider_events(
        SPORTSBET_BASE, "PulseScore / Sportsbet soccer", api_key, "sportsbet_events"
    )
    pm_raw, pm_requests = _fetch_all_provider_events(
        POLYMARKET_BASE, "PulseScore / Polymarket soccer", api_key, "polymarket_events"
    )
    pin_raw, pin_requests = _fetch_all_provider_events(
        PINNACLE_BASE, "PulseScore / Pinnacle soccer", api_key, "pinnacle_events"
    )

    sb_matches: list[ProviderMatch] = []
    for event in sb_raw:
        league = engine.normalise_text(str(event.get("league") or event.get("leagueName") or ""))
        if not league or league not in allowed:
            continue
        match = _event_match(event, "Sportsbet", start_date, end_date)
        if match is not None:
            sb_matches.append(match)

    def external_rows(raw: list[dict[str, Any]], name: str) -> list[ProviderMatch]:
        out: list[ProviderMatch] = []
        for event in raw:
            match = _event_match(event, name, start_date, end_date)
            if match is not None:
                out.append(match)
        return out

    return {
        "leagues": leagues,
        "sportsbet": ProviderBundle("Sportsbet", sorted(sb_matches, key=lambda x: x.kickoff), sb_raw, sb_requests),
        "polymarket": ProviderBundle("Polymarket", external_rows(pm_raw, "Polymarket"), pm_raw, pm_requests),
        "pinnacle": ProviderBundle("Pinnacle", external_rows(pin_raw, "Pinnacle"), pin_raw, pin_requests),
        "request_count": league_requests + sb_requests + pm_requests + pin_requests,
    }


def _league_bonus(a: ProviderMatch, b: ProviderMatch) -> float:
    la = engine.normalise_text(str(getattr(a, "league", "")))
    lb = engine.normalise_text(str(getattr(b, "league", "")))
    if la and lb and la == lb:
        return 0.04
    return 0.0


def _match_external(sb: ProviderMatch, candidates: list[ProviderMatch], used: set[int]) -> tuple[Optional[ProviderMatch], Optional[int]]:
    best_i: Optional[int] = None
    best_score = 0.0
    for i, candidate in enumerate(candidates):
        if i in used:
            continue
        hours = abs((sb.kickoff - candidate.kickoff).total_seconds()) / 3600.0
        if hours > 8:
            continue
        score = engine.teams_match(sb.home_team, sb.away_team, candidate.home_team, candidate.away_team)
        score += _league_bonus(sb, candidate)
        score -= min(hours / 100.0, 0.05)
        if score > best_score:
            best_i, best_score = i, score
    if best_i is not None and best_score >= 0.72:
        return candidates[best_i], best_i
    return None, None


def combine_sportsbet_catalogue(
    sportsbet: list[ProviderMatch],
    polymarket: list[ProviderMatch],
    min_ev_pct: float,
) -> list[CombinedMatch]:
    """Create rows only for fixtures that Sportsbet actually offers."""
    used_pm: set[int] = set()
    rows: list[CombinedMatch] = []

    for sb in sportsbet:
        pm, pm_i = _match_external(sb, polymarket, used_pm)
        if pm_i is not None:
            used_pm.add(pm_i)

        fav, away_fav = engine.identify_sportsbet_favourite(
            sb.home_team, sb.away_team, sb.home_odds, sb.away_odds
        )
        row = CombinedMatch(
            kickoff=sb.kickoff.astimezone(BRISBANE),
            home_team=sb.home_team,
            away_team=sb.away_team,
            sb_home=sb.home_odds,
            sb_draw=sb.draw_odds,
            sb_away=sb.away_odds,
            pm_home=pm.home_odds if pm else None,
            pm_draw=pm.draw_odds if pm else None,
            pm_away=pm.away_odds if pm else None,
            sportsbet_favourite=fav,
            away_favourite=away_fav,
            sportsbet_overround_pct=engine.overround_pct(sb.home_odds, sb.draw_odds, sb.away_odds),
            polymarket_sum_minus_100_pct=engine.overround_pct(pm.home_odds, pm.draw_odds, pm.away_odds) if pm else None,
            sportsbet_updated=sb.updated,
            polymarket_updated=pm.updated if pm else "—",
            polymarket_volume=pm.volume if pm else None,
            polymarket_liquidity=pm.liquidity if pm else None,
            match_status="Matched" if pm else "Sportsbet only",
        )
        setattr(row, "league", str(getattr(sb, "league", "") or "Unknown league"))
        setattr(row, "country", str(getattr(sb, "country", "") or ""))
        setattr(row, "sportsbet_event_id", str(getattr(sb, "event_id", "")))
        rows.append(engine.add_strategy_analysis(row, min_ev_pct))

    return sorted(rows, key=lambda x: x.kickoff)


def _context_event(event: dict[str, Any]) -> Optional[dict[str, Any]]:
    kickoff = engine.parse_datetime(event.get("startTime") or event.get("startsAt") or event.get("startDate"))
    if kickoff is None:
        return None
    home = str(event.get("home") or event.get("homeTeam") or "").strip()
    away = str(event.get("away") or event.get("awayTeam") or "").strip()
    if not home or not away:
        return None
    h, d, a = extract_1x2(event)
    ah_h_line, ah_h, ah_a_line, ah_a = extract_main_asian_handicap(event)
    total_line, total_over, total_under = extract_main_total(event)
    return {
        "kickoff": kickoff,
        "home": home,
        "away": away,
        "h": h,
        "d": d,
        "a": a,
        "ah_h_line": ah_h_line,
        "ah_h": ah_h,
        "ah_a_line": ah_a_line,
        "ah_a": ah_a,
        "total_line": total_line,
        "total_over": total_over,
        "total_under": total_under,
        "league": str(event.get("league") or event.get("leagueName") or ""),
    }


def _find_raw_context(row: CombinedMatch, raw_events: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    best = None
    best_score = 0.0
    target_league = engine.normalise_text(str(getattr(row, "league", "")))
    for event in raw_events:
        context = _context_event(event)
        if context is None:
            continue
        hours = abs((row.kickoff - context["kickoff"].astimezone(BRISBANE)).total_seconds()) / 3600.0
        if hours > 8:
            continue
        score = engine.teams_match(row.home_team, row.away_team, context["home"], context["away"])
        event_league = engine.normalise_text(context["league"])
        if target_league and event_league and target_league == event_league:
            score += 0.04
        score -= min(hours / 100.0, 0.05)
        if score > best_score:
            best, best_score = context, score
    return best if best_score >= 0.72 else None


def enrich_multileague_market_context(
    rows: list[CombinedMatch],
    sportsbet_raw: list[dict[str, Any]],
    pinnacle_raw: list[dict[str, Any]],
) -> list[CombinedMatch]:
    """Attach the same Sportsbet/Pinnacle 1X2, AH and totals fields V1.8 expects."""
    for row in rows:
        sb = _find_raw_context(row, sportsbet_raw)
        pin = _find_raw_context(row, pinnacle_raw)

        if sb:
            row.sb_ah_home_line = sb["ah_h_line"]
            row.sb_ah_home_odds = sb["ah_h"]
            row.sb_ah_away_line = sb["ah_a_line"]
            row.sb_ah_away_odds = sb["ah_a"]
            row.sb_total_line = sb["total_line"]
            row.sb_total_over = sb["total_over"]
            row.sb_total_under = sb["total_under"]

        if pin:
            row.pin_home = pin["h"]
            row.pin_draw = pin["d"]
            row.pin_away = pin["a"]
            row.pin_ah_home_line = pin["ah_h_line"]
            row.pin_ah_home_odds = pin["ah_h"]
            row.pin_ah_away_line = pin["ah_a_line"]
            row.pin_ah_away_odds = pin["ah_a"]
            row.pin_total_line = pin["total_line"]
            row.pin_total_over = pin["total_over"]
            row.pin_total_under = pin["total_under"]

    return rows


def sportsbet_league_counts(rows: list[CombinedMatch]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        name = str(getattr(row, "league", "Unknown league") or "Unknown league")
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0].lower())))
