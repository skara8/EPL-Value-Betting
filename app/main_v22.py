from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import messagebox, ttk
from typing import Optional

import main as base_main
from edge_parallel_v22 import EdgeAccelerationStats, enrich_edge_model_parallel, recommended_workers
from edge_storage import save_edge_snapshot
from football_intelligence import refresh_football_intelligence
from main_v21 import V21App, LOGGER
from market_context_v21 import enrich_market_context_fast
from market_storage import save_market_context
from multileague_data import combine_sportsbet_catalogue
from price_shop import fetch_best_prices
from progressive_data import fetch_multileague_sources_progressive
from research_models_v22 import ResearchMatchFeatures, build_research_features, load_epl_history
from strategy_v21 import build_v21_decisions
from v21_validation import save_v21_decisions
from v22_research_storage import research_summary, save_research_features


class V22App(V21App):
    """V2.2: multicore market fitting plus research-only football residual models."""

    def _create_vars(self) -> None:
        super()._create_vars()
        self.v22_research_features: dict[str, ResearchMatchFeatures] = {}
        self._v22_history_notes: tuple[str, ...] = ()
        self._edge_acceleration = EdgeAccelerationStats(workers=1, parallel=False, completed=0)
        self.v22_research_status_var = tk.StringVar(value="Research models load after the EPL football layer finishes.")
        self.v22_snapshot_count_var = tk.StringVar(value="0")
        self.v22_elo_count_var = tk.StringVar(value="0")
        self.v22_poisson_count_var = tk.StringVar(value="0")
        self.v22_lineup_count_var = tk.StringVar(value="0")
        self.v22_cpu_var = tk.StringVar(
            value=f"{recommended_workers()} workers from {os.cpu_count() or 1} logical CPU(s)"
        )

    def _build_tabs(self) -> None:
        super()._build_tabs()
        self.research_models_tab = ttk.Frame(self.research_book, padding=12)
        self.research_book.add(self.research_models_tab, text="Research models")
        self._build_research_models_tab()

    def _build_settings(self) -> None:
        super()._build_settings()
        cpu = ttk.LabelFrame(self.settings_tab, text="V2.2 multicore acceleration", padding=12)
        cpu.pack(fill="x", pady=(10, 0))
        ttk.Label(cpu, textvariable=self.v22_cpu_var, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(
            cpu,
            text=(
                "The expensive Asian-handicap/total Poisson calibration is independent for each fixture. V2.2 therefore distributes those calculations across about 75% of your logical CPUs (maximum 10 worker processes), while leaving capacity for Windows and the UI. "
                "Small scans stay single-process because process start-up would be slower. If Windows or security software blocks multiprocessing, the app automatically falls back to the safe serial model."
            ),
            wraplength=1260,
            justify="left",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(5, 0))

    def _build_research_models_tab(self) -> None:
        intro = ttk.LabelFrame(self.research_models_tab, text="Independent research features — not production weights", padding=12)
        intro.pack(fill="x", pady=(0, 9))
        ttk.Label(
            intro,
            text=(
                "This page implements the strongest reusable ideas from the public-model review without letting them manufacture a larger green EV. "
                "It compares the sharp-market anchor with a historical Elo model, a time-decayed goals-strength Poisson model, expected-XI continuity and recent xG context. "
                "The production ROBUST +EV rule is unchanged; these features are stored so we can later test whether their residuals improve closing-line value, Brier score or log loss out of sample."
            ),
            wraplength=1320,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(intro, textvariable=self.v22_research_status_var, style="Muted.TLabel").pack(anchor="w", pady=(7, 0))

        cards = ttk.Frame(self.research_models_tab)
        cards.pack(fill="x", pady=(0, 9))
        specs = (
            ("Saved feature rows", self.v22_snapshot_count_var),
            ("Elo available", self.v22_elo_count_var),
            ("Decay-Poisson available", self.v22_poisson_count_var),
            ("Lineup continuity", self.v22_lineup_count_var),
        )
        for i, (title, var) in enumerate(specs):
            cards.columnconfigure(i, weight=1)
            box = ttk.LabelFrame(cards, text=title, padding=8)
            box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 5, 0))
            ttk.Label(box, textvariable=var, font=("Segoe UI", 13, "bold")).pack(anchor="w")

        self.v22_research_tree = self._make_tree(
            self.research_models_tab,
            [
                ("match", "Match", 205),
                ("market", "Sharp market H/D/A", 155),
                ("elo", "Elo H/D/A", 145),
                ("poisson", "Decay Poisson H/D/A", 165),
                ("lambdas", "Poisson xG", 92),
                ("xi", "XI continuity H/A", 120),
                ("xg", "Recent net xG H/A", 125),
                ("opp", "Recent opp Elo H/A", 125),
                ("spread", "Model spread", 90),
                ("consensus", "Agreement", 125),
                ("quality", "Data", 65),
            ],
            height=20,
        )

    @staticmethod
    def _triplet(a, b, c) -> str:
        if a is None or b is None or c is None:
            return "—"
        return f"{a * 100:.1f}/{b * 100:.1f}/{c * 100:.1f}"

    @staticmethod
    def _pair(a, b, suffix: str = "") -> str:
        if a is None or b is None:
            return "—"
        return f"{a:.1f}{suffix}/{b:.1f}{suffix}"

    def _fill_research_models(self) -> None:
        if not hasattr(self, "v22_research_tree"):
            return
        for item in self.v22_research_tree.get_children():
            self.v22_research_tree.delete(item)
        features = sorted(
            self.v22_research_features.values(),
            key=lambda f: f.market_research_disagreement_pp if f.market_research_disagreement_pp is not None else -1.0,
            reverse=True,
        )
        for f in features:
            lambdas = "—" if f.poisson_lambda_home is None or f.poisson_lambda_away is None else f"{f.poisson_lambda_home:.2f}/{f.poisson_lambda_away:.2f}"
            xi = self._pair(
                None if f.lineup_home is None else f.lineup_home * 100,
                None if f.lineup_away is None else f.lineup_away * 100,
                "%",
            )
            xg = self._pair(f.home_recent_net_xg, f.away_recent_net_xg)
            opp = self._pair(f.home_recent_opponent_elo, f.away_recent_opponent_elo)
            spread = "—" if f.market_research_disagreement_pp is None else f"{f.market_research_disagreement_pp:.1f} pp"
            self.v22_research_tree.insert("", "end", values=(
                f.match_name,
                self._triplet(f.market_home, f.market_draw, f.market_away),
                self._triplet(f.elo_home, f.elo_draw, f.elo_away),
                self._triplet(f.poisson_home, f.poisson_draw, f.poisson_away),
                lambdas,
                xi,
                xg,
                opp,
                spread,
                f.consensus,
                f.data_quality,
            ))

    # ------------------------------------------------------------------
    # Multicore core-market pipeline
    # ------------------------------------------------------------------

    def fetch_matches(self) -> None:
        # V2.1 already captures every Tk variable before leaving the UI thread.
        # Keep that behaviour while using V2.2's worker implementation.
        super().fetch_matches()

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
            rows, acceleration = enrich_edge_model_parallel(
                rows,
                min_ev_pct=min_ev,
                progress=self._progress_callback,
                start_pct=73,
                end_pct=81,
            )
            self._edge_acceleration = acceleration
            timings["Probability model"] = time.perf_counter() - t
            if acceleration.parallel:
                info_notes.append(
                    f"V2.2 CPU acceleration: {acceleration.workers} worker processes calculated {acceleration.completed} fixtures."
                )
            elif acceleration.fallback_reason:
                info_notes.append(
                    f"V2.2 CPU acceleration fell back to one process: {acceleration.fallback_reason}"
                )

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
                self._progress_callback(82, "Best-price scan", "Core probabilities ready. Checking leading matches at additional price sources")

                def price_progress(pct: int, stage: str, detail: str) -> None:
                    fraction = max(0.0, min(1.0, (float(pct) - 66.0) / 23.0))
                    self._progress_callback(82 + int(9 * fraction), stage, detail)

                try:
                    self.price_shop_result = fetch_best_prices(api_key, rows, progress=price_progress)
                    self._last_api_request_count += self.price_shop_result.request_count
                    warnings.extend(self.price_shop_result.notes)
                except Exception as exc:
                    warnings.append(f"Best-price scan: {exc}")
                    LOGGER.exception("V2.2 price-shopping pass failed")
            else:
                self._progress_callback(91, "Best-price scan", "Additional bookmaker comparison is disabled")
            timings["Best-price scan"] = time.perf_counter() - t

            decisions = build_v21_decisions(rows, min_ev_pct=min_ev)
            robust = [d for d in decisions if d.status == "ROBUST +EV"]
            matched_pm = sum(1 for r in rows if r.pm_home is not None)
            matched_pin = sum(1 for r in rows if getattr(r, "pin_home", None) is not None)
            info_notes.append(
                f"Sportsbet catalogue: {len(self._sportsbet_leagues)} soccer league name(s); {len(rows)} Sportsbet fixture(s); "
                f"Polymarket matched {matched_pm}; Pinnacle matched {matched_pin}; robust edges {len(robust)}."
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
            info_notes.append(f"V2.2 stage timing: {timing_text}. Stored {v21_saved} robust-decision row update(s).")
            self._stage_timings = timings

            self._progress_callback(95, "Preparing dashboard", "Ranking robust edges first, then the highest-EV fallback")
            self.after(0, lambda: self._apply_v21_result(rows, warnings, info_notes, saved, context_saved, edge_saved))
        except Exception as exc:
            LOGGER.exception("V2.2 fetch failed")
            self.after(0, lambda: self._fatal_fetch_error(exc))

    # ------------------------------------------------------------------
    # GitHub-inspired research models, loaded in the existing background pass
    # ------------------------------------------------------------------

    def _football_worker_v19(self, epl_rows) -> None:
        bundle = None
        bundle_error: Optional[str] = None
        history = []
        history_notes: tuple[str, ...] = ()
        try:
            self._progress_callback(
                96,
                "Football research",
                "Loading EPL player/xG data and five seasons of historical model data in parallel",
            )
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="v22-football") as pool:
                football_future = pool.submit(refresh_football_intelligence, epl_rows)
                history_future = pool.submit(load_epl_history)
                try:
                    bundle = football_future.result()
                except Exception as exc:
                    bundle_error = str(exc)
                    LOGGER.exception("V2.2 football-intelligence refresh failed")
                try:
                    history, history_notes = history_future.result()
                except Exception:
                    LOGGER.exception("V2.2 historical-model data refresh failed")
                    history, history_notes = [], ("Historical EPL data unavailable",)
            self.after(
                0,
                lambda: self._apply_football_bundle_v22(bundle, bundle_error, history, history_notes),
            )
        except Exception as exc:
            LOGGER.exception("V2.2 football research worker failed")
            self.after(0, lambda: self._apply_football_bundle_v22(None, str(exc), [], (str(exc),)))

    def _apply_football_bundle_v22(self, bundle, error, history, history_notes) -> None:
        # Preserve all V1.9-V2.1 football/intelligence/validation behaviour.
        super()._apply_football_bundle_v19(bundle, error)
        self._v22_history_notes = tuple(history_notes or ())
        try:
            self.v22_research_features = build_research_features(self.rows, bundle, history)
            stored = 0
            if bool(self.save_snapshots_var.get()) and self.v22_research_features:
                stored = save_research_features(self.rows, self.v22_research_features)
            self._fill_research_models()
            self._refresh_v22_research_summary()
            history_count = len(history)
            self.v22_research_status_var.set(
                f"Research layer ready: {len(self.v22_research_features)} EPL fixture(s) · {history_count:,} historical results loaded · {stored} feature row update(s) saved. "
                "These models do not alter ROBUST +EV until out-of-sample validation supports doing so."
            )
            self._fill_dashboard()
        except Exception as exc:
            LOGGER.exception("V2.2 research feature calculation failed")
            self.v22_research_status_var.set(f"Research feature layer unavailable: {exc}. Core robust market model is unaffected.")

    def _refresh_v22_research_summary(self) -> None:
        try:
            summary = research_summary()
        except Exception:
            LOGGER.exception("V2.2 research summary failed")
            return
        self.v22_snapshot_count_var.set(str(summary.snapshots))
        self.v22_elo_count_var.set(str(summary.with_elo))
        self.v22_poisson_count_var.set(str(summary.with_poisson))
        self.v22_lineup_count_var.set(str(summary.with_lineup))

    def _fill_dashboard(self) -> None:
        super()._fill_dashboard()
        decision = getattr(self, "_v21_dashboard_best", None)
        if decision is None:
            return
        feature = self.v22_research_features.get(decision.match_name)
        if feature is None:
            return
        # Research agreement is context only. The displayed EV and green/amber
        # classification continue to come solely from the V2.1 robust market rule.
        self.best_pick_confidence_var.set(
            f"{decision.confidence.title()} · {feature.consensus}"
        )
        current = self.best_pick_reason_var.get()
        self.best_pick_reason_var.set(
            current
            + f" Research check: {feature.consensus}; cross-model spread {feature.market_research_disagreement_pp:.1f} pp."
            if feature.market_research_disagreement_pp is not None
            else current + f" Research check: {feature.consensus}."
        )

    def _diagnostic_text(self) -> str:
        accel = self._edge_acceleration
        cpu_text = (
            f"parallel with {accel.workers} workers" if accel.parallel
            else f"single-process{' fallback: ' + accel.fallback_reason if accel.fallback_reason else ''}"
        )
        history = " | ".join(self._v22_history_notes) if self._v22_history_notes else "not loaded yet"
        return (
            f"V2.2 CPU acceleration: {cpu_text}; detected logical CPUs {os.cpu_count() or 1}; auto target {recommended_workers()}\n"
            "V2.2 research models: historical Elo + time-decayed goals Poisson + expected-XI continuity + recent xG/opponent-strength context\n"
            f"V2.2 historical data: {history}\n"
            "V2.2 rule: research residuals are recorded but do not change production ROBUST +EV until validated out of sample\n"
            + super()._diagnostic_text()
        )


def main() -> None:
    try:
        app = V22App()
        app.after(1000, app._refresh_validation_view)
        app.after(1200, app._refresh_v22_research_summary)
        app.mainloop()
    except Exception:
        LOGGER.exception("Fatal V2.2 application error")
        raise
