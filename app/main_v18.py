from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from typing import Optional

import main as base_main
from main_v17 import (
    V17App,
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
from context_model import ContextInputs, FACTOR_LABELS, adjusted_ev, fpl_context_summary
from decision_model_v18 import V18BetIdea, V18DutchIdea, build_bet_ideas_v18, find_dutch_ideas_v18
from engine import CombinedMatch, fmt_odds, fmt_pct, fmt_probability
from football_intelligence import (
    IntelligenceBundle,
    MatchIntelligence,
    context_adjustment_v18,
    intelligence_plain_summary,
    merge_context_inputs,
    refresh_football_intelligence,
)
from research_validation import (
    backfill_recommendations_from_edge_history,
    save_recommendations,
    sync_results,
    validation_rows,
    validation_summary,
)


class V18App(V17App):
    """V1.8: free football-intelligence layer plus closing-line validation."""

    def _create_vars(self) -> None:
        super()._create_vars()
        self.football_bundle: Optional[IntelligenceBundle] = None
        self.football_status_var = tk.StringVar(value="Football intelligence not loaded yet")
        self.intelligence_match_var = tk.StringVar(value="")
        self.intelligence_headline_var = tk.StringVar(value="Select a match")
        self.intelligence_summary_var = tk.StringVar(value="Expected XI, recent xG-style performance and tactical profiles will appear here.")
        self.intelligence_home_var = tk.StringVar(value="—")
        self.intelligence_away_var = tk.StringVar(value="—")
        self.validation_status_var = tk.StringVar(value="Validation will populate as stored recommendations settle.")
        self.validation_recs_var = tk.StringVar(value="0")
        self.validation_settled_var = tk.StringVar(value="0")
        self.validation_roi_var = tk.StringVar(value="—")
        self.validation_clv_var = tk.StringVar(value="—")
        self.validation_positive_clv_var = tk.StringVar(value="—")
        self.validation_brier_var = tk.StringVar(value="—")
        self._intelligence_loading = False
        self._dashboard_best: Optional[V18BetIdea] = None
        self._dashboard_dutch: Optional[V18DutchIdea] = None

    def _build_tabs(self) -> None:
        super()._build_tabs()
        self.intelligence_tab = ttk.Frame(self.notebook, padding=12)
        self.validation_tab = ttk.Frame(self.notebook, padding=12)
        # Dashboard, Matches, Candidates, Edge Lab, Context Lab,
        # Football Model, Dutch Calculator, Validation, History, Settings...
        self.notebook.insert(5, self.intelligence_tab, text="Football Model")
        self.notebook.insert(7, self.validation_tab, text="Validation")
        self._build_intelligence_tab()
        self._build_validation_tab()

    # ------------------------------------------------------------------
    # V1.8 dashboard
    # ------------------------------------------------------------------

    def _current_intelligence(self) -> dict[str, MatchIntelligence]:
        return self.football_bundle.matches if self.football_bundle else {}

    def _max_context_shift(self) -> float:
        try:
            return max(0.0, min(3.0, float(self.context_max_shift_var.get())))
        except Exception:
            return 1.5

    def _build_current_ideas(self) -> list[V18BetIdea]:
        min_ev = base_main.safe_float(self.min_ev_var.get(), self.settings.min_ev_pct)
        return build_bet_ideas_v18(
            self.rows,
            fpl_context=self.fpl_context,
            context_inputs_by_key=self._context_inputs_by_key,
            intelligence_by_match=self._current_intelligence(),
            max_context_shift_pp=self._max_context_shift(),
            min_ev_pct=min_ev,
        )

    def _build_current_dutch(self) -> list[V18DutchIdea]:
        return find_dutch_ideas_v18(
            self.rows,
            fpl_context=self.fpl_context,
            context_inputs_by_key=self._context_inputs_by_key,
            intelligence_by_match=self._current_intelligence(),
            max_context_shift_pp=self._max_context_shift(),
        )

    def _fill_dashboard(self) -> None:
        if not hasattr(self, "smart_top_tree"):
            return
        min_ev = base_main.safe_float(self.min_ev_var.get(), self.settings.min_ev_pct)
        ideas = self._build_current_ideas()
        dutch = self._build_current_dutch()

        for item in self.smart_top_tree.get_children():
            self.smart_top_tree.delete(item)
        for idx, idea in enumerate(ideas[:8]):
            quality = getattr(idea, "football_quality", "LOW").title()
            why = idea.short_reason
            if len(why) > 155:
                why = why[:152] + "…"
            self.smart_top_tree.insert("", "end", iid=f"smart-{idx}", values=(
                idea.match_name,
                idea.selection,
                f"${idea.sportsbet_odds:.2f}",
                f"{idea.decision_ev_pct:+.1f}%",
                f"{idea.confidence.title()} / {quality} data",
                why,
            ))

        self._dashboard_best = ideas[0] if ideas else None
        if self._dashboard_best is not None:
            idea = self._dashboard_best
            self.best_pick_title_var.set(idea.selection)
            self.best_pick_match_var.set(idea.match_name)
            self.best_pick_price_var.set(f"${idea.sportsbet_odds:.2f}")
            self.best_pick_ev_var.set(f"{idea.decision_ev_pct:+.1f}%")
            self.best_pick_confidence_var.set(f"{idea.confidence.title()} · football data {idea.football_quality.title()}")
            self.best_pick_reason_var.set(idea.short_reason)
            data_state = "football model loaded" if self.football_bundle else "market model only — football data still loading"
            self.dashboard_status_var.set(f"{len(ideas)} option(s) clear {min_ev:.1f}% · {data_state}")
            self._set_hero_colour(True)
        else:
            self.best_pick_title_var.set("No +EV option found")
            self.best_pick_match_var.set("None of the current EPL prices clears the model threshold with a non-negative independent-market base edge.")
            self.best_pick_price_var.set("—")
            self.best_pick_ev_var.set("PASS")
            self.best_pick_confidence_var.set("—")
            if self._intelligence_loading:
                self.best_pick_reason_var.set("The market layer has no qualifying pick right now. Football intelligence is still loading and may only make a small capped adjustment.")
            else:
                self.best_pick_reason_var.set("That is a valid result. The model does not force a selection when the available price is not good enough.")
            self.dashboard_status_var.set("No current recommendation")
            self._set_hero_colour(False)

        self._dashboard_dutch = dutch[0] if dutch else None
        if self._dashboard_dutch is None:
            self.dutch_headline_var.set("No useful Dutch option found")
            self.dutch_summary_var.set("The best current Sportsbet/Polymarket prices do not produce a qualifying arbitrage or positive-model-EV two-result Dutch.")
        else:
            d = self._dashboard_dutch
            if d.arbitrage:
                self.dutch_headline_var.set(f"Full-market arbitrage: {d.match_name}")
                self.dutch_summary_var.set(d.short_reason)
            else:
                self.dutch_headline_var.set(f"Model-based Dutch: {' + '.join(d.labels)}")
                self.dutch_summary_var.set(d.short_reason)

    # ------------------------------------------------------------------
    # Football intelligence data refresh
    # ------------------------------------------------------------------

    def _refresh_football_data(self) -> None:
        if self._intelligence_loading or not self.rows:
            return
        self._intelligence_loading = True
        self.football_status_var.set("Loading free player, xG and tactical data…")
        self._fill_dashboard()
        threading.Thread(target=self._football_worker, daemon=True).start()

    def _football_worker(self) -> None:
        try:
            bundle = refresh_football_intelligence(self.rows)
            self.after(0, lambda: self._apply_football_bundle(bundle, None))
        except Exception as exc:
            LOGGER.exception("V1.8 football-intelligence refresh failed")
            self.after(0, lambda: self._apply_football_bundle(None, str(exc)))

    def _apply_football_bundle(self, bundle: Optional[IntelligenceBundle], error: Optional[str]) -> None:
        self._intelligence_loading = False
        if error or bundle is None:
            self.football_status_var.set("Football intelligence unavailable — market model still works")
            self._fill_dashboard()
            return

        self.football_bundle = bundle
        quality_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for item in bundle.matches.values():
            quality_counts[item.data_quality] = quality_counts.get(item.data_quality, 0) + 1
        self.football_status_var.set(
            f"Football model loaded: {len(bundle.teams)} teams · "
            f"{quality_counts.get('HIGH', 0)} high / {quality_counts.get('MEDIUM', 0)} medium / {quality_counts.get('LOW', 0)} low-data match(es)"
        )

        names = [row.match_name for row in self.rows]
        if hasattr(self, "intelligence_match_combo"):
            self.intelligence_match_combo["values"] = names
            if names and self.intelligence_match_var.get() not in names:
                self.intelligence_match_var.set(names[0])

        try:
            sync_results(bundle.league_matches)
            backfill_recommendations_from_edge_history(
                base_main.safe_float(self.min_ev_var.get(), self.settings.min_ev_pct)
            )
        except Exception:
            LOGGER.exception("V1.8 result/history sync failed")

        ideas = self._build_current_ideas()
        try:
            save_recommendations(self.rows, ideas, bundle.matches)
        except Exception:
            LOGGER.exception("V1.8 recommendation persistence failed")

        self._fill_dashboard()
        self._refresh_intelligence_view()
        self._refresh_validation_view()
        self._recalculate_context()

    def _apply_fetch_result_v15(self, rows, warnings, info_notes, saved, context_saved, edge_saved) -> None:
        super()._apply_fetch_result_v15(rows, warnings, info_notes, saved, context_saved, edge_saved)
        self.football_bundle = None
        self._refresh_football_data()

    def _apply_fpl_context(self, contexts, error):
        # V1.7 already refreshes the dashboard; V1.8's override of
        # _fill_dashboard automatically uses the richer model when available.
        super()._apply_fpl_context(contexts, error)

    # ------------------------------------------------------------------
    # Context Lab now uses V1.8 automatic inputs
    # ------------------------------------------------------------------

    def _recalculate_context(self) -> None:
        row = self._selected_context_row()
        if row is None or not hasattr(self, "context_tree"):
            return
        manual = self._inputs_from_ui()
        intel = self._current_intelligence().get(row.match_name)
        adjustment = context_adjustment_v18(
            row,
            manual,
            self.fpl_context,
            intel,
            max_shift_pp=self._max_context_shift(),
        )

        for item in self.context_tree.get_children():
            self.context_tree.delete(item)
        self.context_fpl_text.configure(state="normal")
        self.context_fpl_text.delete("1.0", "end")
        source_text = fpl_context_summary(row, self.fpl_context) if self.fpl_context else "FPL availability has not loaded."
        source_text += "\n\nV1.8 AUTOMATIC FOOTBALL MODEL\n" + intelligence_plain_summary(intel)
        self.context_fpl_text.insert("1.0", source_text)
        self.context_fpl_text.configure(state="disabled")

        if adjustment is None:
            self._set_context_summary("No independent base probability is available, so V1.8 context cannot be calculated.")
            self.context_easy_title_var.set("Not enough market data")
            self.context_easy_ev_var.set("No calculation available")
            self.context_easy_reason_var.set("The app needs the independent market probability first.")
            self.context_easy_context_var.set(intelligence_plain_summary(intel))
            return

        evs = adjusted_ev(row, adjustment)
        edges = getattr(row, "edge_outcomes", {})
        names = {"HOME": row.home_team, "DRAW": "Draw", "AWAY": row.away_team}
        probs = {
            "HOME": (getattr(row, "model_fair_home", None), adjustment.home_probability, adjustment.home_shift_pp, row.sb_home),
            "DRAW": (getattr(row, "model_fair_draw", None), adjustment.draw_probability, adjustment.draw_shift_pp, row.sb_draw),
            "AWAY": (getattr(row, "model_fair_away", None), adjustment.away_probability, adjustment.away_shift_pp, row.sb_away),
        }
        for side in ("HOME", "DRAW", "AWAY"):
            base_p, adj_p, shift, odds = probs[side]
            edge = edges.get(side)
            base_ev = getattr(edge, "model_ev_pct", None) if edge else None
            self.context_tree.insert("", "end", values=(
                names[side], fmt_odds(odds), fmt_probability(base_p), fmt_probability(adj_p),
                fmt_pct(shift), fmt_pct(base_ev), fmt_pct(evs.get(side)),
            ))

        combined = merge_context_inputs(manual, intel)
        factor_lines = [
            f"• {FACTOR_LABELS[key]}: combined rating {getattr(combined, key):+.2f}; weighted contribution {contribution:+.3f}"
            for key, contribution in adjustment.factor_breakdown.items()
        ]
        auto_lines = [
            f"Expected-XI rating: {getattr(intel, 'xi_rating', 0.0):+.2f}",
            f"Recent underlying-performance rating: {getattr(intel, 'recent_form_rating', 0.0):+.2f}",
            f"Tactical/style rating: {getattr(intel, 'tactical_rating', 0.0):+.2f}",
            f"Rest/schedule rating: {getattr(intel, 'rest_rating', 0.0):+.2f}",
        ] if intel else ["Automatic football intelligence not available."]
        text = (
            f"{row.match_name}\n\n"
            "V1.8 starts with the independent market probability. It then adds a small capped football layer.\n\n"
            "AUTOMATIC FOOTBALL INPUTS\n" + "\n".join(auto_lines) + "\n\n"
            + intelligence_plain_summary(intel) + "\n\n"
            f"Automatic FPL availability rating: {adjustment.auto_availability_rating:+.2f}\n"
            f"Final combined context score: {adjustment.weighted_score:+.3f}\n"
            f"Maximum allowed probability movement: {adjustment.max_shift_pp:.2f} percentage points\n\n"
            "FACTOR BREAKDOWN\n" + "\n".join(factor_lines) +
            "\n\nThe cap is deliberate. A team-data story is not allowed to overpower a negative independent-market price signal."
        )
        self._set_context_summary(text)
        self._update_easy_context_summary_v18(row, manual, intel, adjustment, evs)
        if hasattr(self, "smart_top_tree"):
            self._fill_dashboard()

    def _update_easy_context_summary_v18(self, row, manual, intel, adjustment, evs) -> None:
        names = {"HOME": row.home_team, "DRAW": "Draw", "AWAY": row.away_team}
        best_side = max(evs, key=lambda s: evs[s] if evs[s] is not None else -9999)
        best_ev = evs.get(best_side)
        odds = {"HOME": row.sb_home, "DRAW": row.sb_draw, "AWAY": row.sb_away}[best_side]
        base_edge = getattr(row, "edge_outcomes", {}).get(best_side)
        base_ev = getattr(base_edge, "model_ev_pct", None) if base_edge else None
        shift = {"HOME": adjustment.home_shift_pp, "DRAW": adjustment.draw_shift_pp, "AWAY": adjustment.away_shift_pp}[best_side]
        min_ev = base_main.safe_float(self.min_ev_var.get(), self.settings.min_ev_pct)

        if best_ev is not None and base_ev is not None and best_ev >= min_ev and base_ev >= 0:
            self.context_easy_title_var.set(f"Best option here: {names[best_side]} at {fmt_odds(odds)}")
            self.context_easy_ev_var.set(f"About {best_ev:+.1f}% theoretical EV")
            self.context_easy_reason_var.set(
                f"The independent market model already had this at {base_ev:+.1f}% EV. "
                f"Expected XI, recent performance, tactics, rest and availability move the probability by only {shift:+.2f} points. "
                "So the football data is confirming or trimming the market idea, not inventing it."
            )
        else:
            self.context_easy_title_var.set("No +EV option from this match")
            self.context_easy_ev_var.set(f"Best after all analysis: {fmt_pct(best_ev)}")
            self.context_easy_reason_var.set(
                f"The best-looking outcome is {names[best_side]} at {fmt_odds(odds)}, but its independent-market EV is {fmt_pct(base_ev)} "
                f"and the football layer only changes the probability by {shift:+.2f} points. The model therefore says PASS."
            )
        self.context_easy_context_var.set(intelligence_plain_summary(intel))

    # ------------------------------------------------------------------
    # Football Model tab
    # ------------------------------------------------------------------

    def _build_intelligence_tab(self) -> None:
        top = ttk.Frame(self.intelligence_tab)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Match").pack(side="left")
        self.intelligence_match_combo = ttk.Combobox(top, textvariable=self.intelligence_match_var, state="readonly", width=44)
        self.intelligence_match_combo.pack(side="left", padx=(6, 12))
        self.intelligence_match_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_intelligence_view())
        ttk.Button(top, text="Refresh football data", command=self._refresh_football_data).pack(side="left")
        ttk.Label(top, textvariable=self.football_status_var, style="Muted.TLabel").pack(side="left", padx=12)

        hero = tk.Frame(self.intelligence_tab, bg=BLUE_BG, bd=1, relief="solid")
        hero.pack(fill="x", pady=(0, 10))
        tk.Label(hero, text="FOOTBALL MODEL — SIMPLE VIEW", bg=BLUE_BG, fg=BLUE_DARK, font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Label(hero, textvariable=self.intelligence_headline_var, bg=BLUE_BG, fg=TEXT_DARK, font=("Segoe UI", 17, "bold"), wraplength=1250, justify="left").pack(anchor="w", padx=16)
        tk.Label(hero, textvariable=self.intelligence_summary_var, bg=BLUE_BG, fg=TEXT_DARK, font=("Segoe UI", 10), wraplength=1300, justify="left").pack(anchor="w", padx=16, pady=(6, 12))

        teams = ttk.Frame(self.intelligence_tab)
        teams.pack(fill="x", pady=(0, 8))
        teams.columnconfigure(0, weight=1)
        teams.columnconfigure(1, weight=1)
        h = ttk.LabelFrame(teams, text="Home team", padding=10)
        a = ttk.LabelFrame(teams, text="Away team", padding=10)
        h.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        a.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        ttk.Label(h, textvariable=self.intelligence_home_var, justify="left", wraplength=630).pack(anchor="w")
        ttk.Label(a, textvariable=self.intelligence_away_var, justify="left", wraplength=630).pack(anchor="w")

        book = ttk.Notebook(self.intelligence_tab)
        book.pack(fill="both", expand=True)
        xi_tab = ttk.Frame(book, padding=8)
        form_tab = ttk.Frame(book, padding=8)
        book.add(xi_tab, text="Expected XI")
        book.add(form_tab, text="Recent performance")

        self.xi_tree = self._tree_in(
            xi_tab,
            [
                ("team", "Team", 150),
                ("pos", "Pos", 55),
                ("player", "Expected player", 180),
                ("start", "Start chance", 90),
                ("strength", "Strength proxy", 95),
                ("starts", "Recent starts", 85),
                ("rating", "Avg rating", 80),
                ("availability", "Availability", 85),
                ("note", "Availability note", 420),
            ],
            height=15,
        )
        self.form_tree = self._tree_in(
            form_tab,
            [
                ("team", "Team", 150),
                ("venue", "Venue", 55),
                ("opponent", "Opponent", 150),
                ("score", "Score", 65),
                ("xg", "xG", 70),
                ("shots", "Shots", 70),
                ("sot", "SOT", 65),
                ("big", "Big chances", 95),
                ("poss", "Possession", 90),
                ("corners", "Corners", 75),
                ("formation", "Formation", 85),
            ],
            height=12,
        )

    def _selected_intelligence(self) -> Optional[MatchIntelligence]:
        if not self.football_bundle:
            return None
        return self.football_bundle.matches.get(self.intelligence_match_var.get())

    def _team_summary_text(self, team) -> str:
        if team is None:
            return "No automatic data available."
        t = team.tactical
        pos = ", ".join(t.labels) if t.labels else "balanced"
        positions = " / ".join(f"{k} {v:.0f}" for k, v in team.position_strength.items()) or "—"
        return (
            f"Expected XI strength: {team.xi_strength:.1f}\n" if team.xi_strength is not None else "Expected XI strength: —\n"
        ) + (
            f"Likely recent formation: {team.latest_formation or '—'}\n"
            f"Recent xG / xGA: {('—' if t.xg is None else f'{t.xg:.2f}')} / {('—' if t.xga is None else f'{t.xga:.2f}')}\n"
            f"Shots / SOT: {('—' if t.shots is None else f'{t.shots:.1f}')} / {('—' if t.shots_on_target is None else f'{t.shots_on_target:.1f}')}\n"
            f"Possession: {('—' if t.possession is None else f'{t.possession:.1f}%')}\n"
            f"Position strength: {positions}\n"
            f"Style: {pos}\n"
            f"Data quality: {team.data_quality.title()}"
        )

    def _refresh_intelligence_view(self) -> None:
        if not hasattr(self, "xi_tree"):
            return
        intel = self._selected_intelligence()
        for tree in (self.xi_tree, self.form_tree):
            for item in tree.get_children():
                tree.delete(item)
        if intel is None:
            self.intelligence_headline_var.set("Football intelligence not available yet")
            self.intelligence_summary_var.set("Fetch EPL odds and let the background football-data refresh finish. The normal market model remains usable if the free source is unavailable.")
            self.intelligence_home_var.set("—")
            self.intelligence_away_var.set("—")
            return

        if intel.overall_rating > 0.35:
            lean = intel.home_team
        elif intel.overall_rating < -0.35:
            lean = intel.away_team
        else:
            lean = "Neither side strongly"
        self.intelligence_headline_var.set(f"Automatic football data: {lean} favoured")
        self.intelligence_summary_var.set(
            intelligence_plain_summary(intel)
            + " These ratings are not a separate prediction model; they only create a small capped adjustment to the independent market probability."
        )
        self.intelligence_home_var.set(self._team_summary_text(intel.home))
        self.intelligence_away_var.set(self._team_summary_text(intel.away))

        for team in (intel.home, intel.away):
            if team is None:
                continue
            for player in team.expected_xi:
                self.xi_tree.insert("", "end", values=(
                    team.team, player.position, player.name,
                    f"{player.start_probability * 100:.0f}%",
                    f"{player.strength:.1f}", player.recent_starts,
                    "—" if player.average_rating is None else f"{player.average_rating:.2f}",
                    f"{player.availability * 100:.0f}%", player.note or "—",
                ))
            for match in team.recent_matches:
                score = f"{int(match.goals_for)}-{int(match.goals_against)}"
                xg = "—" if match.xg_for is None or match.xg_against is None else f"{match.xg_for:.2f}-{match.xg_against:.2f}"
                shots = "—" if match.shots_for is None or match.shots_against is None else f"{match.shots_for:.0f}-{match.shots_against:.0f}"
                sot = "—" if match.shots_on_target_for is None or match.shots_on_target_against is None else f"{match.shots_on_target_for:.0f}-{match.shots_on_target_against:.0f}"
                big = "—" if match.big_chances_for is None or match.big_chances_against is None else f"{match.big_chances_for:.0f}-{match.big_chances_against:.0f}"
                poss = "—" if match.possession_for is None else f"{match.possession_for:.0f}%"
                corners = "—" if match.corners_for is None or match.corners_against is None else f"{match.corners_for:.0f}-{match.corners_against:.0f}"
                self.form_tree.insert("", "end", values=(
                    team.team, match.venue, match.opponent, score, xg, shots, sot, big, poss, corners, match.formation or "—",
                ))

    # ------------------------------------------------------------------
    # Validation tab
    # ------------------------------------------------------------------

    def _build_validation_tab(self) -> None:
        intro = ttk.LabelFrame(self.validation_tab, text="Does the model actually hold up?", padding=12)
        intro.pack(fill="x", pady=(0, 8))
        ttk.Label(
            intro,
            text=(
                "This page turns the project into a testable model. A recommendation is saved the first time it appears. After the match, V1.8 records the result and compares the original Sportsbet price with the last price we observed before kick-off. "
                "Positive CLV means the market later moved towards our original view. That is useful evidence even before the sample is large enough to judge profit reliably."
            ),
            wraplength=1360,
            justify="left",
        ).pack(anchor="w")

        cards = ttk.Frame(self.validation_tab)
        cards.pack(fill="x", pady=(0, 8))
        vars_and_titles = [
            ("Recommendations", self.validation_recs_var),
            ("Settled", self.validation_settled_var),
            ("Flat-stake ROI", self.validation_roi_var),
            ("Average CLV", self.validation_clv_var),
            ("Positive CLV", self.validation_positive_clv_var),
            ("Brier score", self.validation_brier_var),
        ]
        for i, (title, var) in enumerate(vars_and_titles):
            cards.columnconfigure(i, weight=1)
            box = ttk.LabelFrame(cards, text=title, padding=10)
            box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 5, 0))
            ttk.Label(box, textvariable=var, font=("Segoe UI", 15, "bold")).pack(anchor="w")

        controls = ttk.Frame(self.validation_tab)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Button(controls, text="Refresh validation", command=self._refresh_validation_view).pack(side="left")
        ttk.Button(controls, text="Refresh football/results data", command=self._refresh_football_data).pack(side="left", padx=8)
        ttk.Label(controls, textvariable=self.validation_status_var, style="Muted.TLabel").pack(side="right")

        self.validation_tree = self._make_tree(
            self.validation_tab,
            [
                ("kickoff", "Kick-off", 120),
                ("match", "Match", 210),
                ("pick", "Pick", 120),
                ("first", "Flagged odds", 82),
                ("close", "Last pre-KO", 82),
                ("clv", "CLV", 65),
                ("quality", "Price timing", 90),
                ("ev", "Model EV", 72),
                ("result", "Result", 95),
                ("pl", "Flat P/L", 72),
                ("football", "Football data", 88),
            ],
            height=20,
        )

    def _refresh_validation_view(self) -> None:
        if not hasattr(self, "validation_tree"):
            return
        try:
            rows = validation_rows(1000)
            summary = validation_summary(rows)
        except Exception as exc:
            LOGGER.exception("Validation view failed")
            self.validation_status_var.set(f"Validation unavailable: {exc}")
            return
        self.validation_recs_var.set(str(summary.recommendations))
        self.validation_settled_var.set(str(summary.settled))
        self.validation_roi_var.set("—" if summary.roi_pct is None else f"{summary.roi_pct:+.1f}%")
        self.validation_clv_var.set("—" if summary.average_clv_pct is None else f"{summary.average_clv_pct:+.2f}%")
        self.validation_positive_clv_var.set("—" if summary.positive_clv_pct is None else f"{summary.positive_clv_pct:.0f}%")
        self.validation_brier_var.set("—" if summary.binary_brier is None else f"{summary.binary_brier:.3f}")
        self.validation_status_var.set(
            f"{summary.close_quality_count} settled recommendation(s) have a close/near-close price. "
            "Treat very small samples as descriptive only."
        )
        for item in self.validation_tree.get_children():
            self.validation_tree.delete(item)
        for row in rows:
            kickoff = row.kickoff[:16].replace("T", " ") if row.kickoff else "—"
            self.validation_tree.insert("", "end", values=(
                kickoff,
                row.match_name,
                row.selection,
                f"{row.first_odds:.2f}",
                "—" if row.closing_odds is None else f"{row.closing_odds:.2f}",
                "—" if row.clv_pct is None else f"{row.clv_pct:+.2f}%",
                row.close_quality,
                "—" if row.decision_ev_pct is None else f"{row.decision_ev_pct:+.1f}%",
                row.result,
                "—" if row.realised_profit_pct is None else f"{row.realised_profit_pct:+.0f}%",
                row.intelligence_quality.title() if row.intelligence_quality else "—",
            ))

    def _diagnostic_text(self) -> str:
        bundle = self.football_bundle
        intelligence = "not loaded" if bundle is None else f"{len(bundle.teams)} teams / {len(bundle.matches)} matches"
        return (
            f"V1.8 football intelligence: {intelligence}\n"
            "Free sources: FPL player feed + FotMob public web JSON (cached/rate-limited, optional)\n"
            "Validation: first recommendation + last observed pre-kickoff price + result tracking\n"
            + super()._diagnostic_text()
        )


def main() -> None:
    try:
        app = V18App()
        app.after(1000, app._refresh_validation_view)
        app.mainloop()
    except Exception:
        LOGGER.exception("Fatal V1.8 application error")
        raise


if __name__ == "__main__":
    main()
