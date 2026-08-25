from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from typing import Optional

import main_v21 as v21_module
from edge_progress_v22 import enrich_edge_model_parallel, recommended_worker_count
from main_v21 import V21App, LOGGER
from research_models_v22 import (
    HistoricalValidation,
    ResearchModelResult,
    build_research_results,
    fetch_recent_epl_history,
    validate_historical_models,
)


# V21's worker resolves this name from the main_v21 module at runtime. Replacing
# it here upgrades the expensive probability/Poisson stage without duplicating
# the entire tested fetch pipeline.
v21_module.enrich_edge_model_progressive = enrich_edge_model_parallel


class V22App(V21App):
    """V2.2: multicore calculation plus historical residual-model research."""

    def _create_vars(self) -> None:
        super()._create_vars()
        self._research_loading = False
        self._research_results: list[ResearchModelResult] = []
        self._research_validation = HistoricalValidation()
        self.research_status_var = tk.StringVar(value="Experimental historical models have not run yet.")
        self.research_history_var = tk.StringVar(value="0")
        self.research_holdout_var = tk.StringVar(value="0")
        self.research_market_brier_var = tk.StringVar(value="—")
        self.research_blend_brier_var = tk.StringVar(value="—")
        self.research_improvement_var = tk.StringVar(value="—")
        self.cpu_worker_var = tk.StringVar(value=str(recommended_worker_count()))

    def _build_tabs(self) -> None:
        super()._build_tabs()
        self.research_models_tab = ttk.Frame(self.analysis_book, padding=12)
        self.analysis_book.add(self.research_models_tab, text="Research models")
        self._build_research_models_tab()

    def _build_research_models_tab(self) -> None:
        intro = ttk.LabelFrame(self.research_models_tab, text="Experimental residual models", padding=12)
        intro.pack(fill="x", pady=(0, 9))
        ttk.Label(
            intro,
            text=(
                "This layer implements the strongest reusable ideas from the GitHub model review: time-decayed Elo, a low-score-corrected time-decayed Poisson/Dixon-Coles-style model, "
                "strict pre-kickoff data cut-offs, lineup-continuity/context residuals, disagreement/uncertainty measurement and chronological holdout Brier testing. "
                "These models are compared with the live independent market; they do not create the primary green Robust +EV signal until validation shows incremental value."
            ),
            wraplength=1320,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(intro, textvariable=self.research_status_var, style="Muted.TLabel").pack(anchor="w", pady=(7, 0))

        cards = ttk.Frame(self.research_models_tab)
        cards.pack(fill="x", pady=(0, 9))
        specs = (
            ("Historical matches", self.research_history_var),
            ("Holdout samples", self.research_holdout_var),
            ("Market Brier", self.research_market_brier_var),
            ("Residual blend Brier", self.research_blend_brier_var),
            ("Holdout improvement", self.research_improvement_var),
        )
        for i, (title, var) in enumerate(specs):
            cards.columnconfigure(i, weight=1)
            box = ttk.LabelFrame(cards, text=title, padding=8)
            box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 5, 0))
            ttk.Label(box, textvariable=var, font=("Segoe UI", 13, "bold")).pack(anchor="w")

        controls = ttk.Frame(self.research_models_tab)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Button(controls, text="Refresh research models", command=self._start_research_models).pack(side="left")
        ttk.Label(
            controls,
            text=f"Core probability stage uses up to {recommended_worker_count()} CPU worker processes automatically.",
            style="Muted.TLabel",
        ).pack(side="right")

        frame = ttk.Frame(self.research_models_tab)
        frame.pack(fill="both", expand=True)
        columns = ("match", "pick", "market", "elo", "poisson", "context", "experimental", "residual", "spread", "agreement")
        self.research_models_tree = ttk.Treeview(frame, columns=columns, show="headings", height=19)
        definitions = (
            ("match", "Match", 230),
            ("pick", "Outcome", 120),
            ("market", "Market %", 75),
            ("elo", "Elo %", 70),
            ("poisson", "Poisson %", 80),
            ("context", "Context shift", 85),
            ("experimental", "Experimental %", 95),
            ("residual", "Residual", 75),
            ("spread", "Dispersion", 75),
            ("agreement", "Model agreement", 145),
        )
        for key, title, width in definitions:
            self.research_models_tree.heading(key, text=title)
            self.research_models_tree.column(key, width=width, anchor="w" if key == "match" else "center")
        vs = ttk.Scrollbar(frame, orient="vertical", command=self.research_models_tree.yview)
        hs = ttk.Scrollbar(frame, orient="horizontal", command=self.research_models_tree.xview)
        self.research_models_tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.research_models_tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

    def _apply_v21_result(self, rows, warnings, info_notes, saved, context_saved, edge_saved) -> None:
        super()._apply_v21_result(rows, warnings, info_notes, saved, context_saved, edge_saved)
        self._start_research_models()

    def _apply_football_bundle_v19(self, bundle, error) -> None:
        super()._apply_football_bundle_v19(bundle, error)
        if not error and bundle is not None:
            # Rerun cheaply from cached history so lineup/xG/context residuals are
            # added once the optional football-intelligence layer finishes.
            self._start_research_models(force=True)

    def _start_research_models(self, force: bool = False) -> None:
        if self._research_loading or not self.rows:
            return
        self._research_loading = True
        self.research_status_var.set("Loading cached/public historical EPL results and running chronological research models…")
        rows = list(self.rows)
        intelligence = dict(self.football_bundle.matches) if self.football_bundle else {}
        target = max((row.kickoff.date() for row in rows), default=None)
        threading.Thread(
            target=self._research_worker,
            args=(rows, intelligence, target),
            daemon=True,
        ).start()

    def _research_worker(self, rows, intelligence, target) -> None:
        try:
            if target is None:
                raise RuntimeError("No fixture date available")
            history = fetch_recent_epl_history(target)
            results = build_research_results(rows, history, intelligence)
            validation = validate_historical_models(history)
            self.after(0, lambda: self._apply_research_results(history, results, validation, None))
        except Exception as exc:
            LOGGER.exception("V2.2 research-model refresh failed")
            self.after(0, lambda: self._apply_research_results([], [], HistoricalValidation(), str(exc)))

    @staticmethod
    def _pct(value: Optional[float]) -> str:
        return "—" if value is None else f"{value * 100:.1f}%"

    def _apply_research_results(self, history, results, validation, error) -> None:
        self._research_loading = False
        if error:
            self.research_status_var.set(f"Experimental models unavailable: {error}. Core market model remains unaffected.")
            return
        self._research_results = results
        self._research_validation = validation
        self.research_history_var.set(str(len(history)))
        self.research_holdout_var.set(str(validation.samples))
        self.research_market_brier_var.set("—" if validation.market_brier is None else f"{validation.market_brier:.4f}")
        self.research_blend_brier_var.set("—" if validation.blend_brier is None else f"{validation.blend_brier:.4f}")
        self.research_improvement_var.set("—" if validation.holdout_improvement_pct is None else f"{validation.holdout_improvement_pct:+.2f}%")

        if validation.holdout_improvement_pct is not None and validation.holdout_improvement_pct > 0:
            status = (
                f"Chronological holdout: residual blend improved Brier by {validation.holdout_improvement_pct:.2f}% using a {validation.selected_blend_weight:.0%} historical-model weight. "
                "This is promising research evidence, not yet permission to override the Robust +EV rule."
            )
        elif validation.samples:
            status = (
                "Chronological holdout did not beat the market baseline. The app therefore keeps the historical-model residual out of the primary betting signal."
            )
        else:
            status = "Not enough historical bookmaker/probability data was available for a reliable holdout comparison."
        self.research_status_var.set(status)

        if hasattr(self, "research_models_tree"):
            for item in self.research_models_tree.get_children():
                self.research_models_tree.delete(item)
            for r in results[:300]:
                self.research_models_tree.insert("", "end", values=(
                    r.match_name,
                    r.selection,
                    self._pct(r.market_probability),
                    self._pct(r.elo_probability),
                    self._pct(r.poisson_probability),
                    f"{r.football_shift_pp:+.2f} pp",
                    self._pct(r.experimental_probability),
                    "—" if r.residual_pp is None else f"{r.residual_pp:+.2f} pp",
                    "—" if r.dispersion_pp is None else f"{r.dispersion_pp:.2f} pp",
                    r.agreement,
                ))
        self._fill_dashboard()

    def _research_for_decision(self, decision) -> Optional[ResearchModelResult]:
        if decision is None:
            return None
        return next(
            (
                r for r in self._research_results
                if r.match_name == decision.match_name and r.side == decision.side
            ),
            None,
        )

    def _fill_dashboard(self) -> None:
        super()._fill_dashboard()
        decision = getattr(self, "_v21_dashboard_best", None)
        research = self._research_for_decision(decision)
        if decision is None or research is None or research.residual_pp is None:
            return
        original = self.best_pick_reason_var.get()
        if research.agreement == "MODELS AGREE":
            extra = (
                f" Experimental Elo/Poisson research agrees in the same direction as the market residual ({research.residual_pp:+.1f} pp; dispersion {research.dispersion_pp:.1f} pp)."
            )
        elif research.agreement == "MODELS DISAGREE":
            extra = (
                f" Experimental Elo and Poisson models disagree around this outcome; no extra confidence is added (residual {research.residual_pp:+.1f} pp)."
            )
        else:
            extra = f" Experimental historical-model residual: {research.residual_pp:+.1f} pp ({research.agreement.lower()})."
        if "Experimental historical-model residual" not in original and "Experimental Elo/Poisson" not in original:
            self.best_pick_reason_var.set(original + extra)

    def _diagnostic_text(self) -> str:
        workers = recommended_worker_count()
        validation = self._research_validation
        research_line = (
            f"V2.2 research holdout: {validation.samples} samples; market Brier {validation.market_brier}; "
            f"blend Brier {validation.blend_brier}; improvement {validation.holdout_improvement_pct}%"
        )
        return (
            f"V2.2 CPU: probability/Poisson stage configured for up to {workers} worker processes ({self.cpu_worker_var.get()} shown in UI)\n"
            "V2.2 research: time-decayed Elo + low-score-corrected time-decayed Poisson + lineup/context residual + chronological Brier holdout\n"
            + research_line + "\n"
            + super()._diagnostic_text()
        )


def main() -> None:
    try:
        app = V22App()
        app.after(1200, app._refresh_validation_view)
        app.mainloop()
    except Exception:
        LOGGER.exception("Fatal V2.2 application error")
        raise


if __name__ == "__main__":
    main()
