from __future__ import annotations

from datetime import datetime, timedelta
import threading
from typing import Optional

import tkinter as tk
from tkinter import messagebox, ttk
from tkcalendar import DateEntry

import main as base_main
from main import SummaryCard
from main_v14 import V14App, _short_ah, _short_total
from config import DATA_DIR
from engine import (
    BRISBANE,
    CombinedMatch,
    combine_sources,
    fetch_polymarket_epl,
    fetch_sportsbet_epl,
    fmt_money,
    fmt_odds,
    fmt_pct,
    fmt_probability,
)
from advanced_market import enrich_rows, handicap_summary
from market_storage import context_snapshot_count, save_market_context
from edge_model import OutcomeEdge, edge_rank_key, edge_summary, enrich_edge_model
from edge_storage import edge_snapshot_count, save_edge_snapshot


LOGGER = base_main.LOGGER


def _edge(row: CombinedMatch, side: str) -> Optional[OutcomeEdge]:
    return getattr(row, "edge_outcomes", {}).get(side)


def _best_edge(row: CombinedMatch) -> Optional[OutcomeEdge]:
    return _edge(row, getattr(row, "edge_best_selection", ""))


def _bias_text(edge: Optional[OutcomeEdge]) -> str:
    if edge is None or not edge.bias_tags:
        return "—"
    return ", ".join(edge.bias_tags)


