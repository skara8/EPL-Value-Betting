from __future__ import annotations

import re
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Optional

import main as base_main
from config import VERSION
from context_model import ContextInputs
from edge_model import enrich_edge_model
from edge_storage import save_edge_snapshot
from market_storage import save_market_context
from main_v19 import (
    V19App,
    LOGGER,
    GREEN_BG,
    GREEN_DARK,
    AMBER_BG,
    AMBER_DARK,
    BLUE_BG,
    BLUE_DARK,
    NEUTRAL_BG,
    TEXT_DARK,
)
from multileague_data import combine_sportsbet_catalogue, enrich_multileague_market_context, sportsbet_league_counts
from progressive_data import fetch_multileague_sources_progressive
from price_shop import MatchPriceShop, PriceShopResult, fetch_best_prices


class V20App(V19App):
    """V2.0: clearer loading UX, condensed navigation and best-price shopping."""

    def _create_vars(self) -> None:
        super()._create_vars()
        self.loading_stage_var = tk.StringVar(value="Ready")
        self.loading_detail_var = tk.StringVar(value="Press Fetch odds & analyse when you are ready.")
        self.loading_time_var = tk.StringVar(value="")
        self.loading_percent_var = tk.DoubleVar(value=0.0)
        self.loading_step_var = tk.StringVar(value="")
        self.price_shop_status_var = tk.StringVar(value="Best-price comparison has not run yet")
        self.price_shop_provider_var = tk.StringVar(value="0")
        self.price_shop_match_var = tk.StringVar(value="0")
        self.price_shop_request_var = tk.StringVar(value="0")
        self.price_shop_best_var = tk.StringVar(value="—")
        self.price_shop_enabled_var = tk.BooleanVar(value=True)
        self.price_shop_result: Optional[PriceShopResult] = None
        self._fetch_started_monotonic: Optional[float] = None
        self._progress_percent = 0
        self._progress_history: list[str] = []
        self._progress_timer_id = None

    # ------------------------------------------------------------------
    # Condensed navigation
    # ------------------------------------------------------------------

    def _build_tabs(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        self.dashboard_tab = ttk.Frame(self.notebook, padding=14)
        self.markets_container = ttk.Frame(self.notebook, padding=8)
        self.analysis_container = ttk.Frame(self.notebook, padding=8)
        self.tools_container = ttk.Frame(self.notebook, padding=8)
        self.research_container = ttk.Frame(self.notebook, padding=8)
        self.settings_tab = ttk.Frame(self.notebook, padding=14)

        self.notebook.add(self.dashboard_tab, text="Dashboard")
        self.notebook.add(self.markets_container, text="Markets")
        self.notebook.add(self.analysis_container, text="Analysis")
        self.notebook.add(self.tools_container, text="Tools")
        self.notebook.add(self.research_container, text="Research")
        self.notebook.add(self.settings_tab, text="Settings")

        self.markets_book = ttk.Notebook(self.markets_container)
        self.markets_book.pack(fill="both", expand=True)
        self.matches_tab = ttk.Frame(self.markets_book, padding=12)
        self.leagues_tab = ttk.Frame(self.markets_book, padding=12)
        self.price_shop_tab = ttk.Frame(self.markets_book, padding=12)
        self.markets_book.add(self.matches_tab, text="Matches")
        self.markets_book.add(self.leagues_tab, text="League coverage")
        self.markets_book.add(self.price_shop_tab, text="Best prices")

        self.analysis_book = ttk.Notebook(self.analysis_container)
        self.analysis_book.pack(fill="both", expand=True)
        self.candidates_tab = ttk.Frame(self.analysis_book, padding=12)
        self.edge_tab = ttk.Frame(self.analysis_book, padding=12)
        self.context_tab = ttk.Frame(self.analysis_book, padding=12)
        self.intelligence_tab = ttk.Frame(self.analysis_book, padding=12)
        self.analysis_book.add(self.candidates_tab, text="Candidates")
        self.analysis_book.add(self.edge_tab, text="Market edge")
        self.analysis_book.add(self.context_tab, text="Team context")
        self.analysis_book.add(self.intelligence_tab, text="Football model")

        self.tools_book = ttk.Notebook(self.tools_container)
        self.tools_book.pack(fill="both", expand=True)
        self.dutch_tab = ttk.Frame(self.tools_book, padding=12)
        self.tools_book.add(self.dutch_tab, text="Dutch calculator")

        self.research_book = ttk.Notebook(self.research_container)
        self.research_book.pack(fill="both", expand=True)
        self.validation_tab = ttk.Frame(self.research_book, padding=12)
        self.history_tab = ttk.Frame(self.research_book, padding=12)
        self.diagnostics_tab = ttk.Frame(self.research_book, padding=12)
        self.research_book.add(self.validation_tab, text="Validation")
        self.research_book.add(self.history_tab, text="History")
        self.research_book.add(self.diagnostics_tab, text="Diagnostics")

        self._build_dashboard()
        self._build_matches()
        self._build_leagues_tab()
        self._build_price_shop_tab()
        self._build_candidates()
        self._build_edge_lab()
        self._build_context_lab()
        self._build_intelligence_tab()
        self._build_dutch_calculator()
        self._build_validation_tab()
        self._build_history()
        self._build_diagnostics()
        self._build_settings()

        self.after(50, self._normalise_visible_copy)
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self.after(20, self._normalise_visible_copy))

    def _show_page(self, group: str, page: str) -> None:
        groups = {
            "markets": (self.markets_container, self.markets_book, {
                "matches": self.matches_tab, "leagues": self.leagues_tab, "prices": self.price_shop_tab,
            }),
            "analysis": (self.analysis_container, self.analysis_book, {
                "candidates": self.candidates_tab, "edge": self.edge_tab, "context": self.context_tab, "football": self.intelligence_tab,
            }),
            "tools": (self.tools_container, self.tools_book, {"dutch": self.dutch_tab}),
            "research": (self.research_container, self.research_book, {
                "validation": self.validation_tab, "history": self.history_tab, "diagnostics": self.diagnostics_tab,
            }),
        }
        top, book, pages = groups[group]
        self.notebook.select(top)
        book.select(pages[page])

    # ------------------------------------------------------------------
    # Dashboard with visible background activity
    # ------------------------------------------------------------------

    def _build_dashboard(self) -> None:
        intro = ttk.Frame(self.dashboard_tab)
        intro.pack(fill="x", pady=(0, 8))
        ttk.Label(intro, text="Current model view", font=("Segoe UI", 12, "bold")).pack(side="left")
        ttk.Label(
            intro,
            text="The detailed market, handicap, team and price-shopping analysis runs underneath. This page shows the clearest result.",
            style="Muted.TLabel",
        ).pack(side="right")

        self.loading_frame = tk.Frame(self.dashboard_tab, bg="#F4F7FB", bd=1, relief="solid")
        self.loading_frame.pack(fill="x", pady=(0, 10))
        top = tk.Frame(self.loading_frame, bg="#F4F7FB")
        top.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(top, text="ANALYSIS ACTIVITY", bg="#F4F7FB", fg=BLUE_DARK, font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Label(top, textvariable=self.loading_step_var, bg="#F4F7FB", fg=TEXT_DARK, font=("Segoe UI", 9)).pack(side="right")
        tk.Label(self.loading_frame, textvariable=self.loading_stage_var, bg="#F4F7FB", fg=TEXT_DARK, font=("Segoe UI", 12, "bold"), anchor="w").pack(fill="x", padx=14)
        tk.Label(self.loading_frame, textvariable=self.loading_detail_var, bg="#F4F7FB", fg=TEXT_DARK, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=1280).pack(fill="x", padx=14, pady=(2, 6))
        bar_row = tk.Frame(self.loading_frame, bg="#F4F7FB")
        bar_row.pack(fill="x", padx=14, pady=(0, 10))
        self.analysis_progress = ttk.Progressbar(bar_row, maximum=100, variable=self.loading_percent_var, mode="determinate")
        self.analysis_progress.pack(side="left", fill="x", expand=True)
        tk.Label(bar_row, textvariable=self.loading_time_var, bg="#F4F7FB", fg=TEXT_DARK, font=("Segoe UI", 9), width=34, anchor="e").pack(side="right", padx=(12, 0))

        self.best_pick_frame = tk.Frame(self.dashboard_tab, bg=NEUTRAL_BG, bd=1, relief="solid")
        self.best_pick_frame.pack(fill="x", pady=(0, 10))
        hero_left = tk.Frame(self.best_pick_frame, bg=NEUTRAL_BG)
        hero_left.pack(side="left", fill="both", expand=True, padx=18, pady=15)
        hero_right = tk.Frame(self.best_pick_frame, bg=NEUTRAL_BG)
        hero_right.pack(side="right", padx=20, pady=15)

        self.best_pick_kicker = tk.Label(hero_left, text="BEST CURRENT THEORETICAL EDGE", bg=NEUTRAL_BG, fg=TEXT_DARK, font=("Segoe UI", 9, "bold"))
        self.best_pick_kicker.pack(anchor="w")
        self.best_pick_title = tk.Label(hero_left, textvariable=self.best_pick_title_var, bg=NEUTRAL_BG, fg=TEXT_DARK, font=("Segoe UI", 22, "bold"), anchor="w")
        self.best_pick_title.pack(anchor="w", pady=(4, 1))
        self.best_pick_match = tk.Label(hero_left, textvariable=self.best_pick_match_var, bg=NEUTRAL_BG, fg=TEXT_DARK, font=("Segoe UI", 11), anchor="w")
        self.best_pick_match.pack(anchor="w")
        self.best_pick_reason = tk.Label(hero_left, textvariable=self.best_pick_reason_var, bg=NEUTRAL_BG, fg=TEXT_DARK, font=("Segoe UI", 10), wraplength=850, justify="left", anchor="w")
        self.best_pick_reason.pack(anchor="w", pady=(9, 0))

        tk.Label(hero_right, text="Best price", bg=NEUTRAL_BG, fg=TEXT_DARK, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="e", padx=8)
        self.best_price_label = tk.Label(hero_right, textvariable=self.best_pick_price_var, bg=NEUTRAL_BG, fg=TEXT_DARK, font=("Segoe UI", 17, "bold"))
        self.best_price_label.grid(row=0, column=1, sticky="e", padx=8)
        tk.Label(hero_right, text="Model EV", bg=NEUTRAL_BG, fg=TEXT_DARK, font=("Segoe UI", 9)).grid(row=1, column=0, sticky="e", padx=8, pady=4)
        self.best_ev_label = tk.Label(hero_right, textvariable=self.best_pick_ev_var, bg=NEUTRAL_BG, fg=TEXT_DARK, font=("Segoe UI", 18, "bold"))
        self.best_ev_label.grid(row=1, column=1, sticky="e", padx=8, pady=4)
        tk.Label(hero_right, text="Confidence", bg=NEUTRAL_BG, fg=TEXT_DARK, font=("Segoe UI", 9)).grid(row=2, column=0, sticky="e", padx=8)
        self.best_conf_label = tk.Label(hero_right, textvariable=self.best_pick_confidence_var, bg=NEUTRAL_BG, fg=TEXT_DARK, font=("Segoe UI", 11, "bold"))
        self.best_conf_label.grid(row=2, column=1, sticky="e", padx=8)
        ttk.Button(hero_right, text="Why this?", command=self._open_best_context).grid(row=3, column=0, columnspan=2, pady=(12, 0), sticky="e")

        middle = ttk.Frame(self.dashboard_tab)
        middle.pack(fill="x", pady=(0, 10))
        middle.columnconfigure(0, weight=3)
        middle.columnconfigure(1, weight=2)

        list_box = ttk.LabelFrame(middle, text="Other leading options", padding=8)
        list_box.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.smart_top_tree = self._tree_in(
            list_box,
            [
                ("league", "League", 155),
                ("match", "Match", 220),
                ("pick", "Pick", 120),
                ("odds", "Best odds", 105),
                ("ev", "EV", 70),
                ("confidence", "Confidence", 100),
                ("why", "Simple reason", 330),
            ],
            height=7,
        )
        self.smart_top_tree.bind("<Double-1>", self._open_tree_pick)

        dutch_box = tk.Frame(middle, bg=BLUE_BG, bd=1, relief="solid")
        dutch_box.grid(row=0, column=1, sticky="nsew")
        tk.Label(dutch_box, text="DUTCH OPTION", bg=BLUE_BG, fg=BLUE_DARK, font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=14, pady=(12, 2))
        tk.Label(dutch_box, textvariable=self.dutch_headline_var, bg=BLUE_BG, fg=TEXT_DARK, font=("Segoe UI", 14, "bold"), wraplength=420, justify="left").pack(anchor="w", padx=14)
        tk.Label(dutch_box, textvariable=self.dutch_summary_var, bg=BLUE_BG, fg=TEXT_DARK, font=("Segoe UI", 9), wraplength=420, justify="left").pack(anchor="w", padx=14, pady=(8, 10))
        ttk.Button(dutch_box, text="Open Dutch calculator", command=self._open_best_dutch).pack(anchor="w", padx=14, pady=(0, 12))

        bottom = ttk.Frame(self.dashboard_tab)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Fetch odds & analyse", command=self.fetch_matches).pack(side="left")
        ttk.Button(bottom, text="See candidates", command=lambda: self._show_page("analysis", "candidates")).pack(side="left", padx=8)
        ttk.Button(bottom, text="Open market edge", command=lambda: self._show_page("analysis", "edge")).pack(side="left")
        ttk.Button(bottom, text="Compare best prices", command=lambda: self._show_page("markets", "prices")).pack(side="left", padx=8)
        ttk.Label(bottom, textvariable=self.dashboard_status_var, style="Muted.TLabel").pack(side="right")

    # ------------------------------------------------------------------
    # Price shop page
    # ------------------------------------------------------------------

    def _build_price_shop_tab(self) -> None:
        intro = ttk.LabelFrame(self.price_shop_tab, text="Best available price comparison", padding=12)
        intro.pack(fill="x", pady=(0, 10))
        ttk.Label(
            intro,
            text=(
                "The fair-probability model does not change here. After the core analysis, the app checks a smaller set of leading matches across supported Australian-facing bookmakers and Polymarket, then recalculates EV using the best observed price. "
                "Price shopping improves the payout side of EV; it does not increase the estimated chance of the result occurring."
            ),
            wraplength=1320,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(intro, textvariable=self.price_shop_status_var, style="Muted.TLabel").pack(anchor="w", pady=(7, 0))

        cards = ttk.Frame(self.price_shop_tab)
        cards.pack(fill="x", pady=(0, 10))
        for i in range(4):
            cards.columnconfigure(i, weight=1)
        base_main.SummaryCard(cards, "Extra bookmakers", self.price_shop_provider_var, "Successfully checked").grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        base_main.SummaryCard(cards, "Matches compared", self.price_shop_match_var, "Leading model-priced matches").grid(row=0, column=1, sticky="nsew", padx=6)
        base_main.SummaryCard(cards, "Extra API requests", self.price_shop_request_var, "Caching reduces repeat calls").grid(row=0, column=2, sticky="nsew", padx=6)
        base_main.SummaryCard(cards, "Best price EV", self.price_shop_best_var, "Highest observed at best price").grid(row=0, column=3, sticky="nsew", padx=(6, 0))

        self.price_shop_tree = self._make_tree(
            self.price_shop_tab,
            [
                ("league", "League", 165),
                ("match", "Match", 240),
                ("pick", "Outcome", 120),
                ("fair", "Model fair", 85),
                ("sb", "Sportsbet", 80),
                ("best", "Best odds", 80),
                ("source", "Best source", 110),
                ("sbev", "EV @ Sportsbet", 100),
                ("bestev", "EV @ best", 90),
                ("gain", "EV improvement", 105),
            ],
            height=22,
        )

    # ------------------------------------------------------------------
    # Settings additions
    # ------------------------------------------------------------------

    def _build_settings(self) -> None:
        super()._build_settings()
        box = ttk.LabelFrame(self.settings_tab, text="Best-price scan", padding=16)
        box.pack(fill="x", pady=(12, 0))
        ttk.Checkbutton(
            box,
            text="Compare additional bookmakers after the core model finishes",
            variable=self.price_shop_enabled_var,
        ).pack(anchor="w")
        ttk.Label(
            box,
            text=(
                "Default sources: Bet365, Ladbrokes, TAB, Unibet AU and BetRight, plus Sportsbet and fee-adjusted Polymarket prices already loaded by the core model. "
                "To control speed and API use, only the leading model-priced matches (up to 15 across up to 5 leagues) are shopped, and bookmaker league/event responses are cached for 15 minutes. "
                "If your PulseScore plan does not expose a source, it is skipped and the rest of the analysis continues."
            ),
            wraplength=1260,
            justify="left",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 0))

    # ------------------------------------------------------------------
    # Progress lifecycle
    # ------------------------------------------------------------------

    def _progress_callback(self, percent: int, stage: str, detail: str) -> None:
        self.after(0, lambda: self._apply_progress(percent, stage, detail))

    def _apply_progress(self, percent: int, stage: str, detail: str) -> None:
        self._progress_percent = max(self._progress_percent, int(percent))
        self.loading_percent_var.set(float(self._progress_percent))
        self.loading_stage_var.set(stage)
        self.loading_detail_var.set(detail)
        self.loading_step_var.set(f"{self._progress_percent}%")
        entry = f"{stage}: {detail}"
        if not self._progress_history or self._progress_history[-1] != entry:
            self._progress_history.append(entry)
            self._progress_history = self._progress_history[-8:]
        self.status_var.set(f"{stage} — {detail}")
        self.update_idletasks()

    def _start_progress(self) -> None:
        self._fetch_started_monotonic = time.monotonic()
        self._progress_percent = 1
        self._progress_history = []
        self.loading_percent_var.set(1.0)
        self.loading_stage_var.set("Starting analysis")
        self.loading_detail_var.set("Preparing the market-data pipeline…")
        self.loading_step_var.set("1%")
        self._schedule_progress_clock()

    def _schedule_progress_clock(self) -> None:
        if self._progress_timer_id is not None:
            try:
                self.after_cancel(self._progress_timer_id)
            except Exception:
                pass
        self._progress_timer_id = self.after(500, self._update_progress_clock)

    def _update_progress_clock(self) -> None:
        if not self._busy or self._fetch_started_monotonic is None:
            self._progress_timer_id = None
            return
        elapsed = max(0.0, time.monotonic() - self._fetch_started_monotonic)
        pct = max(1, self._progress_percent)
        if pct >= 8:
            estimate = elapsed * (100.0 - pct) / pct
            # Avoid presenting a false sense of precision for network calls.
            if estimate < 90:
                eta = f"about {max(1, int(round(estimate / 5.0) * 5))}s remaining"
            else:
                eta = f"about {max(1, int(round(estimate / 60.0)))} min remaining"
        else:
            eta = "estimating time remaining"
        self.loading_time_var.set(f"Elapsed {int(elapsed)}s · {eta}")
        self._progress_timer_id = self.after(500, self._update_progress_clock)

    def _finish_progress(self, detail: str = "Analysis complete") -> None:
        self._progress_percent = 100
        self.loading_percent_var.set(100.0)
        self.loading_stage_var.set("Analysis ready")
        self.loading_detail_var.set(detail)
        self.loading_step_var.set("100%")
        if self._fetch_started_monotonic is not None:
            elapsed = time.monotonic() - self._fetch_started_monotonic
            self.loading_time_var.set(f"Completed in {elapsed:.1f}s")
        self._fetch_started_monotonic = None

    def _set_busy(self, busy: bool, text: Optional[str] = None) -> None:
        if busy and not self._busy:
            self._start_progress()
        super()._set_busy(busy, "Running football market scan and analysis…" if busy else text)
        if not busy and self._progress_percent < 100 and self.rows:
            # Market results may be ready before the optional football layer.
            self.loading_detail_var.set("Market results are available. Optional team intelligence may still be finishing in the background.")

    # ------------------------------------------------------------------
    # Fetch pipeline
    # ------------------------------------------------------------------

    def _fetch_worker(self, api_key, start, end, min_ev) -> None:
        warnings: list[str] = []
        info_notes: list[str] = []
        try:
            source = fetch_multileague_sources_progressive(api_key, start, end, self._progress_callback)
            self._sportsbet_leagues = list(source["leagues"])
            self._last_api_request_count = int(source.get("request_count", 0) or 0)
            sb_bundle = source["sportsbet"]
            pm_bundle = source["polymarket"]
            pin_bundle = source["pinnacle"]

            self._progress_callback(65, "Matching markets", "Pairing Sportsbet fixtures with independent reference markets")
            rows = combine_sportsbet_catalogue(sb_bundle.matches, pm_bundle.matches, min_ev)
            self._progress_callback(69, "Asian handicap & totals", "Attaching Sportsbet/Pinnacle handicap and goal-total context")
            rows = enrich_multileague_market_context(rows, sb_bundle.raw_events, pin_bundle.raw_events)
            self._progress_callback(73, "Probability model", "Removing margin, combining external probabilities and calculating EV")
            rows = enrich_edge_model(rows, min_ev_pct=min_ev)

            min_volume = max(0.0, base_main.safe_float(self.min_volume_var.get(), 0.0))
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

            self.price_shop_result = None
            if self.price_shop_enabled_var.get() and rows:
                self._progress_callback(75, "Best-price scan", "Core model ready. Checking leading matches at additional bookmakers")
                try:
                    self.price_shop_result = fetch_best_prices(api_key, rows, progress=self._progress_callback)
                    self._last_api_request_count += self.price_shop_result.request_count
                    warnings.extend(self.price_shop_result.notes)
                except Exception as exc:
                    warnings.append(f"Best-price scan: {exc}")
                    LOGGER.exception("V2 price-shopping pass failed")
            else:
                self._progress_callback(89, "Best-price scan", "Additional bookmaker comparison is disabled")

            matched_pm = sum(1 for r in rows if r.pm_home is not None)
            matched_pin = sum(1 for r in rows if getattr(r, "pin_home", None) is not None)
            info_notes.append(
                f"Sportsbet catalogue: {len(self._sportsbet_leagues)} soccer league name(s); {len(rows)} Sportsbet fixture(s) in range; "
                f"Polymarket matched {matched_pm}; Pinnacle matched {matched_pin}."
            )
            if self.price_shop_result:
                info_notes.append(
                    f"Best-price scan: {len(self.price_shop_result.providers_checked)} extra bookmaker(s), "
                    f"{len(self.price_shop_result.matches)} leading match(es), {self.price_shop_result.request_count} extra API request(s), "
                    f"{self.price_shop_result.cache_hits} cache hit(s)."
                )

            self._progress_callback(91, "Saving research", "Saving market snapshots and current model features")
            saved = context_saved = edge_saved = 0
            if self.save_snapshots_var.get() and rows:
                saved = base_main.save_snapshot(rows)
                context_saved = save_market_context(rows)
                edge_saved = save_edge_snapshot(rows)

            self._progress_callback(94, "Preparing dashboard", "Sorting the strongest edges and best observed prices")
            self.after(0, lambda: self._apply_v20_result(rows, warnings, info_notes, saved, context_saved, edge_saved))
        except Exception as exc:
            LOGGER.exception("V2 fetch failed")
            self.after(0, lambda: self._fatal_fetch_error(exc))

    def _apply_v20_result(self, rows, warnings, info_notes, saved, context_saved, edge_saved) -> None:
        super()._apply_multileague_result(rows, warnings, info_notes, saved, context_saved, edge_saved)
        self._fill_price_shop()
        self._apply_progress(96, "Market results ready", "Dashboard is usable now. Finishing optional EPL player/xG/tactical analysis in the background.")
        if not self._intelligence_loading:
            self._finish_progress("Market, price and available football analysis are complete.")
        self._normalise_visible_copy()

    def _refresh_football_data(self) -> None:
        self._progress_callback(96, "Football context", "Checking whether EPL matches need player, xG and tactical enrichment")
        super()._refresh_football_data()
        if not self._intelligence_loading:
            self._finish_progress("Core market and best-price analysis complete. No additional EPL football layer was required.")

    def _apply_football_bundle_v19(self, bundle, error) -> None:
        super()._apply_football_bundle_v19(bundle, error)
        if error:
            self._finish_progress("Market and best-price analysis complete. Optional EPL football intelligence was unavailable.")
        else:
            self._finish_progress("All market, best-price and available EPL football analysis is complete.")
        self._normalise_visible_copy()

    def _fatal_fetch_error(self, exc: Exception) -> None:
        super()._fatal_fetch_error(exc)
        self.loading_stage_var.set("Analysis stopped")
        self.loading_detail_var.set(str(exc))
        self.loading_time_var.set("Check Diagnostics for more information")

    # ------------------------------------------------------------------
    # Dashboard and best-price integration
    # ------------------------------------------------------------------

    @staticmethod
    def _side_from_selection(row, selection: str) -> Optional[str]:
        if selection == row.home_team:
            return "HOME"
        if selection == row.away_team:
            return "AWAY"
        if selection.lower() == "draw":
            return "DRAW"
        return None

    def _best_quote(self, row, side: str):
        shop: Optional[MatchPriceShop] = getattr(row, "price_shop", None)
        return shop.best.get(side) if shop else None

    def _best_price_ev(self, row, side: str) -> Optional[float]:
        shop: Optional[MatchPriceShop] = getattr(row, "price_shop", None)
        return shop.best_ev_pct.get(side) if shop else None

    def _highest_best_price_edge(self):
        best = None
        for row in self.rows:
            for side in ("HOME", "DRAW", "AWAY"):
                ev = self._best_price_ev(row, side)
                quote = self._best_quote(row, side)
                if ev is None or quote is None:
                    continue
                if best is None or ev > best[3]:
                    best = (row, side, quote, float(ev))
        return best

    def _fill_dashboard(self) -> None:
        super()._fill_dashboard()
        if not hasattr(self, "smart_top_tree"):
            return

        # Rebuild the short list using current model ideas but show the best
        # price observed for each outcome when the price-shopping pass covered it.
        ideas = self._build_current_ideas()
        row_by_match = {r.match_name: r for r in self.rows}
        for item in self.smart_top_tree.get_children():
            self.smart_top_tree.delete(item)
        for idx, idea in enumerate(ideas[:8]):
            row = row_by_match.get(idea.match_name)
            side = self._side_from_selection(row, idea.selection) if row else None
            quote = self._best_quote(row, side) if row and side else None
            odds = quote.decimal_odds if quote else idea.sportsbet_odds
            probability = idea.adjusted_probability
            ev = (probability * odds - 1.0) * 100.0
            odds_text = f"${odds:.2f} {quote.source}" if quote else f"${odds:.2f} Sportsbet"
            league = self._league_label(row) if row else "—"
            why = idea.short_reason if len(idea.short_reason) <= 135 else idea.short_reason[:132] + "…"
            self.smart_top_tree.insert("", "end", iid=f"smart-{idx}", values=(
                league, idea.match_name, idea.selection, odds_text, f"{ev:+.1f}%",
                f"{idea.confidence.title()} / {idea.football_quality.title()} data", why,
            ))

        # Qualifying existing idea: preserve the model decision but improve its
        # payout/EV display if another observed source offers a better price.
        if self._dashboard_best is not None:
            idea = self._dashboard_best
            row = row_by_match.get(idea.match_name)
            side = self._side_from_selection(row, idea.selection) if row else None
            quote = self._best_quote(row, side) if row and side else None
            if quote:
                ev = (idea.adjusted_probability * quote.decimal_odds - 1.0) * 100.0
                self.best_pick_price_var.set(f"${quote.decimal_odds:.2f} · {quote.source}")
                self.best_pick_ev_var.set(f"{ev:+.1f}%")
                self.best_pick_reason_var.set(
                    idea.short_reason + f" The best observed price is {quote.source} at ${quote.decimal_odds:.2f}, which raises the same model probability to about {ev:+.1f}% EV."
                )
            return

        # When there is no threshold-clearing Sportsbet recommendation, the
        # fallback now considers the best observed price across the sources that
        # were actually checked. It is still labelled honestly when negative.
        fallback = self._highest_best_price_edge()
        if fallback is None:
            return
        row, side, quote, ev = fallback
        edge = getattr(row, "edge_outcomes", {}).get(side)
        fair = getattr(edge, "model_probability", None) if edge else None
        name = row.home_team if side == "HOME" else row.away_team if side == "AWAY" else "Draw"
        threshold = base_main.safe_float(self.min_ev_var.get(), self.settings.min_ev_pct)
        if ev >= threshold:
            title = f"{name} — +EV at best observed price"
            reason_prefix = "The core model did not qualify the Sportsbet price, but a better observed price changes the payout side of the EV calculation."
            self._set_hero_colour(True)
        elif ev > 0:
            title = f"{name} — positive EV, below threshold"
            reason_prefix = "This is positive at the best observed price, but it is below your recommendation threshold."
            self._set_hero_colour(False)
        else:
            title = f"{name} — highest EV available"
            reason_prefix = "Still negative EV — shown because it is the least negative best-price option currently observed."
            self._set_hero_colour(False)
        self.best_pick_title_var.set(title)
        self.best_pick_match_var.set(f"{self._league_label(row)} · {row.match_name}")
        self.best_pick_price_var.set(f"${quote.decimal_odds:.2f} · {quote.source}")
        self.best_pick_ev_var.set(f"{ev:+.1f}%")
        self.best_pick_confidence_var.set(str(getattr(edge, "confidence", "LOW") or "LOW").title())
        if fair is not None:
            self.best_pick_reason_var.set(
                f"{reason_prefix} The model estimates {fair * 100:.1f}% for this outcome. At {quote.source}'s ${quote.decimal_odds:.2f}, that works out to {ev:+.1f}% theoretical EV."
            )
        else:
            self.best_pick_reason_var.set(reason_prefix)

    def _fill_price_shop(self) -> None:
        if not hasattr(self, "price_shop_tree"):
            return
        for item in self.price_shop_tree.get_children():
            self.price_shop_tree.delete(item)
        result = self.price_shop_result
        if not result:
            self.price_shop_provider_var.set("0")
            self.price_shop_match_var.set("0")
            self.price_shop_request_var.set("0")
            self.price_shop_best_var.set("—")
            self.price_shop_status_var.set("No additional bookmaker scan was completed. Core market analysis remains available.")
            return

        best_seen = None
        row_by_match = {r.match_name: r for r in self.rows}
        table_rows = []
        for match_name, shop in result.matches.items():
            row = row_by_match.get(match_name)
            if not row:
                continue
            for side in ("HOME", "DRAW", "AWAY"):
                quote = shop.best.get(side)
                best_ev = shop.best_ev_pct.get(side)
                edge = getattr(row, "edge_outcomes", {}).get(side)
                fair = getattr(edge, "model_probability", None) if edge else None
                sb_odds = getattr(edge, "sportsbet_odds", None) if edge else None
                sb_ev = getattr(edge, "model_ev_pct", None) if edge else None
                if quote is None or best_ev is None or fair is None:
                    continue
                selection = row.home_team if side == "HOME" else row.away_team if side == "AWAY" else "Draw"
                gain = best_ev - float(sb_ev) if sb_ev is not None else None
                table_rows.append((best_ev, (
                    self._league_label(row), match_name, selection, f"{fair * 100:.2f}%",
                    f"{sb_odds:.2f}" if sb_odds else "—", f"{quote.decimal_odds:.2f}", quote.source,
                    f"{sb_ev:+.2f}%" if sb_ev is not None else "—", f"{best_ev:+.2f}%",
                    f"{gain:+.2f} pp" if gain is not None else "—",
                )))
                if best_seen is None or best_ev > best_seen:
                    best_seen = float(best_ev)

        for _, values in sorted(table_rows, key=lambda x: x[0], reverse=True):
            self.price_shop_tree.insert("", "end", values=values)

        self.price_shop_provider_var.set(str(len(result.providers_checked)))
        self.price_shop_match_var.set(str(len(result.matches)))
        self.price_shop_request_var.set(str(result.request_count))
        self.price_shop_best_var.set(f"{best_seen:+.2f}%" if best_seen is not None else "—")
        sources = ", ".join(result.providers_checked) if result.providers_checked else "none"
        self.price_shop_status_var.set(
            f"Checked: {sources}. {result.cache_hits} cached response(s) avoided repeat API calls. "
            "Polymarket is fee-adjusted using the Dutch calculator's taker-fee assumption."
        )
        self._fill_dashboard()

    # ------------------------------------------------------------------
    # Navigation overrides for nested notebooks
    # ------------------------------------------------------------------

    def _open_best_context(self) -> None:
        idea = self._dashboard_best
        if idea is None:
            self._show_page("analysis", "edge")
            return
        self.context_match_var.set(idea.match_name)
        row = self._selected_context_row()
        if row is not None:
            self._context_active_key = self._context_key_for_row(row)
            self._load_inputs_to_ui(self._context_inputs_by_key.get(self._context_active_key, ContextInputs()))
            self._recalculate_context()
        self._show_page("analysis", "context")

    def _open_tree_pick(self, _event=None) -> None:
        selected = self.smart_top_tree.selection()
        if not selected:
            return
        values = self.smart_top_tree.item(selected[0], "values")
        if len(values) >= 2:
            self.context_match_var.set(values[1])
            self._context_match_changed()
            self._show_page("analysis", "context")

    def _open_best_dutch(self) -> None:
        if self._dashboard_dutch is not None:
            self.dutch_match_var.set(self._dashboard_dutch.match_name)
            self._load_match_into_dutch("best")
        self._show_page("tools", "dutch")

    # ------------------------------------------------------------------
    # User-facing copy cleanup
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_copy(text: str) -> str:
        if not text:
            return text
        replacements = {
            "V1.6 keeps the V1.5 external-market fair probability as the baseline.": "The independent external-market fair probability remains the baseline.",
            "Manual in V1.6.": "Manual research input.",
            "How V1.5 calculates edge": "How the model calculates edge",
            "Why V1.5 does not simply 'increase' the EV": "Why the model does not simply 'increase' EV",
            "How V1.3 interprets the market": "How the model interprets the market",
            "fetch EPL odds": "fetch football odds",
            "Fetch EPL odds": "Fetch football odds",
            "Fetch current EPL odds": "Fetch football odds",
            "Fetch EPL market snapshot": "Fetch football market snapshot",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        text = re.sub(r"\bV1\.(?:3|4|5|6|7|8|9)(?:\.\d+)?\b", "the current model", text)
        text = text.replace("the current model AUTOMATIC FOOTBALL MODEL", "AUTOMATIC FOOTBALL MODEL")
        text = text.replace("The current model", "The model")
        return text

    def _normalise_visible_copy(self) -> None:
        def visit(widget):
            # Diagnostics intentionally preserves literal historical version
            # strings because they are useful when debugging old logs.
            if widget is getattr(self, "diagnostics_text", None):
                return
            try:
                if "text" in widget.keys():
                    current = widget.cget("text")
                    cleaned = self._clean_copy(str(current))
                    if cleaned != current:
                        widget.configure(text=cleaned)
            except Exception:
                pass
            if isinstance(widget, tk.Text):
                try:
                    current = widget.get("1.0", "end-1c")
                    cleaned = self._clean_copy(current)
                    if cleaned != current:
                        state = str(widget.cget("state"))
                        widget.configure(state="normal")
                        widget.delete("1.0", "end")
                        widget.insert("1.0", cleaned)
                        widget.configure(state=state)
                except Exception:
                    pass
            for child in widget.winfo_children():
                visit(child)
        visit(self)

    def _recalculate_context(self) -> None:
        super()._recalculate_context()
        self._normalise_visible_copy()

    def _diagnostic_text(self) -> str:
        text = super()._diagnostic_text()
        return f"Current application version: {VERSION}\n" + text


def main() -> None:
    try:
        V20App().mainloop()
    except Exception:
        LOGGER.exception("Fatal V2.0 application error")
        raise


if __name__ == "__main__":
    main()
