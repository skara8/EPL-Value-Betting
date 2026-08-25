from __future__ import annotations

import json
import re
import time as time_module
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time
from difflib import SequenceMatcher
from typing import Any, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BRISBANE = ZoneInfo("Australia/Brisbane")
UTC = ZoneInfo("UTC")
PULSESCORE_BASE = "https://api.pulsescore.net/api/sportsbet-com-au"
POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"
SPORTSBET_LEAGUE = "Premier League"
REQUEST_TIMEOUT = 30
SPORTSBET_CACHE_SECONDS = 60
DEFAULT_MIN_EV_PCT = 4.0

EPL_CLUB_ALIASES = {
    "afc bournemouth": "AFC Bournemouth", "bournemouth": "AFC Bournemouth",
    "arsenal": "Arsenal", "aston villa": "Aston Villa", "villa": "Aston Villa",
    "brentford": "Brentford", "brighton and hove albion": "Brighton & Hove Albion",
    "brighton hove albion": "Brighton & Hove Albion", "brighton": "Brighton & Hove Albion",
    "chelsea": "Chelsea", "coventry city": "Coventry City", "coventry": "Coventry City",
    "crystal palace": "Crystal Palace", "palace": "Crystal Palace", "everton": "Everton",
    "fulham": "Fulham", "hull city": "Hull City", "hull": "Hull City",
    "ipswich town": "Ipswich Town", "ipswich": "Ipswich Town", "leeds united": "Leeds United",
    "leeds": "Leeds United", "liverpool": "Liverpool", "manchester city": "Manchester City",
    "man city": "Manchester City", "manchester united": "Manchester United",
    "man united": "Manchester United", "man utd": "Manchester United",
    "newcastle united": "Newcastle United", "newcastle": "Newcastle United",
    "nottingham forest": "Nottingham Forest", "nottm forest": "Nottingham Forest",
    "sunderland": "Sunderland", "tottenham hotspur": "Tottenham Hotspur",
    "tottenham": "Tottenham Hotspur", "spurs": "Tottenham Hotspur",
}
DERIVATIVE_WORDS = (
    "halftime", "half-time", "first half", "1st half", "second half", "2nd half",
    "exact score", "correct score", "first team to score", "first goalscorer",
    "total corners", "corners", "player props", "player prop", "more markets",
    "both teams to score", "btts", "winning margin", "race to",
)

_sportsbet_cache: dict[str, Any] = {"time": 0.0, "events": None}


@dataclass
class ProviderMatch:
    provider: str
    kickoff: datetime
    home_team: str
    away_team: str
    home_odds: Optional[float]
    draw_odds: Optional[float]
    away_odds: Optional[float]
    updated: str = "—"
    volume: Optional[float] = None
    liquidity: Optional[float] = None


@dataclass
class CombinedMatch:
    kickoff: datetime
    home_team: str
    away_team: str
    sb_home: Optional[float] = None
    sb_draw: Optional[float] = None
    sb_away: Optional[float] = None
    pm_home: Optional[float] = None
    pm_draw: Optional[float] = None
    pm_away: Optional[float] = None
    sportsbet_favourite: str = "—"
    away_favourite: str = "—"
    sportsbet_overround_pct: Optional[float] = None
    polymarket_sum_minus_100_pct: Optional[float] = None
    sportsbet_updated: str = "—"
    polymarket_updated: str = "—"
    polymarket_volume: Optional[float] = None
    polymarket_liquidity: Optional[float] = None
    pm_fair_home: Optional[float] = None
    pm_fair_draw: Optional[float] = None
    pm_fair_away: Optional[float] = None
    ev_home_pct: Optional[float] = None
    ev_draw_pct: Optional[float] = None
    ev_away_pct: Optional[float] = None
    best_selection: str = "—"
    best_ev_pct: Optional[float] = None
    strategy_flag: str = "NO COMPARISON"
    match_status: str = ""

    @property
    def match_name(self) -> str:
        return f"{self.home_team} v {self.away_team}"


