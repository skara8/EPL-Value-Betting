from __future__ import annotations

import time
from typing import Optional

import main as base_main
from edge_parallel_v22 import enrich_edge_model_parallel
from edge_storage import save_edge_snapshot
from main_v22 import V22App, LOGGER
from market_context_v21 import enrich_market_context_fast
from market_storage import save_market_context
from multileague_data import combine_sportsbet_catalogue
from price_shop import fetch_best_prices
from progressive_data import fetch_multileague_sources_progressive
from reference_consensus_v23 import fetch_reference_consensus
from strategy_v21 import build_v21_decisions
from v21_validation import save_v21_decisions


class V23App(V22App):
    """V2.3: wider independent reference coverage without using Sportsbet as fair value."""

    def _fetch_worker(self, api_key, start, end, min_ev, min_volume, price_shop_enabled, save_snapshots) -> None:
        warnings: list[str] = []
        info_notes: list[str] = []
        started = time.perf_counter()
        timings: dict[str, float] = {}
        try:
            t = time.perf_counter()
            source = fetch_multileague_sources_progressive(api_key, start, end, self._progress_callback)
            timings["API catalogue"] = time.perf_counter() - t
            self._sportsbet_leagues = list(source["leagues"])
            self._last_api_request_count = int(source.get("request_count", 0) or 0)
            sb_bundle = source["sportsbet"]
            pm_bundle = source["polymarket"]
            pin_bundle = source["pinnacle"]

            t = time.perf_counter()
            self._progress_callback(65, "Matching markets", "Pairing Sportsbet fixtures with Polymarket where available")
            rows = combine_sportsbet_catalogue(sb_bundle.matches, pm_bundle.matches, min_ev)
            timings["Fixture matching"] = time.perf_counter() - t

            t = time.perf_counter()
            rows = enrich_market_context_fast(
                rows,
                sb_bundle.raw_events,
                pin_bundle.raw_events,
                progress=self._progress_callback,
                start_pct=66,
                end_pct=72,
            )
            timings["AH/total context"] = time.perf_counter() - t

            # V2.3 coverage fallback. This happens before EV calculation so
            # Sportsbet-only rows can still obtain an independent probability
            # when several other bookmakers offer the same fixture.
            t = time.perf_counter()
            consensus_stats = fetch_reference_consensus(
                api_key,
                rows,
                progress=self._progress_callback,
                min_books=2,
                max_books=4,
            )
            self._last_api_request_count += consensus_stats.request_count
            timings["Fallback consensus"] = time.perf_counter() - t

            t = time.perf_counter()
            rows, acceleration = enrich_edge_model_parallel(
                rows,
                min_ev_pct=min_ev,
                progress=self._progress_callback,
                start_pct=81,
                end_pct=87,
            )
            self._edge_acceleration = acceleration
            timings["Probability model"] = time.perf_counter() - t
            if acceleration.parallel:
                info_notes.append(
                    f"CPU acceleration: {acceleration.workers} worker processes calculated {acceleration.completed} fixtures."
                )
            elif acceleration.fallback_reason:
                info_notes.append(
                    f"CPU acceleration fell back to one process: {acceleration.fallback_reason}"
                )

            if min_volume > 0:
                for row in rows:
                    if row.polymarket_volume is None or row.polymarket_volume < min_volume:
                        for edge in getattr(row, "edge_outcomes", {}).values():
                            # Do not downgrade consensus-only rows merely because
                            # Polymarket does not cover their league.
                            tier = str(getattr(row, "reference_tier", ""))
                            if tier.startswith("TIER 2"):
                                continue
                            if edge.model_ev_pct is not None and edge.model_ev_pct >= min_ev:
                                edge.signal = "EDGE - LOW PM VOLUME"
                                edge.confidence = "LOW"
                        best = getattr(row, "edge_outcomes", {}).get(getattr(row, "edge_best_selection", ""))
                        if best is not None:
                            row.edge_signal = best.signal
                            row.edge_confidence = best.confidence

            t = time.perf_counter()
            self.price_shop_result = None
            if price_shop_enabled and rows:
                self._progress_callback(88, "Best-price scan", "Fair probabilities ready. Checking leading matches at additional price sources")

                def price_progress(pct: int, stage: str, detail: str) -> None:
                    fraction = max(0.0, min(1.0, (float(pct) - 66.0) / 23.0))
                    self._progress_callback(88 + int(6 * fraction), stage, detail)

                try:
                    self.price_shop_result = fetch_best_prices(api_key, rows, progress=price_progress)
                    self._last_api_request_count += self.price_shop_result.request_count
                    warnings.extend(self.price_shop_result.notes)
                except Exception as exc:
                    warnings.append(f"Best-price scan: {exc}")
                    LOGGER.exception("V2.3 price-shopping pass failed")
            else:
                self._progress_callback(94, "Best-price scan", "Additional bookmaker comparison is disabled")
            timings["Best-price scan"] = time.perf_counter() - t

            decisions = build_v21_decisions(rows, min_ev_pct=min_ev)
            robust = [d for d in decisions if d.status == "ROBUST +EV"]
            consensus_edges = [d for d in decisions if d.status == "CONSENSUS +EV"]
            matched_pm = sum(1 for r in rows if r.pm_home is not None)
            matched_pin = sum(1 for r in rows if getattr(r, "pin_home", None) is not None)
            modelled = sum(1 for r in rows if getattr(r, "model_fair_home", None) is not None)
            tier2 = sum(1 for r in rows if str(getattr(r, "reference_tier", "")).startswith("TIER 2"))
            info_notes.append(
                f"Reference coverage: {len(rows)} Sportsbet fixture(s); Polymarket {matched_pm}; Pinnacle {matched_pin}; "
                f"fallback consensus {consensus_stats.modelled_rows}; independently model-priced {modelled}; "
                f"sharp robust edges {len(robust)}; consensus edges {len(consensus_edges)}."
            )
            if tier2:
                info_notes.append(
                    f"{tier2} fixture(s) use lower-confidence bookmaker consensus because primary sharp/exchange references were unavailable."
                )
            if self.price_shop_result:
                info_notes.append(
                    f"Best-price scan: {len(self.price_shop_result.providers_checked)} extra source(s), "
                    f"{len(self.price_shop_result.matches)} leading match(es), {self.price_shop_result.request_count} extra API request(s), "
                    f"{self.price_shop_result.cache_hits} cache hit(s)."
                )

            self._progress_callback(96, "Saving research", "Saving market snapshots, execution prices and model decisions")
            t = time.perf_counter()
            saved = context_saved = edge_saved = v21_saved = 0
            if save_snapshots and rows:
                saved = base_main.save_snapshot(rows)
                context_saved = save_market_context(rows)
                edge_saved = save_edge_snapshot(rows)
                v21_saved = save_v21_decisions(rows, decisions)
            timings["Persistence"] = time.perf_counter() - t
            timings["Total market pipeline"] = time.perf_counter() - started
            timing_text = " · ".join(f"{name} {seconds:.1f}s" for name, seconds in timings.items())
            info_notes.append(f"Stage timing: {timing_text}. Stored {v21_saved} decision row update(s).")
            self._stage_timings = timings

            self._progress_callback(98, "Preparing dashboard", "Ranking sharp robust edges first, then consensus and highest-EV fallbacks")
            self.after(0, lambda: self._apply_v21_result(rows, warnings, info_notes, saved, context_saved, edge_saved))
        except Exception as exc:
            LOGGER.exception("V2.3 fetch failed")
            self.after(0, lambda: self._fatal_fetch_error(exc))


def main() -> None:
    try:
        V23App().mainloop()
    except Exception:
        LOGGER.exception("Fatal V2.3 application error")
        raise


if __name__ == "__main__":
    main()
