from __future__ import annotations

from datetime import date

from multileague_data import ProviderBundle
from multileague_sources import fetch_reference_provider, fetch_sportsbet_catalogue


def fetch_multileague_sources_resilient(api_key: str, start_date: date, end_date: date):
    """
    Sportsbet is mandatory because it defines the eligible competition set.
    Polymarket and Pinnacle are optional reference feeds: a subscription or
    temporary provider error should reduce model coverage, not prevent the
    Sportsbet soccer catalogue from loading.
    """
    leagues, sportsbet = fetch_sportsbet_catalogue(api_key, start_date, end_date)
    requests = sportsbet.request_count

    try:
        polymarket = fetch_reference_provider("polymarket", api_key, start_date, end_date)
        requests += polymarket.request_count
    except Exception:
        polymarket = ProviderBundle("Polymarket", [], [], 0)

    try:
        pinnacle = fetch_reference_provider("pinnacle", api_key, start_date, end_date)
        requests += pinnacle.request_count
    except Exception:
        pinnacle = ProviderBundle("Pinnacle", [], [], 0)

    return {
        "leagues": leagues,
        "sportsbet": sportsbet,
        "polymarket": polymarket,
        "pinnacle": pinnacle,
        "request_count": requests,
    }