def build_session() -> requests.Session:
    retry = Retry(
        total=4, connect=3, read=3, status=3, backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}), respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"Accept": "application/json", "User-Agent": "EPL-Value-Betting/1.3"})
    return session


SESSION = build_session()


def get_json(url: str, *, headers=None, params=None, provider_name: str) -> Any:
    r = SESSION.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    if r.status_code == 401:
        raise RuntimeError(f"{provider_name}: API key rejected (401).")
    if r.status_code == 403:
        raise RuntimeError(f"{provider_name}: access denied (403).")
    if r.status_code == 429:
        raise RuntimeError(f"{provider_name}: rate/request limit reached (429).")
    if r.status_code >= 400:
        raise RuntimeError(f"{provider_name}: HTTP {r.status_code}: {r.text[:250]}")
    try:
        return r.json()
    except ValueError as exc:
        raise RuntimeError(f"{provider_name}: server returned non-JSON data.") from exc


def safe_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        text += "T00:00:00+00:00"
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except ValueError:
        return None


def normalise_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower().replace("&", " and ")
    text = re.sub(r"\b(fc|afc|cf|football club)\b", " ", text)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def canonical_epl_club(value: str) -> Optional[str]:
    key = normalise_text(value)
    if key in EPL_CLUB_ALIASES:
        return EPL_CLUB_ALIASES[key]
    best_name, best_score = None, 0.0
    for alias, canonical in EPL_CLUB_ALIASES.items():
        score = SequenceMatcher(None, key, alias).ratio()
        if score > best_score:
            best_name, best_score = canonical, score
    return best_name if best_score >= 0.86 else None


def is_current_epl_fixture(home: str, away: str) -> bool:
    h, a = canonical_epl_club(home), canonical_epl_club(away)
    return h is not None and a is not None and h != a


def team_similarity(a: str, b: str) -> float:
    na, nb = normalise_text(a), normalise_text(b)
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.93
    return SequenceMatcher(None, na, nb).ratio()


def teams_match(h1: str, a1: str, h2: str, a2: str) -> float:
    return (team_similarity(h1, h2) + team_similarity(a1, a2)) / 2.0


def in_range(dt: datetime, start: date, end: date) -> bool:
    return start <= dt.astimezone(BRISBANE).date() <= end


def fmt_odds(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.2f}"


def fmt_pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.2f}%"


def fmt_probability(value: Optional[float]) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def fmt_money(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}m"
    if value >= 1_000:
        return f"${value / 1_000:.1f}k"
    return f"${value:.0f}"


def overround_pct(h: Optional[float], d: Optional[float], a: Optional[float]) -> Optional[float]:
    odds = (h, d, a)
    if any(x is None or x <= 1 for x in odds):
        return None
    return (sum(1 / float(x) for x in odds) - 1) * 100


def normalised_polymarket_probabilities(h, d, a) -> Optional[tuple[float, float, float]]:
    odds = (h, d, a)
    if any(x is None or x <= 1 for x in odds):
        return None
    raw = [1 / float(x) for x in odds]
    total = sum(raw)
    return raw[0] / total, raw[1] / total, raw[2] / total


def expected_value_pct(probability: Optional[float], odds: Optional[float]) -> Optional[float]:
    if probability is None or odds is None or odds <= 1:
        return None
    return (probability * odds - 1) * 100


def add_strategy_analysis(row: CombinedMatch, min_ev_pct: float) -> CombinedMatch:
    fair = normalised_polymarket_probabilities(row.pm_home, row.pm_draw, row.pm_away)
    if fair is None:
        row.strategy_flag = "NO COMPARISON"
        return row
    row.pm_fair_home, row.pm_fair_draw, row.pm_fair_away = fair
    row.ev_home_pct = expected_value_pct(fair[0], row.sb_home)
    row.ev_draw_pct = expected_value_pct(fair[1], row.sb_draw)
    row.ev_away_pct = expected_value_pct(fair[2], row.sb_away)
    available = [("HOME", row.ev_home_pct), ("DRAW", row.ev_draw_pct), ("AWAY", row.ev_away_pct)]
    available = [(name, ev) for name, ev in available if ev is not None]
    if not available:
        return row
    row.best_selection, row.best_ev_pct = max(available, key=lambda item: item[1])
    if row.best_ev_pct < min_ev_pct:
        row.strategy_flag = "PASS"
    elif row.best_selection == "AWAY" and row.away_favourite == "YES":
        row.strategy_flag = "AWAY-FAV VALUE"
    else:
        row.strategy_flag = "VALUE"
    return row


