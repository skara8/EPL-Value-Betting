from __future__ import annotations

import time as time_module
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any, Callable, Optional

import engine
import multileague_data as ml
from multileague_data import LeagueInfo, ProviderBundle


ProgressCallback = Callable[[int, str, str], None]


def _emit(callback: Optional[ProgressCallback], percent: int, stage: str, detail: str) -> None:
    if callback:
        callback(max(0, min(100, int(percent))), stage, detail)


def _fetch_events_progressive(
    base_url: str,
    provider_name: str,
    api_key: str,
    cache_key: str,
    start_pct: int,
    end_pct: int,
    progress: Optional[ProgressCallback],
) -> tuple[list[dict[str, Any]], int]:
    cached = ml._cache_get(cache_key)
    if cached is not None:
        _emit(progress, end_pct, provider_name, f"Using cached {provider_name} catalogue ({len(cached)} events)")
        return list(cached), 0

    events: list[dict[str, Any]] = []
    requests = 0
    total_pages: Optional[int] = None

    for page in range(1, 60):
        payload = engine.get_json(
            f"{base_url}/soccer/events",
            headers={"X-Secret": api_key.strip()},
            params={"page": page, "limit": ml.PAGE_LIMIT},
            provider_name=provider_name,
        )
        requests += 1
        batch = ml._items(payload, ("events", "data", "results"))
        events.extend(batch)

        if isinstance(payload, dict):
            raw_pages = engine.safe_float(payload.get("totalPages"))
            if raw_pages is not None and raw_pages > 0:
                total_pages = max(1, int(raw_pages))

        if total_pages:
            fraction = min(1.0, page / total_pages)
        else:
            # Unknown catalogue size: move gradually but keep some room until
            # the endpoint tells us there is no next page.
            fraction = min(0.92, page / 10.0)
        pct = start_pct + int((end_pct - start_pct) * fraction)
        page_text = f"page {page}/{total_pages}" if total_pages else f"page {page}"
        _emit(progress, pct, provider_name, f"Downloaded {len(events)} events · {page_text}")

        if not batch or not ml._has_next(payload, page, len(batch)):
            break
        time_module.sleep(0.10)

    ml._cache_put(cache_key, events)
    _emit(progress, end_pct, provider_name, f"{len(events)} events ready")
    return events, requests


def fetch_multileague_sources_progressive(
    api_key: str,
    start_date: date,
    end_date: date,
    progress: Optional[ProgressCallback] = None,
):
    """V2 fetch pipeline with live progress while preserving the V1.9 data model."""
    if not api_key.strip():
        raise ValueError("A PulseScore API key is required.")

    _emit(progress, 2, "Starting", "Checking the selected date range and API key")
    _emit(progress, 5, "Sportsbet leagues", "Checking which soccer competitions Sportsbet currently offers")
    leagues, league_requests = ml.fetch_sportsbet_soccer_leagues(api_key)
    allowed = {engine.normalise_text(item.name) for item in leagues}
    _emit(progress, 10, "Sportsbet leagues", f"Sportsbet catalogue contains {len(leagues)} soccer league names")

    sb_raw, sb_requests = _fetch_events_progressive(
        ml.SPORTSBET_BASE,
        "Sportsbet fixtures",
        api_key,
        "sportsbet_events",
        10,
        38,
        progress,
    )

    # Polymarket and Pinnacle are independent references and can be downloaded
    # concurrently once the Sportsbet eligibility universe is known.
    _emit(progress, 40, "Reference markets", "Loading Polymarket and Pinnacle in parallel")
    reference_jobs = {
        "polymarket": (ml.POLYMARKET_BASE, "Polymarket reference", "polymarket_events", 40, 58),
        "pinnacle": (ml.PINNACLE_BASE, "Pinnacle reference", "pinnacle_events", 40, 58),
    }
    results: dict[str, tuple[list[dict[str, Any]], int]] = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="reference-market") as pool:
        future_map = {
            pool.submit(
                _fetch_events_progressive,
                base,
                label,
                api_key,
                cache_key,
                start_pct,
                end_pct,
                progress,
            ): key
            for key, (base, label, cache_key, start_pct, end_pct) in reference_jobs.items()
        }
        for future in as_completed(future_map):
            key = future_map[future]
            try:
                results[key] = future.result()
            except Exception as exc:
                # Optional reference failure must not discard Sportsbet data.
                results[key] = ([], 0)
                _emit(progress, 58, "Reference markets", f"{key.title()} unavailable: {exc}")

    pm_raw, pm_requests = results.get("polymarket", ([], 0))
    pin_raw, pin_requests = results.get("pinnacle", ([], 0))

    _emit(progress, 60, "Filtering fixtures", "Keeping complete pre-match H/D/A markets in the selected dates")
    sb_matches = []
    for event in sb_raw:
        league = engine.normalise_text(str(event.get("league") or event.get("leagueName") or ""))
        if not league or league not in allowed:
            continue
        match = ml._event_match(event, "Sportsbet", start_date, end_date)
        if match is not None:
            sb_matches.append(match)

    def external_rows(raw: list[dict[str, Any]], name: str):
        out = []
        for event in raw:
            match = ml._event_match(event, name, start_date, end_date)
            if match is not None:
                out.append(match)
        return out

    pm_matches = external_rows(pm_raw, "Polymarket")
    pin_matches = external_rows(pin_raw, "Pinnacle")
    _emit(
        progress,
        64,
        "Filtering fixtures",
        f"Sportsbet {len(sb_matches)} · Polymarket {len(pm_matches)} · Pinnacle {len(pin_matches)} fixtures in range",
    )

    return {
        "leagues": leagues,
        "sportsbet": ProviderBundle("Sportsbet", sorted(sb_matches, key=lambda x: x.kickoff), sb_raw, sb_requests),
        "polymarket": ProviderBundle("Polymarket", sorted(pm_matches, key=lambda x: x.kickoff), pm_raw, pm_requests),
        "pinnacle": ProviderBundle("Pinnacle", sorted(pin_matches, key=lambda x: x.kickoff), pin_raw, pin_requests),
        "request_count": league_requests + sb_requests + pm_requests + pin_requests,
    }