class V15App(V14App):
    """V1.5 market-edge research application."""

    def _create_vars(self) -> None:
        super()._create_vars()
        self.summary_robust = tk.StringVar(value="0")
        self.summary_conservative = tk.StringVar(value="—")
        self.summary_edge_history = tk.StringVar(value="0")
        self.edge_robust_only_var = tk.BooleanVar(value=False)
        self._edge_iid_map: dict[str, tuple[CombinedMatch, str]] = {}

    def _build_tabs(self) -> None:
        super()._build_tabs()
        self.edge_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.insert(3, self.edge_tab, text="Edge Lab")
        self._build_edge_lab()

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    def _build_dashboard(self) -> None:
        cards = ttk.Frame(self.dashboard_tab)
        cards.pack(fill="x")
        for col in range(7):
            cards.columnconfigure(col, weight=1)

        specs = [
            ("EPL fixtures", self.summary_matches, "Current fetch"),
            ("Matched markets", self.summary_matched, "Sportsbet + PM"),
            ("Model edges", self.summary_candidates, "Model EV ≥ threshold"),
            ("Robust edges", self.summary_robust, "Positive worst-case EV"),
            ("Best model EV", self.summary_best, "External fair model"),
            ("Best conservative EV", self.summary_conservative, "Worst reference"),
            ("Saved edge rows", self.summary_edge_history, "V1.5 research DB"),
        ]
        for idx, (title, var, subtitle) in enumerate(specs):
            SummaryCard(cards, title, var, subtitle).grid(
                row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 6, 0), pady=(0, 12)
            )

        actions = ttk.LabelFrame(self.dashboard_tab, text="Quick analysis", padding=12)
        actions.pack(fill="x", pady=(0, 12))
        ttk.Button(actions, text="Fetch current EPL odds", command=self.fetch_matches).pack(side="left")
        ttk.Button(actions, text="Open Edge Lab", command=lambda: self.notebook.select(self.edge_tab)).pack(side="left", padx=8)
        ttk.Button(actions, text="Explain a match", command=lambda: self.notebook.select(self.matches_tab)).pack(side="left")
        ttk.Button(actions, text="Open research folder", command=lambda: base_main.open_path(DATA_DIR)).pack(side="left", padx=8)
        ttk.Label(
            actions,
            text="Sportsbet is the tested price. Fair probability comes only from external references; Sportsbet de-vig is a bias diagnostic.",
            style="Muted.TLabel",
        ).pack(side="right")

        ttk.Label(self.dashboard_tab, text="Highest current model edges", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(4, 6))
        self.dashboard_tree = self._make_tree(
            self.dashboard_tab,
            [
                ("kickoff", "Kick-off", 120),
                ("match", "Match", 225),
                ("selection", "Outcome", 95),
                ("sb", "SB odds", 70),
                ("model", "Model fair", 82),
                ("edge", "Price edge", 82),
                ("ev", "Model EV", 75),
                ("cev", "Conservative EV", 105),
                ("confidence", "Confidence", 85),
                ("bias", "Bias tags", 235),
                ("signal", "Signal", 190),
            ],
            height=15,
        )

    # ------------------------------------------------------------------
    # Matches with model-focused explanation
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
        ttk.Label(controls, text="Min model EV %").pack(side="left")
        ttk.Entry(controls, textvariable=self.min_ev_var, width=7).pack(side="left", padx=(5, 12))
        ttk.Checkbutton(
            controls, text="Candidates only", variable=self.candidates_only_var,
            command=self.refresh_match_tables,
        ).pack(side="left", padx=(0, 12))
        self.fetch_button = ttk.Button(controls, text="Fetch odds", command=self.fetch_matches)
        self.fetch_button.pack(side="left")
        ttk.Button(controls, text="Clear view", command=self.clear_current_view).pack(side="left", padx=8)
        ttk.Label(controls, text="Click any fixture for the full calculation.", style="Muted.TLabel").pack(side="right")

        split = ttk.Panedwindow(self.matches_tab, orient="vertical")
        split.pack(fill="both", expand=True)
        top, bottom = ttk.Frame(split), ttk.Frame(split)
        split.add(top, weight=3)
        split.add(bottom, weight=2)

        self.matches_tree = self._tree_in(
            top,
            [
                ("kickoff", "Kick-off", 118),
                ("match", "Match", 215),
                ("sbh", "SB H", 60), ("sbd", "SB D", 60), ("sba", "SB A", 60),
                ("sbvh", "SB de-vig H", 82), ("sbvd", "SB de-vig D", 82), ("sbva", "SB de-vig A", 82),
                ("mh", "Model H", 75), ("md", "Model D", 75), ("ma", "Model A", 75),
                ("evh", "Model EV H", 80), ("evd", "Model EV D", 80), ("eva", "Model EV A", 80),
                ("ah", "Sharp AH", 165),
                ("total", "Sharp total", 140),
                ("best", "Best", 65),
                ("cev", "Conservative EV", 100),
                ("confidence", "Confidence", 82),
                ("signal", "Edge signal", 190),
            ],
            height=11,
        )
        self.matches_tree.bind("<<TreeviewSelect>>", self._on_match_selected)

        details = ttk.Notebook(bottom)
        details.pack(fill="both", expand=True, pady=(8, 0))
        summary_tab, ev_tab, context_tab = ttk.Frame(details, padding=8), ttk.Frame(details, padding=8), ttk.Frame(details, padding=8)
        details.add(summary_tab, text="V1.5 summary")
        details.add(ev_tab, text="Outcome calculations")
        details.add(context_tab, text="Asian handicap & goals")

        self.analysis_summary_text = tk.Text(summary_tab, wrap="word", height=10, font=("Segoe UI", 10))
        self.analysis_summary_text.pack(fill="both", expand=True)
        self.analysis_summary_text.insert("1.0", "Fetch odds, then click a fixture. V1.5 will explain exactly where each probability and EV number comes from.")
        self.analysis_summary_text.configure(state="disabled")

        self.analysis_ev_tree = self._tree_in(
            ev_tab,
            [
                ("outcome", "Outcome", 150),
                ("sb", "SB odds", 65),
                ("break", "Break-even", 82),
                ("sbdevig", "SB de-vig", 80),
                ("pm", "PM", 75),
                ("pin", "PIN provider", 85),
                ("ah", "AH model", 80),
                ("model", "Model fair", 82),
                ("fair", "Fair odds", 72),
                ("edge", "Price edge", 78),
                ("resid", "SB residual", 82),
                ("ev", "Model EV", 75),
                ("cev", "Conservative EV", 105),
                ("need", "Odds needed", 85),
                ("bias", "Bias tags", 230),
                ("signal", "Signal", 185),
            ],
            height=7,
        )

        self.market_context_text = tk.Text(context_tab, wrap="word", height=10, font=("Segoe UI", 10))
        self.market_context_text.pack(fill="both", expand=True)
        self.market_context_text.insert("1.0", "Asian Handicap, totals and implied expected-goals context will appear here.")
        self.market_context_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Candidates = V1.5 external-model candidates
    # ------------------------------------------------------------------

    def _build_candidates(self) -> None:
        top = ttk.Frame(self.candidates_tab)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="V1.5 model candidates", font=("Segoe UI", 12, "bold")).pack(side="left")
        ttk.Label(
            top,
            text="Sorted by conservative EV first. Bias tags never add an untested probability bonus.",
            style="Muted.TLabel",
        ).pack(side="right")

        self.candidates_tree = self._make_tree(
            self.candidates_tab,
            [
                ("kickoff", "Kick-off", 120),
                ("match", "Match", 240),
                ("selection", "Outcome", 90),
                ("sb", "SB odds", 70),
                ("model", "Model fair", 82),
                ("fair", "Fair odds", 72),
                ("edge", "Price edge", 80),
                ("ev", "Model EV", 75),
                ("cev", "Conservative EV", 105),
                ("refs", "External refs", 82),
                ("diff", "Disagreement", 90),
                ("confidence", "Confidence", 85),
                ("bias", "Bias tags", 230),
                ("signal", "Signal", 190),
            ],
            height=24,
        )

    # ------------------------------------------------------------------
    # Edge Lab
    # ------------------------------------------------------------------

    def _build_edge_lab(self) -> None:
        intro = ttk.LabelFrame(self.edge_tab, text="How to read the V1.5 edge model", padding=12)
        intro.pack(fill="x", pady=(0, 8))
        ttk.Label(
            intro,
            text=(
                "Sportsbet odds are the price being tested. 'SB de-vig' removes Sportsbet's margin with a power method and is used only to detect bookmaker shading. "
                "Model fair probability is built from independent providers: Polymarket and Pinnacle, with Pinnacle Asian Handicap + totals folded into the Pinnacle component when available. "
                "Conservative EV uses the least favourable external provider probability, so an edge that remains positive is more robust to model disagreement."
            ),
            wraplength=1380,
            justify="left",
        ).pack(anchor="w")

        controls = ttk.Frame(self.edge_tab)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Checkbutton(
            controls,
            text="Robust edges only",
            variable=self.edge_robust_only_var,
            command=self._fill_edge_lab,
        ).pack(side="left")
        ttk.Label(
            controls,
            text="Robust = model EV meets threshold, worst external reference remains > 0 EV, and references are not strongly divergent.",
            style="Muted.TLabel",
        ).pack(side="left", padx=12)

        split = ttk.Panedwindow(self.edge_tab, orient="vertical")
        split.pack(fill="both", expand=True)
        top, bottom = ttk.Frame(split), ttk.Frame(split)
        split.add(top, weight=3)
        split.add(bottom, weight=2)

        self.edge_tree = self._tree_in(
            top,
            [
                ("kickoff", "Kick-off", 118),
                ("match", "Match", 220),
                ("outcome", "Outcome", 100),
                ("sb", "SB odds", 68),
                ("break", "Break-even", 80),
                ("sbdevig", "SB de-vig", 80),
                ("pm", "PM", 72),
                ("pin", "PIN", 72),
                ("ah", "AH model", 78),
                ("model", "Model fair", 82),
                ("fair", "Fair odds", 70),
                ("edge", "Price edge", 80),
                ("resid", "SB residual", 82),
                ("ev", "Model EV", 75),
                ("cev", "Conservative EV", 105),
                ("confidence", "Confidence", 82),
                ("bias", "Bias tags", 220),
                ("signal", "Signal", 185),
            ],
            height=14,
        )
        self.edge_tree.bind("<<TreeviewSelect>>", self._on_edge_selected)

        self.edge_detail_text = tk.Text(bottom, wrap="word", height=12, font=("Segoe UI", 10))
        self.edge_detail_text.pack(fill="both", expand=True, pady=(8, 0))
        self.edge_detail_text.insert("1.0", "Fetch odds to populate the Edge Lab. Every match contributes Home, Draw and Away rows.")
        self.edge_detail_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Fetch + enrichment
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
            rows = enrich_edge_model(rows, min_ev_pct=min_ev)

            for note in advanced_notes:
                if note.startswith("INFO:"):
                    info_notes.append(note[5:].strip())
                else:
                    warnings.append(note)

            saved = context_saved = edge_saved = 0
            if self.save_snapshots_var.get() and rows:
                saved = base_main.save_snapshot(rows)
                context_saved = save_market_context(rows)
                edge_saved = save_edge_snapshot(rows)
                LOGGER.info(
                    "Saved %d base, %d context and %d edge rows",
                    saved, context_saved, edge_saved,
                )

            self.after(
                0,
                lambda: self._apply_fetch_result_v15(
                    rows, warnings, info_notes, saved, context_saved, edge_saved
                ),
            )
        except Exception as exc:
            LOGGER.exception("Unexpected V1.5 fetch worker failure")
            self.after(0, lambda: self._fatal_fetch_error(exc))

    def _apply_fetch_result_v15(self, rows, warnings, info_notes, saved, context_saved, edge_saved) -> None:
        # Reuse V1.4's stable application result handling, then replace the
        # status line with the V1.5 model summary.
        super()._apply_fetch_result_v14(rows, warnings, info_notes, saved, context_saved)
        robust = sum(
            1 for r in rows
            if getattr(r, "edge_signal", "") in {"ROBUST EDGE", "BIAS-ALIGNED ROBUST EDGE"}
        )
        candidates = len(self._candidate_rows())
        self.status_var.set(
            f"Loaded {len(rows)} EPL fixture(s); V1.5 model edges {candidates}; robust {robust}; saved edge rows {edge_saved}"
        )
        self._fill_edge_lab()

    # ------------------------------------------------------------------
    # Population + selection
    # ------------------------------------------------------------------

    def _candidate_rows(self) -> list[CombinedMatch]:
        min_ev = base_main.safe_float(self.min_ev_var.get(), self.settings.min_ev_pct)
        return [
            r for r in self.rows
            if getattr(r, "edge_best_ev_pct", None) is not None
            and getattr(r, "edge_best_ev_pct") >= min_ev
        ]

    def refresh_match_tables(self) -> None:
        super().refresh_match_tables()
        if hasattr(self, "edge_tree"):
            self._fill_edge_lab()

    def _fill_matches_tree(self) -> None:
        self._match_iid_map.clear()
        for item in self.matches_tree.get_children():
            self.matches_tree.delete(item)
        rows = self._candidate_rows() if self.candidates_only_var.get() else self.rows
        for idx, row in enumerate(rows):
            iid = f"match-{idx}"
            self._match_iid_map[iid] = row
            h, d, a = _edge(row, "HOME"), _edge(row, "DRAW"), _edge(row, "AWAY")
            best = _best_edge(row)
            sharp_ah = _short_ah(row, "pin") if getattr(row, "pin_ah_home_line", None) is not None else _short_ah(row, "sb")
            sharp_total = _short_total(row, "pin") if getattr(row, "pin_total_line", None) is not None else _short_total(row, "sb")
            self.matches_tree.insert("", "end", iid=iid, values=(
                row.kickoff.strftime("%d/%m/%y %H:%M"), row.match_name,
                fmt_odds(row.sb_home), fmt_odds(row.sb_draw), fmt_odds(row.sb_away),
                fmt_probability(h.sportsbet_devig_probability if h else None),
                fmt_probability(d.sportsbet_devig_probability if d else None),
                fmt_probability(a.sportsbet_devig_probability if a else None),
                fmt_probability(h.model_probability if h else None),
                fmt_probability(d.model_probability if d else None),
                fmt_probability(a.model_probability if a else None),
                fmt_pct(h.model_ev_pct if h else None),
                fmt_pct(d.model_ev_pct if d else None),
                fmt_pct(a.model_ev_pct if a else None),
                sharp_ah, sharp_total,
                getattr(row, "edge_best_selection", "—"),
                fmt_pct(best.conservative_ev_pct if best else None),
                best.confidence if best else "—",
                best.signal if best else "NO MODEL",
            ))

    def _fill_candidates_tree(self) -> None:
        for item in self.candidates_tree.get_children():
            self.candidates_tree.delete(item)
        rows = sorted(self._candidate_rows(), key=edge_rank_key, reverse=True)
        for row in rows:
            edge = _best_edge(row)
            if edge is None:
                continue
            self.candidates_tree.insert("", "end", values=(
                row.kickoff.strftime("%d/%m/%y %H:%M"), row.match_name, edge.name,
                fmt_odds(edge.sportsbet_odds), fmt_probability(edge.model_probability),
                fmt_odds(edge.model_fair_odds), fmt_pct(edge.price_edge_pp),
                fmt_pct(edge.model_ev_pct), fmt_pct(edge.conservative_ev_pct),
                edge.source_count, fmt_pct(edge.external_disagreement_pp),
                edge.confidence, _bias_text(edge), edge.signal,
            ))

    def _fill_dashboard(self) -> None:
        for item in self.dashboard_tree.get_children():
            self.dashboard_tree.delete(item)
        matched = sum(1 for r in self.rows if r.match_status == "Matched")
        candidates = self._candidate_rows()
        robust = [
            r for r in candidates
            if getattr(r, "edge_signal", "") in {"ROBUST EDGE", "BIAS-ALIGNED ROBUST EDGE"}
        ]
        best_model = max((getattr(r, "edge_best_ev_pct", None) for r in self.rows if getattr(r, "edge_best_ev_pct", None) is not None), default=None)
        best_cons = max((getattr(r, "edge_best_conservative_ev_pct", None) for r in self.rows if getattr(r, "edge_best_conservative_ev_pct", None) is not None), default=None)

        self.summary_matches.set(str(len(self.rows)))
        self.summary_matched.set(str(matched))
        self.summary_candidates.set(str(len(candidates)))
        self.summary_robust.set(str(len(robust)))
        self.summary_best.set(fmt_pct(best_model))
        self.summary_conservative.set(fmt_pct(best_cons))
        try:
            self.summary_edge_history.set(str(edge_snapshot_count()))
        except Exception:
            self.summary_edge_history.set("—")

        for row in sorted(candidates, key=edge_rank_key, reverse=True)[:15]:
            edge = _best_edge(row)
            if edge is None:
                continue
            self.dashboard_tree.insert("", "end", values=(
                row.kickoff.strftime("%d/%m/%y %H:%M"), row.match_name, edge.name,
                fmt_odds(edge.sportsbet_odds), fmt_probability(edge.model_probability),
                fmt_pct(edge.price_edge_pp), fmt_pct(edge.model_ev_pct),
                fmt_pct(edge.conservative_ev_pct), edge.confidence,
                _bias_text(edge), edge.signal,
            ))

    def _show_match_analysis(self, row: Optional[CombinedMatch]) -> None:
        if row is None:
            return
        min_ev = base_main.safe_float(self.min_ev_var.get(), self.settings.min_ev_pct)
        text = edge_summary(row, min_ev)
        self.analysis_summary_text.configure(state="normal")
        self.analysis_summary_text.delete("1.0", "end")
        self.analysis_summary_text.insert("1.0", text)
        self.analysis_summary_text.configure(state="disabled")

        for item in self.analysis_ev_tree.get_children():
            self.analysis_ev_tree.delete(item)
        for side in ("HOME", "DRAW", "AWAY"):
            edge = _edge(row, side)
            if edge is None:
                continue
            self.analysis_ev_tree.insert("", "end", values=(
                edge.name,
                fmt_odds(edge.sportsbet_odds),
                fmt_probability(edge.break_even_probability),
                fmt_probability(edge.sportsbet_devig_probability),
                fmt_probability(edge.polymarket_probability),
                fmt_probability(edge.pinnacle_probability),
                fmt_probability(edge.ah_probability),
                fmt_probability(edge.model_probability),
                fmt_odds(edge.model_fair_odds),
                fmt_pct(edge.price_edge_pp),
                fmt_pct(edge.sportsbet_residual_pp),
                fmt_pct(edge.model_ev_pct),
                fmt_pct(edge.conservative_ev_pct),
                fmt_odds(edge.required_odds_for_threshold),
                _bias_text(edge),
                edge.signal,
            ))

        context = handicap_summary(row)
        ah_source = getattr(row, "ah_model_source", "NONE")
        if ah_source != "NONE":
            context += (
                "\n\nV1.5 AH/TOTALS SCORE MODEL\n"
                f"Source used for the score model: {ah_source}\n"
                f"Implied expected goals: {row.home_team} {getattr(row, 'ah_model_lambda_home', 0):.2f} — "
                f"{row.away_team} {getattr(row, 'ah_model_lambda_away', 0):.2f}\n"
                f"Implied 1X2: H {fmt_probability(getattr(row, 'ah_model_home', None))} / "
                f"D {fmt_probability(getattr(row, 'ah_model_draw', None))} / "
                f"A {fmt_probability(getattr(row, 'ah_model_away', None))}\n"
                f"Calibration error: {fmt_pct(getattr(row, 'ah_model_fit_error_pct', None))}\n\n"
                "If the source is PINNACLE, this probability can contribute to the independent fair model. "
                "If the source is SPORTSBET-DIAGNOSTIC, it is displayed only as a consistency check so we do not use Sportsbet to prove that Sportsbet is mispriced."
            )
        self.market_context_text.configure(state="normal")
        self.market_context_text.delete("1.0", "end")
        self.market_context_text.insert("1.0", context)
        self.market_context_text.configure(state="disabled")

    def _fill_edge_lab(self) -> None:
        if not hasattr(self, "edge_tree"):
            return
        self._edge_iid_map.clear()
        for item in self.edge_tree.get_children():
            self.edge_tree.delete(item)

        all_items: list[tuple[CombinedMatch, OutcomeEdge]] = []
        for row in self.rows:
            for side in ("HOME", "DRAW", "AWAY"):
                edge = _edge(row, side)
                if edge is not None:
                    if self.edge_robust_only_var.get() and edge.signal not in {"ROBUST EDGE", "BIAS-ALIGNED ROBUST EDGE"}:
                        continue
                    all_items.append((row, edge))
        all_items.sort(
            key=lambda item: (
                item[1].conservative_ev_pct if item[1].conservative_ev_pct is not None else -99999,
                item[1].model_ev_pct if item[1].model_ev_pct is not None else -99999,
            ),
            reverse=True,
        )

        for idx, (row, edge) in enumerate(all_items):
            iid = f"edge-{idx}"
            self._edge_iid_map[iid] = (row, edge.side)
            self.edge_tree.insert("", "end", iid=iid, values=(
                row.kickoff.strftime("%d/%m/%y %H:%M"), row.match_name, edge.name,
                fmt_odds(edge.sportsbet_odds), fmt_probability(edge.break_even_probability),
                fmt_probability(edge.sportsbet_devig_probability),
                fmt_probability(edge.polymarket_probability), fmt_probability(edge.pinnacle_probability),
                fmt_probability(edge.ah_probability), fmt_probability(edge.model_probability),
                fmt_odds(edge.model_fair_odds), fmt_pct(edge.price_edge_pp),
                fmt_pct(edge.sportsbet_residual_pp), fmt_pct(edge.model_ev_pct),
                fmt_pct(edge.conservative_ev_pct), edge.confidence, _bias_text(edge), edge.signal,
            ))
        children = self.edge_tree.get_children()
        if children:
            self.edge_tree.selection_set(children[0])
            self.edge_tree.focus(children[0])
            self._show_edge_detail(*self._edge_iid_map[children[0]])

    def _on_edge_selected(self, _event=None) -> None:
        selected = self.edge_tree.selection()
        if not selected:
            return
        pair = self._edge_iid_map.get(selected[0])
        if pair:
            self._show_edge_detail(*pair)

    def _show_edge_detail(self, row: CombinedMatch, side: str) -> None:
        edge = _edge(row, side)
        if edge is None:
            return
        odds = edge.sportsbet_odds
        p = edge.model_probability
        equation = "unavailable"
        if odds is not None and p is not None:
            equation = f"({p:.5f} × {odds:.2f}) - 1 = {(p * odds - 1) * 100:.2f}%"
        sources = ", ".join(getattr(row, "edge_source_names", ())) or "none"
        text = (
            f"{row.match_name} — {edge.name}\n\n"
            f"MODEL EV FORMULA\nEV = fair probability × decimal odds - 1\n{equation}\n\n"
            f"Offered Sportsbet odds: {fmt_odds(odds)}\n"
            f"Break-even probability: {fmt_probability(edge.break_even_probability)}\n"
            f"Sportsbet raw implied probability: {fmt_probability(edge.sportsbet_raw_probability)}\n"
            f"Sportsbet power de-vig probability: {fmt_probability(edge.sportsbet_devig_probability)}\n\n"
            f"Independent sources: {sources}\n"
            f"Polymarket: {fmt_probability(edge.polymarket_probability)}\n"
            f"Pinnacle provider component: {fmt_probability(edge.pinnacle_probability)}\n"
            f"Asian Handicap + totals model: {fmt_probability(edge.ah_probability)}\n"
            f"Final V1.5 fair probability: {fmt_probability(edge.model_probability)}\n"
            f"Fair odds: {fmt_odds(edge.model_fair_odds)}\n\n"
            f"Price edge: {fmt_pct(edge.price_edge_pp)} probability points\n"
            f"Model EV: {fmt_pct(edge.model_ev_pct)}\n"
            f"Conservative EV: {fmt_pct(edge.conservative_ev_pct)}\n"
            f"External disagreement: {fmt_pct(edge.external_disagreement_pp)} probability points\n"
            f"Confidence: {edge.confidence}\n"
            f"Signal: {edge.signal}\n"
            f"Bias tags: {_bias_text(edge)}\n\n"
            "Conservative EV uses the lowest probability among the external provider components. "
            "This is deliberately harder to pass than the headline model EV. A favourite/away-favourite tag does not change the probability; it marks a hypothesis for later backtesting."
        )
        self.edge_detail_text.configure(state="normal")
        self.edge_detail_text.delete("1.0", "end")
        self.edge_detail_text.insert("1.0", text)
        self.edge_detail_text.configure(state="disabled")

    def _diagnostic_text(self) -> str:
        text = super()._diagnostic_text()
        try:
            count = edge_snapshot_count()
        except Exception:
            count = "unavailable"
        return f"V1.5 edge-model snapshot rows: {count}\n" + text


def main() -> None:
    try:
        V15App().mainloop()
    except Exception:
        LOGGER.exception("Fatal V1.5 application error")
        raise


if __name__ == "__main__":
    main()
