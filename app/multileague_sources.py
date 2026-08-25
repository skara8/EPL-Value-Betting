from __future__ import annotations

from datetime import date

import engine
from engine import ProviderMatch
from multileague_data import (
    SPORTSBET_BASE,
    POLYMARKET_BASE,
    PINNACLE_BASE,
    ProviderBundle,
    _event_match,
    _fetch_all_provider_events,
    fetch_sportsbet_soccer_leagues,
)


def fetch_sportsbet_catalogue(api_key: str, start_date: date, end_date: date):
    """League eligibility check first, then the actual Sportsbet event catalogue."""
    leagues, league_requests = fetch_sportsbet_soccer_leagues(api_key)
    allowed = {engine.normalise_text(item.name) for item in leagues}
    raw, event_requests = _fetch_all_provider_events(
        SPORTSBET_BASE,
        "PulseScore / Sportsbet soccer",
        api_key,
        "sportsbet_events",
    )

    matches: list[ProviderMatch] = []
    for event in raw:
        league = engine.normalise_text(str(event.get("league") or event.get("leagueName") or ""))
        if not league or league not in allowed:
            continue
        match = _event_match(event, "Sportsbet", start_date, end_date)
        if match is not None:
            matches.append(match)

    return leagues, ProviderBundle(
        "Sportsbet",
        sorted(matches, key=lambda x: x.kickoff),
        raw,
        league_requests + event_requests,
    )


def fetch_reference_provider(
    provider: str,
    api_key: str,
    start_date: date,
    end_date: date,
) -> ProviderBundle:
    """Fetch an optional external reference provider without affecting Sportsbet eligibility."""
    key = provider.strip().lower()
    if key == "polymarket":
        base, cache_key, label = POLYMARKET_BASE, "polymarket_events", "Polymarket"
    elif key in {"pinnacle", "ps3838"}:
        base, cache_key, label = PINNACLE_BASE, "pinnacle_events", "Pinnacle"
    else:
        raise ValueError(f"Unsupported reference provider: {provider}")

    raw, requests = _fetch_all_provider_events(
        base,
        f"PulseScore / {label} soccer",
        api_key,
        cache_key,
    )
    matches: list[ProviderMatch] = []
    for event in raw:
        match = _event_match(event, label, start_date, end_date)
        if match is not None:
            matches.append(match)
    return ProviderBundle(label, sorted(matches, key=lambda x: x.kickoff), raw, requests)
