from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Optional

import tkinter as tk
from tkinter import messagebox, ttk
from tkcalendar import DateEntry

import main as base_main
from main import App as BaseApp, SummaryCard
from config import DATA_DIR, VERSION
from engine import BRISBANE, CombinedMatch, combine_sources, fetch_polymarket_epl, fetch_sportsbet_epl, fmt_money, fmt_odds, fmt_pct, fmt_probability
from advanced_market import enrich_rows, handicap_summary, outcome_analysis, plain_english_summary
from market_storage import context_snapshot_count, save_market_context


LOGGER = base_main.LOGGER


def _line(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if abs(value) < 1e-9:
        return "0"
    return f"{value:+g}"


def _short_ah(row: CombinedMatch, prefix: str) -> str:
    if prefix == "sb":
        h_line = getattr(row, "sb_ah_home_line", None)
        h_odds = getattr(row, "sb_ah_home_odds", None)
        a_line = getattr(row, "sb_ah_away_line", None)
        a_odds = getattr(row, "sb_ah_away_odds", None)
    else:
        h_line = getattr(row, "pin_ah_home_line", None)
        h_odds = getattr(row, "pin_ah_home_odds", None)
        a_line = getattr(row, "pin_ah_away_line", None)
        a_odds = getattr(row, "pin_ah_away_odds", None)
    if h_line is None or a_line is None:
        return "—"
    return f"H {_line(h_line)} {fmt_odds(h_odds)} / A {_line(a_line)} {fmt_odds(a_odds)}"


def _short_total(row: CombinedMatch, prefix: str) -> str:
    if prefix == "sb":
        line = getattr(row, "sb_total_line", None)
        over = getattr(row, "sb_total_over", None)
        under = getattr(row, "sb_total_under", None)
    else:
        line = getattr(row, "pin_total_line", None)
        over = getattr(row, "pin_total_over", None)
        under = getattr(row, "pin_total_under", None)
    if line is None:
        return "—"
    return f"{line:g} O {fmt_odds(over)} / U {fmt_odds(under)}"


def _prob_for_side(row: CombinedMatch, side: str, prefix: str):
    names = {
        "pm": {"HOME": "pm_fair_home", "DRAW": "pm_fair_draw", "AWAY": "pm_fair_away"},
        "pin": {"HOME": "pin_fair_home", "DRAW": "pin_fair_draw", "AWAY": "pin_fair_away"},
        "consensus": {"HOME": "consensus_home", "DRAW": "consensus_draw", "AWAY": "consensus_away"},
    }
    return getattr(row, names[prefix][side], None)


def _ev_for_side(row: CombinedMatch, side: str, prefix: str):
    names = {
        "pm": {"HOME": "ev_home_pct", "DRAW": "ev_draw_pct", "AWAY": "ev_away_pct"},
        "pin": {"HOME": "pin_ev_home_pct", "DRAW": "pin_ev_draw_pct", "AWAY": "pin_ev_away_pct"},
        "consensus": {
            "HOME": "consensus_ev_home_pct",
            "DRAW": "consensus_ev_draw_pct",
            "AWAY": "consensus_ev_away_pct",
        },
    }
    return getattr(row, names[prefix][side], None)


def _odds_for_side(row: CombinedMatch, side: str):
    return {"HOME": row.sb_home, "DRAW": row.sb_draw, "AWAY": row.sb_away}.get(side)


def _two_way_share(a: Optional[float], b: Optional[float]):
    if a is None or b is None or a <= 1 or b <= 1:
        return None
    x, y = 1.0 / a, 1.0 / b
    total = x + y
    return x / total, y / total


class V14App(BaseApp):
    """V1.4 UI layered on top of the stable V1.3 application."""

    def _create_vars(self) -> None:
        super()._create_vars()
        self.summary_sharp = tk.StringVar(value="—")
        self.summary_context = tk.StringVar(value="0")
        self._match_iid_map: dict[str, CombinedMatch] = {}

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    def _build_dashboard(self) -> None:
        cards = ttk.Frame(self.dashboard_tab)
        cards.pack(fill="x")
        for col in range(7):
            cards.columnconfigure(col, weight=1)

        card_specs = [
            ("EPL fixtures", self.summary_matches, "Current fetch"),
            ("Matched markets", self.summary_matched, "Sportsbet + Polymarket"),
            ("Value candidates", self.summary_candidates, "PM EV threshold"),
            ("Away-fav value", self.summary_away, "Research flag"),
            ("Best PM EV", self.summary_best, "Primary screen"),
            ("Sharp agreement", self.summary_sharp, "Pinnacle cross-check"),
            ("Saved context", self.summary_context, "AH / totals snapshots"),
        ]
        for idx, (title, var, subtitle) in enumerate(card_specs):
            SummaryCard(cards, title, var, subtitle).grid(
                row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 6, 0), pady=(0, 12)
            )

        actions = ttk.LabelFrame(self.dashboard_tab, text="Quick analysis", padding=12)
        actions.pack(fill="x", pady=(0, 12))
        ttk.Button(actions, text="Fetch current EPL odds", command=self.fetch_matches).pack(side="left")
        ttk.Button(actions, text="Open Matches + explanation", command=lambda: self.notebook.select(self.matches_tab)).pack(side="left", padx=8)
        ttk.Button(actions, text="Show candidates", command=lambda: self.notebook.select(self.candidates_tab)).pack(side="left")
        ttk.Button(actions, text="Open research folder", command=lambda: base_main.open_path(DATA_DIR)).pack(side="left", padx=8)
        ttk.Label(
            actions,
            text="V1.4 keeps PM EV as the primary signal, while Asian Handicap, totals and Pinnacle are independent research cross-checks.",
            style="Muted.TLabel",
        ).pack(side="right")

        ttk.Label(self.dashboard_tab, text="Top current candidates", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(4, 6))
        self.dashboard_tree = self._make_tree(
            self.dashboard_tab,
            [
                ("kickoff", "Kick-off", 125),
                ("match", "Match", 245),
                ("selection", "Selection", 85),
                ("sb", "Sportsbet", 80),
                ("pm", "PM fair", 80),
                ("pmev", "PM EV", 75),
                ("pin", "PIN fair", 80),
                ("pinev", "PIN EV", 75),
                ("sharp", "Sharp check", 145),
                ("signal", "Signal", 125),
            ],
            height=15,
        )

    # ------------------------------------------------------------------
    # Matches and per-match explanation
    # ------------------------------------------------------------------

    def _build_matches(self) -> None:
        controls = ttk.LabelFrame(self.matches_tab, text="Fetch EPL market snapshot", padding=10)
        controls.pack(fill="x", pady=(0, 8))

        today = datetime.now(BRISBANE).date()
        end = today + timedelta(days=self.settings.default_days_ahead)

        ttk.Label(controls, text="Start date").pack(side="left")
        self.start_date = DateEntry(
            controls, width=11, date_pattern="dd/mm/yy",
            year=today.year, month=today.month, day=today.day,
        )
        self.start_date.pack(side="left", padx=(5, 14))
        ttk.Label(controls, text="End date").pack(side="left")
        self.end_date = DateEntry(
            controls, width=11, date_pattern="dd/mm/yy",
            year=end.year, month=end.month, day=end.day,
        )
        self.end_date.pack(side="left", padx=(5, 14))
        ttk.Label(controls, text="Min EV %").pack(side="left")
        ttk.Entry(controls, textvariable=self.min_ev_var, width=7).pack(side="left", padx=(5, 12))
        ttk.Checkbutton(
            controls,
            text="Candidates only",
            variable=self.candidates_only_var,
            command=self.refresh_match_tables,
        ).pack(side="left", padx=(0, 12))
        self.fetch_button = ttk.Button(controls, text="Fetch odds", command=self.fetch_matches)
        self.fetch_button.pack(side="left")
        ttk.Button(controls, text="Clear view", command=self.clear_current_view).pack(side="left", padx=8)
        ttk.Label(
            controls,
            text="Tip: click any fixture below to see the calculation explained.",
            style="Muted.TLabel",
        ).pack(side="right")

        split = ttk.Panedwindow(self.matches_tab, orient="vertical")
        split.pack(fill="both", expand=True)

        top = ttk.Frame(split)
        bottom = ttk.Frame(split)
        split.add(top, weight=3)
        split.add(bottom, weight=2)

        self.matches_tree = self._tree_in(
            top,
            [
                ("kickoff", "Kick-off", 120),
                ("match", "Match", 220),
                ("sbh", "SB H", 62), ("sbd", "SB D", 62), ("sba", "SB A", 62),
                ("pmh", "PM fair H", 78), ("pmd", "PM fair D", 78), ("pma", "PM fair A", 78),
                ("evh", "EV H", 68), ("evd", "EV D", 68), ("eva", "EV A", 68),
                ("pinh", "PIN fair H", 78), ("pind", "PIN fair D", 78), ("pina", "PIN fair A", 78),
                ("sbah", "Sportsbet AH", 170),
                ("pinah", "Pinnacle AH", 170),
                ("pintotal", "PIN total", 140),
                ("bestev", "Best EV", 72),
                ("sharp", "Sharp check", 140),
                ("signal", "Strategy", 125),
            ],
            height=11,
        )
        self.matches_tree.bind("<<TreeviewSelect>>", self._on_match_selected)

        detail_tabs = ttk.Notebook(bottom)
        detail_tabs.pack(fill="both", expand=True, pady=(8, 0))

        summary_tab = ttk.Frame(detail_tabs, padding=8)
        ev_tab = ttk.Frame(detail_tabs, padding=8)
        context_tab = ttk.Frame(detail_tabs, padding=8)
        detail_tabs.add(summary_tab, text="Plain-English summary")
        detail_tabs.add(ev_tab, text="EV calculations")
        detail_tabs.add(context_tab, text="Handicap & goals")

        self.analysis_summary_text = tk.Text(summary_tab, wrap="word", height=10, font=("Segoe UI", 10))
        self.analysis_summary_text.pack(fill="both", expand=True)
        self.analysis_summary_text.insert("1.0", "Fetch odds, then click a fixture to see the calculation explained in plain English.")
        self.analysis_summary_text.configure(state="disabled")

        self.analysis_ev_tree = self._tree_in(
            ev_tab,
            [
                ("outcome", "Outcome", 170),
                ("sb", "SB odds", 70),
                ("break", "Break-even", 85),
                ("pm", "PM baseline", 90),
                ("fair", "PM fair odds", 90),
                ("edge", "Edge pp", 75),
                ("ev", "PM EV", 75),
                ("need", "Odds needed", 90),
                ("pin", "PIN fair", 85),
                ("pinev", "PIN EV", 75),
                ("cons", "Consensus EV", 95),
                ("diff", "PM↔PIN diff", 90),
            ],
            height=7,
        )

        self.market_context_text = tk.Text(context_tab, wrap="word", height=10, font=("Segoe UI", 10))
        self.market_context_text.pack(fill="both", expand=True)
        self.market_context_text.insert("1.0", "Asian Handicap and goal-total market context will appear here for the selected fixture.")
        self.market_context_text.configure(state="disabled")

    def _tree_in(self, parent, columns, height=10):
        wrapper = ttk.Frame(parent)
        wrapper.pack(fill="both", expand=True)
        keys = [x[0] for x in columns]
        tree = ttk.Treeview(wrapper, columns=keys, show="headings", height=height)
        for key, title, width in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=55, anchor="w" if key in {"match", "outcome"} else "center")
        vs = ttk.Scrollbar(wrapper, orient="vertical", command=tree.yview)
        hs = ttk.Scrollbar(wrapper, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        wrapper.rowconfigure(0, weight=1)
        wrapper.columnconfigure(0, weight=1)
        return tree

    # ------------------------------------------------------------------
    # Candidates and settings
    # ------------------------------------------------------------------

    def _build_candidates(self) -> None:
        top = ttk.Frame(self.candidates_tab)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Current strategy candidates", font=("Segoe UI", 12, "bold")).pack(side="left")
        ttk.Label(
            top,
            text="Primary signal = PM-based EV. PIN fair/EV is a sharp cross-check; consensus is research-only.",
            style="Muted.TLabel",
        ).pack(side="right")

        self.candidates_tree = self._make_tree(
            self.candidates_tab,
            [
                ("kickoff", "Kick-off", 125),
                ("match", "Match", 250),
                ("selection", "Selection", 85),
                ("sb", "SB odds", 75),
                ("pm", "PM fair", 80),
                ("pmev", "PM EV", 75),
                ("pin", "PIN fair", 80),
                ("pinev", "PIN EV", 75),
                ("consev", "Consensus EV", 95),
                ("sharp", "Sharp check", 145),
                ("ah", "Pinnacle AH", 165),
                ("total", "PIN total", 135),
                ("signal", "Signal", 125),
                ("volume", "PM volume", 90),
            ],
            height=24,
        )

    def _build_settings(self) -> None:
        left = ttk.LabelFrame(self.settings_tab, text="Data and strategy", padding=16)
        left.pack(fill="x", pady=(0, 12))
        left.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(left, text="PulseScore API key").grid(row=row, column=0, sticky="w", pady=6)
        self.api_key_entry = ttk.Entry(left, textvariable=self.api_key_var, show="•", width=48)
        self.api_key_entry.grid(row=row, column=1, sticky="ew", padx=(14, 8), pady=6)
        ttk.Checkbutton(left, text="Remember in Windows Credential Manager", variable=self.remember_key_var).grid(row=row, column=2, sticky="w", pady=6)

        row += 1
        ttk.Label(left, text="Minimum PM EV %").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(left, textvariable=self.min_ev_var, width=10).grid(row=row, column=1, sticky="w", padx=(14, 8), pady=6)
        ttk.Label(left, text="Default 4.0%. This remains the primary V1.4 screening threshold.", style="Muted.TLabel").grid(row=row, column=2, sticky="w")

        row += 1
        ttk.Label(left, text="Minimum Polymarket volume ($)").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(left, textvariable=self.min_volume_var, width=12).grid(row=row, column=1, sticky="w", padx=(14, 8), pady=6)
        ttk.Label(left, text="0 = do not filter. This is a research-quality filter, not a guarantee.", style="Muted.TLabel").grid(row=row, column=2, sticky="w")

        row += 1
        ttk.Label(left, text="Default days ahead").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(left, textvariable=self.days_ahead_var, width=10).grid(row=row, column=1, sticky="w", padx=(14, 8), pady=6)
        ttk.Label(left, text="1–60 days", style="Muted.TLabel").grid(row=row, column=2, sticky="w")

        row += 1
        ttk.Checkbutton(left, text="Save each successful fetch to the research database", variable=self.save_snapshots_var).grid(row=row, column=0, columnspan=2, sticky="w", pady=6)
        ttk.Checkbutton(left, text="Check for application updates on start", variable=self.check_updates_var).grid(row=row, column=2, sticky="w", pady=6)

        buttons = ttk.Frame(self.settings_tab)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Save settings", command=self.save_settings_ui).pack(side="left")
        ttk.Button(buttons, text="Test all data sources", command=self.fetch_matches).pack(side="left", padx=8)
        ttk.Button(buttons, text="Open data folder", command=lambda: base_main.open_path(DATA_DIR)).pack(side="left")

        info = ttk.LabelFrame(self.settings_tab, text="How V1.4 interprets the markets", padding=16)
        info.pack(fill="x", pady=(16, 0))
        ttk.Label(
            info,
            text=(
                "Primary strategy calculation: Polymarket executable YES asks are normalised to 100%, then compared with Sportsbet H/D/A prices. "
                "Sportsbet Asian Handicap and goal totals are shown as market context. V1.4 also attempts to retrieve Pinnacle/PS3838 through PulseScore: its no-vig 1X2 probability is an independent sharp cross-check, while its AH and totals show how the sharper market prices team strength and expected scoring. "
                "The app does not yet change the official betting signal because Pinnacle or AH disagrees/agrees — those fields are being collected so we can backtest them properly."
            ),
            wraplength=1250,
            justify="left",
        ).pack(anchor="w")

        ttk.Label(
            info,
            text="PulseScore request note: a normal fetch uses the existing Sportsbet request plus approximately one additional Pinnacle request when Pinnacle access is available.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(10, 0))

    # ------------------------------------------------------------------
    # Fetch/enrichment
    # ------------------------------------------------------------------

    def _fetch_worker(self, api_key, start, end, min_ev) -> None:
        warnings: list[str] = []
        info_notes: list[str] = []
        try:
            try:
                sb_rows = fetch_sportsbet_epl(api_key, start, end)
            except Exception as exc:
                sb_rows = []
                warnings.append(f"Sportsbet: {exc}")
                LOGGER.exception("Sportsbet fetch failed")

            try:
                pm_rows = fetch_polymarket_epl(start, end)
            except Exception as exc:
                pm_rows = []
                warnings.append(f"Polymarket: {exc}")
                LOGGER.exception("Polymarket fetch failed")

            rows = combine_sources(sb_rows, pm_rows, min_ev_pct=min_ev)
            rows, advanced_notes = enrich_rows(rows, api_key, start, end)
            for note in advanced_notes:
                if note.startswith("INFO:"):
                    info_notes.append(note[5:].strip())
                else:
                    warnings.append(note)

            min_volume = max(0.0, base_main.safe_float(self.min_volume_var.get(), 0.0))
            if min_volume > 0:
                for row in rows:
                    if row.strategy_flag in {"VALUE", "AWAY-FAV VALUE"}:
                        if row.polymarket_volume is None or row.polymarket_volume < min_volume:
                            row.strategy_flag = "LOW VOLUME"

            saved = context_saved = 0
            if self.save_snapshots_var.get() and rows:
                saved = base_main.save_snapshot(rows)
                context_saved = save_market_context(rows)
                LOGGER.info("Saved %d base snapshot rows and %d market-context rows", saved, context_saved)

            LOGGER.info(
                "V1.4 fetch: Sportsbet=%d Polymarket=%d combined=%d warnings=%d info=%d",
                len(sb_rows), len(pm_rows), len(rows), len(warnings), len(info_notes),
            )
            self.after(0, lambda: self._apply_fetch_result_v14(rows, warnings, info_notes, saved, context_saved))
        except Exception as exc:
            LOGGER.exception("Unexpected V1.4 fetch worker failure")
            self.after(0, lambda: self._fatal_fetch_error(exc))

    def _apply_fetch_result_v14(self, rows, warnings, info_notes, saved, context_saved) -> None:
        self.rows = rows
        self.last_fetch_at = datetime.now().astimezone()
        self.refresh_match_tables()
        self.refresh_history()
        self.summary_context.set(str(context_snapshot_count()))
        self._set_busy(False)

        matched = sum(1 for r in rows if r.match_status == "Matched")
        candidates = sum(1 for r in rows if r.strategy_flag in {"VALUE", "AWAY-FAV VALUE"})
        pinnacle = sum(1 for r in rows if getattr(r, "pin_fair_home", None) is not None)
        self.status_var.set(
            f"Loaded {len(rows)} EPL fixture(s); matched {matched}; candidates {candidates}; Pinnacle cross-check {pinnacle}/{len(rows)}"
            + (f"; saved {saved}" if saved else "")
        )
        if warnings:
            messagebox.showwarning("Some primary data could not be fetched", "\n\n".join(warnings))
        if info_notes:
            LOGGER.info("Market context notes: %s", " | ".join(info_notes))

        if rows:
            self._select_first_match()

    # ------------------------------------------------------------------
    # Table population and explanation
    # ------------------------------------------------------------------

    def _fill_matches_tree(self) -> None:
        self._match_iid_map.clear()
        for item in self.matches_tree.get_children():
            self.matches_tree.delete(item)
        rows = self._candidate_rows() if self.candidates_only_var.get() else self.rows
        for idx, r in enumerate(rows):
            iid = f"match-{idx}"
            self._match_iid_map[iid] = r
            self.matches_tree.insert("", "end", iid=iid, values=(
                r.kickoff.strftime("%d/%m/%y %H:%M"), r.match_name,
                fmt_odds(r.sb_home), fmt_odds(r.sb_draw), fmt_odds(r.sb_away),
                fmt_probability(r.pm_fair_home), fmt_probability(r.pm_fair_draw), fmt_probability(r.pm_fair_away),
                fmt_pct(r.ev_home_pct), fmt_pct(r.ev_draw_pct), fmt_pct(r.ev_away_pct),
                fmt_probability(getattr(r, "pin_fair_home", None)),
                fmt_probability(getattr(r, "pin_fair_draw", None)),
                fmt_probability(getattr(r, "pin_fair_away", None)),
                _short_ah(r, "sb"), _short_ah(r, "pin"), _short_total(r, "pin"),
                fmt_pct(r.best_ev_pct), getattr(r, "sharp_check", "NO PINNACLE"), r.strategy_flag,
            ))

    def _fill_candidates_tree(self) -> None:
        for item in self.candidates_tree.get_children():
            self.candidates_tree.delete(item)
        rows = sorted(self._candidate_rows(), key=lambda r: r.best_ev_pct if r.best_ev_pct is not None else -999, reverse=True)
        for r in rows:
            side = r.best_selection
            self.candidates_tree.insert("", "end", values=(
                r.kickoff.strftime("%d/%m/%y %H:%M"), r.match_name, side,
                fmt_odds(_odds_for_side(r, side)),
                fmt_probability(_prob_for_side(r, side, "pm")),
                fmt_pct(_ev_for_side(r, side, "pm")),
                fmt_probability(_prob_for_side(r, side, "pin")),
                fmt_pct(_ev_for_side(r, side, "pin")),
                fmt_pct(_ev_for_side(r, side, "consensus")),
                getattr(r, "sharp_check", "NO PINNACLE"),
                _short_ah(r, "pin"), _short_total(r, "pin"),
                r.strategy_flag, fmt_money(r.polymarket_volume),
            ))

    def _fill_dashboard(self) -> None:
        for item in self.dashboard_tree.get_children():
            self.dashboard_tree.delete(item)
        matched = sum(1 for r in self.rows if r.match_status == "Matched")
        candidates = self._candidate_rows()
        away = sum(1 for r in candidates if r.strategy_flag == "AWAY-FAV VALUE")
        best = max((r.best_ev_pct for r in candidates if r.best_ev_pct is not None), default=None)
        sharp_rows = [r for r in self.rows if getattr(r, "pin_fair_home", None) is not None]
        strong = sum(1 for r in sharp_rows if getattr(r, "reference_quality", "") == "STRONG AGREEMENT")

        self.summary_matches.set(str(len(self.rows)))
        self.summary_matched.set(str(matched))
        self.summary_candidates.set(str(len(candidates)))
        self.summary_away.set(str(away))
        self.summary_best.set(fmt_pct(best))
        self.summary_sharp.set(f"{strong}/{len(sharp_rows)}" if sharp_rows else "—")
        try:
            self.summary_context.set(str(context_snapshot_count()))
        except Exception:
            self.summary_context.set("—")

        for r in sorted(candidates, key=lambda x: x.best_ev_pct or -999, reverse=True)[:15]:
            side = r.best_selection
            self.dashboard_tree.insert("", "end", values=(
                r.kickoff.strftime("%d/%m/%y %H:%M"), r.match_name, side,
                fmt_odds(_odds_for_side(r, side)),
                fmt_probability(_prob_for_side(r, side, "pm")),
                fmt_pct(_ev_for_side(r, side, "pm")),
                fmt_probability(_prob_for_side(r, side, "pin")),
                fmt_pct(_ev_for_side(r, side, "pin")),
                getattr(r, "sharp_check", "NO PINNACLE"), r.strategy_flag,
            ))

    def _select_first_match(self) -> None:
        children = self.matches_tree.get_children()
        if children:
            self.matches_tree.selection_set(children[0])
            self.matches_tree.focus(children[0])
            self._show_match_analysis(self._match_iid_map.get(children[0]))

    def _on_match_selected(self, _event=None) -> None:
        selected = self.matches_tree.selection()
        if not selected:
            return
        self._show_match_analysis(self._match_iid_map.get(selected[0]))

    def _show_match_analysis(self, row: Optional[CombinedMatch]) -> None:
        if row is None:
            return
        min_ev = base_main.safe_float(self.min_ev_var.get(), self.settings.min_ev_pct)

        summary = plain_english_summary(row, min_ev)
        self.analysis_summary_text.configure(state="normal")
        self.analysis_summary_text.delete("1.0", "end")
        self.analysis_summary_text.insert("1.0", summary)
        self.analysis_summary_text.configure(state="disabled")

        for item in self.analysis_ev_tree.get_children():
            self.analysis_ev_tree.delete(item)
        for item in outcome_analysis(row, min_ev):
            self.analysis_ev_tree.insert("", "end", values=(
                item["name"],
                fmt_odds(item["sportsbet_odds"]),
                fmt_probability(item["break_even"]),
                fmt_probability(item["pm_probability"]),
                fmt_odds(item["pm_fair_odds"]),
                fmt_pct(item["edge_pp"]),
                fmt_pct(item["pm_ev_pct"]),
                fmt_odds(item["threshold_odds"]),
                fmt_probability(item["pinnacle_probability"]),
                fmt_pct(item["pinnacle_ev_pct"]),
                fmt_pct(item["consensus_ev_pct"]),
                fmt_pct(item["reference_diff_pp"]),
            ))

        context = self._context_explanation(row)
        self.market_context_text.configure(state="normal")
        self.market_context_text.delete("1.0", "end")
        self.market_context_text.insert("1.0", context)
        self.market_context_text.configure(state="disabled")

    def _context_explanation(self, row: CombinedMatch) -> str:
        lines = [handicap_summary(row), ""]

        sb_ah_share = _two_way_share(getattr(row, "sb_ah_home_odds", None), getattr(row, "sb_ah_away_odds", None))
        if sb_ah_share:
            lines.append(
                f"Sportsbet AH no-vig price share: {row.home_team} {sb_ah_share[0]*100:.1f}% / {row.away_team} {sb_ah_share[1]*100:.1f}%."
            )
        pin_ah_share = _two_way_share(getattr(row, "pin_ah_home_odds", None), getattr(row, "pin_ah_away_odds", None))
        if pin_ah_share:
            lines.append(
                f"Pinnacle AH no-vig price share: {row.home_team} {pin_ah_share[0]*100:.1f}% / {row.away_team} {pin_ah_share[1]*100:.1f}%."
            )
        pin_total_share = _two_way_share(getattr(row, "pin_total_over", None), getattr(row, "pin_total_under", None))
        if pin_total_share:
            lines.append(
                f"Pinnacle total price share: Over {pin_total_share[0]*100:.1f}% / Under {pin_total_share[1]*100:.1f}% at the displayed main goal line."
            )

        lines.extend([
            "",
            "How to read this:",
            "• Asian Handicap describes how much stronger the market considers one team after removing the draw from the bet. A negative line means that side gives goals; a positive line means it receives goals.",
            "• The goal total describes the market's expected scoring environment. Higher total lines mean a more open/high-scoring match is expected.",
            "• At integer and quarter Asian lines, pushes and half-wins/half-losses are possible. The two-way percentages above are therefore price shares, not literal raw probabilities of covering the line.",
            "• Pinnacle no-vig H/D/A is the useful independent sharp cross-check. V1.4 does not automatically average it into the official strategy signal yet.",
        ])

        pin_h = getattr(row, "pin_fair_home", None)
        if pin_h is not None:
            lines.extend([
                "",
                f"Reference agreement: {getattr(row, 'reference_quality', '—')}. Largest PM vs Pinnacle H/D/A difference: {fmt_pct(getattr(row, 'reference_max_diff_pp', None))}.",
                f"Sharp check for the PM-best selection: {getattr(row, 'sharp_check', '—')}.",
            ])
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def _diagnostic_text(self) -> str:
        text = super()._diagnostic_text()
        try:
            extra = context_snapshot_count()
        except Exception:
            extra = "unavailable"
        pinnacle_rows = sum(1 for r in self.rows if getattr(r, "pin_fair_home", None) is not None)
        return (
            f"V1.4 market-context snapshots: {extra}\n"
            f"Current Pinnacle-enriched fixtures: {pinnacle_rows}/{len(self.rows)}\n"
            + text
        )


def main() -> None:
    try:
        V14App().mainloop()
    except Exception:
        LOGGER.exception("Fatal V1.4 application error")
        raise


if __name__ == "__main__":
    main()
