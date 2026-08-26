from __future__ import annotations

"""V3 execution-price scanner.

Unlike V2.x, this module never filters fixtures by Sportsbet EV before checking
other books.  Every independently priced fixture is eligible.  Matching is
also deliberately conservative: a missing quote is preferable to attaching a
price from the wrong match and manufacturing a phantom edge.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import engine
import price_shop as legacy
from advanced_market import extract_1x2
from dutch_calc import polymarket_effective_decimal_odds

ProgressCallback = Callable[[int, str, str], None]
SIDES = ("HOME", "DRAW", "AWAY")
MAX_KICKOFF_DELTA_MINUTES = 45.0
MIN_PAIR_SCORE = 0.88
MIN_INDIVIDUAL_TEAM_SCORE = 0.82
MIN_LEAGUE_SCORE = 0.84
AMBIGUITY_GAP = 0.03


@dataclass(frozen=True)
class V3PriceQuote:
    source: str
    side: str
    decimal_odds: float
    raw_display: str = ""
    received_at: str = ""
    event_id: str = ""
    match_confidence: float = 1.0
    market_timestamp: str = ""


@dataclass
class V3MatchPriceShop:
    match_name: str
    league: str
    event_fingerprint: str
    quotes: dict[str, list[V3PriceQuote]] = field(default_factory=lambda: {s: [] for s in SIDES})
    best: dict[str, Optional[V3PriceQuote]] = field(default_factory=lambda: {s: None for s in SIDES})
    model_probability: dict[str, Optional[float]] = field(default_factory=dict)
    best_ev_pct: dict[str, Optional[float]] = field(default_factory=dict)
    rejected_ambiguous_matches: int = 0


@dataclass
class V3PriceShopResult:
    matches: dict[str, V3MatchPriceShop]
    providers_checked: list[str]
    request_count: int
    cache_hits: int
    notes: list[str]
    target_matches: int
    matched_quotes: int
    ambiguous_rejections: int


def _emit(cb: Optional[ProgressCallback], percent: int, stage: str, detail: str) -> None:
    if cb:
        cb(max(0, min(100, int(percent))), stage, detail)


def _canonical_fingerprint(row) -> str:
    kickoff = row.kickoff.astimezone(timezone.utc).strftime("%Y%m%dT%H%M")
    league = engine.normalise_text(str(getattr(row, "league", "") or "unknown"))
    home = engine.normalise_text(row.home_team)
    away = engine.normalise_text(row.away_team)
    return f"{league}|{kickoff}|{home}|{away}"


def _event_id(event: dict[str, Any]) -> str:
    for key in ("id", "eventId", "event_id", "key", "slug"):
        value = event.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _market_timestamp(event: dict[str, Any]) -> str:
    for key in ("updatedAt", "lastUpdated", "marketTimestamp", "timestamp", "updated"):
        value = event.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _marker_set(value: str) -> set[str]:
    n = engine.normalise_text(value)
    markers = set()
    for marker in ("women", "u18", "u19", "u20", "u21", "u23", "youth", "reserve", "reserves"):
        if marker in n.split() or marker in n:
            markers.add(marker)
    # Roman-numeral/second sides are risky when only one feed includes the tag.
    if n.endswith(" ii") or " b team" in n:
        markers.add("second-team")
    return markers


def _team_pair_score(target, home: str, away: str) -> Optional[float]:
    if _marker_set(target.home_team) != _marker_set(home):
        return None
    if _marker_set(target.away_team) != _marker_set(away):
        return None
    h = engine.team_similarity(target.home_team, home)
    a = engine.team_similarity(target.away_team, away)
    if min(h, a) < MIN_INDIVIDUAL_TEAM_SCORE:
        return None
    pair = (h + a) / 2.0
    return pair if pair >= MIN_PAIR_SCORE else None


def _strict_league_match(target: str, offered: list[str]) -> Optional[str]:
    scored = sorted(
        ((legacy._league_similarity(target, candidate), candidate) for candidate in offered),
        reverse=True,
    )
    if not scored or scored[0][0] < MIN_LEAGUE_SCORE:
        return None
    if len(scored) > 1 and scored[0][0] < 0.96 and scored[0][0] - scored[1][0] < 0.04:
        return None
    return scored[0][1]


def _event_candidate(event: dict[str, Any], target) -> Optional[tuple[float, tuple[float, float, float], str, str]]:
    if event.get("live") is True or event.get("isInPlay") is True:
        return None
    kickoff = engine.parse_datetime(event.get("startTime") or event.get("startsAt") or event.get("startDate"))
    if kickoff is None:
        return None
    delta_min = abs((target.kickoff.astimezone(timezone.utc) - kickoff.astimezone(timezone.utc)).total_seconds()) / 60.0
    if delta_min > MAX_KICKOFF_DELTA_MINUTES:
        return None

    home = str(event.get("home") or event.get("homeTeam") or "").strip()
    away = str(event.get("away") or event.get("awayTeam") or "").strip()
    pair = _team_pair_score(target, home, away)
    if pair is None:
        return None

    h, d, a = extract_1x2(event)
    if any(x is None or x <= 1 for x in (h, d, a)):
        return None

    # Kickoff contributes to confidence but cannot compensate for poor team IDs.
    time_score = max(0.0, 1.0 - delta_min / MAX_KICKOFF_DELTA_MINUTES)
    confidence = 0.88 * pair + 0.12 * time_score
    return confidence, (float(h), float(d), float(a)), _event_id(event), _market_timestamp(event)


def _choose_event(events: list[dict[str, Any]], target) -> tuple[Optional[tuple[float, float, float]], str, float, str, bool]:
    candidates = []
    for event in events:
        candidate = _event_candidate(event, target)
        if candidate is not None:
            candidates.append(candidate)
    if not candidates:
        return None, "", 0.0, "", False
    candidates.sort(key=lambda item: item[0], reverse=True)
    best = candidates[0]
    if len(candidates) > 1 and best[0] - candidates[1][0] < AMBIGUITY_GAP:
        # If the provider IDs differ, we cannot be certain which event is the
        # target.  Reject both rather than pick the slightly better fuzzy match.
        if best[2] != candidates[1][2] or not best[2]:
            return None, "", best[0], "", True
    return best[1], best[2], best[0], best[3], False


def _add_base_quotes(shop: V3MatchPriceShop, row) -> None:
    received = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for side, odds in (("HOME", row.sb_home), ("DRAW", row.sb_draw), ("AWAY", row.sb_away)):
        if odds is not None and odds > 1:
            shop.quotes[side].append(V3PriceQuote("Sportsbet", side, float(odds), f"{float(odds):.2f}", received, shop.event_fingerprint, 1.0, str(getattr(row, "sportsbet_updated", "") or "")))

    for side, odds in (("HOME", row.pm_home), ("DRAW", row.pm_draw), ("AWAY", row.pm_away)):
        if odds is None or odds <= 1:
            continue
        price = 1.0 / float(odds)
        try:
            effective = polymarket_effective_decimal_odds(price)
        except ValueError:
            continue
        shop.quotes[side].append(V3PriceQuote("Polymarket", side, effective, f"{price * 100:.1f}¢", received, shop.event_fingerprint, 1.0, str(getattr(row, "polymarket_updated", "") or "")))


def _provider_scan(
    name: str,
    base: str,
    api_key: str,
    target_leagues: list[str],
    league_rows: dict[str, list],
) -> tuple[str, dict[str, tuple[tuple[float, float, float], str, float, str]], int, int, int, list[str]]:
    found: dict[str, tuple[tuple[float, float, float], str, float, str]] = {}
    requests = cache_hits = ambiguous = 0
    notes: list[str] = []
    try:
        offered, used, hit = legacy._fetch_leagues(name, base, api_key)
        requests += used
        cache_hits += int(hit)
        for target_league in target_leagues:
            matched = _strict_league_match(target_league, offered)
            if not matched:
                continue
            events, used, hit = legacy._fetch_league_events(name, base, matched, api_key)
            requests += used
            cache_hits += int(hit)
            for row in league_rows[target_league]:
                prices, event_id, confidence, market_ts, rejected = _choose_event(events, row)
                if rejected:
                    ambiguous += 1
                    continue
                if prices is not None:
                    found[row.match_name] = (prices, event_id, confidence, market_ts)
    except Exception as exc:
        notes.append(f"{name}: {exc}")
    return name, found, requests, cache_hits, ambiguous, notes


def fetch_best_prices_v3(
    api_key: str,
    rows: list,
    progress: Optional[ProgressCallback] = None,
    max_workers: int = 4,
) -> V3PriceShopResult:
    """Build the quote matrix for *all* independently priced fixtures.

    There is intentionally no max_matches or max_leagues parameter: V3 ranks
    only after the execution universe has been scanned.
    """
    targets = [
        row for row in rows
        if all(getattr(row, attr, None) is not None for attr in ("model_fair_home", "model_fair_draw", "model_fair_away"))
    ]
    matches: dict[str, V3MatchPriceShop] = {}
    for row in targets:
        shop = V3MatchPriceShop(row.match_name, str(getattr(row, "league", "") or "Unknown league"), _canonical_fingerprint(row))
        shop.model_probability = {
            "HOME": float(row.model_fair_home),
            "DRAW": float(row.model_fair_draw),
            "AWAY": float(row.model_fair_away),
        }
        _add_base_quotes(shop, row)
        matches[row.match_name] = shop

    if not targets:
        return V3PriceShopResult(matches, [], 0, 0, ["No independently priced matches were available for price shopping."], 0, 0, 0)

    league_rows: dict[str, list] = {}
    for row in targets:
        league_rows.setdefault(str(getattr(row, "league", "") or "Unknown league"), []).append(row)
    target_leagues = sorted(league_rows)
    providers_checked: list[str] = []
    notes: list[str] = []
    request_count = cache_hits = ambiguous_total = 0
    received = datetime.now(timezone.utc).isoformat(timespec="seconds")

    _emit(progress, 86, "Complete quote matrix", f"Scanning {len(targets)} independently priced fixtures across {len(legacy.BOOKMAKERS)} additional books before ranking")
    workers = max(1, min(max_workers, len(legacy.BOOKMAKERS)))
    completed = 0
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="v3-prices") as pool:
        futures = {
            pool.submit(_provider_scan, name, base, api_key, target_leagues, league_rows): name
            for name, base in legacy.BOOKMAKERS
        }
        for future in as_completed(futures):
            name, found, used, hits, ambiguous, provider_notes = future.result()
            providers_checked.append(name)
            request_count += used
            cache_hits += hits
            ambiguous_total += ambiguous
            notes.extend(provider_notes)
            for match_name, (prices, event_id, confidence, market_ts) in found.items():
                shop = matches[match_name]
                for side, odds in zip(SIDES, prices):
                    shop.quotes[side].append(V3PriceQuote(name, side, odds, f"{odds:.2f}", received, event_id, confidence, market_ts))
            completed += 1
            _emit(progress, 86 + int(8 * completed / len(legacy.BOOKMAKERS)), "Complete quote matrix", f"{completed}/{len(legacy.BOOKMAKERS)} books complete · {sum(len(v) for s in matches.values() for v in s.quotes.values())} quotes observed")

    matched_quotes = 0
    row_by_name = {row.match_name: row for row in targets}
    for match_name, shop in matches.items():
        row = row_by_name[match_name]
        for side in SIDES:
            quotes = shop.quotes[side]
            matched_quotes += len(quotes)
            shop.best[side] = max(quotes, key=lambda q: q.decimal_odds) if quotes else None
            p = shop.model_probability.get(side)
            quote = shop.best[side]
            shop.best_ev_pct[side] = ((float(p) * quote.decimal_odds - 1.0) * 100.0) if p is not None and quote else None
            suffix = side.lower()
            setattr(row, f"best_price_{suffix}", quote.decimal_odds if quote else None)
            setattr(row, f"best_price_{suffix}_source", quote.source if quote else None)
            setattr(row, f"best_price_ev_{suffix}", shop.best_ev_pct[side])
        row.price_shop = shop

    notes.append(
        f"V3 scanned all {len(targets)} independently priced fixtures before ranking. "
        f"Strict matcher rejected {ambiguous_total} ambiguous provider event candidate(s)."
    )
    _emit(progress, 94, "Complete quote matrix", f"Finished {len(targets)} fixtures · {matched_quotes} side quotes · {ambiguous_total} ambiguous matches rejected")
    return V3PriceShopResult(
        matches=matches,
        providers_checked=sorted(providers_checked),
        request_count=request_count,
        cache_hits=cache_hits,
        notes=notes,
        target_matches=len(targets),
        matched_quotes=matched_quotes,
        ambiguous_rejections=ambiguous_total,
    )
