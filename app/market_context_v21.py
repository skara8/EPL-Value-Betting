from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Optional

import engine
from engine import BRISBANE, CombinedMatch
from execution_v3 import (
    AMBIGUITY_MARGIN,
    KICKOFF_TOLERANCE_MINUTES,
    MIN_PAIR_SCORE,
    MIN_TEAM_SCORE,
    _team_score,
)
from independent_model_v24 import resolve_league_source
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
    """Parse each provider event once and build small lookup buckets."""
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

    # Include neighbouring dates for feeds that cross a timezone boundary. The
    # actual event match below still enforces the much tighter V3 kickoff gate.
    for offset in (-1, 0, 1):
        day = (local_date + timedelta(days=offset)).isoformat()
        if league:
            candidates.extend(index.by_league_day.get((league, day), ()))

    if candidates:
        return candidates

    for offset in (-1, 0, 1):
        day = (local_date + timedelta(days=offset)).isoformat()
        candidates.extend(index.by_day.get(day, ()))
    return candidates


def _same_resolved_league(target: str, offered: str) -> bool:
    if not target or not offered:
        return True
    target_source = resolve_league_source(target)
    offered_source = resolve_league_source(offered)
    if target_source is not None and offered_source is not None:
        return target_source.key == offered_source.key
    # If the project cannot map either provider label to a known league, require
    # strong text agreement rather than silently accepting a cross-league match.
    a, b = engine.normalise_text(target), engine.normalise_text(offered)
    return bool(a and b and (a == b or a in b or b in a))


def find_indexed_context(row: CombinedMatch, index: ContextIndex) -> Optional[dict[str, Any]]:
    """Strict V3 context matcher used for diagnostics and sharp snapshots.

    The old V2.1 path allowed an eight-hour kickoff window and a broad paired
    fuzzy score. That was acceptable for a display-only prototype but is unsafe
    once Pinnacle observations feed CLV research. V3 now uses the same identity
    philosophy as execution: tight time, separate home/away scores, orientation
    checks, league agreement and ambiguity rejection.
    """
    target_league = str(getattr(row, "league", "") or "")
    candidates: list[tuple[float, dict[str, Any]]] = []
    for context in _candidate_contexts(row, index):
        minutes = abs((row.kickoff - context["kickoff"].astimezone(BRISBANE)).total_seconds()) / 60.0
        if minutes > KICKOFF_TOLERANCE_MINUTES:
            continue
        if not _same_resolved_league(target_league, str(context.get("league") or "")):
            continue

        hs = _team_score(row.home_team, str(context.get("home") or ""))
        as_ = _team_score(row.away_team, str(context.get("away") or ""))
        if hs < MIN_TEAM_SCORE or as_ < MIN_TEAM_SCORE:
            continue
        pair = (hs + as_) / 2.0
        if pair < MIN_PAIR_SCORE:
            continue
        reverse = (
            _team_score(row.home_team, str(context.get("away") or ""))
            + _team_score(row.away_team, str(context.get("home") or ""))
        ) / 2.0
        if reverse >= pair - 0.01:
            continue
        confidence = .88 * pair + .12 * max(0.0, 1.0 - minutes / KICKOFF_TOLERANCE_MINUTES)
        candidates.append((confidence, context))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < AMBIGUITY_MARGIN:
        return None
    return candidates[0][1]


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
    row.pinnacle_event_id = context.get("event_id") or context.get("id")
    row.pinnacle_market_timestamp = context.get("updated_at") or context.get("updatedAt") or context.get("timestamp")


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
                f"Strictly matched context for {i:,}/{total:,} fixtures · Sportsbet {sb_found:,} · Pinnacle {pin_found:,}",
            )

    return rows
