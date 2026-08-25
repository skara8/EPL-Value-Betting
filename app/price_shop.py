from __future__ import annotations

import time as time_module
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Any, Callable, Optional
from urllib.parse import quote

import engine
from advanced_market import extract_1x2
from dutch_calc import polymarket_effective_decimal_odds


ProgressCallback = Callable[[int, str, str], None]
PAGE_LIMIT = 100
CACHE_SECONDS = 900

# Price-shopping sources currently exposed through PulseScore's normalised
# soccer API. Australian-facing fixed-odds books and crypto sportsbooks are
# used only to improve the executable price; they never vote in the independent
# fair-probability model.
BOOKMAKERS = (
    ("Bet365", "https://api.pulsescore.net/api/v3/bet365"),
    ("Ladbrokes", "https://api.pulsescore.net/api/ladbrokes"),
    ("TAB", "https://api.pulsescore.net/api/tab"),
    ("Unibet AU", "https://api.pulsescore.net/api/unibetau"),
    ("BetRight", "https://api.pulsescore.net/api/betright"),
    ("Stake (crypto)", "https://api.pulsescore.net/api/stake"),
    ("Cloudbet (crypto)", "https://api.pulsescore.net/api/cloudbet"),
)


@dataclass(frozen=True)
class PriceQuote:
    source: str
    side: str
    decimal_odds: float
    raw_display: str = ""


@dataclass
class MatchPriceShop:
    match_name: str
    league: str
    quotes: dict[str, list[PriceQuote]] = field(default_factory=lambda: {"HOME": [], "DRAW": [], "AWAY": []})
    best: dict[str, Optional[PriceQuote]] = field(default_factory=lambda: {"HOME": None, "DRAW": None, "AWAY": None})
    model_probability: dict[str, Optional[float]] = field(default_factory=dict)
    best_ev_pct: dict[str, Optional[float]] = field(default_factory=dict)


@dataclass
class PriceShopResult:
    matches: dict[str, MatchPriceShop]
    providers_checked: list[str]
    request_count: int
    cache_hits: int
    notes: list[str]


_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str):
    item = _cache.get(key)
    if not item:
        return None
    at, value = item
    return value if time_module.time() - at < CACHE_SECONDS else None


def _cache_put(key: str, value: Any) -> None:
    _cache[key] = (time_module.time(), value)


def _emit(cb: Optional[ProgressCallback], percent: int, stage: str, detail: str) -> None:
    if cb:
        cb(max(0, min(100, int(percent))), stage, detail)


def _items(payload: Any, names=("events", "leagues", "data", "results")) -> list[dict[str, Any]]:
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
        pages = engine.safe_float(payload.get("totalPages"))
        if pages is not None:
            return page < int(pages)
    return batch_size >= PAGE_LIMIT


def _league_name(item: dict[str, Any]) -> str:
    return str(item.get("league") or item.get("name") or "").strip()


def _fetch_leagues(name: str, base: str, api_key: str) -> tuple[list[str], int, bool]:
    key = f"leagues:{base}"
    cached = _cache_get(key)
    if cached is not None:
        return list(cached), 0, True

    result: list[str] = []
    requests = 0
    for page in range(1, 20):
        payload = engine.get_json(
            f"{base}/soccer/leagues",
            headers={"X-Secret": api_key.strip()},
            params={"page": page, "limit": PAGE_LIMIT},
            provider_name=f"PulseScore / {name} leagues",
        )
        requests += 1
        batch = _items(payload, ("leagues", "data", "results"))
        result.extend(x for x in (_league_name(item) for item in batch) if x)
        if not batch or not _has_next(payload, page, len(batch)):
            break
    result = sorted(set(result), key=str.lower)
    _cache_put(key, result)
    return result, requests, False


def _league_similarity(a: str, b: str) -> float:
    na, nb = engine.normalise_text(a), engine.normalise_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.93
    return SequenceMatcher(None, na, nb).ratio()


def _match_league(target: str, offered: list[str]) -> Optional[str]:
    best, score = None, 0.0
    for candidate in offered:
        value = _league_similarity(target, candidate)
        if value > score:
            best, score = candidate, value
    return best if score >= 0.72 else None


def _fetch_league_events(name: str, base: str, league: str, api_key: str) -> tuple[list[dict[str, Any]], int, bool]:
    key = f"events:{base}:{engine.normalise_text(league)}"
    cached = _cache_get(key)
    if cached is not None:
        return list(cached), 0, True

    events: list[dict[str, Any]] = []
    requests = 0
    for page in range(1, 12):
        payload = engine.get_json(
            f"{base}/soccer/leagues/{quote(league, safe='')}/events",
            headers={"X-Secret": api_key.strip()},
            params={"page": page, "limit": PAGE_LIMIT},
            provider_name=f"PulseScore / {name} {league}",
        )
        requests += 1
        batch = _items(payload, ("events", "data", "results"))
        events.extend(batch)
        if not batch or not _has_next(payload, page, len(batch)):
            break
    _cache_put(key, events)
    return events, requests, False


def _event_quote(event: dict[str, Any], target) -> Optional[tuple[float, float, float]]:
    if event.get("live") is True or event.get("isInPlay") is True:
        return None
    kickoff = engine.parse_datetime(event.get("startTime") or event.get("startsAt") or event.get("startDate"))
    if kickoff is None:
        return None
    hours = abs((target.kickoff - kickoff.astimezone(engine.BRISBANE)).total_seconds()) / 3600.0
    if hours > 8:
        return None
    home = str(event.get("home") or event.get("homeTeam") or "").strip()
    away = str(event.get("away") or event.get("awayTeam") or "").strip()
    if engine.teams_match(target.home_team, target.away_team, home, away) < 0.72:
        return None
    h, d, a = extract_1x2(event)
    if any(x is None or x <= 1 for x in (h, d, a)):
        return None
    return float(h), float(d), float(a)


