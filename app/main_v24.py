from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk
from typing import Optional

import main as base_main
from edge_parallel_v22 import enrich_edge_model_parallel
from edge_storage import save_edge_snapshot
from independent_model_v24 import IndependentForecast, IndependentModelResult, build_and_apply_independent_model
from main_v23 import V23App, LOGGER
from market_context_v21 import enrich_market_context_fast
from market_storage import save_market_context
from multileague_data import combine_sportsbet_catalogue
from price_shop import fetch_best_prices
from progressive_data import fetch_multileague_sources_progressive
from strategy_v24 import V24Decision, best_available_v24, build_v24_decisions
from v24_storage import save_v24_snapshots, v24_counts


class V24App(V23App):
    """V2.4: bookmaker-independent multi-league football probabilities."""

    def _create_vars(self) -> None:
        super()._create_vars()
        self.v24_model_result: Optional[IndependentModelResult] = None
        self.v24_forecasts: dict[str, IndependentForecast] = {}
        self._v24_dashboard_best: Optional[V24Decision] = None
        self.v24_model_status_var = tk.StringVar(value="Independent league models populate after the next fetch.")
        self.v24_supported_var = tk.StringVar(value="0")
        self.v24_priced_var = tk.StringVar(value="0")
        self.v24_high_conf_var = tk.StringVar(value="0")
        self.v24_saved_var = tk.StringVar(value="0")

    def _build_tabs(self) -> None:
        super()._build_tabs()
        if hasattr(self, "analysis_book"):
            self.independent_model_tab = ttk.Frame(self.analysis_book, padding=12)
            self.analysis_book.add(self.independent_model_tab, text="Independent model")
            self._build_independent_model_tab()

    def _build_settings(self) -> None:
        super()._build_settings()
        frame = ttk.LabelFrame(self.settings_tab, text="V2.4 independent probability rule", padding=12)
        frame.pack(fill="x", pady=(10, 0))
        ttk.Label(
            frame,
            text=(
                "Headline EV now uses only a football-data probability. Current Sportsbet, Pinnacle, Polymarket, Asian Handicap and other bookmaker prices are excluded from the probability engine. "
                "For supported leagues, four independent football estimates are compared: a time-decayed Dixon-Coles score model, a league-local Elo model, and short/long decay variants. "
                "Their equal-weight average is the central probability; the least optimistic component is the cautious probability used for Robust Independent EV. "
                "Market prices remain visible as diagnostics and execution opportunities only."
            ),
            wraplength=1260,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text=(
                "V2.4 deliberately does not use bookmaker consensus as a fallback fair probability. If a Sportsbet league cannot be matched to a supported historical league, or the teams do not have enough history, the app says Independent model unavailable rather than fabricating EV."
            ),
            wraplength=1260,
            justify="left",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 0))

    def _build_independent_model_tab(self) -> None:
        intro = ttk.LabelFrame(self.independent_model_tab, text="Football probability first — prices second", padding=12)
        intro.pack(fill="x", pady=(0, 8))
        ttk.Label(
            intro,
            text=(
                "This is the probability actually used by V2.4 EV. No current bookmaker or exchange price enters these H/D/A estimates. "
                "Dixon-Coles models team attack/defence and low-score dependence; Elo measures result strength with league-specific home/draw behaviour; short and long decay models test sensitivity to recent form. "
                "The market-reference column is shown only so you can see where the independent football model disagrees with current prices."
            ),
            wraplength=1320,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(intro, textvariable=self.v24_model_status_var, style="Muted.TLabel").pack(anchor="w", pady=(6, 0))

        cards = ttk.Frame(self.independent_model_tab)
        cards.pack(fill="x", pady=(0, 8))
        for i, (title, var) in enumerate((
            ("Supported histories", self.v24_supported_var),
            ("Fixtures independently priced", self.v24_priced_var),
            ("High-confidence models", self.v24_high_conf_var),
            ("Saved independent rows", self.v24_saved_var),
        )):
            cards.columnconfigure(i, weight=1)
            box = ttk.LabelFrame(cards, text=title, padding=8)
            box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 5, 0))
            ttk.Label(box, textvariable=var, font=("Segoe UI", 13, "bold")).pack(anchor="w")

        self.v24_model_tree = self._make_tree(
            self.independent_model_tab,
            [
                ("league", "League", 145),
                ("match", "Match", 220),
                ("ind", "Independent H/D/A", 145),
                ("fair", "Fair odds H/D/A", 135),
                ("dc", "Dixon-Coles H/D/A", 145),
                ("elo", "Elo H/D/A", 140),
                ("recent", "Short decay H/D/A", 145),
                ("long", "Long decay H/D/A", 145),
                ("xg", "Model xG H/A", 95),
                ("spread", "Model spread", 88),
                ("market", "Market reference H/D/A", 155),
                ("gap", "Largest market gap", 110),
                ("confidence", "Confidence", 78),
            ],
            height=20,
        )

    @staticmethod
    def _triplet_pct(a, b, c) -> str:
        if a is None or b is None or c is None:
            return "—"
        return f"{a * 100:.1f}/{b * 100:.1f}/{c * 100:.1f}"

    @staticmethod
    def _triplet_odds(a, b, c) -> str:
        if not all(x is not None and x > 0 for x in (a, b, c)):
            return "—"
        return f"{a:.2f}/{b:.2f}/{c:.2f}"

    def _fill_independent_model(self) -> None:
        if not hasattr(self, "v24_model_tree"):
            return
        for item in self.v24_model_tree.get_children():
            self.v24_model_tree.delete(item)
        rows_by_name = {r.match_name: r for r in self.rows}
        forecasts = sorted(self.v24_forecasts.values(), key=lambda f: (f.confidence == "HIGH", -f.model_spread_pp), reverse=True)
        for f in forecasts:
            row = rows_by_name.get(f.match_name)
            mh = getattr(row, "market_reference_home", None) if row else None
            md = getattr(row, "market_reference_draw", None) if row else None
            ma = getattr(row, "market_reference_away", None) if row else None
            gaps = []
            if mh is not None:
                gaps.append((abs(f.home_probability - mh) * 100.0, "H", (f.home_probability - mh) * 100.0))
            if md is not None:
                gaps.append((abs(f.draw_probability - md) * 100.0, "D", (f.draw_probability - md) * 100.0))
            if ma is not None:
                gaps.append((abs(f.away_probability - ma) * 100.0, "A", (f.away_probability - ma) * 100.0))
            gap = "—"
            if gaps:
                _, side, signed = max(gaps)
                gap = f"{side} {signed:+.1f} pp"
            self.v24_model_tree.insert("", "end", values=(
                f.league_name,
                f.match_name,
                self._triplet_pct(f.home_probability, f.draw_probability, f.away_probability),
                self._triplet_odds(f.fair_home_odds, f.fair_draw_odds, f.fair_away_odds),
                self._triplet_pct(f.dc_home, f.dc_draw, f.dc_away),
                self._triplet_pct(f.elo_home, f.elo_draw, f.elo_away),
                self._triplet_pct(f.short_home, f.short_draw, f.short_away),
                self._triplet_pct(f.long_home, f.long_draw, f.long_away),
                f"{f.lambda_home:.2f}/{f.lambda_away:.2f}",
                f"{f.model_spread_pp:.1f} pp",
                self._triplet_pct(mh, md, ma),
                gap,
                f.confidence.title(),
            ))
        try:
            saved, _ = v24_counts()
            self.v24_saved_var.set(str(saved))
        except Exception:
            self.v24_saved_var.set("—")
        self.v24_supported_var.set(str(len(self.v24_model_result.supported_leagues) if self.v24_model_result else 0))
        self.v24_priced_var.set(str(len(self.v24_forecasts)))
        self.v24_high_conf_var.set(str(sum(1 for f in self.v24_forecasts.values() if f.confidence == "HIGH")))

    # ------------------------------------------------------------------
    # V2.4 market + independent probability pipeline
    # ------------------------------------------------------------------

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
            self._progress_callback(65, "Matching market intelligence", "Building Sportsbet fixture rows and attaching Polymarket where available")
            rows = combine_sportsbet_catalogue(sb_bundle.matches, pm_bundle.matches, min_ev)
            timings["Fixture matching"] = time.perf_counter() - t

            t = time.perf_counter()
            rows = enrich_market_context_fast(
                rows,
                sb_bundle.raw_events,
                pin_bundle.raw_events,
                progress=self._progress_callback,
                start_pct=66,
                end_pct=71,
            )
            timings["Market diagnostics"] = time.perf_counter() - t

            # Build the legacy market-reference calculations only so PM/Pinnacle,
            # Sportsbet de-vig and AH context remain visible. V2.4 immediately
            # freezes them as diagnostics and replaces the production fair
            # probability with the independent football model below.
            t = time.perf_counter()
            rows, acceleration = enrich_edge_model_parallel(
                rows,
                min_ev_pct=min_ev,
                progress=self._progress_callback,
                start_pct=71,
                end_pct=74,
            )
            self._edge_acceleration = acceleration
            timings["Market reference calculation"] = time.perf_counter() - t

            t = time.perf_counter()
            self._progress_callback(74, "Independent football model", "Loading league history; current odds are excluded from this probability calculation")
            self.v24_model_result = build_and_apply_independent_model(rows, min_ev_pct=min_ev, progress=self._progress_callback)
            self.v24_forecasts = dict(self.v24_model_result.forecasts)
            timings["Independent football model"] = time.perf_counter() - t

            supported = len(self.v24_model_result.supported_leagues)
            priced = len(self.v24_forecasts)
            high = sum(1 for f in self.v24_forecasts.values() if f.confidence == "HIGH")
            info_notes.append(
                f"V2.4 independent model: {supported} supported historical league source(s); {priced}/{len(rows)} current fixture(s) independently priced; {high} high-confidence. "
                f"Historical cache: {self.v24_model_result.cache_hits} hit(s), {self.v24_model_result.downloaded_files} download(s)."
            )
            if self.v24_model_result.unavailable_leagues:
                info_notes.append(
                    f"Independent history unavailable/unmatched for {len(self.v24_model_result.unavailable_leagues)} Sportsbet league label(s); those fixtures do not receive headline EV."
                )

            # Polymarket volume is now a market-quality diagnostic only. It is
            # deliberately not allowed to change an independent football probability.
            if min_volume > 0:
                low_pm = sum(1 for r in rows if r.polymarket_volume is not None and r.polymarket_volume < min_volume)
                if low_pm:
                    info_notes.append(f"{low_pm} matched Polymarket fixture(s) are below the configured volume guide; independent model probabilities are unchanged.")

            t = time.perf_counter()
            self.price_shop_result = None
            if price_shop_enabled and self.v24_forecasts:
                self._progress_callback(87, "Best-price scan", "Independent probabilities are frozen. Now comparing execution prices without changing them")

                def price_progress(pct: int, stage: str, detail: str) -> None:
                    fraction = max(0.0, min(1.0, (float(pct) - 66.0) / 23.0))
                    self._progress_callback(87 + int(7 * fraction), stage, detail)

                try:
                    self.price_shop_result = fetch_best_prices(api_key, rows, progress=price_progress, max_matches=20, max_leagues=8)
                    self._last_api_request_count += self.price_shop_result.request_count
                    for note in self.price_shop_result.notes:
                        if note.startswith("No model-priced"):
                            info_notes.append(note)
                        else:
                            warnings.append(note)
                except Exception as exc:
                    warnings.append(f"Best-price scan: {exc}")
                    LOGGER.exception("V2.4 price-shopping pass failed")
            else:
                self._progress_callback(94, "Best-price scan", "No independently priced fixtures available for price shopping")
            timings["Best-price scan"] = time.perf_counter() - t

            decisions = build_v24_decisions(rows, min_ev_pct=min_ev)
            robust = [d for d in decisions if d.status == "ROBUST INDEPENDENT +EV"]
            positive = [d for d in decisions if d.model_ev_pct > 0]
            info_notes.append(
                f"V2.4 decisions: {len(decisions)} priced outcome(s); {len(positive)} positive by central independent estimate; {len(robust)} robust independent +EV outcome(s)."
            )

            self._progress_callback(95, "Saving research", "Saving independent probabilities separately from market prices")
            t = time.perf_counter()
            saved = context_saved = edge_saved = v24_model_saved = v24_decision_saved = 0
            if save_snapshots and rows:
                saved = base_main.save_snapshot(rows)
                context_saved = save_market_context(rows)
                edge_saved = save_edge_snapshot(rows)
                v24_model_saved, v24_decision_saved = save_v24_snapshots(rows, decisions)
            timings["Persistence"] = time.perf_counter() - t
            timings["Total market pipeline"] = time.perf_counter() - started
            timing_text = " · ".join(f"{name} {seconds:.1f}s" for name, seconds in timings.items())
            info_notes.append(
                f"V2.4 stage timing: {timing_text}. Stored {v24_model_saved} independent forecast row(s) and {v24_decision_saved} price decision row(s)."
            )
            self._stage_timings = timings

            self._progress_callback(98, "Preparing dashboard", "Ranking robust independent EV first; if none exists, showing the highest EV available with an explicit label")
            self.after(0, lambda: self._apply_v24_result(rows, warnings, info_notes, saved, context_saved, edge_saved))
        except Exception as exc:
            LOGGER.exception("V2.4 fetch failed")
            self.after(0, lambda: self._fatal_fetch_error(exc))

    def _apply_v24_result(self, rows, warnings, info_notes, saved, context_saved, edge_saved) -> None:
        # Reuse the mature V2 UI application path; dynamic dispatch calls this
        # class's independent dashboard afterwards.
        super()._apply_v21_result(rows, warnings, info_notes, saved, context_saved, edge_saved)
        self._fill_independent_model()
        self.v24_model_status_var.set(
            f"{len(self.v24_forecasts)} fixture(s) have bookmaker-independent probabilities. Market prices shown elsewhere are comparisons only."
        )

    # ------------------------------------------------------------------
    # Independent decision-first dashboard
    # ------------------------------------------------------------------

    def _current_v24_decisions(self) -> list[V24Decision]:
        min_ev = base_main.safe_float(self.min_ev_var.get(), self.settings.min_ev_pct)
        return build_v24_decisions(self.rows, min_ev_pct=min_ev)

    def _fill_dashboard(self) -> None:
        # Preserve Dutch analysis and the rest of the V2 layout, then replace
        # only the hero/shortlist with V2.4 independent decisions.
        super()._fill_dashboard()
        if not hasattr(self, "smart_top_tree"):
            return
        min_ev = base_main.safe_float(self.min_ev_var.get(), self.settings.min_ev_pct)
        decisions = self._current_v24_decisions()
        robust = [d for d in decisions if d.status == "ROBUST INDEPENDENT +EV"]
        best = robust[0] if robust else best_available_v24(self.rows, min_ev_pct=min_ev)
        self._v24_dashboard_best = best
        self._v21_dashboard_best = best
        self._dashboard_best = None

        for item in self.smart_top_tree.get_children():
            self.smart_top_tree.delete(item)
        visible = robust[:8] if robust else sorted(decisions, key=lambda d: d.model_ev_pct, reverse=True)[:8]
        for idx, d in enumerate(visible):
            ev = d.robust_ev_pct if d.status == "ROBUST INDEPENDENT +EV" else d.model_ev_pct
            reason = d.reason if len(d.reason) <= 135 else d.reason[:132] + "…"
            self.smart_top_tree.insert("", "end", iid=f"smart-{idx}", values=(
                d.league,
                d.match_name,
                d.selection,
                f"${d.quote_odds:.2f} {d.quote_source}",
                f"{ev:+.1f}%",
                f"{d.confidence.title()} · {d.status.title()}",
                reason,
            ))

        if best is None:
            self.best_pick_kicker.configure(text="INDEPENDENT MODEL")
            self.best_pick_title_var.set("No independent probability available")
            self.best_pick_match_var.set("The current Sportsbet fixtures could not be matched to a supported historical league/team model.")
            self.best_pick_price_var.set("—")
            self.best_pick_ev_var.set("—")
            self.best_pick_confidence_var.set("—")
            self.best_pick_reason_var.set("V2.4 will not use bookmaker consensus as a substitute for its own football probability.")
            self.dashboard_status_var.set("No bookmaker-independent comparison available")
            self._set_hero_colour(False)
            return

        self.best_pick_title_var.set(best.selection)
        self.best_pick_match_var.set(f"{best.league} · {best.match_name}")
        self.best_pick_price_var.set(f"${best.quote_odds:.2f} · {best.quote_source}")
        self.best_pick_confidence_var.set(best.confidence.title())
        market_text = ""
        if best.market_probability is not None and best.market_gap_pp is not None:
            market_text = f" The current market reference is about {best.market_probability * 100:.1f}%, so our football model differs by {best.market_gap_pp:+.1f} percentage points."

        if best.status == "ROBUST INDEPENDENT +EV":
            self.best_pick_kicker.configure(text="BEST ROBUST INDEPENDENT EDGE")
            self.best_pick_ev_var.set(f"{best.robust_ev_pct:+.1f}%")
            self.best_pick_reason_var.set(
                f"Our football-only model gives {best.selection} about {best.model_probability * 100:.1f}% chance, equal to fair odds near ${best.fair_odds:.2f}. "
                f"The best observed price is ${best.quote_odds:.2f} at {best.quote_source}. The central EV is {best.model_ev_pct:+.1f}%, and even the cautious model estimate is {best.robust_ev_pct:+.1f}% EV.{market_text}"
            )
            self.dashboard_status_var.set("Robust independent +EV found · probability created before comparing prices")
            self._set_hero_colour(True)
        else:
            self.best_pick_kicker.configure(text="HIGHEST INDEPENDENT EV AVAILABLE — NOT A ROBUST SIGNAL")
            self.best_pick_ev_var.set(f"{best.model_ev_pct:+.1f}%")
            self.best_pick_reason_var.set(
                f"The football-only model gives {best.selection} about {best.model_probability * 100:.1f}% chance (fair odds ${best.fair_odds:.2f}). "
                f"The best observed price is ${best.quote_odds:.2f} at {best.quote_source}, giving {best.model_ev_pct:+.1f}% EV. {best.reason}{market_text}"
            )
            self.dashboard_status_var.set(best.status)
            self._set_hero_colour(False)


def main() -> None:
    try:
        V24App().mainloop()
    except Exception:
        LOGGER.exception("Fatal V2.4 application error")
        raise


if __name__ == "__main__":
    main()
