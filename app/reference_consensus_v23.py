from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Optional

import engine
from edge_model import power_devig
from price_shop import BOOKMAKERS, _fetch_leagues, _fetch_league_events, _match_league


ProgressCallback = Callable[[int, str, str], None]

# Start with broad fixed-odds books. Crypto books remain available to the normal
# price-shopping layer but are not needed to establish the fallback probability
# when conventional independent books already provide enough coverage.
REFERENCE_BOOKMAKERS = tuple(
    item for item in BOOKMAKERS
    if item[0] in {"Bet365", "Ladbrokes", "TAB", "Unibet AU", "BetRight"}
)


@dataclass
class ConsensusStats:
    target_rows: int = 0
    modelled_rows: int = 0
    request_count: int = 0
    cache_hits: int = 0
    providers_checked: tuple[str, ...] = ()


def _emit(cb: Optional[ProgressCallback], percent: int, detail: str) -> None:
    if cb:
        cb(max(0, min(100, int(percent))), "Fallback reference consensus", detail)


def _row_key(row) -> str:
    league = engine.normalise_text(str(getattr(row, "league", "") or ""))
    kickoff = row.kickoff.astimezone(engine.BRISBANE).strftime("%Y-%m-%dT%H:%M")
    return f"{league}|{kickoff}|{engine.normalise_text(row.home_team)}|{engine.normalise_text(row.away_team)}"


def _primary_reference_count(row) -> int:
    count = 0
    if all(getattr(row, name, None) is not None for name in ("pm_fair_home", "pm_fair_draw", "pm_fair_away")):
        count += 1
    if all(getattr(row, name, None) is not None for name in ("pin_home", "pin_draw", "pin_away")):
        count += 1
    return count


def _best_event_odds(events: list[dict], row) -> Optional[tuple[float, float, float]]:
    best = None
    best_score = 0.0
    for event in events:
        if event.get("live") is True or event.get("isInPlay") is True:
            continue
        kickoff = engine.parse_datetime(event.get("startTime") or event.get("startsAt") or event.get("startDate"))
        if kickoff is None:
            continue
        hours = abs((row.kickoff - kickoff.astimezone(engine.BRISBANE)).total_seconds()) / 3600.0
        if hours > 8:
            continue
        home = str(event.get("home") or event.get("homeTeam") or "").strip()
        away = str(event.get("away") or event.get("awayTeam") or "").strip()
        score = engine.teams_match(row.home_team, row.away_team, home, away) - min(hours / 100.0, 0.05)
        if score <= best_score:
            continue
        # Keep extraction here independent of price_shop._event_quote so we can
        # select the highest-scoring event rather than the first acceptable one.
        from advanced_market import extract_1x2
        h, d, a = extract_1x2(event)
        if any(x is None or x <= 1 for x in (h, d, a)):
            continue
        best = (float(h), float(d), float(a))
        best_score = score
    return best if best_score >= 0.74 else None


def apply_consensus_quotes(row, source_odds: dict[str, tuple[float, float, float]], min_books: int = 2) -> bool:
    """Attach de-vigged independent bookmaker components to one fixture."""
    components = []
    for source, odds in sorted(source_odds.items()):
        fair = power_devig(*odds)
        if fair is None:
            continue
        components.append((source, fair[0], fair[1], fair[2]))

    if len(components) < min_books:
        row.consensus_components = tuple()
        row.consensus_book_count = len(components)
        row.consensus_sources = tuple(source for source, *_ in components)
        return False

    row.consensus_components = tuple(components)
    row.consensus_book_count = len(components)
    row.consensus_sources = tuple(source for source, *_ in components)
    row.consensus_fair_home = sum(x[1] for x in components) / len(components)
    row.consensus_fair_draw = sum(x[2] for x in components) / len(components)
    row.consensus_fair_away = sum(x[3] for x in components) / len(components)
    return True


def fetch_reference_consensus(
    api_key: str,
    rows: list,
    progress: Optional[ProgressCallback] = None,
    min_books: int = 2,
    max_books: int = 4,
) -> ConsensusStats:
    """Fill reference gaps from multiple independent non-Sportsbet books.

    The scan only targets fixtures with fewer than two primary external
    references. Provider league/event responses use price_shop's 15-minute
    cache, so the later best-price pass can reuse the same API work.
    """
    targets = [row for row in rows if _primary_reference_count(row) < 2]
    stats = ConsensusStats(target_rows=len(targets))
    if not targets:
        _emit(progress, 80, "All fixtures already have sufficient primary reference coverage")
        return stats

    by_league: dict[str, list] = defaultdict(list)
    for row in targets:
        by_league[str(getattr(row, "league", "") or "Unknown league")].append(row)

    quotes: dict[str, dict[str, tuple[float, float, float]]] = defaultdict(dict)
    checked: list[str] = []
    total_provider_steps = max(1, len(REFERENCE_BOOKMAKERS))

    _emit(progress, 73, f"Trying up to {len(REFERENCE_BOOKMAKERS)} independent bookmakers for {len(targets):,} reference-poor fixtures")

    for provider_index, (name, base) in enumerate(REFERENCE_BOOKMAKERS, start=1):
        # Skip leagues where every target already has enough independent books.
        open_leagues = [
            league for league, league_rows in by_league.items()
            if any(len(quotes[_row_key(row)]) < max_books for row in league_rows)
        ]
        if not open_leagues:
            break
        try:
            offered, used, hit = _fetch_leagues(name, base, api_key)
            stats.request_count += used
            stats.cache_hits += int(hit)
            checked.append(name)

            for league in open_leagues:
                matched_league = _match_league(league, offered)
                if not matched_league:
                    continue
                league_rows = [row for row in by_league[league] if len(quotes[_row_key(row)]) < max_books]
                if not league_rows:
                    continue
                events, used, hit = _fetch_league_events(name, base, matched_league, api_key)
                stats.request_count += used
                stats.cache_hits += int(hit)
                for row in league_rows:
                    odds = _best_event_odds(events, row)
                    if odds is not None:
                        quotes[_row_key(row)][name] = odds
        except Exception:
            # These providers are optional. One inaccessible bookmaker should
            # never discard Sportsbet or the existing sharp-reference model.
            pass

        pct = 73 + int(7 * provider_index / total_provider_steps)
        covered = sum(1 for row in targets if len(quotes[_row_key(row)]) >= min_books)
        _emit(progress, pct, f"{name} checked · {covered:,}/{len(targets):,} fixtures now have {min_books}+ fallback books")

    modelled = 0
    for row in targets:
        if apply_consensus_quotes(row, quotes[_row_key(row)], min_books=min_books):
            modelled += 1
    stats.modelled_rows = modelled
    stats.providers_checked = tuple(checked)
    _emit(progress, 80, f"Fallback consensus available for {modelled:,}/{len(targets):,} reference-poor fixtures")
    return stats