def _top_rows(rows: list, max_matches: int, max_leagues: int) -> list:
    ranked = []
    for row in rows:
        best = max(
            (getattr(edge, "model_ev_pct", -9999.0) for edge in getattr(row, "edge_outcomes", {}).values() if getattr(edge, "model_ev_pct", None) is not None),
            default=-9999.0,
        )
        if best > -9999:
            ranked.append((float(best), row))
    ranked.sort(key=lambda x: x[0], reverse=True)

    chosen = []
    leagues: set[str] = set()
    for _, row in ranked:
        league = str(getattr(row, "league", "") or "Unknown league")
        if league not in leagues and len(leagues) >= max_leagues:
            continue
        leagues.add(league)
        chosen.append(row)
        if len(chosen) >= max_matches:
            break
    return chosen


def _add_base_quotes(shop: MatchPriceShop, row) -> None:
    for side, odds in (("HOME", row.sb_home), ("DRAW", row.sb_draw), ("AWAY", row.sb_away)):
        if odds is not None and odds > 1:
            shop.quotes[side].append(PriceQuote("Sportsbet", side, float(odds), f"{float(odds):.2f}"))

    # Polymarket is an event-market price, not an Australian bookmaker. It is
    # included as a research/executable comparison using the same sports taker
    # fee assumption as the Dutch calculator.
    for side, odds in (("HOME", row.pm_home), ("DRAW", row.pm_draw), ("AWAY", row.pm_away)):
        if odds is None or odds <= 1:
            continue
        price = 1.0 / float(odds)
        try:
            effective = polymarket_effective_decimal_odds(price)
        except ValueError:
            continue
        shop.quotes[side].append(PriceQuote("Polymarket", side, effective, f"{price * 100:.1f}¢"))


def fetch_best_prices(
    api_key: str,
    rows: list,
    progress: Optional[ProgressCallback] = None,
    max_matches: int = 15,
    max_leagues: int = 5,
) -> PriceShopResult:
    targets = _top_rows(rows, max_matches=max_matches, max_leagues=max_leagues)
    matches: dict[str, MatchPriceShop] = {}
    for row in targets:
        shop = MatchPriceShop(match_name=row.match_name, league=str(getattr(row, "league", "") or "Unknown league"))
        shop.model_probability = {
            "HOME": getattr(row, "model_fair_home", None),
            "DRAW": getattr(row, "model_fair_draw", None),
            "AWAY": getattr(row, "model_fair_away", None),
        }
        _add_base_quotes(shop, row)
        matches[row.match_name] = shop

    if not targets:
        return PriceShopResult(matches, [], 0, 0, ["No model-priced matches were available for price shopping."])

    target_leagues = sorted({str(getattr(row, "league", "") or "Unknown league") for row in targets})
    requests = 0
    cache_hits = 0
    checked: list[str] = []
    notes: list[str] = []
    total_steps = max(1, len(BOOKMAKERS) * (1 + len(target_leagues)))
    step = 0

    for name, base in BOOKMAKERS:
        try:
            offered, used, hit = _fetch_leagues(name, base, api_key)
            requests += used
            cache_hits += int(hit)
            checked.append(name)
            step += 1
            _emit(progress, 66 + int(22 * step / total_steps), "Best-price scan", f"{name}: league catalogue ready")

            for target_league in target_leagues:
                matched_league = _match_league(target_league, offered)
                step += 1
                if not matched_league:
                    _emit(progress, 66 + int(22 * step / total_steps), "Best-price scan", f"{name}: no close league match for {target_league}")
                    continue
                events, used, hit = _fetch_league_events(name, base, matched_league, api_key)
                requests += used
                cache_hits += int(hit)
                league_targets = [r for r in targets if str(getattr(r, "league", "") or "Unknown league") == target_league]
                for row in league_targets:
                    for event in events:
                        prices = _event_quote(event, row)
                        if prices is None:
                            continue
                        for side, odds in zip(("HOME", "DRAW", "AWAY"), prices):
                            matches[row.match_name].quotes[side].append(PriceQuote(name, side, odds, f"{odds:.2f}"))
                        break
                _emit(
                    progress,
                    66 + int(22 * step / total_steps),
                    "Best-price scan",
                    f"{name}: checked {matched_league} ({len(events)} events)",
                )
        except Exception as exc:
            notes.append(f"{name}: {exc}")
            _emit(progress, 66 + int(22 * max(step, 1) / total_steps), "Best-price scan", f"{name} unavailable; continuing")

    for row in targets:
        shop = matches[row.match_name]
        for side in ("HOME", "DRAW", "AWAY"):
            quotes = shop.quotes[side]
            shop.best[side] = max(quotes, key=lambda q: q.decimal_odds) if quotes else None
            p = shop.model_probability.get(side)
            best = shop.best[side]
            shop.best_ev_pct[side] = ((float(p) * best.decimal_odds - 1.0) * 100.0) if p is not None and best else None

        setattr(row, "price_shop", shop)
        for side in ("HOME", "DRAW", "AWAY"):
            suffix = side.lower()
            best = shop.best[side]
            setattr(row, f"best_price_{suffix}", best.decimal_odds if best else None)
            setattr(row, f"best_price_{suffix}_source", best.source if best else None)
            setattr(row, f"best_price_ev_{suffix}", shop.best_ev_pct[side])

    _emit(progress, 89, "Best-price scan", f"Compared {len(targets)} matches across {len(checked)} additional bookmakers")
    return PriceShopResult(matches, checked, requests, cache_hits, notes)
