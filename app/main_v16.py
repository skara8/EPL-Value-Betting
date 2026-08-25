from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

import main as base_main
from main_v15_final import V15FinalApp, LOGGER
from engine import CombinedMatch, fmt_odds, fmt_pct, fmt_probability
from context_model import (
    FACTOR_LABELS,
    ContextInputs,
    FPLTeamContext,
    adjusted_ev,
    context_adjustment_for_match,
    fetch_fpl_team_context,
    fpl_context_summary,
)
from context_storage import context_snapshot_count, save_context_snapshot
from dutch_calc import (
    DEFAULT_POLYMARKET_SPORTS_TAKER_FEE_RATE,
    DutchSelection,
    calculate_dutch,
    polymarket_effective_decimal_odds,
)


class V16App(V15FinalApp):
    """V1.6: contextual research layer plus Dutch/arbitrage calculator."""

    def _create_vars(self) -> None:
        super()._create_vars()
        self.context_match_var = tk.StringVar(value="")
        self.context_max_shift_var = tk.StringVar(value="1.50")
        self.context_home_spend_var = tk.StringVar(value="")
        self.context_away_spend_var = tk.StringVar(value="")
        self.context_home_manager_var = tk.StringVar(value="")
        self.context_away_manager_var = tk.StringVar(value="")
        self.context_notes_var = tk.StringVar(value="")
        self.context_factor_vars = {key: tk.DoubleVar(value=0.0) for key in FACTOR_LABELS}
        self._context_active_key: Optional[str] = None
        self._context_inputs_by_key: dict[str, ContextInputs] = {}
        self.fpl_context: dict[str, FPLTeamContext] = {}
        self.fpl_status_var = tk.StringVar(value="FPL availability data not loaded")

        self.dutch_match_var = tk.StringVar(value="")
        self.dutch_total_stake_var = tk.StringVar(value="100.00")
        self.dutch_complete_market_var = tk.BooleanVar(value=True)
        self.dutch_pm_order_var = tk.StringVar(value="Taker")
        self.dutch_fee_rate_var = tk.StringVar(value=f"{DEFAULT_POLYMARKET_SPORTS_TAKER_FEE_RATE * 100:.2f}")
        self.dutch_status_var = tk.StringVar(value="Enter at least two selections, then calculate.")
        self._dutch_rows: list[dict[str, object]] = []

    def _build_tabs(self) -> None:
        super()._build_tabs()
        self.context_tab = ttk.Frame(self.notebook, padding=12)
        self.dutch_tab = ttk.Frame(self.notebook, padding=12)
        # Place research/context next to Edge Lab, and Dutch as a separate tool.
        self.notebook.insert(4, self.context_tab, text="Context Lab")
        self.notebook.insert(5, self.dutch_tab, text="Dutch Calculator")
        self._build_context_lab()
        self._build_dutch_calculator()

    # ------------------------------------------------------------------
    # Context Lab
    # ------------------------------------------------------------------

    def _build_context_lab(self) -> None:
        intro = ttk.LabelFrame(self.context_tab, text="Experimental contextual probability layer", padding=12)
        intro.pack(fill="x", pady=(0, 8))
        ttk.Label(
            intro,
            text=(
                "V1.6 keeps the V1.5 external-market fair probability as the baseline. Context is shown as a SECOND estimate. "
                "The contextual layer is deliberately capped so subjective research cannot manufacture a large edge. Positive ratings favour the HOME team; negative ratings favour the AWAY team. "
                "Transfer spend is recorded but does not move probability unless you separately rate whether the squad change actually improves the matchup."
            ),
            wraplength=1380,
            justify="left",
        ).pack(anchor="w")

        controls = ttk.Frame(self.context_tab)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Label(controls, text="Match").pack(side="left")
        self.context_match_combo = ttk.Combobox(controls, textvariable=self.context_match_var, state="readonly", width=43)
        self.context_match_combo.pack(side="left", padx=(6, 14))
        self.context_match_combo.bind("<<ComboboxSelected>>", self._context_match_changed)
        ttk.Label(controls, text="Max context shift (pp)").pack(side="left")
        ttk.Entry(controls, textvariable=self.context_max_shift_var, width=7).pack(side="left", padx=(6, 14))
        ttk.Button(controls, text="Refresh FPL player availability", command=self._refresh_fpl_context).pack(side="left")
        ttk.Label(controls, textvariable=self.fpl_status_var, style="Muted.TLabel").pack(side="left", padx=12)

        split = ttk.Panedwindow(self.context_tab, orient="horizontal")
        split.pack(fill="both", expand=True)
        left = ttk.Frame(split)
        right = ttk.Frame(split)
        split.add(left, weight=2)
        split.add(right, weight=3)

        factors = ttk.LabelFrame(left, text="Research factors — rating from -3 (away edge) to +3 (home edge)", padding=10)
        factors.pack(fill="x", pady=(0, 8))
        explanations = {
            "player_lineup": "Automatic FPL availability is added to your manual line-up judgement. Highest-priority context factor.",
            "recent_performance": "Underlying form rather than raw wins: xG/xGA, chance quality, shot quality, etc. Manual in V1.6.",
            "tactical_matchup": "Pressing/build-up/transition/set-piece style compatibility. Use matchup evidence, not generic 'better tactics'.",
            "manager_coaching": "Manager/coaching fit and demonstrated implementation. Raw manager H2H should not drive this by itself.",
            "transfer_squad": "Rate actual squad improvement/cohesion, not pounds spent. High turnover can also increase uncertainty.",
            "schedule_rest": "Rest, travel and congestion. Evidence is mixed, so this has the smallest research weight.",
        }
        for row_idx, key in enumerate(FACTOR_LABELS):
            ttk.Label(factors, text=FACTOR_LABELS[key], width=31).grid(row=row_idx, column=0, sticky="w", pady=5)
            spin = ttk.Spinbox(
                factors, from_=-3.0, to=3.0, increment=0.25,
                textvariable=self.context_factor_vars[key], width=7,
                command=self._recalculate_context,
            )
            spin.grid(row=row_idx, column=1, sticky="w", padx=(6, 10), pady=5)
            spin.bind("<KeyRelease>", lambda _e: self._recalculate_context())
            ttk.Label(factors, text=explanations[key], style="Muted.TLabel", wraplength=510, justify="left").grid(
                row=row_idx, column=2, sticky="w", pady=5
            )

        meta = ttk.LabelFrame(left, text="Record the evidence behind the ratings", padding=10)
        meta.pack(fill="x", pady=(0, 8))
        ttk.Label(meta, text="Home transfer spend (£m)").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(meta, textvariable=self.context_home_spend_var, width=11).grid(row=0, column=1, sticky="w", padx=(6, 18))
        ttk.Label(meta, text="Away transfer spend (£m)").grid(row=0, column=2, sticky="w", pady=4)
        ttk.Entry(meta, textvariable=self.context_away_spend_var, width=11).grid(row=0, column=3, sticky="w", padx=6)
        ttk.Label(meta, text="Home manager").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(meta, textvariable=self.context_home_manager_var, width=24).grid(row=1, column=1, sticky="w", padx=(6, 18))
        ttk.Label(meta, text="Away manager").grid(row=1, column=2, sticky="w", pady=4)
        ttk.Entry(meta, textvariable=self.context_away_manager_var, width=24).grid(row=1, column=3, sticky="w", padx=6)
        ttk.Label(meta, text="Evidence notes").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(meta, textvariable=self.context_notes_var, width=78).grid(row=2, column=1, columnspan=3, sticky="ew", padx=6)
        meta.columnconfigure(3, weight=1)

        buttons = ttk.Frame(left)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Recalculate", command=self._recalculate_context).pack(side="left")
        ttk.Button(buttons, text="Save research snapshot", command=self._save_current_context).pack(side="left", padx=8)
        ttk.Button(buttons, text="Reset ratings", command=self._reset_context_ratings).pack(side="left")

        fpl_box = ttk.LabelFrame(right, text="Automatic player availability context", padding=8)
        fpl_box.pack(fill="both", expand=False, pady=(0, 8))
        self.context_fpl_text = tk.Text(fpl_box, wrap="word", height=10, font=("Segoe UI", 9))
        self.context_fpl_text.pack(fill="both", expand=True)
        self.context_fpl_text.insert("1.0", "Click Refresh FPL player availability, or fetch EPL odds to load it automatically.")
        self.context_fpl_text.configure(state="disabled")

        calc_box = ttk.LabelFrame(right, text="Base model vs context-adjusted model", padding=8)
        calc_box.pack(fill="both", expand=True)
        self.context_tree = self._tree_in(
            calc_box,
            [
                ("outcome", "Outcome", 150),
                ("sb", "SB odds", 70),
                ("base", "Base fair", 78),
                ("adjusted", "Context fair", 88),
                ("shift", "Shift pp", 72),
                ("baseev", "Base EV", 74),
                ("contextev", "Context EV", 84),
            ],
            height=5,
        )
        self.context_summary_text = tk.Text(calc_box, wrap="word", height=11, font=("Segoe UI", 9))
        self.context_summary_text.pack(fill="both", expand=True, pady=(8, 0))
        self.context_summary_text.insert("1.0", "Fetch matches, select a fixture and enter contextual research ratings.")
        self.context_summary_text.configure(state="disabled")

    def _context_key_for_row(self, row: CombinedMatch) -> str:
        return f"{row.kickoff.isoformat()}|{row.home_team}|{row.away_team}"

    def _selected_context_row(self) -> Optional[CombinedMatch]:
        name = self.context_match_var.get()
        return next((row for row in self.rows if row.match_name == name), None)

    def _selected_dutch_row(self) -> Optional[CombinedMatch]:
        name = self.dutch_match_var.get()
        return next((row for row in self.rows if row.match_name == name), None)

    def _inputs_from_ui(self) -> ContextInputs:
        def n(var: tk.StringVar) -> Optional[float]:
            text = var.get().strip()
            if not text:
                return None
            try:
                return float(text)
            except ValueError:
                return None
        return ContextInputs(
            player_lineup=float(self.context_factor_vars["player_lineup"].get()),
            recent_performance=float(self.context_factor_vars["recent_performance"].get()),
            tactical_matchup=float(self.context_factor_vars["tactical_matchup"].get()),
            manager_coaching=float(self.context_factor_vars["manager_coaching"].get()),
            transfer_squad=float(self.context_factor_vars["transfer_squad"].get()),
            schedule_rest=float(self.context_factor_vars["schedule_rest"].get()),
            home_transfer_spend_m=n(self.context_home_spend_var),
            away_transfer_spend_m=n(self.context_away_spend_var),
            home_manager=self.context_home_manager_var.get().strip(),
            away_manager=self.context_away_manager_var.get().strip(),
            notes=self.context_notes_var.get().strip(),
        )

    def _save_active_context_ui(self) -> None:
        if self._context_active_key:
            self._context_inputs_by_key[self._context_active_key] = self._inputs_from_ui()

    def _load_inputs_to_ui(self, inputs: ContextInputs) -> None:
        for key in FACTOR_LABELS:
            self.context_factor_vars[key].set(getattr(inputs, key))
        self.context_home_spend_var.set("" if inputs.home_transfer_spend_m is None else str(inputs.home_transfer_spend_m))
        self.context_away_spend_var.set("" if inputs.away_transfer_spend_m is None else str(inputs.away_transfer_spend_m))
        self.context_home_manager_var.set(inputs.home_manager)
        self.context_away_manager_var.set(inputs.away_manager)
        self.context_notes_var.set(inputs.notes)

    def _context_match_changed(self, _event=None) -> None:
        self._save_active_context_ui()
        row = self._selected_context_row()
        if row is None:
            return
        key = self._context_key_for_row(row)
        self._context_active_key = key
        self._load_inputs_to_ui(self._context_inputs_by_key.get(key, ContextInputs()))
        self._recalculate_context()

    def _reset_context_ratings(self) -> None:
        for var in self.context_factor_vars.values():
            var.set(0.0)
        self.context_home_spend_var.set("")
        self.context_away_spend_var.set("")
        self.context_home_manager_var.set("")
        self.context_away_manager_var.set("")
        self.context_notes_var.set("")
        self._recalculate_context()

    def _refresh_fpl_context(self) -> None:
        self.fpl_status_var.set("Loading FPL availability…")
        threading.Thread(target=self._fpl_worker, daemon=True).start()

    def _fpl_worker(self) -> None:
        try:
            contexts = fetch_fpl_team_context()
            self.after(0, lambda: self._apply_fpl_context(contexts, None))
        except Exception as exc:
            LOGGER.exception("FPL context fetch failed")
            self.after(0, lambda: self._apply_fpl_context({}, str(exc)))

    def _apply_fpl_context(self, contexts: dict[str, FPLTeamContext], error: Optional[str]) -> None:
        if error:
            self.fpl_status_var.set("FPL availability unavailable")
        else:
            self.fpl_context = contexts
            self.fpl_status_var.set(f"FPL availability loaded for {len(contexts)} EPL teams")
        self._recalculate_context()

    def _recalculate_context(self) -> None:
        row = self._selected_context_row()
        if row is None or not hasattr(self, "context_tree"):
            return
        inputs = self._inputs_from_ui()
        try:
            max_shift = max(0.0, min(3.0, float(self.context_max_shift_var.get())))
        except ValueError:
            max_shift = 1.5
        adjustment = context_adjustment_for_match(
            row, inputs, self.fpl_context, max_shift_pp=max_shift
        )
        for item in self.context_tree.get_children():
            self.context_tree.delete(item)

        self.context_fpl_text.configure(state="normal")
        self.context_fpl_text.delete("1.0", "end")
        if self.fpl_context:
            self.context_fpl_text.insert("1.0", fpl_context_summary(row, self.fpl_context))
        else:
            self.context_fpl_text.insert("1.0", "FPL availability data has not been loaded. Manual context ratings still work.")
        self.context_fpl_text.configure(state="disabled")

        if adjustment is None:
            self._set_context_summary("No V1.5 independent fair probability is available for this fixture, so context EV cannot be calculated.")
            return

        evs = adjusted_ev(row, adjustment)
        base_edges = getattr(row, "edge_outcomes", {})
        names = {"HOME": row.home_team, "DRAW": "Draw", "AWAY": row.away_team}
        probs = {
            "HOME": (getattr(row, "model_fair_home", None), adjustment.home_probability, adjustment.home_shift_pp, row.sb_home),
            "DRAW": (getattr(row, "model_fair_draw", None), adjustment.draw_probability, adjustment.draw_shift_pp, row.sb_draw),
            "AWAY": (getattr(row, "model_fair_away", None), adjustment.away_probability, adjustment.away_shift_pp, row.sb_away),
        }
        for side in ("HOME", "DRAW", "AWAY"):
            base_p, adj_p, shift, odds = probs[side]
            base_ev = base_edges.get(side).model_ev_pct if side in base_edges else None
            self.context_tree.insert("", "end", values=(
                names[side], fmt_odds(odds), fmt_probability(base_p), fmt_probability(adj_p),
                fmt_pct(shift), fmt_pct(base_ev), fmt_pct(evs.get(side)),
            ))

        auto = adjustment.auto_availability_rating
        factor_lines = []
        for key, contribution in adjustment.factor_breakdown.items():
            factor_lines.append(f"• {FACTOR_LABELS[key]}: weighted contribution {contribution:+.3f}")
        best_side = max(evs, key=lambda side: evs[side] if evs[side] is not None else -9999)
        base_best = getattr(row, "edge_best_selection", "—")
        text = (
            f"{row.match_name}\n\n"
            f"Context score: {adjustment.weighted_score:+.3f} on a roughly -3 to +3 scale. Positive favours {row.home_team}; negative favours {row.away_team}.\n"
            f"Automatic FPL availability rating: {auto:+.2f}.\n"
            f"Maximum permitted probability movement: {adjustment.max_shift_pp:.2f} percentage points.\n\n"
            f"Base V1.5 best outcome: {base_best} at {fmt_pct(getattr(row, 'edge_best_ev_pct', None))} model EV.\n"
            f"Experimental context best outcome: {names[best_side]} at {fmt_pct(evs.get(best_side))} context EV.\n\n"
            "Factor contribution breakdown:\n" + "\n".join(factor_lines) +
            "\n\nInterpretation: this is a sensitivity analysis, not a proven probability correction. If a contextual factor consistently improves closing-line value and out-of-sample calibration, a later version can learn its coefficient from data instead of using these small research weights."
        )
        self._set_context_summary(text)

    def _set_context_summary(self, text: str) -> None:
        self.context_summary_text.configure(state="normal")
        self.context_summary_text.delete("1.0", "end")
        self.context_summary_text.insert("1.0", text)
        self.context_summary_text.configure(state="disabled")

    def _save_current_context(self) -> None:
        row = self._selected_context_row()
        if row is None:
            messagebox.showinfo("Context Lab", "Select a match first.")
            return
        inputs = self._inputs_from_ui()
        try:
            max_shift = max(0.0, min(3.0, float(self.context_max_shift_var.get())))
        except ValueError:
            max_shift = 1.5
        adjustment = context_adjustment_for_match(row, inputs, self.fpl_context, max_shift_pp=max_shift)
        if adjustment is None:
            messagebox.showwarning("Context Lab", "This match does not have a V1.5 base fair probability yet.")
            return
        evs = adjusted_ev(row, adjustment)
        save_context_snapshot(row, inputs, adjustment, evs)
        self._context_inputs_by_key[self._context_key_for_row(row)] = inputs
        messagebox.showinfo("Context saved", f"Saved the contextual research snapshot. Total saved: {context_snapshot_count()}")

    # ------------------------------------------------------------------
    # Dutch calculator
    # ------------------------------------------------------------------

    def _build_dutch_calculator(self) -> None:
        intro = ttk.LabelFrame(self.dutch_tab, text="Equal-return Dutch calculator", padding=12)
        intro.pack(fill="x", pady=(0, 8))
        ttk.Label(
            intro,
            text=(
                "Dutch betting splits a total stake across multiple mutually exclusive selections so each selected winner returns approximately the same amount. "
                "If the selected outcomes cover every possible result and the sum of inverse effective odds is below 1.00, the combination is an arbitrage. "
                "If it is above 1.00, equalising the return locks in a loss. Always make sure Sportsbet and Polymarket use the same settlement period/rules."
            ),
            wraplength=1380,
            justify="left",
        ).pack(anchor="w")

        controls = ttk.Frame(self.dutch_tab)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Label(controls, text="Match").pack(side="left")
        self.dutch_match_combo = ttk.Combobox(controls, textvariable=self.dutch_match_var, state="readonly", width=42)
        self.dutch_match_combo.pack(side="left", padx=(6, 12))
        ttk.Button(controls, text="Load Sportsbet H/D/A", command=lambda: self._load_match_into_dutch("sportsbet")).pack(side="left")
        ttk.Button(controls, text="Load Polymarket H/D/A", command=lambda: self._load_match_into_dutch("polymarket")).pack(side="left", padx=6)
        ttk.Button(controls, text="Load best H/D/A", command=lambda: self._load_match_into_dutch("best")).pack(side="left")

        settings = ttk.LabelFrame(self.dutch_tab, text="Dutch settings", padding=10)
        settings.pack(fill="x", pady=(0, 8))
        ttk.Label(settings, text="Total outlay").pack(side="left")
        ttk.Entry(settings, textvariable=self.dutch_total_stake_var, width=11).pack(side="left", padx=(6, 14))
        ttk.Checkbutton(settings, text="Selections cover all possible outcomes", variable=self.dutch_complete_market_var).pack(side="left")
        ttk.Label(settings, text="Polymarket order").pack(side="left", padx=(18, 5))
        ttk.Combobox(settings, textvariable=self.dutch_pm_order_var, values=("Taker", "Maker"), state="readonly", width=8).pack(side="left")
        ttk.Label(settings, text="PM sports feeRate %").pack(side="left", padx=(18, 5))
        ttk.Entry(settings, textvariable=self.dutch_fee_rate_var, width=7).pack(side="left")
        ttk.Button(settings, text="Calculate Dutch", command=self._calculate_dutch_ui).pack(side="right")

        rows_box = ttk.LabelFrame(self.dutch_tab, text="Selections — Sportsbet/Other uses decimal odds; Polymarket accepts 0.60 or 60 for 60¢", padding=10)
        rows_box.pack(fill="x", pady=(0, 8))
        headers = ("Use", "Selection", "Source", "Odds / PM price", "Effective odds")
        for col, text in enumerate(headers):
            ttk.Label(rows_box, text=text, font=("Segoe UI", 9, "bold")).grid(row=0, column=col, sticky="w", padx=4, pady=(0, 5))
        rows_box.columnconfigure(1, weight=1)
        for idx in range(6):
            use = tk.BooleanVar(value=idx < 3)
            label = tk.StringVar(value=("Home", "Draw", "Away", "Selection 4", "Selection 5", "Selection 6")[idx])
            source = tk.StringVar(value="Sportsbet")
            value = tk.StringVar(value="")
            effective = tk.StringVar(value="—")
            ttk.Checkbutton(rows_box, variable=use).grid(row=idx + 1, column=0, padx=4, pady=3)
            ttk.Entry(rows_box, textvariable=label, width=30).grid(row=idx + 1, column=1, sticky="ew", padx=4, pady=3)
            ttk.Combobox(rows_box, textvariable=source, values=("Sportsbet", "Polymarket", "Other"), state="readonly", width=12).grid(row=idx + 1, column=2, padx=4, pady=3)
            ttk.Entry(rows_box, textvariable=value, width=15).grid(row=idx + 1, column=3, padx=4, pady=3)
            ttk.Label(rows_box, textvariable=effective, width=14).grid(row=idx + 1, column=4, sticky="w", padx=4, pady=3)
            self._dutch_rows.append({"use": use, "label": label, "source": source, "value": value, "effective": effective})

        results = ttk.LabelFrame(self.dutch_tab, text="Dutch result", padding=8)
        results.pack(fill="both", expand=True)
        self.dutch_tree = self._tree_in(
            results,
            [
                ("selection", "Selection", 220),
                ("source", "Source", 100),
                ("input", "Input", 90),
                ("odds", "Effective odds", 90),
                ("stake", "Stake", 90),
                ("return", "Return if wins", 105),
                ("profit", "Net P/L", 90),
            ],
            height=8,
        )
        ttk.Label(results, textvariable=self.dutch_status_var, font=("Segoe UI", 10, "bold"), wraplength=1350, justify="left").pack(anchor="w", pady=(8, 0))

    def _parse_pm_price(self, text: str) -> float:
        value = float(text.strip().replace("¢", ""))
        if value > 1.0:
            value /= 100.0
        if value <= 0 or value >= 1:
            raise ValueError("Polymarket price must be between 0 and 1, or between 0 and 100 cents.")
        return value

    def _load_match_into_dutch(self, mode: str) -> None:
        row = self._selected_dutch_row()
        if row is None:
            messagebox.showinfo("Dutch Calculator", "Select a match first.")
            return
        labels = (row.home_team, "Draw", row.away_team)
        sb = (row.sb_home, row.sb_draw, row.sb_away)
        pm = (row.pm_home, row.pm_draw, row.pm_away)
        try:
            fee_rate = max(0.0, float(self.dutch_fee_rate_var.get()) / 100.0)
        except ValueError:
            fee_rate = DEFAULT_POLYMARKET_SPORTS_TAKER_FEE_RATE
        maker = self.dutch_pm_order_var.get() == "Maker"

        for i in range(3):
            widget = self._dutch_rows[i]
            widget["use"].set(True)
            widget["label"].set(labels[i])
            if mode == "sportsbet":
                widget["source"].set("Sportsbet")
                widget["value"].set("" if sb[i] is None else f"{sb[i]:.3f}")
            elif mode == "polymarket":
                widget["source"].set("Polymarket")
                widget["value"].set("" if pm[i] is None else f"{100.0 / pm[i]:.2f}")
            else:
                sb_odds = sb[i] or 0.0
                pm_effective = 0.0
                if pm[i] is not None and pm[i] > 1:
                    price = 1.0 / pm[i]
                    pm_effective = polymarket_effective_decimal_odds(price, fee_rate=fee_rate, maker=maker)
                if pm_effective > sb_odds:
                    widget["source"].set("Polymarket")
                    widget["value"].set(f"{100.0 / pm[i]:.2f}")
                else:
                    widget["source"].set("Sportsbet")
                    widget["value"].set("" if sb[i] is None else f"{sb[i]:.3f}")
        for i in range(3, 6):
            self._dutch_rows[i]["use"].set(False)
        self.dutch_complete_market_var.set(True)
        self._calculate_dutch_ui()

    def _calculate_dutch_ui(self) -> None:
        try:
            total_stake = float(self.dutch_total_stake_var.get())
            fee_rate = max(0.0, float(self.dutch_fee_rate_var.get()) / 100.0)
            maker = self.dutch_pm_order_var.get() == "Maker"
            selections: list[DutchSelection] = []
            for widget in self._dutch_rows:
                if not widget["use"].get():
                    widget["effective"].set("—")
                    continue
                label = widget["label"].get().strip() or "Selection"
                source = widget["source"].get()
                text = widget["value"].get().strip()
                if not text:
                    raise ValueError(f"{label}: enter an odds/price value.")
                if source == "Polymarket":
                    price = self._parse_pm_price(text)
                    eff = polymarket_effective_decimal_odds(price, fee_rate=fee_rate, maker=maker)
                    widget["effective"].set(f"{eff:.3f}")
                    selections.append(DutchSelection(label=label, source=source, polymarket_price=price, fee_rate=fee_rate, maker=maker))
                else:
                    odds = float(text)
                    if odds <= 1:
                        raise ValueError(f"{label}: decimal odds must be greater than 1.00.")
                    widget["effective"].set(f"{odds:.3f}")
                    selections.append(DutchSelection(label=label, source=source, decimal_odds=odds))

            result = calculate_dutch(selections, total_stake, complete_market=self.dutch_complete_market_var.get())
            for item in self.dutch_tree.get_children():
                self.dutch_tree.delete(item)
            for item in result.rows:
                self.dutch_tree.insert("", "end", values=(
                    item.label, item.source, item.input_display, f"{item.effective_odds:.3f}",
                    f"${item.stake:,.2f}", f"${item.gross_return:,.2f}", f"${item.net_profit:,.2f}",
                ))

            if not result.complete_market:
                classification = "PARTIAL DUTCH — equal return only if one of the selected outcomes occurs; uncovered outcomes can lose the full outlay."
            elif result.arbitrage:
                classification = "ARBITRAGE — the selected prices cover the full market and imply a positive equal return."
            elif abs(result.equal_profit) < 0.01:
                classification = "NEAR BREAK-EVEN DUTCH."
            else:
                classification = "NEGATIVE DUTCH — equalising these prices locks in a loss if the market is fully covered."
            self.dutch_status_var.set(
                f"{classification}  Inverse-odds sum {result.inverse_sum:.5f}; combined Dutch odds {result.combined_decimal_odds:.3f}; "
                f"equal return ${result.equal_return:,.2f}; P/L ${result.equal_profit:,.2f} ({result.return_on_stake_pct:+.2f}%)."
            )
        except Exception as exc:
            self.dutch_status_var.set(f"Could not calculate: {exc}")

    # ------------------------------------------------------------------
    # Integration with V1.5 fetch lifecycle
    # ------------------------------------------------------------------

    def _apply_fetch_result_v15(self, rows, warnings, info_notes, saved, context_saved, edge_saved) -> None:
        super()._apply_fetch_result_v15(rows, warnings, info_notes, saved, context_saved, edge_saved)
        names = [row.match_name for row in rows]
        self.context_match_combo["values"] = names
        self.dutch_match_combo["values"] = names
        if names:
            if self.context_match_var.get() not in names:
                self.context_match_var.set(names[0])
                self._context_active_key = self._context_key_for_row(rows[0])
                self._load_inputs_to_ui(self._context_inputs_by_key.get(self._context_active_key, ContextInputs()))
            if self.dutch_match_var.get() not in names:
                self.dutch_match_var.set(names[0])
            self._recalculate_context()
            # One free public request after a successful odds fetch.  Failure is
            # non-fatal and never blocks the betting-market analysis.
            if not self.fpl_context:
                self._refresh_fpl_context()

    def _diagnostic_text(self) -> str:
        text = super()._diagnostic_text()
        try:
            count = context_snapshot_count()
        except Exception:
            count = "unavailable"
        return f"V1.6 contextual research rows: {count}\nFPL teams cached: {len(self.fpl_context)}\n" + text


def main() -> None:
    try:
        V16App().mainloop()
    except Exception:
        LOGGER.exception("Fatal V1.6 application error")
        raise


if __name__ == "__main__":
    main()
