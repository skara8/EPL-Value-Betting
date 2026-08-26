from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Optional

import main as base_main
import independent_model_v24 as v24_history
from edge_parallel_v22 import enrich_edge_model_parallel
from edge_storage import save_edge_snapshot
from independent_model_v3 import V3IndependentForecast, V3ModelResult, build_and_apply_v3_model
from main_v24 import V24App, LOGGER
from market_context_v21 import enrich_market_context_fast
from market_storage import save_market_context
from multileague_data import combine_sportsbet_catalogue
from price_shop_v3 import V3PriceShopResult, fetch_best_prices_v3
from progressive_data import fetch_multileague_sources_progressive
from strategy_v3 import V3Decision, best_available_v3, build_v3_decisions
from v3_storage import (
    current_validation_grade,
    latest_walkforward_summary,
    save_v3_live_snapshot,
    save_walkforward_result,
    v3_counts,
)
from v3_walkforward import WalkForwardResult, run_walk_forward


class V3App(V24App):
    """V3 scientific forecasting laboratory.

    Production decisions continue to use a bookmaker-independent football
    baseline. New statistical models are challengers until chronological
    validation demonstrates incremental information.
    """

    def _create_vars(self) -> None:
        super()._create_vars()
        self.v3_model_result: Optional[V3ModelResult] = None
        self.v3_forecasts: dict[str, V3IndependentForecast] = {}
        self._v3_dashboard_best: Optional[V3Decision] = None
        self.v3_validation_grade_var = tk.StringVar(value="UNVALIDATED")
        self.v3_quote_count_var = tk.StringVar(value="0")
        self.v3_ambiguity_var = tk.StringVar(value="0")
        self.v3_challenger_var = tk.StringVar(value="0")
        self.v3_walk_count_var = tk.StringVar(value="0")
        self.v3_walk_status_var = tk.StringVar(value="No V3 walk-forward run stored yet.")
        self.v3_lab_status_var = tk.StringVar(
            value="V3 keeps new statistical models in research until they beat the frozen baseline chronologically."
        )
        self._v3_walk_running = False
        self._last_v3_walk_result: Optional[WalkForwardResult] = None

    def _build_tabs(self) -> None:
        super()._build_tabs()
        self.v3_lab_tab = ttk.Frame(self.research_book, padding=12)
        self.research_book.insert(0, self.v3_lab_tab, text="V3 laboratory")
        self._build_v3_lab()
        self.after(80, self._normalise_visible_copy)

    def _build_settings(self) -> None:
        super()._build_settings()
        # Remove the inherited V2.4 methodology panel because V3 deliberately
        # changes its interpretation of model spread and robust EV.
        for child in list(self.settings_tab.winfo_children()):
            try:
                if isinstance(child, ttk.LabelFrame) and "V2.4 independent probability rule" in str(child.cget("text")):
                    child.destroy()
            except Exception:
                pass

        frame = ttk.LabelFrame(self.settings_tab, text="V3 scientific decision rule", padding=12)
        frame.pack(fill="x", pady=(10, 0))
        ttk.Label(
            frame,
            text=(
                "Current bookmaker/exchange prices never enter the independent football probability. The probability is frozen first; then V3 scans execution prices. "
                "Missing model variants remain missing and do not count as extra evidence. The dynamic-state model is a challenger only: it cannot change the Dashboard probability until chronological validation proves an improvement."
            ),
            wraplength=1260,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text=(
                "A displayed +EV value is a research estimate, not a proven betting edge. Forecast validation and point-in-time market/CLV validation are separate gates. "
                "V3 will only use a future EDGE_VALIDATED grade to label a signal as validated."
            ),
            wraplength=1260,
            justify="left",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 0))

    @staticmethod
    def _clean_copy(text: str) -> str:
        # Keep the existing general cleanup and remove stale product-version
        # labels from ordinary user-facing pages. Diagnostics remains exempt in
        # the inherited traversal because literal versions are useful there.
        from main_v20 import V20App
        text = V20App._clean_copy(text)
        replacements = {
            "V2.4 independent": "V3 independent",
            "V2.4 decisions": "V3 research decisions",
            "V2.4 stage timing": "V3 stage timing",
            "V2.4": "V3",
            "V2.3": "the previous model",
            "V2.2": "the previous model",
            "Robust Independent EV": "stress-test EV",
            "Robust independent +EV": "research +EV",
            "robust independent +EV": "research +EV",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text

    # ------------------------------------------------------------------
    # Independent model page
    # ------------------------------------------------------------------

    def _build_independent_model_tab(self) -> None:
        intro = ttk.LabelFrame(self.independent_model_tab, text="Independent football probability", padding=12)
        intro.pack(fill="x", pady=(0, 8))
        ttk.Label(
            intro,
            text=(
                "These H/D/A probabilities are created before bookmaker prices are compared. The production baseline uses genuinely available football-model components only; a missing short/long variant is not copied from another model. "
                "The Dynamic challenger is shown beside the baseline for research, but it cannot change EV until it passes the walk-forward gate."
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
            ("High-confidence baseline", self.v24_high_conf_var),
            ("Saved V3 model rows", self.v24_saved_var),
        )):
            cards.columnconfigure(i, weight=1)
            box = ttk.LabelFrame(cards, text=title, padding=8)
            box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 5, 0))
            ttk.Label(box, textvariable=var, font=("Segoe UI", 13, "bold")).pack(anchor="w")

        self.v24_model_tree = self._make_tree(
            self.independent_model_tab,
            [
                ("league", "League", 135),
                ("match", "Match", 215),
                ("base", "Baseline H/D/A", 135),
                ("fair", "Fair odds H/D/A", 125),
                ("dc", "DC H/D/A", 125),
                ("elo", "Elo H/D/A", 125),
                ("short", "Short H/D/A", 125),
                ("long", "Long H/D/A", 125),
                ("dynamic", "Dynamic challenger", 135),
                ("gap", "Challenger gap", 92),
                ("models", "Models", 72),
                ("spread", "Baseline spread", 92),
                ("market", "Market reference", 135),
                ("confidence", "Confidence", 80),
            ],
            height=20,
        )

    def _fill_independent_model(self) -> None:
        if not hasattr(self, "v24_model_tree"):
            return
        for item in self.v24_model_tree.get_children():
            self.v24_model_tree.delete(item)
        rows_by_name = {row.match_name: row for row in self.rows}
        forecasts = sorted(
            self.v3_forecasts.values(),
            key=lambda f: (f.confidence == "HIGH", -f.model_spread_pp),
            reverse=True,
        )
        for f in forecasts:
            row = rows_by_name.get(f.match_name)
            mh = getattr(row, "market_reference_home", None) if row else None
            md = getattr(row, "market_reference_draw", None) if row else None
            ma = getattr(row, "market_reference_away", None) if row else None
            self.v24_model_tree.insert("", "end", values=(
                f.league_name,
                f.match_name,
                self._triplet_pct(f.home_probability, f.draw_probability, f.away_probability),
                self._triplet_odds(f.fair_home_odds, f.fair_draw_odds, f.fair_away_odds),
                self._triplet_pct(f.dc_home, f.dc_draw, f.dc_away),
                self._triplet_pct(f.elo_home, f.elo_draw, f.elo_away),
                self._triplet_pct(f.short_home, f.short_draw, f.short_away),
                self._triplet_pct(f.long_home, f.long_draw, f.long_away),
                self._triplet_pct(f.challenger_home, f.challenger_draw, f.challenger_away),
                f"{f.challenger_gap_pp:.1f} pp" if f.challenger_gap_pp is not None else "—",
                len(f.components),
                f"{f.model_spread_pp:.1f} pp",
                self._triplet_pct(mh, md, ma),
                f.confidence.title(),
            ))
        try:
            models, _, _, _ = v3_counts()
            self.v24_saved_var.set(str(models))
        except Exception:
            self.v24_saved_var.set("—")
        self.v24_supported_var.set(str(len(self.v3_model_result.supported_leagues) if self.v3_model_result else 0))
        self.v24_priced_var.set(str(len(self.v3_forecasts)))
        self.v24_high_conf_var.set(str(sum(1 for f in self.v3_forecasts.values() if f.confidence == "HIGH")))
        self.v24_model_status_var.set(
            f"{len(self.v3_forecasts)} fixture(s) have bookmaker-independent baseline probabilities. Dynamic challenger predictions are research-only."
        )

    # ------------------------------------------------------------------
    # Research laboratory
    # ------------------------------------------------------------------

    def _build_v3_lab(self) -> None:
        intro = ttk.LabelFrame(self.v3_lab_tab, text="Scientific status", padding=12)
        intro.pack(fill="x", pady=(0, 8))
        ttk.Label(
            intro,
            text=(
                "V3 separates three questions: (1) can football data predict outcomes? (2) does it add information beyond a sharp market? (3) can that information be executed at a real price? "
                "A high displayed EV is not treated as evidence by itself."
            ),
            wraplength=1320,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(intro, textvariable=self.v3_lab_status_var, style="Muted.TLabel").pack(anchor="w", pady=(5, 0))

        cards = ttk.Frame(self.v3_lab_tab)
        cards.pack(fill="x", pady=(0, 8))
        specs = (
            ("Validation grade", self.v3_validation_grade_var),
            ("Quotes stored", self.v3_quote_count_var),
            ("Ambiguous matches rejected", self.v3_ambiguity_var),
            ("Challenger fixtures", self.v3_challenger_var),
            ("Walk-forward runs", self.v3_walk_count_var),
        )
        for i, (title, var) in enumerate(specs):
            cards.columnconfigure(i, weight=1)
            box = ttk.LabelFrame(cards, text=title, padding=8)
            box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 5, 0))
            ttk.Label(box, textvariable=var, font=("Segoe UI", 12, "bold")).pack(anchor="w")

        controls = ttk.Frame(self.v3_lab_tab)
        controls.pack(fill="x", pady=(0, 8))
        self.v3_walk_button = ttk.Button(controls, text="Run walk-forward validation", command=self._run_walkforward_validation)
        self.v3_walk_button.pack(side="left")
        ttk.Label(
            controls,
            text="Uses only historical results earlier than each prediction date. This can validate forecast improvement, not betting edge by itself.",
            style="Muted.TLabel",
        ).pack(side="left", padx=10)
        ttk.Label(controls, textvariable=self.v3_walk_status_var).pack(side="right")

        split = ttk.Panedwindow(self.v3_lab_tab, orient="vertical")
        split.pack(fill="both", expand=True)
        top = ttk.Frame(split)
        bottom = ttk.Frame(split)
        split.add(top, weight=3)
        split.add(bottom, weight=2)

        self.v3_challenger_tree = self._tree_in(
            top,
            [
                ("league", "League", 135),
                ("match", "Match", 220),
                ("base", "Baseline H/D/A", 135),
                ("challenger", "Dynamic H/D/A", 135),
                ("gap", "Max gap", 75),
                ("eta", "η", 55),
                ("shrink", "Annual shrink", 90),
                ("val", "Tuning log loss", 95),
                ("market", "Market reference", 135),
                ("note", "Role", 260),
            ],
            height=12,
        )

        self.v3_walk_text = tk.Text(bottom, wrap="word", height=11, font=("Segoe UI", 10))
        self.v3_walk_text.pack(fill="both", expand=True, pady=(8, 0))
        self.v3_walk_text.insert(
            "1.0",
            "No walk-forward result yet. Run the validation after fetching current fixtures. The test will use the cached historical leagues represented in the current scan."
        )
        self.v3_walk_text.configure(state="disabled")

    def _fill_v3_lab(self) -> None:
        if not hasattr(self, "v3_challenger_tree"):
            return
        for item in self.v3_challenger_tree.get_children():
            self.v3_challenger_tree.delete(item)
        rows_by_name = {row.match_name: row for row in self.rows}
        for f in sorted(self.v3_forecasts.values(), key=lambda x: abs(x.challenger_gap_pp or 0.0), reverse=True):
            row = rows_by_name.get(f.match_name)
            market = self._triplet_pct(
                getattr(row, "market_reference_home", None) if row else None,
                getattr(row, "market_reference_draw", None) if row else None,
                getattr(row, "market_reference_away", None) if row else None,
            )
            self.v3_challenger_tree.insert("", "end", values=(
                f.league_name,
                f.match_name,
                self._triplet_pct(f.home_probability, f.draw_probability, f.away_probability),
                self._triplet_pct(f.challenger_home, f.challenger_draw, f.challenger_away),
                f"{f.challenger_gap_pp:.1f} pp" if f.challenger_gap_pp is not None else "—",
                f"{f.challenger_eta:.3f}" if f.challenger_eta is not None else "—",
                f"{f.challenger_annual_shrink:.2f}" if f.challenger_annual_shrink is not None else "—",
                f"{f.challenger_validation_logloss:.4f}" if f.challenger_validation_logloss is not None else "—",
                market,
                "Research challenger — cannot alter production probability",
            ))

        grade = current_validation_grade()
        self.v3_validation_grade_var.set(grade.replace("_", " ").title())
        result: Optional[V3PriceShopResult] = self.price_shop_result if isinstance(self.price_shop_result, V3PriceShopResult) else None
        self.v3_ambiguity_var.set(str(result.ambiguous_rejections if result else 0))
        self.v3_challenger_var.set(str(sum(1 for f in self.v3_forecasts.values() if f.challenger_home is not None)))
        try:
            _, quotes, _, runs = v3_counts()
            self.v3_quote_count_var.set(str(quotes))
            self.v3_walk_count_var.set(str(runs))
        except Exception:
            self.v3_quote_count_var.set("—")
            self.v3_walk_count_var.set("—")

        summary = latest_walkforward_summary()
        if summary and self._last_v3_walk_result is None:
            self.v3_walk_status_var.set(
                f"Latest: {summary.get('predictions', 0)} predictions · {str(summary.get('forecast_grade', '')).replace('_', ' ').title()}"
            )

    def _set_walk_text(self, text: str) -> None:
        self.v3_walk_text.configure(state="normal")
        self.v3_walk_text.delete("1.0", "end")
        self.v3_walk_text.insert("1.0", text)
        self.v3_walk_text.configure(state="disabled")

    def _run_walkforward_validation(self) -> None:
        if self._v3_walk_running:
            return
        if not self.rows:
            self.v3_walk_status_var.set("Fetch current fixtures first so V3 knows which league histories to test.")
            return
        self._v3_walk_running = True
        self.v3_walk_button.configure(state="disabled")
        self.v3_walk_status_var.set("Loading cached league histories…")
        self._set_walk_text("Walk-forward validation is running. Same-day results are withheld until every match on that date has been predicted.")

        def worker() -> None:
            try:
                def load_progress(_pct: int, stage: str, detail: str) -> None:
                    self.after(0, lambda s=stage, d=detail: self.v3_walk_status_var.set(f"{s}: {d}"))

                histories, initial = v24_history.load_histories_for_rows(self.rows, progress=load_progress)
                if not histories:
                    raise RuntimeError("No supported historical leagues were available for walk-forward testing.")

                def wf_progress(pct: int, stage: str, detail: str) -> None:
                    self.after(0, lambda p=pct, s=stage, d=detail: self.v3_walk_status_var.set(f"{s} {p}% · {d}"))

                result = run_walk_forward(
                    histories,
                    league_keys=sorted(histories),
                    max_predictions_per_league=700,
                    bootstrap_samples=500,
                    progress=wf_progress,
                )
                run_id = save_walkforward_result(result)
                self.after(0, lambda: self._apply_walkforward_result(result, run_id))
            except Exception as exc:
                LOGGER.exception("V3 walk-forward validation failed")
                self.after(0, lambda e=exc: self._walkforward_failed(e))

        threading.Thread(target=worker, name="v3-walk-forward", daemon=True).start()

    def _apply_walkforward_result(self, result: WalkForwardResult, run_id: int) -> None:
        self._v3_walk_running = False
        self.v3_walk_button.configure(state="normal")
        self._last_v3_walk_result = result
        self.v3_walk_status_var.set(
            f"Run #{run_id}: {len(result.records)} predictions · {result.forecast_grade.replace('_', ' ').title()}"
        )
        text = (
            "V3 WALK-FORWARD RESULT\n\n"
            f"Predictions: {len(result.records)} across {len(result.leagues)} league(s) and {result.periods} league-month clusters.\n\n"
            "BASELINE (current independent production model)\n"
            f"Log loss: {result.baseline.log_loss:.5f}\n"
            f"Brier: {result.baseline.brier:.5f}\n"
            f"RPS: {result.baseline.rps:.5f}\n"
            f"Calibration ECE: {result.baseline.ece:.5f}\n\n"
            "DYNAMIC-STATE CHALLENGER\n"
            f"Log loss: {result.challenger.log_loss:.5f}\n"
            f"Brier: {result.challenger.brier:.5f}\n"
            f"RPS: {result.challenger.rps:.5f}\n"
            f"Calibration ECE: {result.challenger.ece:.5f}\n\n"
            f"Challenger minus baseline log loss: {result.delta_log_loss:+.6f} (negative is better)\n"
            f"Cluster-bootstrap 95% CI: [{result.delta_log_loss_ci_low:+.6f}, {result.delta_log_loss_ci_high:+.6f}]\n"
            f"Bootstrap probability challenger improves log loss: {result.challenger_better_fraction * 100:.1f}%\n\n"
            f"FORECAST GRADE: {result.forecast_grade.replace('_', ' ')}\n\n"
            "This grade concerns forecasting only. V3 does not convert it into a validated betting edge. A later point-in-time market/CLV gate is still required before a +EV signal may be labelled validated."
        )
        self._set_walk_text(text)
        self._fill_v3_lab()
        self._fill_dashboard()

    def _walkforward_failed(self, exc: Exception) -> None:
        self._v3_walk_running = False
        self.v3_walk_button.configure(state="normal")
        self.v3_walk_status_var.set(f"Validation failed: {exc}")
        self._set_walk_text(f"Walk-forward validation failed.\n\n{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------
    # V3 live pipeline
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
            self._progress_callback(64, "Market identity", "Building Sportsbet fixture universe and attaching exchange references where identities match")
            rows = combine_sportsbet_catalogue(sb_bundle.matches, pm_bundle.matches, min_ev)
            timings["Fixture matching"] = time.perf_counter() - t

            t = time.perf_counter()
            rows = enrich_market_context_fast(
                rows,
                sb_bundle.raw_events,
                pin_bundle.raw_events,
                progress=self._progress_callback,
                start_pct=65,
                end_pct=70,
            )
            timings["Market diagnostics"] = time.perf_counter() - t

            # Market-derived calculations are retained strictly as diagnostics.
            t = time.perf_counter()
            rows, acceleration = enrich_edge_model_parallel(
                rows,
                min_ev_pct=min_ev,
                progress=self._progress_callback,
                start_pct=70,
                end_pct=73,
            )
            self._edge_acceleration = acceleration
            timings["Market reference calculation"] = time.perf_counter() - t

            t = time.perf_counter()
            self._progress_callback(74, "V3 independent football model", "Building bookmaker-independent baseline and research-only dynamic challenger")
            self.v3_model_result = build_and_apply_v3_model(rows, min_ev_pct=min_ev, progress=self._progress_callback)
            self.v3_forecasts = dict(self.v3_model_result.forecasts)
            # Compatibility for inherited model counters/widgets.
            self.v24_model_result = self.v3_model_result  # type: ignore[assignment]
            self.v24_forecasts = self.v3_forecasts  # type: ignore[assignment]
            timings["Independent football model"] = time.perf_counter() - t

            info_notes.append(
                f"V3 independent baseline: {len(self.v3_model_result.supported_leagues)} historical league source(s); "
                f"{len(self.v3_forecasts)}/{len(rows)} fixture(s) independently priced; "
                f"{self.v3_model_result.challenger_leagues} league(s) also have dynamic challenger forecasts."
            )
            if self.v3_model_result.unavailable_leagues:
                info_notes.append(
                    f"Independent history unavailable/unmatched for {len(self.v3_model_result.unavailable_leagues)} Sportsbet league label(s); those fixtures receive no headline EV."
                )

            t = time.perf_counter()
            self.price_shop_result = None
            if price_shop_enabled and self.v3_forecasts:
                self._progress_callback(86, "Complete quote matrix", "Scanning every independently priced fixture before ranking")
                try:
                    self.price_shop_result = fetch_best_prices_v3(api_key, rows, progress=self._progress_callback)
                    self._last_api_request_count += self.price_shop_result.request_count
                    info_notes.extend(self.price_shop_result.notes)
                except Exception as exc:
                    warnings.append(f"Complete quote matrix: {exc}")
                    LOGGER.exception("V3 full price-shopping pass failed")
            else:
                self._progress_callback(94, "Complete quote matrix", "Additional bookmaker comparison is disabled or no independent fixtures are available")
            timings["Complete quote matrix"] = time.perf_counter() - t

            validation_grade = current_validation_grade()
            decisions = build_v3_decisions(rows, min_ev_pct=min_ev, validation_grade=validation_grade)
            research_edges = [d for d in decisions if d.status == "RESEARCH +EV — UNVALIDATED"]
            validated_edges = [d for d in decisions if d.status == "VALIDATED +EV"]
            info_notes.append(
                f"V3 decisions: {len(decisions)} priced outcome(s); {len(research_edges)} threshold-clearing research signal(s); "
                f"{len(validated_edges)} edge-validated signal(s). Current validation grade: {validation_grade}."
            )

            self._progress_callback(95, "Immutable research snapshot", "Saving probabilities, every observed quote and the exact decision-time state")
            t = time.perf_counter()
            saved = context_saved = edge_saved = v3_models = v3_quotes = v3_decisions_saved = 0
            if save_snapshots and rows:
                saved = base_main.save_snapshot(rows)
                context_saved = save_market_context(rows)
                edge_saved = save_edge_snapshot(rows)
                v3_models, v3_quotes, v3_decisions_saved = save_v3_live_snapshot(rows, decisions)
            timings["Persistence"] = time.perf_counter() - t
            timings["Total market pipeline"] = time.perf_counter() - started
            self._stage_timings = timings
            timing_text = " · ".join(f"{name} {seconds:.1f}s" for name, seconds in timings.items())
            info_notes.append(
                f"V3 stage timing: {timing_text}. Stored {v3_models} model row(s), {v3_quotes} quote row(s) and {v3_decisions_saved} decision row(s)."
            )

            self._progress_callback(98, "Preparing dashboard", "Showing the highest independent EV while keeping unvalidated research signals clearly labelled")
            self.after(0, lambda: self._apply_v3_result(rows, warnings, info_notes, saved, context_saved, edge_saved))
        except Exception as exc:
            LOGGER.exception("V3 fetch failed")
            self.after(0, lambda: self._fatal_fetch_error(exc))

    def _apply_v3_result(self, rows, warnings, info_notes, saved, context_saved, edge_saved) -> None:
        # Reuse the mature V2 navigation/data rendering. Dynamic dispatch calls
        # our Dashboard implementation, so the V3 evidence labels win.
        super()._apply_v21_result(rows, warnings, info_notes, saved, context_saved, edge_saved)
        self._fill_independent_model()
        self._fill_v3_lab()
        self._normalise_visible_copy()

    # ------------------------------------------------------------------
    # Decision-first Dashboard
    # ------------------------------------------------------------------

    def _current_v3_decisions(self) -> list[V3Decision]:
        threshold = base_main.safe_float(self.min_ev_var.get(), self.settings.min_ev_pct)
        return build_v3_decisions(self.rows, min_ev_pct=threshold, validation_grade=current_validation_grade())

    def _fill_dashboard(self) -> None:
        super()._fill_dashboard()
        if not hasattr(self, "smart_top_tree"):
            return
        threshold = base_main.safe_float(self.min_ev_var.get(), self.settings.min_ev_pct)
        grade = current_validation_grade()
        decisions = build_v3_decisions(self.rows, min_ev_pct=threshold, validation_grade=grade)
        best = best_available_v3(self.rows, min_ev_pct=threshold, validation_grade=grade)
        self._v3_dashboard_best = best
        self._v24_dashboard_best = None
        self._v21_dashboard_best = None
        self._dashboard_best = None

        for item in self.smart_top_tree.get_children():
            self.smart_top_tree.delete(item)
        for idx, d in enumerate(sorted(decisions, key=lambda x: x.model_ev_pct, reverse=True)[:8]):
            reason = d.reason if len(d.reason) <= 135 else d.reason[:132] + "…"
            self.smart_top_tree.insert("", "end", iid=f"smart-{idx}", values=(
                d.league,
                d.match_name,
                d.selection,
                f"${d.quote_odds:.2f} {d.quote_source}",
                f"{d.model_ev_pct:+.1f}%",
                f"{d.confidence.title()} · {d.validation_grade.replace('_', ' ').title()}",
                reason,
            ))

        if best is None:
            self.best_pick_kicker.configure(text="V3 INDEPENDENT MODEL")
            self.best_pick_title_var.set("No independent probability available")
            self.best_pick_match_var.set("No current fixture could be matched to a supported football-history model.")
            self.best_pick_price_var.set("—")
            self.best_pick_ev_var.set("—")
            self.best_pick_confidence_var.set("—")
            self.best_pick_reason_var.set("V3 will not substitute bookmaker consensus for an independent football probability.")
            self.dashboard_status_var.set("No independent comparison available")
            self._set_hero_colour(False)
            return

        self.best_pick_title_var.set(best.selection)
        self.best_pick_match_var.set(f"{best.league} · {best.match_name}")
        self.best_pick_price_var.set(f"${best.quote_odds:.2f} · {best.quote_source}")
        self.best_pick_ev_var.set(f"{best.model_ev_pct:+.1f}%")
        self.best_pick_confidence_var.set(best.confidence.title())

        market_text = ""
        if best.market_probability is not None and best.market_gap_pp is not None:
            market_text = f" The current market reference is {best.market_probability * 100:.1f}%, a {best.market_gap_pp:+.1f} percentage-point difference from our football baseline."

        if best.status == "VALIDATED +EV":
            self.best_pick_kicker.configure(text="BEST VALIDATED +EV")
            self.best_pick_reason_var.set(best.reason + market_text)
            self.dashboard_status_var.set("Edge validation gate passed")
            self._set_hero_colour(True)
        elif best.model_ev_pct >= threshold:
            self.best_pick_kicker.configure(text="HIGHEST RESEARCH EV — NOT YET A PROVEN EDGE")
            self.best_pick_reason_var.set(
                f"Our football-only baseline gives {best.selection} about {best.model_probability * 100:.1f}% chance, equal to fair odds near ${best.fair_odds:.2f}. "
                f"The best observed price is ${best.quote_odds:.2f} at {best.quote_source}, which gives {best.model_ev_pct:+.1f}% theoretical EV. "
                f"This is still a research signal because the betting-edge validation gate has not passed.{market_text}"
            )
            self.dashboard_status_var.set(f"Research signal · validation grade {grade.replace('_', ' ')}")
            self._set_hero_colour(False)
        else:
            self.best_pick_kicker.configure(text="HIGHEST INDEPENDENT EV AVAILABLE — NOT A +EV SIGNAL")
            self.best_pick_reason_var.set(
                f"The football-only baseline gives {best.selection} about {best.model_probability * 100:.1f}% chance (fair odds ${best.fair_odds:.2f}). "
                f"The best observed price is ${best.quote_odds:.2f} at {best.quote_source}, producing {best.model_ev_pct:+.1f}% theoretical EV. "
                f"It is shown because it is the highest available comparison, not because V3 recommends it.{market_text}"
            )
            self.dashboard_status_var.set(best.status)
            self._set_hero_colour(False)

    def _open_best_context(self) -> None:
        if hasattr(self, "independent_model_tab"):
            self.notebook.select(self.analysis_container)
            self.analysis_book.select(self.independent_model_tab)
        else:
            super()._open_best_context()

    def _diagnostic_text(self) -> str:
        text = super()._diagnostic_text()
        result = self.price_shop_result if isinstance(self.price_shop_result, V3PriceShopResult) else None
        extra = (
            f"V3 validation grade: {current_validation_grade()}\n"
            f"V3 independently priced fixtures: {len(self.v3_forecasts)}\n"
            f"V3 full quote-matrix targets: {result.target_matches if result else 0}\n"
            f"V3 ambiguous event matches rejected: {result.ambiguous_rejections if result else 0}\n"
        )
        return extra + text


def main() -> None:
    try:
        V3App().mainloop()
    except Exception:
        LOGGER.exception("Fatal V3 application error")
        raise


if __name__ == "__main__":
    main()
