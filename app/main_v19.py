from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from typing import Optional

import main as base_main
from main_v18 import V18App, LOGGER, GREEN_BG, GREEN_DARK, AMBER_BG, AMBER_DARK, NEUTRAL_BG, TEXT_DARK
from config import APP_NAME, VERSION
from engine import CombinedMatch, fmt_odds, fmt_pct, fmt_probability
from edge_model import enrich_edge_model
from edge_storage import save_edge_snapshot
from market_storage import save_market_context
from multileague_data import (
    LeagueInfo,
    combine_sportsbet_catalogue,
    enrich_multileague_market_context,
    fetch_multileague_sources,
    sportsbet_league_counts,
)
from football_intelligence import refresh_football_intelligence
from research_validation import (
    backfill_recommendations_from_edge_history,
    save_recommendations,
    sync_results,
)


class V19App(V18App):
    """V1.9: dynamic Sportsbet soccer catalogue across every offered league."""

    def _create_vars(self) -> None:
        super()._create_vars()
        self.best_pick_title_var.set("Fetch football odds to begin")
        self.best_pick_match_var.set("The model will scan every current Sportsbet soccer league and show the best available theoretical edge here.")
        self.dutch_summary_var.set("Fetch matches and the app will compare Sportsbet/Polymarket prices automatically where both are available.")
        self.league_status_var = tk.StringVar(value="Sportsbet league catalogue not loaded yet")
        self.league_count_var = tk.StringVar(value="0")
        self.match_count_var = tk.StringVar(value="0")
        self.external_match_var = tk.StringVar(value="0")
        self.api_request_var = tk.StringVar(value="0")
        self._sportsbet_leagues: list[LeagueInfo] = []
        self._last_api_request_count = 0

    def _build_header(self) -> None:
        header = ttk.Frame(self, padding=(18, 14, 18, 8))
        header.pack(fill="x")
        left = ttk.Frame(header)
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text="Football Value Betting", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            left,
            text="Multi-league Sportsbet soccer market comparison, value screening and research capture",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))
        right = ttk.Frame(header)
        right.pack(side="right")
        ttk.Label(right, textvariable=self.update_banner_var, style="Muted.TLabel").pack(side="left", padx=(0, 10))
        ttk.Button(right, text="Check for updates", command=self.check_for_updates).pack(side="left")

    def _build_tabs(self) -> None:
        super()._build_tabs()
        self.leagues_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.insert(1, self.leagues_tab, text="Leagues")
        self._build_leagues_tab()

    def _build_leagues_tab(self) -> None:
        intro = ttk.LabelFrame(self.leagues_tab, text="Sportsbet soccer coverage", padding=12)
        intro.pack(fill="x", pady=(0, 10))
        ttk.Label(
            intro,
            text=(
                "V1.9 asks PulseScore for Sportsbet's current soccer league catalogue first. "
                "Only competitions Sportsbet currently lists are eligible for analysis. The app then fetches Sportsbet's current pre-match soccer events and uses Polymarket/Pinnacle only as external reference markets where matching fixtures exist."
            ),
            wraplength=1360,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(intro, textvariable=self.league_status_var, style="Muted.TLabel").pack(anchor="w", pady=(8, 0))

        cards = ttk.Frame(self.leagues_tab)
        cards.pack(fill="x", pady=(0, 10))
        for i in range(4):
            cards.columnconfigure(i, weight=1)
        base_main.SummaryCard(cards, "Sportsbet leagues", self.league_count_var, "Current PulseScore catalogue").grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        base_main.SummaryCard(cards, "Sportsbet matches", self.match_count_var, "Selected date range").grid(row=0, column=1, sticky="nsew", padx=6)
        base_main.SummaryCard(cards, "External matches", self.external_match_var, "PM/Pinnacle references matched").grid(row=0, column=2, sticky="nsew", padx=6)
        base_main.SummaryCard(cards, "API requests", self.api_request_var, "This refresh; cache can reduce this").grid(row=0, column=3, sticky="nsew", padx=(6, 0))

        frame = ttk.Frame(self.leagues_tab)
        frame.pack(fill="both", expand=True)
        columns = ("league", "matches", "pm", "pin", "model")
        self.leagues_tree = ttk.Treeview(frame, columns=columns, show="headings", height=24)
        for key, title, width in (
            ("league", "Sportsbet league", 420),
            ("matches", "Matches in range", 130),
            ("pm", "Polymarket matched", 140),
            ("pin", "Pinnacle matched", 130),
            ("model", "Model available", 120),
        ):
            self.leagues_tree.heading(key, text=title)
            self.leagues_tree.column(key, width=width, anchor="w" if key == "league" else "center")
        vs = ttk.Scrollbar(frame, orient="vertical", command=self.leagues_tree.yview)
        self.leagues_tree.configure(yscrollcommand=vs.set)
        self.leagues_tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")

    @staticmethod
    def _league_label(row: CombinedMatch) -> str:
        country = str(getattr(row, "country", "") or "").strip()
        league = str(getattr(row, "league", "") or "Unknown league").strip()
        return f"{country} · {league}" if country else league

    # ------------------------------------------------------------------
    # Multi-league fetch
    # ------------------------------------------------------------------

    def _fetch_worker(self, api_key, start, end, min_ev) -> None:
        warnings: list[str] = []
        info_notes: list[str] = []
        try:
            source = fetch_multileague_sources(api_key, start, end)
            self._sportsbet_leagues = list(source["leagues"])
            self._last_api_request_count = int(source.get("request_count", 0) or 0)

            sb_bundle = source["sportsbet"]
            pm_bundle = source["polymarket"]
            pin_bundle = source["pinnacle"]

            rows = combine_sportsbet_catalogue(sb_bundle.matches, pm_bundle.matches, min_ev)
            rows = enrich_multileague_market_context(rows, sb_bundle.raw_events, pin_bundle.raw_events)
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

            matched_pm = sum(1 for r in rows if r.pm_home is not None)
            matched_pin = sum(1 for r in rows if getattr(r, "pin_home", None) is not None)
            info_notes.append(
                f"Sportsbet catalogue: {len(self._sportsbet_leagues)} soccer league name(s); "
                f"{len(rows)} Sportsbet fixture(s) in range; Polymarket matched {matched_pm}; Pinnacle matched {matched_pin}."
            )
            info_notes.append(
                f"PulseScore requests this refresh: {self._last_api_request_count}. Responses are cached for a few minutes to protect the free allowance."
            )

            saved = context_saved = edge_saved = 0
            if self.save_snapshots_var.get() and rows:
                saved = base_main.save_snapshot(rows)
                context_saved = save_market_context(rows)
                edge_saved = save_edge_snapshot(rows)
                LOGGER.info("Saved %d base, %d context and %d edge rows", saved, context_saved, edge_saved)

            self.after(
                0,
                lambda: self._apply_multileague_result(
                    rows, warnings, info_notes, saved, context_saved, edge_saved
                ),
            )
        except Exception as exc:
            LOGGER.exception("V1.9 multi-league fetch failed")
            self.after(0, lambda: self._fatal_fetch_error(exc))

    def _apply_multileague_result(self, rows, warnings, info_notes, saved, context_saved, edge_saved) -> None:
        # V1.8's result handling populates all existing tabs and starts the
        # football-intelligence refresh. Our overridden refresh method limits
        # the FPL/xG layer to the EPL while every league still receives the
        # unchanged market model.
        super()._apply_fetch_result_v15(rows, warnings, info_notes, saved, context_saved, edge_saved)
        self._fill_league_table()
        self.status_var.set(
            f"Loaded {len(rows)} Sportsbet soccer fixture(s) across {len(sportsbet_league_counts(rows))} league(s) in the selected date range"
        )

    def _fill_league_table(self) -> None:
        if not hasattr(self, "leagues_tree"):
            return
        for item in self.leagues_tree.get_children():
            self.leagues_tree.delete(item)

        counts = sportsbet_league_counts(self.rows)
        total_pm = total_pin = model_count = 0
        for league, count in counts.items():
            league_rows = [r for r in self.rows if str(getattr(r, "league", "Unknown league") or "Unknown league") == league]
            pm = sum(1 for r in league_rows if r.pm_home is not None)
            pin = sum(1 for r in league_rows if getattr(r, "pin_home", None) is not None)
            model = sum(1 for r in league_rows if getattr(r, "model_fair_home", None) is not None)
            total_pm += pm
            total_pin += pin
            model_count += model
            self.leagues_tree.insert("", "end", values=(league, count, pm, pin, model))

        self.league_count_var.set(str(len(self._sportsbet_leagues)))
        self.match_count_var.set(str(len(self.rows)))
        self.external_match_var.set(f"PM {total_pm} / PIN {total_pin}")
        self.api_request_var.set(str(self._last_api_request_count))
        self.league_status_var.set(
            f"Sportsbet eligibility check passed. {len(counts)} league(s) have at least one complete 1X2 fixture in your current date range."
        )

    # ------------------------------------------------------------------
    # Keep automatic football intelligence EPL-specific for now.
    # ------------------------------------------------------------------

    def _epl_rows(self) -> list[CombinedMatch]:
        result = []
        for row in self.rows:
            league = base_main.safe_float("", 0.0)  # harmless sentinel to keep this method dependency-light
            del league
            league_name = str(getattr(row, "league", "") or "").lower()
            country = str(getattr(row, "country", "") or "").lower()
            if "premier league" in league_name and (not country or "england" in country or "united kingdom" in country):
                result.append(row)
        return result

    def _refresh_football_data(self) -> None:
        if self._intelligence_loading or not self.rows:
            return
        epl_rows = self._epl_rows()
        if not epl_rows:
            self.football_bundle = None
            self.football_status_var.set("Football intelligence is currently EPL-specific; other leagues use the unchanged multi-market model.")
            self._fill_dashboard()
            return
        self._intelligence_loading = True
        self.football_status_var.set(
            f"Loading the EPL player/xG/tactical layer for {len(epl_rows)} match(es). Other leagues remain market-model only."
        )
        self._fill_dashboard()
        threading.Thread(target=self._football_worker_v19, args=(epl_rows,), daemon=True).start()

    def _football_worker_v19(self, epl_rows: list[CombinedMatch]) -> None:
        try:
            bundle = refresh_football_intelligence(epl_rows)
            self.after(0, lambda: self._apply_football_bundle_v19(bundle, None))
        except Exception as exc:
            LOGGER.exception("V1.9 EPL football-intelligence refresh failed")
            self.after(0, lambda: self._apply_football_bundle_v19(None, str(exc)))

    def _apply_football_bundle_v19(self, bundle, error) -> None:
        self._intelligence_loading = False
        if error or bundle is None:
            self.football_status_var.set("EPL football intelligence unavailable — all market models still work")
            self._fill_dashboard()
            return
        self.football_bundle = bundle
        self.football_status_var.set(
            f"EPL football model loaded for {len(bundle.matches)} match(es). Non-EPL leagues use market data only in V1.9."
        )
        try:
            sync_results(bundle.league_matches)
            backfill_recommendations_from_edge_history(base_main.safe_float(self.min_ev_var.get(), self.settings.min_ev_pct))
            save_recommendations(self.rows, self._build_current_ideas(), bundle.matches)
        except Exception:
            LOGGER.exception("V1.9 EPL validation sync failed")
        self._fill_dashboard()
        self._refresh_intelligence_view()
        self._refresh_validation_view()
        self._recalculate_context()

    # ------------------------------------------------------------------
    # Dashboard: always show the highest EV, even when it is negative.
    # ------------------------------------------------------------------

    def _highest_available_edge(self):
        best = None
        for row in self.rows:
            for side, edge in getattr(row, "edge_outcomes", {}).items():
                ev = getattr(edge, "model_ev_pct", None)
                odds = getattr(edge, "sportsbet_odds", None)
                if ev is None or odds is None:
                    continue
                if best is None or float(ev) > float(best[2].model_ev_pct):
                    best = (row, side, edge)
        return best

    def _fill_dashboard(self) -> None:
        super()._fill_dashboard()

        # Add league names to the qualifying shortlist without changing the
        # model/ranking inherited from V1.8.
        if hasattr(self, "smart_top_tree"):
            ideas = self._build_current_ideas()
            for item in self.smart_top_tree.get_children():
                self.smart_top_tree.delete(item)
            row_by_match = {r.match_name: r for r in self.rows}
            for idx, idea in enumerate(ideas[:8]):
                row = row_by_match.get(idea.match_name)
                prefix = f"[{self._league_label(row)}] " if row else ""
                why = idea.short_reason if len(idea.short_reason) <= 155 else idea.short_reason[:152] + "…"
                self.smart_top_tree.insert("", "end", iid=f"smart-{idx}", values=(
                    prefix + idea.match_name,
                    idea.selection,
                    f"${idea.sportsbet_odds:.2f}",
                    f"{idea.decision_ev_pct:+.1f}%",
                    f"{idea.confidence.title()} / {idea.football_quality.title()} data",
                    why,
                ))

        # If V1.8 found a qualifying recommendation, simply make its league
        # explicit. Otherwise show the highest EV option rather than a blank.
        if self._dashboard_best is not None:
            row = next((r for r in self.rows if r.match_name == self._dashboard_best.match_name), None)
            if row is not None:
                self.best_pick_match_var.set(f"{self._league_label(row)} · {row.match_name}")
            return

        fallback = self._highest_available_edge()
        if fallback is None:
            self.best_pick_title_var.set("No priced model comparison available")
            self.best_pick_match_var.set("Sportsbet fixtures were found, but no external fair probability could be built for the current rows.")
            self.best_pick_price_var.set("—")
            self.best_pick_ev_var.set("—")
            self.best_pick_confidence_var.set("—")
            self.best_pick_reason_var.set("Try a wider date range or inspect the Leagues tab to see where Polymarket/Pinnacle reference coverage exists.")
            self.dashboard_status_var.set("No comparable market yet")
            self._set_hero_colour(False)
            return

        row, side, edge = fallback
        ev = float(edge.model_ev_pct)
        fair = getattr(edge, "model_probability", None)
        break_even = getattr(edge, "break_even_probability", None)
        selection = getattr(edge, "name", side.title())
        confidence = str(getattr(edge, "confidence", "LOW") or "LOW").title()

        if ev > 0:
            self.best_pick_title_var.set(f"{selection} — positive EV, below threshold")
            label = "Positive EV but below your recommendation threshold"
        else:
            self.best_pick_title_var.set(f"{selection} — highest EV available")
            label = "Still negative EV — shown because it is the least negative option"

        self.best_pick_match_var.set(f"{self._league_label(row)} · {row.match_name}")
        self.best_pick_price_var.set(f"${float(edge.sportsbet_odds):.2f}")
        self.best_pick_ev_var.set(f"{ev:+.1f}%")
        self.best_pick_confidence_var.set(confidence)
        if fair is not None and break_even is not None:
            self.best_pick_reason_var.set(
                f"{label}. Sportsbet's price needs about {break_even * 100:.1f}% to break even; "
                f"the independent model estimates about {fair * 100:.1f}%. That works out to {ev:+.1f}% model EV."
            )
        else:
            self.best_pick_reason_var.set(f"{label}. It is displayed for comparison, not as a +EV recommendation.")
        self.dashboard_status_var.set("No option clears the recommendation threshold; showing the highest EV available")
        self._set_hero_colour(False)


def main() -> None:
    try:
        V19App().mainloop()
    except Exception:
        LOGGER.exception("Fatal V1.9 application error")
        raise


if __name__ == "__main__":
    main()