def latest_update(values: list[Any]) -> str:
    parsed = [parse_datetime(v) for v in values if v]
    parsed = [x for x in parsed if x is not None]
    return "—" if not parsed else max(parsed).astimezone(BRISBANE).strftime("%d/%m/%y %H:%M:%S")


def _events(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("events", "data", "results"):
            if isinstance(payload.get(key), list):
                return [x for x in payload[key] if isinstance(x, dict)]
    return []


def _sportsbet_epl(event: dict[str, Any]) -> bool:
    league = normalise_text(str(event.get("league") or event.get("leagueName") or ""))
    if any(x in league for x in ("northern premier", "southern premier", "women", "u21", "u23", "reserve")):
        return False
    if league and "premier league" not in league:
        return False
    return is_current_epl_fixture(str(event.get("home") or event.get("homeTeam") or ""), str(event.get("away") or event.get("awayTeam") or ""))


def _sportsbet_1x2(event: dict[str, Any]) -> tuple[Optional[float], Optional[float], Optional[float], str]:
    markets = event.get("markets") or []
    if isinstance(markets, dict):
        markets = list(markets.values())
    updates: list[Any] = []
    for market in markets:
        if not isinstance(market, dict) or market.get("isActive") is False:
            continue
        canonical = str(market.get("canonicalMarket") or market.get("marketType") or "").upper()
        raw = str(market.get("rawName") or market.get("name") or "").lower()
        period = str(market.get("period") or market.get("periodName") or "").upper().replace(" ", "_")
        if canonical != "MATCH_RESULT" and not any(x in raw for x in ("win-draw-win", "match result", "3 way", "3-way")):
            continue
        if period and period not in {"FULL_TIME", "FULLTIME", "REGULATION", "REG", "MATCH", "GAME"}:
            continue
        selections = market.get("selections") or []
        if isinstance(selections, dict):
            selections = list(selections.values())
        prices: dict[str, float] = {}
        for selection in selections:
            if not isinstance(selection, dict) or selection.get("isActive") is False:
                continue
            outcome = str(selection.get("canonicalOutcome") or selection.get("outcome") or selection.get("side") or "").upper()
            name = normalise_text(str(selection.get("rawName") or selection.get("name") or ""))
            odds = safe_float(selection.get("odds") if selection.get("odds") is not None else selection.get("decimal"))
            if odds is None or odds <= 1:
                continue
            if outcome in {"HOME", "H"}:
                prices["home"] = odds
            elif outcome in {"DRAW", "D", "TIE"} or name == "draw":
                prices["draw"] = odds
            elif outcome in {"AWAY", "A"}:
                prices["away"] = odds
            updates.extend([selection.get("updatedAt"), selection.get("lastUpdatedAt")])
        updates.extend([market.get("updatedAt"), market.get("lastUpdatedAt")])
        if prices:
            return prices.get("home"), prices.get("draw"), prices.get("away"), latest_update(updates)
    return None, None, None, "—"


def fetch_sportsbet_epl(api_key: str, start_date: date, end_date: date) -> list[ProviderMatch]:
    if not api_key.strip():
        raise ValueError("A PulseScore API key is required for Sportsbet data.")
    now = time_module.time()
    cached = _sportsbet_cache["events"]
    if cached is not None and now - float(_sportsbet_cache["time"] or 0) < SPORTSBET_CACHE_SECONDS:
        events = cached
    else:
        url = f"{PULSESCORE_BASE}/soccer/leagues/{quote(SPORTSBET_LEAGUE, safe='')}/events"
        payload = get_json(url, headers={"X-Secret": api_key.strip()}, provider_name="PulseScore / Sportsbet")
        events = _events(payload)
        if not events:
            events = []
            for page in range(1, 11):
                payload = get_json(
                    f"{PULSESCORE_BASE}/soccer/events",
                    headers={"X-Secret": api_key.strip()}, params={"page": page, "limit": 100},
                    provider_name="PulseScore / Sportsbet",
                )
                batch = _events(payload)
                events.extend(batch)
                if not batch or not (payload.get("hasNextPage") if isinstance(payload, dict) else False):
                    break
                time_module.sleep(1.05)
        _sportsbet_cache.update({"events": events, "time": now})

    rows: list[ProviderMatch] = []
    for event in events:
        if not _sportsbet_epl(event) or event.get("live") is True or event.get("isInPlay") is True:
            continue
        kickoff = parse_datetime(event.get("startTime") or event.get("startsAt") or event.get("startDate"))
        if kickoff is None or not in_range(kickoff, start_date, end_date):
            continue
        home = str(event.get("home") or event.get("homeTeam") or "").strip()
        away = str(event.get("away") or event.get("awayTeam") or "").strip()
        h, d, a, updated = _sportsbet_1x2(event)
        if all(x is None for x in (h, d, a)):
            continue
        rows.append(ProviderMatch("Sportsbet", kickoff, home, away, h, d, a, updated=updated))
    return sorted(rows, key=lambda r: r.kickoff)


def _split_pm_teams(event: dict[str, Any]) -> tuple[str, str]:
    for source in (str(event.get("title") or "").strip(), str(event.get("subtitle") or "").strip()):
        if not source or " - " in source:
            continue
        lowered = normalise_text(source)
        if any(normalise_text(x) in lowered for x in DERIVATIVE_WORDS):
            continue
        parts = re.split(r"\s+v(?:s\.)?\s+", source, maxsplit=1, flags=re.I)
        if len(parts) != 2:
            continue
        home, away = canonical_epl_club(parts[0]), canonical_epl_club(parts[1])
        if home and away and home != away:
            return home, away
    return "", ""


def _is_epl_polymarket_event(event: dict[str, Any]) -> bool:
    title = str(event.get("title") or "").strip()
    if not title or " - " in title:
        return False
    t = normalise_text(title)
    if any(normalise_text(x) in t for x in DERIVATIVE_WORDS):
        return False
    home, away = _split_pm_teams(event)
    if not home or not away or not is_current_epl_fixture(home, away):
        return False
    slug = str(event.get("slug") or "").lower()
    series_slug = str(event.get("seriesSlug") or "").lower()
    if slug.startswith("epl-") or series_slug == "epl":
        return True
    for item in event.get("series") or []:
        if isinstance(item, dict) and (str(item.get("slug") or "").lower() == "epl" or "premier league" in str(item.get("title") or "").lower()):
            return True
    for item in event.get("tags") or []:
        if isinstance(item, dict):
            slug2 = str(item.get("slug") or "").lower()
            label = str(item.get("label") or "").lower()
            if slug2 in {"epl", "premier-league", "english-premier-league"} or "premier league" in label:
                return True
    return True


def _pm_kickoff(event: dict[str, Any]) -> Optional[datetime]:
    values = [event.get("startTime"), event.get("eventDate")]
    for market in event.get("markets") or []:
        if isinstance(market, dict):
            values.extend([market.get("gameStartTime"), market.get("eventStartTime")])
    values.append(event.get("endDate"))
    for value in values:
        dt = parse_datetime(value)
        if dt is not None:
            return dt
    return None


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _yes_prob(market: dict[str, Any]) -> Optional[float]:
    ask = safe_float(market.get("bestAsk"))
    if ask is not None and 0 < ask < 1:
        return ask
    outcomes, prices = _json_list(market.get("outcomes")), _json_list(market.get("outcomePrices"))
    if len(outcomes) == len(prices):
        for outcome, price in zip(outcomes, prices):
            if str(outcome).strip().lower() == "yes":
                p = safe_float(price)
                if p is not None and 0 < p < 1:
                    return p
    last = safe_float(market.get("lastTradePrice"))
    return last if last is not None and 0 < last < 1 else None


def _decimal_from_probability(p: Optional[float]) -> Optional[float]:
    return None if p is None or not (0 < p < 1) else 1 / p


def _pm_1x2(event: dict[str, Any], home: str, away: str) -> tuple[Optional[float], Optional[float], Optional[float], str]:
    prices: dict[str, float] = {}
    updates: list[Any] = []
    reject = ("spread", "handicap", "total", "over", "under", "score", "corner", "goal", "half", "first team", "both teams")
    for market in event.get("markets") or []:
        if not isinstance(market, dict) or market.get("closed") is True or market.get("active") is False or market.get("acceptingOrders") is False:
            continue
        market_type = str(market.get("sportsMarketType") or "").lower()
        if any(x in market_type for x in reject) or market.get("line") not in (None, "", 0, 0.0):
            continue
        if market_type and "moneyline" not in market_type and market_type not in {"ml", "match_result", "match-result"}:
            continue
        label = str(market.get("groupItemTitle") or market.get("shortOutcomes") or market.get("question") or "").strip()
        question = str(market.get("question") or "").lower()
        p = _yes_prob(market)
        if p is None:
            continue
        if normalise_text(label) == "draw" or re.search(r"\b(draw|tie)\b", question):
            prices["draw"] = p
        elif team_similarity(label, home) >= 0.78 or normalise_text(home) in normalise_text(question):
            prices["home"] = p
        elif team_similarity(label, away) >= 0.78 or normalise_text(away) in normalise_text(question):
            prices["away"] = p
        updates.extend([market.get("updatedAt"), market.get("lastTradePriceTimestamp")])
    return (
        _decimal_from_probability(prices.get("home")),
        _decimal_from_probability(prices.get("draw")),
        _decimal_from_probability(prices.get("away")),
        latest_update(updates),
    )


def _pm_events(payload: Any) -> tuple[list[dict[str, Any]], Optional[str]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)], None
    if isinstance(payload, dict) and isinstance(payload.get("events"), list):
        return [x for x in payload["events"] if isinstance(x, dict)], payload.get("next_cursor")
    return [], None


