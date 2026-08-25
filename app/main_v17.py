from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional

import main as base_main
from main_v16 import V16App, LOGGER
from context_model import (
    FACTOR_LABELS,
    ContextInputs,
    adjusted_ev,
    context_adjustment_for_match,
    fpl_context_summary,
)
from decision_model import SimpleBetIdea, DutchIdea, build_bet_ideas, find_dutch_ideas
from engine import CombinedMatch, fmt_odds, fmt_pct, fmt_probability


GREEN_BG = "#E9F7EF"
GREEN_DARK = "#146C43"
AMBER_BG = "#FFF7E6"
AMBER_DARK = "#8A5A00"
BLUE_BG = "#EEF5FF"
BLUE_DARK = "#174EA6"
NEUTRAL_BG = "#F5F5F5"
TEXT_DARK = "#202124"


class V17App(V16App):
    """V1.7: decision-first dashboard with complex analysis kept underneath."""

    def _create_vars(self) -> None:
        super()._create_vars()
        self.best_pick_title_var = tk.StringVar(value="Fetch EPL odds to begin")
        self.best_pick_match_var = tk.StringVar(value="The model will show the clearest current theoretical edge here.")
        self.best_pick_price_var = tk.StringVar(value="—")
        self.best_pick_ev_var = tk.StringVar(value="—")
        self.best_pick_confidence_var = tk.StringVar(value="—")
        self.best_pick_reason_var = tk.StringVar(value="")
        self.dutch_headline_var = tk.StringVar(value="No Dutch analysis yet")
        self.dutch_summary_var = tk.StringVar(value="Fetch matches and the app will compare the best Sportsbet/Polymarket prices automatically.")
        self.dashboard_status_var = tk.StringVar(value="Waiting for market data")
        self._dashboard_best: Optional[SimpleBetIdea] = None
        self._dashboard_dutch: Optional[DutchIdea] = None

        self.context_easy_title_var = tk.StringVar(value="Select a match")
        self.context_easy_ev_var = tk.StringVar(value="—")
        self.context_easy_reason_var = tk.StringVar(value="Fetch odds, then select a match for a simple explanation.")
        self.context_easy_context_var = tk.StringVar(value="")

    # ------------------------------------------------------------------
    # Dashboard: deliberately simple
    # ------------------------------------------------------------------

    def _build_dashboard(self) -> None:
        intro = ttk.Frame(self.dashboard_tab)
        intro.pack(fill="x", pady=(0, 10))
        ttk.Label(intro, text="Current model view", font=("Segoe UI", 12, "bold")).pack(side="left")
        ttk.Label(
            intro,
            text="Complex market, handicap and team analysis runs underneath. This page only shows the clearest result.",
            style="Muted.TLabel",
        ).pack(side="right")

        self.best_pick_frame = tk.Frame(self.dashboard_tab, bg=NEUTRAL_BG, bd=1, relief="solid")
        self.best_pick_frame.pack(fill="x", pady=(0, 10))
        hero_left = tk.Frame(self.best_pick_frame, bg=NEUTRAL_BG)
        hero_left.pack(side="left", fill="both", expand=True, padx=18, pady=15)
        hero_right = tk.Frame(self.best_pick_frame, bg=NEUTRAL_BG)
        hero_right.pack(side="right", padx=20, pady=15)

        self.best_pick_kicker = tk.Label(
            hero_left, text="BEST CURRENT THEORETICAL EDGE", bg=NEUTRAL_BG, fg=TEXT_DARK,
            font=("Segoe UI", 9, "bold")
        )
        self.best_pick_kicker.pack(anchor="w")
        self.best_pick_title = tk.Label(
            hero_left, textvariable=self.best_pick_title_var, bg=NEUTRAL_BG, fg=TEXT_DARK,
            font=("Segoe UI", 22, "bold"), anchor="w"
        )
        self.best_pick_title.pack(anchor="w", pady=(4, 1))
        self.best_pick_match = tk.Label(
            hero_left, textvariable=self.best_pick_match_var, bg=NEUTRAL_BG, fg=TEXT_DARK,
            font=("Segoe UI", 11), anchor="w"
        )
        self.best_pick_match.pack(anchor="w")
        self.best_pick_reason = tk.Label(
            hero_left, textvariable=self.best_pick_reason_var, bg=NEUTRAL_BG, fg=TEXT_DARK,
            font=("Segoe UI", 10), wraplength=850, justify="left", anchor="w"
        )
        self.best_pick_reason.pack(anchor="w", pady=(9, 0))

        tk.Label(hero_right, text="Sportsbet price", bg=NEUTRAL_BG, fg=TEXT_DARK, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="e", padx=8)
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

        list_box = ttk.LabelFrame(middle, text="Other +EV options", padding=8)
        list_box.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.smart_top_tree = self._tree_in(
            list_box,
            [
                ("match", "Match", 230),
                ("pick", "Pick", 120),
                ("odds", "Odds", 65),
                ("ev", "EV", 70),
                ("confidence", "Confidence", 85),
                ("why", "Simple reason", 430),
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
        ttk.Button(bottom, text="Refresh all analysis", command=self.fetch_matches).pack(side="left")
        ttk.Button(bottom, text="See all candidates", command=lambda: self.notebook.select(self.candidates_tab)).pack(side="left", padx=8)
        ttk.Button(bottom, text="Open Edge Lab", command=lambda: self.notebook.select(self.edge_tab)).pack(side="left")
        ttk.Label(bottom, textvariable=self.dashboard_status_var, style="Muted.TLabel").pack(side="right")

    def _set_hero_colour(self, positive: bool) -> None:
        bg = GREEN_BG if positive else AMBER_BG
        fg = GREEN_DARK if positive else AMBER_DARK
        self.best_pick_frame.configure(bg=bg)
        for widget in (
            self.best_pick_frame.winfo_children()[0],
            self.best_pick_frame.winfo_children()[1],
        ):
            widget.configure(bg=bg)
            for child in widget.winfo_children():
                try:
                    child.configure(bg=bg)
                except tk.TclError:
                    pass
        for label in (self.best_pick_kicker, self.best_pick_title, self.best_pick_match, self.best_pick_reason, self.best_price_label, self.best_ev_label, self.best_conf_label):
            label.configure(bg=bg)
        self.best_pick_kicker.configure(fg=fg)
        self.best_ev_label.configure(fg=fg)

    def _fill_dashboard(self) -> None:
        if not hasattr(self, "smart_top_tree"):
            return
        min_ev = base_main.safe_float(self.min_ev_var.get(), self.settings.min_ev_pct)
        try:
            max_shift = max(0.0, min(3.0, float(self.context_max_shift_var.get())))
        except Exception:
            max_shift = 1.5

        ideas = build_bet_ideas(
            self.rows,
            fpl_context=self.fpl_context,
            context_inputs_by_key=self._context_inputs_by_key,
            max_context_shift_pp=max_shift,
            min_ev_pct=min_ev,
        )
        dutch = find_dutch_ideas(
            self.rows,
            fpl_context=self.fpl_context,
            context_inputs_by_key=self._context_inputs_by_key,
            max_context_shift_pp=max_shift,
        )

        for item in self.smart_top_tree.get_children():
            self.smart_top_tree.delete(item)
        for idx, idea in enumerate(ideas[:8]):
            self.smart_top_tree.insert("", "end", iid=f"smart-{idx}", values=(
                idea.match_name,
                idea.selection,
                f"${idea.sportsbet_odds:.2f}",
                f"{idea.decision_ev_pct:+.1f}%",
                idea.confidence.title(),
                idea.short_reason,
            ))

        self._dashboard_best = ideas[0] if ideas else None
        if self._dashboard_best is not None:
            idea = self._dashboard_best
            self.best_pick_title_var.set(idea.selection)
            self.best_pick_match_var.set(idea.match_name)
            self.best_pick_price_var.set(f"${idea.sportsbet_odds:.2f}")
            self.best_pick_ev_var.set(f"{idea.decision_ev_pct:+.1f}%")
            self.best_pick_confidence_var.set(idea.confidence.title())
            self.best_pick_reason_var.set(idea.short_reason)
            self.dashboard_status_var.set(f"{len(ideas)} option(s) currently clear the {min_ev:.1f}% model-EV threshold")
            self._set_hero_colour(True)
        else:
            self.best_pick_title_var.set("No +EV option found")
            self.best_pick_match_var.set("None of the current EPL prices clears the model threshold without relying on a negative base edge.")
            self.best_pick_price_var.set("—")
            self.best_pick_ev_var.set("PASS")
            self.best_pick_confidence_var.set("—")
            self.best_pick_reason_var.set("That is a valid result. The model does not force a pick when the available prices do not look good enough.")
            self.dashboard_status_var.set("No current recommendation")
            self._set_hero_colour(False)

        self._dashboard_dutch = dutch[0] if dutch else None
        if self._dashboard_dutch is None:
            self.dutch_headline_var.set("No useful Dutch option found")
            self.dutch_summary_var.set("The best current Sportsbet/Polymarket price combination does not produce a qualifying full-market arbitrage or positive-EV two-result Dutch.")
        else:
            d = self._dashboard_dutch
            if d.arbitrage:
                self.dutch_headline_var.set(f"Full-market arbitrage: {d.match_name}")
                self.dutch_summary_var.set(d.short_reason)
            else:
                self.dutch_headline_var.set(f"Model-based Dutch: {' + '.join(d.labels)}")
                self.dutch_summary_var.set(d.short_reason + " This is not risk-free because an uncovered result can lose the whole outlay.")

    def _open_best_context(self) -> None:
        idea = self._dashboard_best
        if idea is None:
            self.notebook.select(self.edge_tab)
            return
        self.context_match_var.set(idea.match_name)
        row = self._selected_context_row()
        if row is not None:
            self._context_active_key = self._context_key_for_row(row)
            self._load_inputs_to_ui(self._context_inputs_by_key.get(self._context_active_key, ContextInputs()))
            self._recalculate_context()
        self.notebook.select(self.context_tab)

    def _open_tree_pick(self, _event=None) -> None:
        selected = self.smart_top_tree.selection()
        if not selected:
            return
        values = self.smart_top_tree.item(selected[0], "values")
        if values:
            self.context_match_var.set(values[0])
            self.notebook.select(self.context_tab)
            self._context_match_changed()

    def _open_best_dutch(self) -> None:
        if self._dashboard_dutch is not None:
            self.dutch_match_var.set(self._dashboard_dutch.match_name)
            self._load_match_into_dutch("best")
        self.notebook.select(self.dutch_tab)

    # ------------------------------------------------------------------
    # Context Lab: simple first, advanced controls second
    # ------------------------------------------------------------------

    def _build_context_lab(self) -> None:
        top = ttk.Frame(self.context_tab)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Match").pack(side="left")
        self.context_match_combo = ttk.Combobox(top, textvariable=self.context_match_var, state="readonly", width=43)
        self.context_match_combo.pack(side="left", padx=(6, 14))
        self.context_match_combo.bind("<<ComboboxSelected>>", self._context_match_changed)
        ttk.Button(top, text="Refresh player availability", command=self._refresh_fpl_context).pack(side="left")
        ttk.Label(top, textvariable=self.fpl_status_var, style="Muted.TLabel").pack(side="left", padx=12)
        ttk.Label(top, text="Max context move").pack(side="right")
        ttk.Entry(top, textvariable=self.context_max_shift_var, width=6).pack(side="right", padx=(6, 4))

        context_book = ttk.Notebook(self.context_tab)
        context_book.pack(fill="both", expand=True)
        summary_tab = ttk.Frame(context_book, padding=12)
        research_tab = ttk.Frame(context_book, padding=12)
        technical_tab = ttk.Frame(context_book, padding=12)
        context_book.add(summary_tab, text="Simple summary")
        context_book.add(research_tab, text="Research inputs")
        context_book.add(technical_tab, text="Technical details")

        easy = tk.Frame(summary_tab, bg=BLUE_BG, bd=1, relief="solid")
        easy.pack(fill="x", pady=(0, 10))
        tk.Label(easy, text="SIMPLE MATCH VIEW", bg=BLUE_BG, fg=BLUE_DARK, font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Label(easy, textvariable=self.context_easy_title_var, bg=BLUE_BG, fg=TEXT_DARK, font=("Segoe UI", 18, "bold"), wraplength=1250, justify="left").pack(anchor="w", padx=16)
        tk.Label(easy, textvariable=self.context_easy_ev_var, bg=BLUE_BG, fg=BLUE_DARK, font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=16, pady=(4, 2))
        tk.Label(easy, textvariable=self.context_easy_reason_var, bg=BLUE_BG, fg=TEXT_DARK, font=("Segoe UI", 10), wraplength=1250, justify="left").pack(anchor="w", padx=16, pady=(2, 8))
        tk.Label(easy, textvariable=self.context_easy_context_var, bg=BLUE_BG, fg=TEXT_DARK, font=("Segoe UI", 9), wraplength=1250, justify="left").pack(anchor="w", padx=16, pady=(0, 12))

        compare = ttk.LabelFrame(summary_tab, text="What changed?", padding=8)
        compare.pack(fill="x", pady=(0, 10))
        self.context_tree = self._tree_in(
            compare,
            [
                ("outcome", "Outcome", 180),
                ("sb", "Sportsbet", 80),
                ("base", "Before context", 110),
                ("adjusted", "After context", 110),
                ("shift", "Change", 80),
                ("baseev", "Base EV", 85),
                ("contextev", "After-context EV", 120),
            ],
            height=4,
        )

        player_box = ttk.LabelFrame(summary_tab, text="Player availability — plain-English source check", padding=8)
        player_box.pack(fill="both", expand=True)
        self.context_fpl_text = tk.Text(player_box, wrap="word", height=8, font=("Segoe UI", 9))
        self.context_fpl_text.pack(fill="both", expand=True)
        self.context_fpl_text.insert("1.0", "Player availability will load automatically after a successful odds fetch.")
        self.context_fpl_text.configure(state="disabled")

        factors = ttk.LabelFrame(research_tab, text="Optional research ratings", padding=10)
        factors.pack(fill="x", pady=(0, 8))
        ttk.Label(
            factors,
            text="Use 0 when you have no extra evidence. + means an edge for the home team; − means an edge for the away team. These inputs can only make a small capped adjustment.",
            style="Muted.TLabel",
            wraplength=1200,
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        explanations = {
            "player_lineup": "Expected starters, important injuries/suspensions and position-by-position availability.",
            "recent_performance": "Underlying chance quality/form, not just whether the team won its last game.",
            "tactical_matchup": "Does one team's style naturally attack the other team's weakness?",
            "manager_coaching": "Evidence that the coaching plan/system gives one side an edge. Avoid tiny manager H2H samples.",
            "transfer_squad": "Did the squad actually improve and fit together? Spending money alone is not an edge.",
            "schedule_rest": "Rest, travel and fixture congestion.",
        }
        for idx, key in enumerate(FACTOR_LABELS, start=1):
            ttk.Label(factors, text=FACTOR_LABELS[key], width=30).grid(row=idx, column=0, sticky="w", pady=5)
            spin = ttk.Spinbox(factors, from_=-3.0, to=3.0, increment=0.25, textvariable=self.context_factor_vars[key], width=7, command=self._recalculate_context)
            spin.grid(row=idx, column=1, sticky="w", padx=(6, 10), pady=5)
            spin.bind("<KeyRelease>", lambda _e: self._recalculate_context())
            ttk.Label(factors, text=explanations[key], style="Muted.TLabel", wraplength=700, justify="left").grid(row=idx, column=2, sticky="w", pady=5)

        meta = ttk.LabelFrame(research_tab, text="Evidence notes", padding=10)
        meta.pack(fill="x", pady=(0, 8))
        ttk.Label(meta, text="Home transfer spend (£m)").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(meta, textvariable=self.context_home_spend_var, width=11).grid(row=0, column=1, sticky="w", padx=(6, 18))
        ttk.Label(meta, text="Away transfer spend (£m)").grid(row=0, column=2, sticky="w", pady=4)
        ttk.Entry(meta, textvariable=self.context_away_spend_var, width=11).grid(row=0, column=3, sticky="w", padx=6)
        ttk.Label(meta, text="Home manager").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(meta, textvariable=self.context_home_manager_var, width=24).grid(row=1, column=1, sticky="w", padx=(6, 18))
        ttk.Label(meta, text="Away manager").grid(row=1, column=2, sticky="w", pady=4)
        ttk.Entry(meta, textvariable=self.context_away_manager_var, width=24).grid(row=1, column=3, sticky="w", padx=6)
        ttk.Label(meta, text="Why did you give these ratings?").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(meta, textvariable=self.context_notes_var, width=86).grid(row=2, column=1, columnspan=3, sticky="ew", padx=6)
        meta.columnconfigure(3, weight=1)

        buttons = ttk.Frame(research_tab)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Recalculate", command=self._recalculate_context).pack(side="left")
        ttk.Button(buttons, text="Save research snapshot", command=self._save_current_context).pack(side="left", padx=8)
        ttk.Button(buttons, text="Reset ratings", command=self._reset_context_ratings).pack(side="left")

        ttk.Label(
            technical_tab,
            text="This tab keeps the detailed explanation for checking the maths. Most users can stay on Simple summary.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 6))
        self.context_summary_text = tk.Text(technical_tab, wrap="word", height=24, font=("Segoe UI", 9))
        self.context_summary_text.pack(fill="both", expand=True)
        self.context_summary_text.insert("1.0", "Fetch matches and select a fixture to see the technical context calculation.")
        self.context_summary_text.configure(state="disabled")

    def _recalculate_context(self) -> None:
        super()._recalculate_context()
        self._update_easy_context_summary()
        if hasattr(self, "smart_top_tree"):
            self._fill_dashboard()

    def _update_easy_context_summary(self) -> None:
        row = self._selected_context_row()
        if row is None:
            return
        inputs = self._inputs_from_ui()
        try:
            max_shift = max(0.0, min(3.0, float(self.context_max_shift_var.get())))
        except Exception:
            max_shift = 1.5
        adjustment = context_adjustment_for_match(row, inputs, self.fpl_context, max_shift_pp=max_shift)
        if adjustment is None:
            self.context_easy_title_var.set("Not enough market data")
            self.context_easy_ev_var.set("No calculation available")
            self.context_easy_reason_var.set("The app needs an independent fair probability before it can add team context.")
            self.context_easy_context_var.set("")
            return

        evs = adjusted_ev(row, adjustment)
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
            self.context_easy_ev_var.set(f"About {best_ev:+.1f}% theoretical EV after context")
            reason = (
                f"Before team context, the market model estimated {base_ev:+.1f}% EV. "
                f"The context layer changes this outcome by {shift:+.2f} probability points. "
                "Because the base market view was already positive, the context is only nudging the answer rather than creating it from scratch."
            )
        else:
            self.context_easy_title_var.set("No +EV option from this match")
            self.context_easy_ev_var.set(f"Best after-context EV: {fmt_pct(best_ev)}")
            reason = (
                f"The best-looking outcome is {names[best_side]} at {fmt_odds(odds)}, but it still does not clear the model threshold safely. "
                f"Its base market EV is {fmt_pct(base_ev)} and the context change is {shift:+.2f} probability points. "
                "The model therefore says PASS rather than forcing a pick."
            )
        self.context_easy_reason_var.set(reason)

        auto = adjustment.auto_availability_rating
        if abs(auto) < 0.35:
            context_text = "Player availability is close enough that it barely changes the match estimate."
        elif auto > 0:
            context_text = f"Automatic player availability gives a small lean towards {row.home_team}."
        else:
            context_text = f"Automatic player availability gives a small lean towards {row.away_team}."
        if any(abs(getattr(inputs, key)) > 0.01 for key in FACTOR_LABELS):
            context_text += " Your saved research ratings are also included, but the total probability movement is capped."
        else:
            context_text += " No manual research ratings are currently adding extra weight."
        self.context_easy_context_var.set(context_text)

    def _apply_fpl_context(self, contexts, error):
        super()._apply_fpl_context(contexts, error)
        if hasattr(self, "smart_top_tree"):
            self._fill_dashboard()

    def _apply_fetch_result_v15(self, rows, warnings, info_notes, saved, context_saved, edge_saved) -> None:
        super()._apply_fetch_result_v15(rows, warnings, info_notes, saved, context_saved, edge_saved)
        if hasattr(self, "smart_top_tree"):
            self._fill_dashboard()

    def _diagnostic_text(self) -> str:
        return "V1.7 UI mode: decision-first dashboard + simplified Context Lab\n" + super()._diagnostic_text()


def main() -> None:
    try:
        V17App().mainloop()
    except Exception:
        LOGGER.exception("Fatal V1.7 application error")
        raise


if __name__ == "__main__":
    main()
