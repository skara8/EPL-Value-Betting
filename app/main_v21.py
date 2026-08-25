from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

import main as base_main
from edge_progress_v21 import enrich_edge_model_progressive
from edge_storage import save_edge_snapshot
from main_v20_final import V20FinalApp, LOGGER
from market_context_v21 import enrich_market_context_fast
from market_storage import save_market_context
from multileague_data import combine_sportsbet_catalogue
from progressive_data import fetch_multileague_sources_progressive
from price_shop import fetch_best_prices
from strategy_v21 import V21Decision, best_available_v21, build_v21_decisions, primary_v21_decisions
from v21_validation import save_v21_decisions, v21_validation_summary


class V21App(V20FinalApp):
    """V2.1: faster market enrichment plus conservative, testable edge decisions."""

    def _create_vars(self) -> None:
        super()._create_vars()
        self._v21_dashboard_best: Optional[V21Decision] = None
        self.v21_validation_status_var = tk.StringVar(value="Robust-edge validation will populate as V2.1 signals accumulate.")
        self.v21_robust_count_var = tk.StringVar(value="0")
        self.v21_sharp_close_var = tk.StringVar(value="0")
        self.v21_avg_clv_var = tk.StringVar(value="—")
        self.v21_positive_clv_var = tk.StringVar(value="—")
        self.v21_avg_robust_ev_var = tk.StringVar(value="—")
        self._stage_timings: dict[str, float] = {}

    # ------------------------------------------------------------------
    # UI additions
    # ------------------------------------------------------------------

    def _build_dashboard(self) -> None:
        super()._build_dashboard()
        self.best_pick_kicker.configure(text="BEST CURRENT ROBUST EDGE")

        # The hero value is the conservative/robust decision EV when a primary
        # signal exists, not merely the average point-estimate EV.
        def relabel(widget) -> None:
            for child in widget.winfo_children():
                if isinstance(child, tk.Label):
                    try:
                        if child.cget("text") == "Model EV":
                            child.configure(text="Decision EV")
                    except Exception:
                        pass
                relabel(child)
        relabel(self.best_pick_frame)

    def _build_validation_tab(self) -> None:
        super()._build_validation_tab()
        robust = ttk.LabelFrame(self.validation_tab, text="V2.1 sharp-market validation", padding=10)
        robust.pack(fill="x", pady=(10, 0))
        ttk.Label(
            robust,
            text=(
                "Primary V2.1 edges must survive the least-favourable external probability, not just the average model. "
                "This panel then compares the first observed execution price with the last captured pre-kickoff Pinnacle price. "
                "Positive sharp CLV is the main early test; realised ROI remains much noisier in small samples."
            ),
            wraplength=1320,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))
        cards = ttk.Frame(robust)
        cards.pack(fill="x")
        specs = (
            ("Robust signals", self.v21_robust_count_var),
            ("Sharp closes captured", self.v21_sharp_close_var),
            ("Average sharp CLV", self.v21_avg_clv_var),
            ("Positive sharp CLV", self.v21_positive_clv_var),
            ("Avg robust EV at flag", self.v21_avg_robust_ev_var),
        )
        for i, (title, var) in enumerate(specs):
            cards.columnconfigure(i, weight=1)
            box = ttk.LabelFrame(cards, text=title, padding=8)
            box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 5, 0))
            ttk.Label(box, textvariable=var, font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(robust, textvariable=self.v21_validation_status_var, style="Muted.TLabel").pack(anchor="w", pady=(7, 0))

    def _build_settings(self) -> None:
        super()._build_settings()
        strategy = ttk.LabelFrame(self.settings_tab, text="V2.1 primary decision rule", padding=12)
        strategy.pack(fill="x", pady=(10, 0))
        ttk.Label(
            strategy,
            text=(
                "The average external-market EV remains visible, but the Dashboard's green primary signal now uses ROBUST EV. "
                "A primary edge requires at least two external provider components, medium/high confidence, market disagreement no greater than 4 percentage points, "
                "and the least-favourable external probability must still clear your EV threshold at the best observed price. "
                "Player/xG/tactical context remains useful research, but it does not rescue a price that fails this market robustness check."
            ),
            wraplength=1260,
            justify="left",
        ).pack(anchor="w")

    # ------------------------------------------------------------------
    # Thread-safe fetch configuration and faster pipeline
    # ------------------------------------------------------------------

    def fetch_matches(self) -> None:
        if self._busy:
            return
        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showerror("PulseScore key required", "Enter your PulseScore API key under Settings first.")
            self.notebook.select(self.settings_tab)
            return

        start = self.start_date.get_date()
        end = self.end_date.get_date()
        if end < start:
            messagebox.showerror("Invalid dates", "End date must be on or after start date.")
            return
        if (end - start).days > 60:
            messagebox.showerror("Range too large", "Choose a range of 60 days or less.")
            return

        min_ev = base_main.safe_float(self.min_ev_var.get(), self.settings.min_ev_pct)
        if not (-50 <= min_ev <= 100):
            messagebox.showerror("Invalid EV threshold", "Enter a minimum EV between -50% and 100%.")
            return
        min_volume = max(0.0, base_main.safe_float(self.min_volume_var.get(), 0.0))
        price_shop_enabled = bool(self.price_shop_enabled_var.get())
        save_snapshots = bool(self.save_snapshots_var.get())

        self._set_busy(True, "Scanning football markets and calculating robust edge…")
        LOGGER.info("V2.1 fetch started %s to %s; min EV %.2f", start, end, min_ev)
        threading.Thread(
            target=self._fetch_worker,
            args=(api_key, start, end, min_ev, min_volume, price_shop_enabled, save_snapshots),
            daemon=True,
        ).start()

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
            self._progress_callback(65, "Matching markets", "Pairing Sportsbet fixtures with independent reference markets")
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

            t = time.perf_counter()
            rows = enrich_edge_model_progressive(
                rows,
                min_ev_pct=min_ev,
                progress=self._progress_callback,
                start_pct=73,
                end_pct=80,
            )
            timings["Probability model"] = time.perf_counter() - t

            if min_volume > 0:
                for row in rows:
                    if row.polymarket_volume is None or row.polymarket_volume < min_volume:
                        for edge in getattr(row, "edge_outcomes", {}).values():
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
                self._progress_callback(81, "Best-price scan", "Core probabilities ready. Checking only the leading matches at additional price sources")

                def price_progress(pct: int, stage: str, detail: str) -> None:
                    # price_shop internally uses 66..89; remap it to this stage's
                    # 81..91 window so the visible bar never appears frozen.
                    fraction = max(0.0, min(1.0, (float(pct) - 66.0) / 23.0))
                    self._progress_callback(81 + int(10 * fraction), stage, detail)

                try:
                    self.price_shop_result = fetch_best_prices(api_key, rows, progress=price_progress)
                    self._last_api_request_count += self.price_shop_result.request_count
                    warnings.extend(self.price_shop_result.notes)
                except Exception as exc:
                    warnings.append(f"Best-price scan: {exc}")
                    LOGGER.exception("V2.1 price-shopping pass failed")
            else:
                self._progress_callback(91, "Best-price scan", "Additional bookmaker comparison is disabled")
            timings["Best-price scan"] = time.perf_counter() - t

            decisions = build_v21_decisions(rows, min_ev_pct=min_ev)
            robust = [d for d in decisions if d.status == "ROBUST +EV"]

            matched_pm = sum(1 for r in rows if r.pm_home is not None)
            matched_pin = sum(1 for r in rows if getattr(r, "pin_home", None) is not None)
            info_notes.append(
                f"Sportsbet catalogue: {len(self._sportsbet_leagues)} soccer league name(s); {len(rows)} Sportsbet fixture(s); "
                f"Polymarket matched {matched_pm}; Pinnacle matched {matched_pin}; V2.1 robust edges {len(robust)}."
            )
            if self.price_shop_result:
                info_notes.append(
                    f"Best-price scan: {len(self.price_shop_result.providers_checked)} extra source(s), "
                    f"{len(self.price_shop_result.matches)} leading match(es), {self.price_shop_result.request_count} extra API request(s), "
                    f"{self.price_shop_result.cache_hits} cache hit(s)."
                )

            self._progress_callback(93, "Saving research", "Saving market snapshots, execution prices and robust-edge decisions")
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
            info_notes.append(f"V2.1 stage timing: {timing_text}. Stored {v21_saved} decision row update(s).")
            self._stage_timings = timings

            self._progress_callback(95, "Preparing dashboard", "Ranking robust edges first, then the highest-EV fallback")
            self.after(0, lambda: self._apply_v21_result(rows, warnings, info_notes, saved, context_saved, edge_saved))
        except Exception as exc:
            LOGGER.exception("V2.1 fetch failed")
            self.after(0, lambda: self._fatal_fetch_error(exc))

    def _apply_v21_result(self, rows, warnings, info_notes, saved, context_saved, edge_saved) -> None:
        super()._apply_v20_result(rows, warnings, info_notes, saved, context_saved, edge_saved)
        self._refresh_v21_validation()

    # ------------------------------------------------------------------
    # Conservative decision-first Dashboard
    # ------------------------------------------------------------------

    def _current_v21_decisions(self) -> list[V21Decision]:
        min_ev = base_main.safe_float(self.min_ev_var.get(), self.settings.min_ev_pct)
        return build_v21_decisions(self.rows, min_ev_pct=min_ev)

    def _fill_dashboard(self) -> None:
        # Retain the existing Dutch analysis and any background context state,
        # then replace the recommendation portion with the V2.1 robust rule.
        super()._fill_dashboard()
        if not hasattr(self, "smart_top_tree"):
            return

        min_ev = base_main.safe_float(self.min_ev_var.get(), self.settings.min_ev_pct)
        decisions = self._current_v21_decisions()
        primary = [d for d in decisions if d.status == "ROBUST +EV"]
        best = primary[0] if primary else best_available_v21(self.rows, min_ev_pct=min_ev)
        self._v21_dashboard_best = best
        # Prevent the inherited football-context recommendation from being used
        # accidentally by the Why-this action.
        self._dashboard_best = None

        for item in self.smart_top_tree.get_children():
            self.smart_top_tree.delete(item)
        visible = primary[:8] if primary else decisions[:8]
        for idx, d in enumerate(visible):
            ev_display = d.robust_ev_pct if d.status == "ROBUST +EV" else d.model_ev_pct
            why = d.reason if len(d.reason) <= 135 else d.reason[:132] + "…"
            confidence = f"{d.confidence.title()} · {d.status.title()}"
            self.smart_top_tree.insert("", "end", iid=f"smart-{idx}", values=(
                d.league,
                d.match_name,
                d.selection,
                f"${d.quote_odds:.2f} {d.quote_source}",
                f"{ev_display:+.1f}%",
                confidence,
                why,
            ))

        if best is None:
            self.best_pick_kicker.configure(text="BEST CURRENT ROBUST EDGE")
            self.best_pick_title_var.set("No independently priced match available")
            self.best_pick_match_var.set("The current fixtures do not have enough external market data to calculate a robust edge.")
            self.best_pick_price_var.set("—")
            self.best_pick_ev_var.set("—")
            self.best_pick_confidence_var.set("—")
            self.best_pick_reason_var.set("The app will not use Sportsbet's own price as proof that Sportsbet is mispriced.")
            self.dashboard_status_var.set("No externally priced comparison available")
            self._set_hero_colour(False)
            return

        self.best_pick_title_var.set(best.selection)
        self.best_pick_match_var.set(f"{best.league} · {best.match_name}")
        self.best_pick_price_var.set(f"${best.quote_odds:.2f} · {best.quote_source}")
        self.best_pick_confidence_var.set(best.confidence.title())

        if best.status == "ROBUST +EV":
            self.best_pick_kicker.configure(text="BEST CURRENT ROBUST EDGE")
            self.best_pick_ev_var.set(f"{best.robust_ev_pct:+.1f}%")
            self.best_pick_reason_var.set(
                f"Two independent market components support this price. The average estimate gives about {best.model_ev_pct:+.1f}% EV, "
                f"and even the more cautious external estimate still gives about {best.robust_ev_pct:+.1f}% EV at {best.quote_source}'s ${best.quote_odds:.2f}. "
                "The green signal uses the cautious number."
            )
            self.dashboard_status_var.set(
                f"{len(primary)} robust option(s) currently clear the +{min_ev:.1f}% conservative-EV threshold"
            )
            self._set_hero_colour(True)
        else:
            self.best_pick_kicker.configure(text="HIGHEST EV AVAILABLE — NOT A ROBUST SIGNAL")
            self.best_pick_ev_var.set(f"{best.model_ev_pct:+.1f}%")
            conservative_text = f"{best.robust_ev_pct:+.1f}%"
            self.best_pick_reason_var.set(
                f"No option currently passes the V2.1 robust test, so this is shown only because it has the highest point-estimate EV. "
                f"The average model is {best.model_ev_pct:+.1f}% EV, while the cautious external estimate is {conservative_text} EV. {best.reason}"
            )
            self.dashboard_status_var.set("No robust recommendation · highest-EV comparison shown")
            self._set_hero_colour(False)

    def _open_best_context(self) -> None:
        decision = self._v21_dashboard_best
        if decision is None:
            self._show_page("analysis", "edge")
            return
        self.context_match_var.set(decision.match_name)
        try:
            self._context_match_changed()
        except Exception:
            pass
        self._show_page("analysis", "context")

    def _open_tree_pick(self, _event=None) -> None:
        selected = self.smart_top_tree.selection()
        if not selected:
            return
        values = self.smart_top_tree.item(selected[0], "values")
        if len(values) >= 2:
            self.context_match_var.set(values[1])
            try:
                self._context_match_changed()
            except Exception:
                pass
            self._show_page("analysis", "context")

    # ------------------------------------------------------------------
    # Validation and diagnostics
    # ------------------------------------------------------------------

    def _refresh_validation_view(self) -> None:
        super()._refresh_validation_view()
        self._refresh_v21_validation()

    def _refresh_v21_validation(self) -> None:
        if not hasattr(self, "v21_robust_count_var"):
            return
        try:
            summary = v21_validation_summary()
        except Exception as exc:
            LOGGER.exception("V2.1 sharp validation failed")
            self.v21_validation_status_var.set(f"Sharp validation unavailable: {exc}")
            return
        self.v21_robust_count_var.set(str(summary.robust_decisions))
        self.v21_sharp_close_var.set(str(summary.with_sharp_close))
        self.v21_avg_clv_var.set("—" if summary.average_sharp_clv_pct is None else f"{summary.average_sharp_clv_pct:+.2f}%")
        self.v21_positive_clv_var.set("—" if summary.positive_sharp_clv_pct is None else f"{summary.positive_sharp_clv_pct:.0f}%")
        self.v21_avg_robust_ev_var.set("—" if summary.average_first_robust_ev_pct is None else f"{summary.average_first_robust_ev_pct:+.2f}%")
        self.v21_validation_status_var.set(
            f"{summary.robust_decisions} robust signal(s); {summary.with_sharp_close} currently have a captured Pinnacle close. "
            "Do not infer profitability from a small sample."
        )

    def _diagnostic_text(self) -> str:
        timings = " · ".join(f"{k} {v:.1f}s" for k, v in self._stage_timings.items()) or "not measured yet"
        return (
            "V2.1 primary strategy: least-favourable external probability must clear the EV threshold at the best observed price\n"
            f"V2.1 last stage timings: {timings}\n"
            "V2.1 AH/total enrichment: indexed provider events rather than repeatedly scanning/parsing the full catalogue\n"
            + super()._diagnostic_text()
        )


def main() -> None:
    try:
        app = V21App()
        app.after(1000, app._refresh_validation_view)
        app.mainloop()
    except Exception:
        LOGGER.exception("Fatal V2.1 application error")
        raise


if __name__ == "__main__":
    main()
