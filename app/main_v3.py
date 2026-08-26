from __future__ import annotations

import re
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from tkinter import messagebox, ttk
from typing import Optional

import main as base_main
from edge_parallel_v22 import enrich_edge_model_parallel
from edge_storage import save_edge_snapshot
from execution_v3 import fetch_all_best_prices
from independent_model_v24 import LEAGUE_SOURCES
from main_v24 import V24App, LOGGER
from market_context_v21 import enrich_market_context_fast
from market_storage import save_market_context
from model_v3 import V3Forecast, V3ModelResult, build_and_apply_v3_model
from multileague_data import combine_sportsbet_catalogue
from progressive_data import fetch_multileague_sources_progressive
from strategy_v3 import V3Decision, best_available_v3, build_v3_decisions
from v3_storage import save_v3_snapshot, save_validation_report, v3_counts
from validation_v3 import WalkForwardReport, pooled_report, walk_forward_validate


class V3App(V24App):
    """V3: dynamic independent football model + point-in-time research system."""

    def _create_vars(self) -> None:
        super()._create_vars()
        self.v3_model_result: Optional[V3ModelResult] = None
        self.v3_forecasts: dict[str, V3Forecast] = {}
        self.v3_histories = {}
        self.v3_validation_reports: dict[str, WalkForwardReport] = {}
        self.v3_pooled_report: Optional[WalkForwardReport] = None
        self._v3_dashboard_best: Optional[V3Decision] = None
        self.v3_model_status_var = tk.StringVar(value="V3 model data populate after the next analysis.")
        self.v3_supported_var = tk.StringVar(value="0")
        self.v3_priced_var = tk.StringVar(value="0")
        self.v3_uncertainty_var = tk.StringVar(value="—")
        self.v3_saved_var = tk.StringVar(value="0")
        self.v3_validation_status_var = tk.StringVar(value="Walk-forward validation has not run yet.")
        self.v3_validation_n_var = tk.StringVar(value="0")
        self.v3_logloss_var = tk.StringVar(value="—")
        self.v3_brier_var = tk.StringVar(value="—")
        self.v3_calibration_var = tk.StringVar(value="—")

    def _build_tabs(self) -> None:
        super()._build_tabs()
        self._hide_notebook_tabs(self.analysis_book, {"Independent model"})
        self._hide_notebook_tabs(self.research_book, {"Research models", "Validation"})
        self.v3_model_tab = ttk.Frame(self.analysis_book, padding=12)
        self.analysis_book.add(self.v3_model_tab, text="Independent model")
        self._build_v3_model_tab()
        self.v3_validation_tab = ttk.Frame(self.research_book, padding=12)
        self.research_book.insert(0, self.v3_validation_tab, text="Walk-forward")
        self._build_v3_validation_tab()

    def _show_page(self, group: str, page: str) -> None:
        if group == "research" and page == "validation" and hasattr(self, "v3_validation_tab"):
            self.notebook.select(self.research_container)
            self.research_book.select(self.v3_validation_tab)
            return
        super()._show_page(group, page)

    def _open_best_context(self) -> None:
        if self._v3_dashboard_best is not None:
            messagebox.showinfo("V3 candidate details", self._v3_dashboard_best.reason)
            return
        super()._open_best_context()

    def _open_tree_pick(self, event=None) -> None:
        if hasattr(self, "smart_top_tree"):
            selected = self.smart_top_tree.selection()
            if selected and str(selected[0]).startswith("v3-"):
                self.notebook.select(self.analysis_container)
                self.analysis_book.select(self.v3_model_tab)
                return
        super()._open_tree_pick(event)

    @staticmethod
    def _hide_notebook_tabs(book: ttk.Notebook, labels: set[str]) -> None:
        for tab_id in list(book.tabs()):
            try:
                if str(book.tab(tab_id, "text")) in labels:
                    book.hide(tab_id)
            except Exception:
                pass

    def _build_settings(self) -> None:
        super()._build_settings()
        for child in list(self.settings_tab.winfo_children()):
            try:
                text = str(child.cget("text"))
            except Exception:
                continue
            if re.search(r"\bV(?:1|2)(?:\.|\b)", text, flags=re.IGNORECASE):
                try:
                    child.pack_forget()
                except Exception:
                    pass
        frame = ttk.LabelFrame(self.settings_tab, text="V3 modelling and decision rules", padding=12)
        frame.pack(fill="x", pady=(10, 0))
        ttk.Label(
            frame,
            text=(
                "V3 first estimates football probabilities from historical results only. Team attack, defence, league scoring and home advantage evolve chronologically; "
                "a separate Elo family provides a genuinely different model view. Their stack weight and probability temperature are selected using only past chronological validation data. "
                "Current bookmaker/exchange prices are frozen out of the independent model and enter only after the probability is complete."
            ),
            wraplength=1260,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text=(
                "Uncertainty is estimated with a moving-block bootstrap rather than the old minimum-component rule. Every independently modelled fixture is then scanned across all available execution books before ranking. "
                "Signals remain labelled research candidates until accumulated out-of-sample forecasting and closing-line evidence clears a separate validation gate."
            ),
            wraplength=1260,
            justify="left",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 0))

    def _build_v3_model_tab(self) -> None:
        intro = ttk.LabelFrame(self.v3_model_tab, text="Dynamic football state → uncertainty → price comparison", padding=12)
        intro.pack(fill="x", pady=(0, 8))
        ttk.Label(
            intro,
            text=(
                "The independent probability shown here is generated before execution prices are compared. The primary score model updates latent attack and defence from chronological results; "
                "Elo is kept as a separate family. The 5th–95th percentile range comes from moving-block bootstrap refits. 'Goal intensity' is the model's expected scoring rate, not shot-derived xG."
            ),
            wraplength=1320,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(intro, textvariable=self.v3_model_status_var, style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        cards = ttk.Frame(self.v3_model_tab)
        cards.pack(fill="x", pady=(0, 8))
        for i, (title, var) in enumerate((
            ("Supported leagues", self.v3_supported_var),
            ("Fixtures modelled", self.v3_priced_var),
            ("Median uncertainty", self.v3_uncertainty_var),
            ("Stored V3 forecasts", self.v3_saved_var),
        )):
            cards.columnconfigure(i, weight=1)
            box = ttk.LabelFrame(cards, text=title, padding=8)
            box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 5, 0))
            ttk.Label(box, textvariable=var, font=("Segoe UI", 13, "bold")).pack(anchor="w")
        self.v3_model_tree = self._make_tree(self.v3_model_tab, [
            ("league", "League", 130), ("match", "Match", 205), ("p", "V3 H/D/A", 132),
            ("range", "5th pct H/D/A", 135), ("fair", "Fair odds H/D/A", 130),
            ("dynamic", "Dynamic H/D/A", 135), ("elo", "Elo H/D/A", 125),
            ("goals", "Goal intensity H/A", 105), ("stack", "Dynamic weight", 92),
            ("cal", "Calibration T", 82), ("market", "Market ref H/D/A", 140),
            ("gap", "Largest gap", 92), ("data", "Evidence", 105),
        ], height=20)

    def _build_v3_validation_tab(self) -> None:
        intro = ttk.LabelFrame(self.v3_validation_tab, text="Chronological out-of-sample validation", padding=12)
        intro.pack(fill="x", pady=(0, 8))
        ttk.Label(
            intro,
            text=(
                "V3 never randomly shuffles matches. Each fold tunes on earlier data, forecasts the next period, then expands the training window. Same-day fixtures are predicted as a batch before any same-day result is admitted. "
                "Log loss is the primary forecasting score; Brier and calibration error are reported separately. These metrics do not by themselves prove a betting edge — CLV/outcome evidence remains a second gate."
            ),
            wraplength=1320,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(intro, textvariable=self.v3_validation_status_var, style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        cards = ttk.Frame(self.v3_validation_tab)
        cards.pack(fill="x", pady=(0, 8))
        for i, (title, var) in enumerate((
            ("OOS predictions", self.v3_validation_n_var),
            ("Multiclass log loss", self.v3_logloss_var),
            ("Brier score", self.v3_brier_var),
            ("Calibration error", self.v3_calibration_var),
        )):
            cards.columnconfigure(i, weight=1)
            box = ttk.LabelFrame(cards, text=title, padding=8)
            box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 5, 0))
            ttk.Label(box, textvariable=var, font=("Segoe UI", 13, "bold")).pack(anchor="w")
        self.v3_validation_tree = self._make_tree(self.v3_validation_tab, [
            ("league", "League", 145), ("n", "OOS matches", 85), ("folds", "Folds", 55),
            ("ll", "Log loss", 78), ("brier", "Brier", 78), ("cal", "Calibration", 85),
            ("home", "Home binary LL", 95), ("draw", "Draw binary LL", 95),
            ("away", "Away binary LL", 95), ("note", "Method", 390),
        ], height=18)

    @staticmethod
    def _triplet_pct(a, b, c) -> str:
        return "—" if any(v is None for v in (a, b, c)) else f"{a*100:.1f}/{b*100:.1f}/{c*100:.1f}"

    @staticmethod
    def _triplet_odds(a, b, c) -> str:
        return "—" if any(v is None or v <= 0 for v in (a, b, c)) else f"{a:.2f}/{b:.2f}/{c:.2f}"

    def _fill_v3_model(self) -> None:
        if not hasattr(self, "v3_model_tree"):
            return
        for item in self.v3_model_tree.get_children():
            self.v3_model_tree.delete(item)
        rows = {r.match_name: r for r in self.rows}
        uncertainty = []
        forecasts = sorted(
            self.v3_forecasts.values(),
            key=lambda f: (f.confidence == "HIGH", -max(f.sd_home, f.sd_draw, f.sd_away)),
            reverse=True,
        )
        for f in forecasts:
            row = rows.get(f.match_name)
            market = (
                getattr(row, "market_reference_home", None) if row else None,
                getattr(row, "market_reference_draw", None) if row else None,
                getattr(row, "market_reference_away", None) if row else None,
            )
            gaps = [
                (abs(p - m), side, (p - m) * 100)
                for side, p, m in zip(("H", "D", "A"), (f.home_probability, f.draw_probability, f.away_probability), market)
                if m is not None
            ]
            gap = "—" if not gaps else f"{max(gaps)[1]} {max(gaps)[2]:+.1f} pp"
            prior = []
            if f.promotion_prior_home:
                prior.append("H transfer")
            if f.promotion_prior_away:
                prior.append("A transfer")
            evidence = f"{f.home_history_matches}/{f.away_history_matches} · {f.confidence.title()}" + (" · " + ", ".join(prior) if prior else "")
            uncertainty.append(max(f.sd_home, f.sd_draw, f.sd_away) * 100)
            self.v3_model_tree.insert("", "end", values=(
                f.league_name, f.match_name,
                self._triplet_pct(f.home_probability, f.draw_probability, f.away_probability),
                self._triplet_pct(f.lower_home, f.lower_draw, f.lower_away),
                self._triplet_odds(f.fair_home_odds, f.fair_draw_odds, f.fair_away_odds),
                self._triplet_pct(f.dynamic_home, f.dynamic_draw, f.dynamic_away),
                self._triplet_pct(f.elo_home, f.elo_draw, f.elo_away),
                f"{f.lambda_home:.2f}/{f.lambda_away:.2f}", f"{f.stack_weight_dynamic*100:.0f}%",
                f"{f.calibration_temperature:.2f}", self._triplet_pct(*market), gap, evidence,
            ))
        self.v3_supported_var.set(str(len(self.v3_model_result.supported_leagues) if self.v3_model_result else 0))
        self.v3_priced_var.set(str(len(self.v3_forecasts)))
        if uncertainty:
            ordered = sorted(uncertainty)
            self.v3_uncertainty_var.set(f"{ordered[len(ordered)//2]:.1f} pp")
        else:
            self.v3_uncertainty_var.set("—")
        try:
            self.v3_saved_var.set(str(v3_counts()[0]))
        except Exception:
            self.v3_saved_var.set("—")

    def _fill_v3_validation(self) -> None:
        if not hasattr(self, "v3_validation_tree"):
            return
        for item in self.v3_validation_tree.get_children():
            self.v3_validation_tree.delete(item)
        names = {s.key: s.name for s in LEAGUE_SOURCES}
        for key, r in sorted(self.v3_validation_reports.items()):
            self.v3_validation_tree.insert("", "end", values=(
                names.get(key, key), r.predictions, r.folds,
                "—" if r.log_loss is None else f"{r.log_loss:.4f}",
                "—" if r.brier_score is None else f"{r.brier_score:.4f}",
                "—" if r.calibration_error is None else f"{r.calibration_error:.3f}",
                "—" if r.home_log_loss is None else f"{r.home_log_loss:.4f}",
                "—" if r.draw_log_loss is None else f"{r.draw_log_loss:.4f}",
                "—" if r.away_log_loss is None else f"{r.away_log_loss:.4f}",
                r.notes[0] if r.notes else "Chronological expanding-window",
            ))
        p = self.v3_pooled_report
        if p is not None:
            self.v3_validation_n_var.set(str(p.predictions))
            self.v3_logloss_var.set("—" if p.log_loss is None else f"{p.log_loss:.4f}")
            self.v3_brier_var.set("—" if p.brier_score is None else f"{p.brier_score:.4f}")
            self.v3_calibration_var.set("—" if p.calibration_error is None else f"{p.calibration_error:.3f}")
            self.v3_validation_status_var.set(
                f"{p.predictions} recent chronological out-of-sample predictions across {len(self.v3_validation_reports)} league(s). Forecast metrics are evidence, not a profitability claim."
            )
        else:
            self.v3_validation_n_var.set("0")
            self.v3_logloss_var.set("—")
            self.v3_brier_var.set("—")
            self.v3_calibration_var.set("—")
            self.v3_validation_status_var.set("No league had enough history for the configured walk-forward diagnostic.")

    def _run_v3_validation(self, histories: dict, supported_keys: tuple[str, ...]) -> dict[str, WalkForwardReport]:
        sources = {s.key: s for s in LEAGUE_SOURCES}
        tasks = [(k, sources[k], histories[k]) for k in supported_keys if k in sources and len(histories.get(k, ())) >= 170]
        if not tasks:
            return {}
        reports = {}
        workers = max(1, min(4, len(tasks)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="v3-validation") as pool:
            futures = {pool.submit(walk_forward_validate, s, h, min_train_matches=140, fold_size=80, max_folds=3): k for k, s, h in tasks}
            completed = 0
            for future in as_completed(futures):
                key = futures[future]
                try:
                    reports[key] = future.result()
                except Exception as exc:
                    LOGGER.exception("V3 validation failed for %s", key)
                    reports[key] = WalkForwardReport(key, 0, 0, None, None, None, None, None, None, notes=(str(exc),))
                completed += 1
                self._progress_callback(
                    95 + int(3 * completed / max(1, len(tasks))),
                    "Walk-forward validation",
                    f"Validated {completed}/{len(tasks)} supported league(s) on untouched chronological folds",
                )
        return reports

    def _fetch_worker(self, api_key, start, end, min_ev, min_volume, price_shop_enabled, save_snapshots) -> None:
        warnings = []
        info_notes = []
        started = time.perf_counter()
        timings = {}
        try:
            t = time.perf_counter()
            source = fetch_multileague_sources_progressive(api_key, start, end, self._progress_callback)
            timings["API catalogue"] = time.perf_counter() - t
            self._sportsbet_leagues = list(source["leagues"])
            self._last_api_request_count = int(source.get("request_count", 0) or 0)
            sb_bundle, pm_bundle, pin_bundle = source["sportsbet"], source["polymarket"], source["pinnacle"]

            t = time.perf_counter()
            self._progress_callback(65, "Resolving current fixtures", "Building the current fixture catalogue before any football probability is calculated")
            rows = combine_sportsbet_catalogue(sb_bundle.matches, pm_bundle.matches, min_ev)
            timings["Fixture catalogue"] = time.perf_counter() - t

            t = time.perf_counter()
            rows = enrich_market_context_fast(rows, sb_bundle.raw_events, pin_bundle.raw_events, progress=self._progress_callback, start_pct=66, end_pct=71)
            rows, acceleration = enrich_edge_model_parallel(rows, min_ev_pct=min_ev, progress=self._progress_callback, start_pct=71, end_pct=74)
            self._edge_acceleration = acceleration
            timings["Decision-time market diagnostics"] = time.perf_counter() - t

            t = time.perf_counter()
            self._progress_callback(74, "V3 independent football model", "Fitting chronological team states; current bookmaker prices are excluded from this probability calculation")
            self.v3_model_result, self.v3_histories = build_and_apply_v3_model(rows, min_ev_pct=min_ev, progress=self._progress_callback, bootstrap_samples=28)
            self.v3_forecasts = dict(self.v3_model_result.forecasts)
            timings["Dynamic football model + uncertainty"] = time.perf_counter() - t
            info_notes.append(
                f"V3 independent model: {len(self.v3_model_result.supported_leagues)} supported league(s); {len(self.v3_forecasts)}/{len(rows)} fixture(s) priced from football data; "
                f"{sum(1 for f in self.v3_forecasts.values() if f.confidence == 'HIGH')} high-confidence probability estimates."
            )

            t = time.perf_counter()
            self.price_shop_result = None
            if price_shop_enabled and self.v3_forecasts:
                self._progress_callback(88, "V3 all-book quote matrix", "Independent probabilities are frozen. Scanning every modelled fixture before any EV ranking")

                def price_progress(pct, stage, detail):
                    self._progress_callback(88 + int(7 * max(0, min(100, pct)) / 100), stage, detail)

                try:
                    self.price_shop_result = fetch_all_best_prices(api_key, rows, progress=price_progress)
                    self._last_api_request_count += self.price_shop_result.request_count
                    warnings.extend(self.price_shop_result.notes)
                    info_notes.append(
                        f"V3 execution scan: all {self.price_shop_result.eligible_matches} modelled fixture(s) checked across {len(self.price_shop_result.providers_checked)} additional bookmaker feed(s); "
                        f"{self.price_shop_result.rejected_ambiguous_events} ambiguous event match(es) and {self.price_shop_result.rejected_non_executable_quotes} non-executable quote(s) rejected rather than ranked."
                    )
                except Exception as exc:
                    warnings.append(f"V3 all-book scan: {exc}")
                    LOGGER.exception("V3 all-book price scan failed")
            else:
                self._progress_callback(95, "V3 all-book quote matrix", "No independently modelled fixtures available, or execution scanning is disabled")
            timings["All-book quote matrix"] = time.perf_counter() - t

            decisions = build_v3_decisions(rows, min_ev_pct=min_ev)
            strong = [d for d in decisions if d.status == "V3 HIGH-CONFIDENCE CANDIDATE"]
            info_notes.append(
                f"V3 decision layer: {len(decisions)} execution-eligible priced outcomes; {len(strong)} high-confidence research candidate(s). Candidates are not labelled validated betting edges."
            )

            t = time.perf_counter()
            self._progress_callback(95, "Walk-forward validation", "Replaying recent untouched folds using only information available before each match")
            self.v3_validation_reports = self._run_v3_validation(self.v3_histories, self.v3_model_result.supported_leagues)
            valid = [r for r in self.v3_validation_reports.values() if r.predictions]
            self.v3_pooled_report = pooled_report(valid) if valid else None
            timings["Walk-forward validation"] = time.perf_counter() - t

            t = time.perf_counter()
            self._progress_callback(98, "Saving V3 research record", "Appending forecasts, quote microstructure, decisions and out-of-sample validation records")
            saved = context_saved = edge_saved = v3f = v3q = v3d = 0
            if save_snapshots and rows:
                saved = base_main.save_snapshot(rows)
                context_saved = save_market_context(rows)
                edge_saved = save_edge_snapshot(rows)
                v3f, v3q, v3d = save_v3_snapshot(rows, decisions)
                for report in valid:
                    save_validation_report(report)
            timings["Persistence"] = time.perf_counter() - t
            timings["Total"] = time.perf_counter() - started
            self._stage_timings = timings
            info_notes.append(
                f"Stored {v3f} V3 forecast(s), {v3q} quote observation(s) and {v3d} decision row(s). "
                + " · ".join(f"{name} {seconds:.1f}s" for name, seconds in timings.items())
            )
            self._progress_callback(99, "Preparing dashboard", "Ranking only after the full quote matrix and uncertainty calculations are complete")
            self.after(0, lambda: self._apply_v3_result(rows, warnings, info_notes, saved, context_saved, edge_saved))
        except Exception as exc:
            LOGGER.exception("V3 analysis failed")
            self.after(0, lambda: self._fatal_fetch_error(exc))

    def _apply_v3_result(self, rows, warnings, info_notes, saved, context_saved, edge_saved) -> None:
        super()._apply_v21_result(rows, warnings, info_notes, saved, context_saved, edge_saved)
        self._fill_v3_model()
        self._fill_v3_validation()
        self.v3_model_status_var.set(
            f"{len(self.v3_forecasts)} fixture(s) use V3 independent probabilities. Execution prices were compared only after those probabilities were frozen."
        )

    def _current_v3_decisions(self) -> list[V3Decision]:
        return build_v3_decisions(self.rows, min_ev_pct=base_main.safe_float(self.min_ev_var.get(), self.settings.min_ev_pct))

    def _fill_dashboard(self) -> None:
        super()._fill_dashboard()
        if not hasattr(self, "smart_top_tree"):
            return
        min_ev = base_main.safe_float(self.min_ev_var.get(), self.settings.min_ev_pct)
        decisions = self._current_v3_decisions()
        strong = [d for d in decisions if d.status == "V3 HIGH-CONFIDENCE CANDIDATE"]
        best = strong[0] if strong else best_available_v3(self.rows, min_ev_pct=min_ev)
        self._v3_dashboard_best = best
        self._v24_dashboard_best = None
        self._v21_dashboard_best = None
        self._dashboard_best = None
        for item in self.smart_top_tree.get_children():
            self.smart_top_tree.delete(item)
        visible = strong[:8] if strong else sorted(decisions, key=lambda d: d.model_ev_pct, reverse=True)[:8]
        for idx, d in enumerate(visible):
            reason = d.reason if len(d.reason) <= 135 else d.reason[:132] + "…"
            self.smart_top_tree.insert(
                "", "end", iid=f"v3-{idx}",
                values=(d.league, d.match_name, d.selection, f"${d.quote_odds:.2f} {d.quote_source}", f"{d.model_ev_pct:+.1f}%", f"{d.confidence.title()} · P+ {d.probability_ev_positive*100:.0f}%", reason),
            )
        if best is None:
            self.best_pick_kicker.configure(text="V3 INDEPENDENT MODEL")
            self.best_pick_title_var.set("No V3 probability available")
            self.best_pick_match_var.set("No current fixture could be resolved to a supported football history with an executable price.")
            self.best_pick_price_var.set("—")
            self.best_pick_ev_var.set("—")
            self.best_pick_confidence_var.set("—")
            self.best_pick_reason_var.set("V3 rejects unresolved fixtures and non-executable quotes rather than manufacturing a probability or event match.")
            self.dashboard_status_var.set("No V3 comparison available")
            self._set_hero_colour(False)
            return
        self.best_pick_title_var.set(best.selection)
        self.best_pick_match_var.set(f"{best.league} · {best.match_name}")
        self.best_pick_price_var.set(f"${best.quote_odds:.2f} · {best.quote_source}")
        self.best_pick_ev_var.set(f"{best.model_ev_pct:+.1f}%")
        self.best_pick_confidence_var.set(f"{best.confidence.title()} · P(EV>0) {best.probability_ev_positive*100:.0f}%")
        market_text = (
            f" Decision-time market reference {best.market_probability*100:.1f}% ({best.market_gap_pp:+.1f} pp model residual)."
            if best.market_probability is not None and best.market_gap_pp is not None else ""
        )
        if best.status == "V3 HIGH-CONFIDENCE CANDIDATE":
            self.best_pick_kicker.configure(text="STRONGEST V3 RESEARCH CANDIDATE — NOT YET A VALIDATED EDGE")
            self.best_pick_reason_var.set(
                f"V3 gives {best.selection} {best.model_probability*100:.1f}% (fair ${best.fair_odds:.2f}). Best execution-eligible observed price ${best.quote_odds:.2f} at {best.quote_source}; "
                f"central EV {best.model_ev_pct:+.1f}%, 5th-percentile EV {best.lower_ev_pct:+.1f}%, P(EV>0) about {best.probability_ev_positive*100:.0f}%.{market_text} "
                "The app will not call this a demonstrated betting edge until the separate chronological/CLV evidence gate is satisfied."
            )
            self.dashboard_status_var.set("Strong V3 research candidate found · full executable quote matrix scanned before ranking")
        else:
            self.best_pick_kicker.configure(text="HIGHEST V3 EV AVAILABLE — UNCERTAINTY / VALIDATION GATE NOT CLEARED")
            self.best_pick_reason_var.set(best.reason + market_text)
            self.dashboard_status_var.set(best.status)
        self._set_hero_colour(False)


def main() -> None:
    try:
        V3App().mainloop()
    except Exception:
        LOGGER.exception("Fatal V3 application error")
        raise


if __name__ == "__main__":
    main()
