from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Optional

import engine
from advanced_market import extract_1x2
from dutch_calc import polymarket_effective_decimal_odds
from independent_model_v24 import canonical_history_team, resolve_league_source
from price_shop import BOOKMAKERS, _fetch_league_events, _fetch_leagues

SIDES = ("HOME", "DRAW", "AWAY")
KICKOFF_TOLERANCE_MINUTES = 90.0
MIN_TEAM_SCORE = 0.90
MIN_PAIR_SCORE = 0.93
AMBIGUITY_MARGIN = 0.035
MIN_EXECUTION_MATCH_CONFIDENCE = 0.94
MAX_KNOWN_QUOTE_AGE_SECONDS = 600.0


@dataclass(frozen=True)
class V3PriceQuote:
    source: str
    side: str
    decimal_odds: float
    raw_display: str = ""
    received_at: str = ""
    market_timestamp: Optional[str] = None
    age_seconds: Optional[float] = None
    liquidity: Optional[float] = None
    available_size: Optional[float] = None
    commission: Optional[float] = None
    line: Optional[float] = None
    event_id: Optional[str] = None
    match_confidence: float = 1.0
    executable: bool = True
    execution_reason: str = ""


@dataclass
class V3MatchPriceShop:
    match_name: str
    league: str
    quotes: dict[str, list[V3PriceQuote]] = field(default_factory=lambda: {"HOME": [], "DRAW": [], "AWAY": []})
    best: dict[str, Optional[V3PriceQuote]] = field(default_factory=lambda: {"HOME": None, "DRAW": None, "AWAY": None})
    model_probability: dict[str, Optional[float]] = field(default_factory=dict)
    best_ev_pct: dict[str, Optional[float]] = field(default_factory=dict)
    rejected_quotes: dict[str, int] = field(default_factory=lambda: {"HOME": 0, "DRAW": 0, "AWAY": 0})


@dataclass
class V3PriceShopResult:
    matches: dict[str, V3MatchPriceShop]
    providers_checked: list[str]
    request_count: int
    cache_hits: int
    notes: list[str]
    eligible_matches: int = 0
    rejected_ambiguous_events: int = 0
    rejected_non_executable_quotes: int = 0


def _emit(progress, percent: int, stage: str, detail: str) -> None:
    if progress:
        progress(max(0, min(100, int(percent))), stage, detail)


def eligible_rows(rows: list) -> list:
    """Every independently modelled fixture is scanned; there is no EV pre-ranking."""
    return [row for row in rows if all(getattr(row, attr, None) is not None for attr in ("model_fair_home", "model_fair_draw", "model_fair_away"))]


def _normal(value: str) -> str:
    return canonical_history_team(value)


