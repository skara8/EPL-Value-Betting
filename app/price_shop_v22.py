from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import price_shop as base
from price_shop import MatchPriceShop, PriceQuote, PriceShopResult, ProgressCallback


MAX_BOOKMAKER_THREADS = 3


def _provider_scan(name, endpoint, api_key, target_leagues, targets):
    notes = []
    requests = 0
    cache_hits = 0
    quotes: list[tuple[str, str, float]] = []
    try:
        offered, used, hit = base._fetch_leagues(name, endpoint, api_key)
        requests += used
        cache_hits += int(hit)
        for target_league in target_leagues:
            matched_league = base._match_league(target_league, offered)
            if not matched_league:
                continue
            events, used, hit = base._fetch_league_events(name, endpoint, matched_league, api_key)
            requests += used
            cache_hits += int(hit)
            for row in (r for r in targets if str(getattr(r, "league", "") or "Unknown league") == target_league):
                for event in events:
                    prices = base._event_quote(event, row)
                    if prices is None:
                        continue
                    for side, odds in zip(("HOME", "DRAW", "AWAY"), prices):
                        quotes.append((row.match_name, side, float(odds)))
                    break
        return name, quotes, requests, cache_hits, notes
    except Exception as exc:
        notes.append(f"{name}: {exc}")
        return name, quotes, requests, cache_hits, notes


def fetch_best_prices_parallel(
    api_key: str,
    rows: list,
    progress: Optional[ProgressCallback] = None,
    max_matches: int = 15,
    max_leagues: int = 5,
) -> PriceShopResult:
    """Shop independent bookmaker feeds concurrently, with a conservative cap.

    These calls are network-bound, so CPU usage is not the bottleneck. Three
    concurrent provider streams cut idle waiting without opening all seven feeds
    at once or creating an unnecessarily aggressive request burst.
    """
    targets = base._top_rows(rows, max_matches=max_matches, max_leagues=max_leagues)
    matches: dict[str, MatchPriceShop] = {}
    for row in targets:
        shop = MatchPriceShop(match_name=row.match_name, league=str(getattr(row, "league", "") or "Unknown league"))
        shop.model_probability = {
            "HOME": getattr(row, "model_fair_home", None),
            "DRAW": getattr(row, "model_fair_draw", None),
            "AWAY": getattr(row, "model_fair_away", None),
        }
        base._add_base_quotes(shop, row)
        matches[row.match_name] = shop

    if not targets:
        return PriceShopResult(matches, [], 0, 0, ["No model-priced matches were available for price shopping."])

    target_leagues = sorted({str(getattr(row, "league", "") or "Unknown league") for row in targets})
    requests = 0
    cache_hits = 0
    checked: list[str] = []
    notes: list[str] = []
    completed = 0
    total = len(base.BOOKMAKERS)

    base._emit(progress, 66, "Best-price scan", f"Checking {total} bookmaker feeds with up to {MAX_BOOKMAKER_THREADS} concurrent network workers")
    with ThreadPoolExecutor(max_workers=min(MAX_BOOKMAKER_THREADS, total)) as executor:
        futures = {
            executor.submit(_provider_scan, name, endpoint, api_key, target_leagues, targets): name
            for name, endpoint in base.BOOKMAKERS
        }
        for future in as_completed(futures):
            name, provider_quotes, used, hits, provider_notes = future.result()
            completed += 1
            requests += used
            cache_hits += hits
            notes.extend(provider_notes)
            if not provider_notes:
                checked.append(name)
            for match_name, side, odds in provider_quotes:
                if match_name in matches:
                    matches[match_name].quotes[side].append(PriceQuote(name, side, odds, f"{odds:.2f}"))
            base._emit(
                progress,
                66 + int(22 * completed / max(1, total)),
                "Best-price scan",
                f"Completed {completed}/{total} bookmaker feeds · {len(checked)} available",
            )

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

    base._emit(progress, 89, "Best-price scan", f"Compared {len(targets)} matches across {len(checked)} additional bookmakers")
    return PriceShopResult(matches, checked, requests, cache_hits, notes)