def _iso_start(d: date) -> str:
    return datetime.combine(d, time.min, BRISBANE).astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _iso_end(d: date) -> str:
    return datetime.combine(d, time.max, BRISBANE).astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def fetch_polymarket_epl(start_date: date, end_date: date) -> list[ProviderMatch]:
    events: list[dict[str, Any]] = []
    params: dict[str, Any] = {
        "limit": 100, "closed": "false", "start_time_min": _iso_start(start_date),
        "start_time_max": _iso_end(end_date), "tag_slug": "epl",
    }
    try:
        payload = get_json(f"{POLYMARKET_GAMMA}/events/keyset", params=params, provider_name="Polymarket")
        batch, cursor = _pm_events(payload)
        events.extend(batch)
        for _ in range(5):
            if not cursor:
                break
            params["after_cursor"] = cursor
            payload = get_json(f"{POLYMARKET_GAMMA}/events/keyset", params=params, provider_name="Polymarket")
            batch, cursor = _pm_events(payload)
            events.extend(batch)
    except RuntimeError:
        payload = get_json(
            f"{POLYMARKET_GAMMA}/public-search",
            params={"q": "Premier League", "limit_per_type": 100}, provider_name="Polymarket",
        )
        if isinstance(payload, dict) and isinstance(payload.get("events"), list):
            events.extend(x for x in payload["events"] if isinstance(x, dict))

    rows: list[ProviderMatch] = []
    seen: set[str] = set()
    for event in events:
        key = str(event.get("id") or event.get("slug") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        if not _is_epl_polymarket_event(event) or event.get("closed") is True or event.get("active") is False:
            continue
        kickoff = _pm_kickoff(event)
        if kickoff is None or not in_range(kickoff, start_date, end_date):
            continue
        home, away = _split_pm_teams(event)
        h, d, a, updated = _pm_1x2(event, home, away)
        if any(x is None for x in (h, d, a)):
            continue
        rows.append(ProviderMatch(
            "Polymarket", kickoff, home, away, h, d, a, updated=updated,
            volume=safe_float(event.get("volume")), liquidity=safe_float(event.get("liquidity")),
        ))
    return sorted(rows, key=lambda r: r.kickoff)


def identify_sportsbet_favourite(home: str, away: str, h: Optional[float], a: Optional[float]) -> tuple[str, str]:
    if h is None or a is None:
        return "—", "—"
    if abs(h - a) < 1e-9:
        return "Joint", "No"
    return (home, "No") if h < a else (away, "YES")


def combine_sources(sb_rows: list[ProviderMatch], pm_rows: list[ProviderMatch], min_ev_pct: float = DEFAULT_MIN_EV_PCT) -> list[CombinedMatch]:
    combined: list[CombinedMatch] = []
    used: set[int] = set()
    for sb in sb_rows:
        best_i, best_score = None, 0.0
        for i, pm in enumerate(pm_rows):
            if i in used:
                continue
            hours = abs((sb.kickoff - pm.kickoff).total_seconds()) / 3600
            if hours > 8:
                continue
            score = teams_match(sb.home_team, sb.away_team, pm.home_team, pm.away_team) - min(hours / 100, 0.05)
            if score > best_score:
                best_i, best_score = i, score
        pm = None
        if best_i is not None and best_score >= 0.72:
            pm = pm_rows[best_i]
            used.add(best_i)
        home = canonical_epl_club(sb.home_team) or sb.home_team
        away = canonical_epl_club(sb.away_team) or sb.away_team
        fav, away_fav = identify_sportsbet_favourite(home, away, sb.home_odds, sb.away_odds)
        row = CombinedMatch(
            sb.kickoff.astimezone(BRISBANE), home, away,
            sb_home=sb.home_odds, sb_draw=sb.draw_odds, sb_away=sb.away_odds,
            pm_home=pm.home_odds if pm else None, pm_draw=pm.draw_odds if pm else None, pm_away=pm.away_odds if pm else None,
            sportsbet_favourite=fav, away_favourite=away_fav,
            sportsbet_overround_pct=overround_pct(sb.home_odds, sb.draw_odds, sb.away_odds),
            polymarket_sum_minus_100_pct=overround_pct(pm.home_odds, pm.draw_odds, pm.away_odds) if pm else None,
            sportsbet_updated=sb.updated, polymarket_updated=pm.updated if pm else "—",
            polymarket_volume=pm.volume if pm else None, polymarket_liquidity=pm.liquidity if pm else None,
            match_status="Matched" if pm else "Sportsbet only",
        )
        combined.append(add_strategy_analysis(row, min_ev_pct))
    for i, pm in enumerate(pm_rows):
        if i in used:
            continue
        row = CombinedMatch(
            pm.kickoff.astimezone(BRISBANE), canonical_epl_club(pm.home_team) or pm.home_team,
            canonical_epl_club(pm.away_team) or pm.away_team,
            pm_home=pm.home_odds, pm_draw=pm.draw_odds, pm_away=pm.away_odds,
            polymarket_sum_minus_100_pct=overround_pct(pm.home_odds, pm.draw_odds, pm.away_odds),
            polymarket_updated=pm.updated, polymarket_volume=pm.volume, polymarket_liquidity=pm.liquidity,
            match_status="Polymarket only",
        )
        combined.append(add_strategy_analysis(row, min_ev_pct))
    return sorted(combined, key=lambda r: r.kickoff)