def _team_score(a: str, b: str) -> float:
    na, nb = _normal(a), _normal(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    overlap = len(ta & tb) / max(1, len(ta | tb))
    return .68 * SequenceMatcher(None, na, nb).ratio() + .32 * overlap


def _strict_league_match(target: str, offered: list[str]) -> Optional[str]:
    target_source = resolve_league_source(target)
    if target_source is not None:
        same = []
        for name in offered:
            source = resolve_league_source(name)
            if source is not None and source.key == target_source.key:
                same.append(name)
        if len(same) == 1:
            return same[0]
        if len(same) > 1:
            exact = [name for name in same if engine.normalise_text(name) == engine.normalise_text(target)]
            if len(exact) == 1:
                return exact[0]
            ranked = sorted(((SequenceMatcher(None, engine.normalise_text(target), engine.normalise_text(name)).ratio(), name) for name in same), reverse=True)
            if len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= AMBIGUITY_MARGIN:
                return ranked[0][1]
            return None
    ranked = sorted(((SequenceMatcher(None, engine.normalise_text(target), engine.normalise_text(name)).ratio(), name) for name in offered), reverse=True)
    if not ranked or ranked[0][0] < .94:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < AMBIGUITY_MARGIN:
        return None
    return ranked[0][1]


def _event_time(event: dict[str, Any]):
    return engine.parse_datetime(event.get("startTime") or event.get("startsAt") or event.get("startDate"))


def _event_names(event: dict[str, Any]) -> tuple[str, str]:
    return str(event.get("home") or event.get("homeTeam") or "").strip(), str(event.get("away") or event.get("awayTeam") or "").strip()


def _market_timestamp(event: dict[str, Any]) -> Optional[str]:
    value = event.get("updatedAt") or event.get("lastUpdated") or event.get("marketTimestamp") or event.get("timestamp")
    return str(value) if value not in (None, "") else None


def _age_seconds(timestamp: Optional[str], received: datetime) -> Optional[float]:
    parsed = engine.parse_datetime(timestamp) if timestamp else None
    return None if parsed is None else max(0.0, (received - parsed.astimezone(timezone.utc)).total_seconds())


def _event_candidate(event: dict[str, Any], target):
    if event.get("live") is True or event.get("isInPlay") is True:
        return None
    kickoff = _event_time(event)
    if kickoff is None:
        return None
    target_utc = target.kickoff.astimezone(timezone.utc)
    minutes = abs((target_utc - kickoff.astimezone(timezone.utc)).total_seconds()) / 60.0
    if minutes > KICKOFF_TOLERANCE_MINUTES:
        return None
    home, away = _event_names(event)
    hs, as_ = _team_score(target.home_team, home), _team_score(target.away_team, away)
    if hs < MIN_TEAM_SCORE or as_ < MIN_TEAM_SCORE:
        return None
    pair = (hs + as_) / 2.0
    if pair < MIN_PAIR_SCORE:
        return None
    reverse = (_team_score(target.home_team, away) + _team_score(target.away_team, home)) / 2.0
    if reverse >= pair - .01:
        return None
    h, d, a = extract_1x2(event)
    if any(x is None or x <= 1 for x in (h, d, a)):
        return None
    confidence = .88 * pair + .12 * max(0.0, 1.0 - minutes / KICKOFF_TOLERANCE_MINUTES)
    return confidence, (float(h), float(d), float(a))


def _match_event(events: list[dict[str, Any]], target):
    candidates = []
    for event in events:
        candidate = _event_candidate(event, target)
        if candidate is not None:
            candidates.append((candidate[0], event, candidate[1]))
    if not candidates:
        return None, None, 0.0, False
    candidates.sort(key=lambda item: item[0], reverse=True)
    if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < AMBIGUITY_MARGIN:
        return None, None, candidates[0][0], True
    confidence, event, prices = candidates[0]
    return event, prices, confidence, False


def quote_execution_reason(quote: V3PriceQuote) -> Optional[str]:
    """Return None only when the observed quote is usable for EV ranking.

    The receive timestamp is the point-in-time evidence. Provider timestamps are
    used as an additional freshness gate when available. Known zero liquidity or
    size is binding rather than merely displayed as metadata.
    """
    if quote.decimal_odds <= 1.0:
        return "invalid odds"
    if not quote.received_at:
        return "missing receive timestamp"
    if float(quote.match_confidence or 0.0) < MIN_EXECUTION_MATCH_CONFIDENCE:
        return "event match confidence below execution gate"
    if quote.age_seconds is not None and quote.age_seconds > MAX_KNOWN_QUOTE_AGE_SECONDS:
        return f"provider quote older than {int(MAX_KNOWN_QUOTE_AGE_SECONDS)}s"
    if quote.available_size is not None and float(quote.available_size) <= 0.0:
        return "known available size is zero"
    if quote.liquidity is not None and float(quote.liquidity) <= 0.0:
        return "known liquidity is zero"
    return None


def is_executable_quote(quote: V3PriceQuote) -> bool:
    return bool(getattr(quote, "executable", True)) and quote_execution_reason(quote) is None


def best_executable_quote(quotes: list[V3PriceQuote]) -> Optional[V3PriceQuote]:
    valid = [quote for quote in quotes if is_executable_quote(quote)]
    return max(valid, key=lambda q: float(q.decimal_odds)) if valid else None


def _make_quote(
    source: str,
    side: str,
    odds: float,
    raw_display: str,
    received: datetime,
    *,
    market_timestamp: Optional[str] = None,
    liquidity: Optional[float] = None,
    available_size: Optional[float] = None,
    commission: Optional[float] = None,
    line: Optional[float] = None,
    event_id: Optional[str] = None,
    match_confidence: float = 1.0,
) -> V3PriceQuote:
    age = _age_seconds(market_timestamp, received)
    provisional = V3PriceQuote(
        source,
        side,
        float(odds),
        raw_display,
        received.isoformat(timespec="seconds"),
        market_timestamp,
        age,
        liquidity,
        available_size,
        commission,
        line,
        event_id,
        match_confidence,
        True,
        "",
    )
    reason = quote_execution_reason(provisional)
    if reason is None:
        return provisional
    return V3PriceQuote(
        provisional.source,
        provisional.side,
        provisional.decimal_odds,
        provisional.raw_display,
        provisional.received_at,
        provisional.market_timestamp,
        provisional.age_seconds,
        provisional.liquidity,
        provisional.available_size,
        provisional.commission,
        provisional.line,
        provisional.event_id,
        provisional.match_confidence,
        False,
        reason,
    )


def _add_base_quotes(shop: V3MatchPriceShop, row) -> None:
    received = datetime.now(timezone.utc)
    for side, odds in (("HOME", row.sb_home), ("DRAW", row.sb_draw), ("AWAY", row.sb_away)):
        if odds is not None and odds > 1:
            shop.quotes[side].append(_make_quote(
                "Sportsbet",
                side,
                float(odds),
                f"{float(odds):.2f}",
                received,
                market_timestamp=getattr(row, "sportsbet_updated", None),
                event_id=str(getattr(row, "sportsbet_event_id", "") or "") or None,
                match_confidence=1.0,
            ))
    for side, odds in (("HOME", row.pm_home), ("DRAW", row.pm_draw), ("AWAY", row.pm_away)):
        if odds is None or odds <= 1:
            continue
        price = 1.0 / float(odds)
        try:
            effective = polymarket_effective_decimal_odds(price)
        except ValueError:
            continue
        shop.quotes[side].append(_make_quote(
            "Polymarket",
            side,
            effective,
            f"{price*100:.1f}¢",
            received,
            market_timestamp=getattr(row, "polymarket_updated", None),
            liquidity=getattr(row, "polymarket_liquidity", None),
            available_size=getattr(row, "polymarket_available_size", None),
            event_id=str(getattr(row, "polymarket_event_id", "") or "") or None,
            match_confidence=1.0,
        ))


def fetch_all_best_prices(api_key: str, rows: list, progress=None) -> V3PriceShopResult:
    targets = eligible_rows(rows)
    matches = {}
    for row in targets:
        shop = V3MatchPriceShop(row.match_name, str(getattr(row,"league","") or "Unknown league"))
        shop.model_probability = {"HOME":getattr(row,"model_fair_home",None),"DRAW":getattr(row,"model_fair_draw",None),"AWAY":getattr(row,"model_fair_away",None)}
        _add_base_quotes(shop,row)
        matches[row.match_name] = shop
    if not targets:
        return V3PriceShopResult(matches, [], 0, 0, ["No independently modelled fixtures were available for execution scanning."], 0, 0, 0)

    leagues = sorted({str(getattr(r,"league","") or "Unknown league") for r in targets})
    requests = hits = rejected = rejected_quotes = 0
    checked, notes = [], []
    total_steps = max(1, len(BOOKMAKERS)*(1+len(leagues)))
    step = 0
    for name, base in BOOKMAKERS:
        try:
            offered, used, hit = _fetch_leagues(name,base,api_key)
            requests += used; hits += int(hit); checked.append(name); step += 1
            _emit(progress,int(100*step/total_steps),"V3 all-book quote matrix",f"{name}: league catalogue ready")
            for league in leagues:
                step += 1
                matched = _strict_league_match(league,offered)
                if not matched:
                    _emit(progress,int(100*step/total_steps),"V3 all-book quote matrix",f"{name}: rejected ambiguous/unmatched league {league}")
                    continue
                events, used, hit = _fetch_league_events(name,base,matched,api_key)
                requests += used; hits += int(hit)
                league_targets = [r for r in targets if str(getattr(r,"league","") or "Unknown league") == league]
                for row in league_targets:
                    event, prices, confidence, ambiguous = _match_event(events,row)
                    rejected += int(ambiguous)
                    if event is None or prices is None:
                        continue
                    received = datetime.now(timezone.utc)
                    market_stamp = _market_timestamp(event)
                    event_id = event.get("id") or event.get("eventId") or event.get("key")
                    liquidity = engine.safe_float(event.get("liquidity") or event.get("marketLiquidity"))
                    available = engine.safe_float(event.get("availableSize") or event.get("maxStake"))
                    for side, odds in zip(SIDES,prices):
                        quote = _make_quote(
                            name,
                            side,
                            odds,
                            f"{odds:.2f}",
                            received,
                            market_timestamp=market_stamp,
                            liquidity=liquidity,
                            available_size=available,
                            event_id=str(event_id) if event_id is not None else None,
                            match_confidence=confidence,
                        )
                        matches[row.match_name].quotes[side].append(quote)
                        rejected_quotes += int(not is_executable_quote(quote))
                _emit(progress,int(100*step/total_steps),"V3 all-book quote matrix",f"{name}: checked all {len(league_targets)} modelled fixture(s) in {league}")
        except Exception as exc:
            notes.append(f"{name}: {exc}")

    for row in targets:
        shop = matches[row.match_name]
        for side in SIDES:
            shop.best[side] = best_executable_quote(shop.quotes[side])
            shop.rejected_quotes[side] = sum(1 for quote in shop.quotes[side] if not is_executable_quote(quote))
            p,best = shop.model_probability.get(side),shop.best[side]
            shop.best_ev_pct[side] = (float(p)*best.decimal_odds-1)*100 if p is not None and best else None
        row.price_shop = shop
        for side in SIDES:
            suffix=side.lower(); best=shop.best[side]
            setattr(row,f"best_price_{suffix}",best.decimal_odds if best else None)
            setattr(row,f"best_price_{suffix}_source",best.source if best else None)
            setattr(row,f"best_price_ev_{suffix}",shop.best_ev_pct[side])
    _emit(progress,100,"V3 all-book quote matrix",f"Completed {len(targets)} fixtures across {len(checked)} additional bookmakers; rejected {rejected_quotes} non-executable quote(s)")
    return V3PriceShopResult(matches,checked,requests,hits,notes,len(targets),rejected,rejected_quotes)
