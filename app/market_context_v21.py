from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Optional

import engine
from engine import BRISBANE, CombinedMatch
from multileague_data import _context_event


ProgressCallback = Callable[[int, str, str], None]


@dataclass
class ContextIndex:
    parsed_count: int
    by_day: dict[str, list[dict[str, Any]]]
    by_league_day: dict[tuple[str, str], list[dict[str, Any]]]


def _day_key(dt) -> str:
    return dt.astimezone(BRISBANE).date().isoformat()


def build_context_index(raw_events: list[dict[str, Any]]) -> ContextIndex:
    """Parse each provider event once and build small lookup buckets.

    V2.0 repeatedly parsed every raw event for every Sportsbet row. With a large
    all-soccer catalogue that becomes O(rows * raw_events) expensive market
    parsing. V2.1 parses each raw event once, then compares a fixture only with
    same-day/near-day candidates, preferring the same league when possible.
    """
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_league_day: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    parsed = 0
    for event in raw_events:
        context = _context_event(event)
        if context is None:
            continue
        parsed += 1
        day = _day_key(context["kickoff"])
        league = engine.normalise_text(str(context.get("league") or ""))
        by_day[day].append(context)
        if league:
            by_league_day[(league, day)].append(context)
    return ContextIndex(parsed, dict(by_day), dict(by_league_day))


def _candidate_contexts(row: CombinedMatch, index: ContextIndex) -> list[dict[str, Any]]:
    league = engine.normalise_text(str(getattr(row, "league", "") or ""))
    local_date = row.kickoff.astimezone(BRISBANE).date()
    candidates: list[dict[str, Any]] = []

    # Start with exact league/date buckets. Kickoff timestamps can cross a local
    # date boundary between feeds, so include the neighbouring dates.
    for offset in (-1, 0, 1):
        day = (local_date + timedelta(days=offset)).isoformat()
        if league:
            candidates.extend(index.by_league_day.get((league, day), ()))

    if candidates:
        return candidates

    # Provider league names are not always identical. Fall back to date buckets
    # while still avoiding a scan of the provider's entire soccer catalogue.
    for offset in (-1, 0, 1):
        day = (local_date + timedelta(days=offset)).isoformat()
        candidates.extend(index.by_day.get(day, ()))
    return candidates


def find_indexed_context(row: CombinedMatch, index: ContextIndex) -> Optional[dict[str, Any]]:
    best = None
    best_score = 0.0
    target_league = engine.normalise_text(str(getattr(row, "league", "") or ""))
    for context in _candidate_contexts(row, index):
        hours = abs((row.kickoff - context["kickoff"].astimezone(BRISBANE)).total_seconds()) / 3600.0
        if hours > 8:
            continue
        score = engine.teams_match(row.home_team, row.away_team, context["home"], context["away"])
        event_league = engine.normalise_text(str(context.get("league") or ""))
        if target_league and event_league and target_league == event_league:
            score += 0.04
        score -= min(hours / 100.0, 0.05)
        if score > best_score:
            best, best_score = context, score
    return best if best_score >= 0.72 else None


def _attach_sportsbet(row: CombinedMatch, context: dict[str, Any]) -> None:
    row.sb_ah_home_line = context["ah_h_line"]
    row.sb_ah_home_odds = context["ah_h"]
    row.sb_ah_away_line = context["ah_a_line"]
    row.sb_ah_away_odds = context["ah_a"]
    row.sb_total_line = context["total_line"]
    row.sb_total_over = context["total_over"]
    row.sb_total_under = context["total_under"]


def _attach_pinnacle(row: CombinedMatch, context: dict[str, Any]) -> None:
    row.pin_home = context["h"]
    row.pin_draw = context["d"]
    row.pin_away = context["a"]
    row.pin_ah_home_line = context["ah_h_line"]
    row.pin_ah_home_odds = context["ah_h"]
    row.pin_ah_away_line = context["ah_a_line"]
    row.pin_ah_away_odds = context["ah_a"]
    row.pin_total_line = context["total_line"]
    row.pin_total_over = context["total_over"]
    row.pin_total_under = context["total_under"]


def enrich_market_context_fast(
    rows: list[CombinedMatch],
    sportsbet_raw: list[dict[str, Any]],
    pinnacle_raw: list[dict[str, Any]],
    progress: Optional[ProgressCallback] = None,
    start_pct: int = 66,
    end_pct: int = 73,
) -> list[CombinedMatch]:
    """Attach Sportsbet/Pinnacle 1X2, Asian Handicap and totals efficiently."""
    if progress:
        progress(start_pct, "Asian handicap & totals", "Indexing Sportsbet and Pinnacle market context")

    sb_index = build_context_index(sportsbet_raw)
    pin_index = build_context_index(pinnacle_raw)

    if progress:
        progress(
            min(end_pct - 2, start_pct + 2),
            "Asian handicap & totals",
            f"Indexed {sb_index.parsed_count:,} Sportsbet and {pin_index.parsed_count:,} Pinnacle events",
        )

    total = max(1, len(rows))
    sb_found = pin_found = 0
    for i, row in enumerate(rows, start=1):
        sb = find_indexed_context(row, sb_index)
        pin = find_indexed_context(row, pin_index)
        if sb:
            _attach_sportsbet(row, sb)
            sb_found += 1
        if pin:
            _attach_pinnacle(row, pin)
            pin_found += 1

        if progress and (i == total or i % max(10, total // 12) == 0):
            fraction = i / total
            pct = start_pct + 2 + int(max(1, end_pct - start_pct - 2) * fraction)
            progress(
                min(end_pct, pct),
                "Asian handicap & totals",
                f"Matched context for {i:,}/{total:,} fixtures · Sportsbet {sb_found:,} · Pinnacle {pin_found:,}",
            )

    return rows
