from __future__ import annotations

from tkinter import ttk

import main as base_main
from config import DATA_DIR
from engine import combine_sources, fetch_polymarket_epl, fetch_sportsbet_epl
from advanced_market import enrich_rows
from market_storage import save_market_context
from edge_model import enrich_edge_model
from edge_storage import save_edge_snapshot
from main_v15 import V15App, LOGGER


class V15FinalApp(V15App):
    """Final V1.5 shell with model-specific settings/help text."""

    def _build_settings(self) -> None:
        left = ttk.LabelFrame(self.settings_tab, text="Data and edge screening", padding=16)
        left.pack(fill="x", pady=(0, 12))
        left.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(left, text="PulseScore API key").grid(row=row, column=0, sticky="w", pady=6)
        self.api_key_entry = ttk.Entry(left, textvariable=self.api_key_var, show="•", width=48)
        self.api_key_entry.grid(row=row, column=1, sticky="ew", padx=(14, 8), pady=6)
        ttk.Checkbutton(left, text="Remember in Windows Credential Manager", variable=self.remember_key_var).grid(row=row, column=2, sticky="w", pady=6)

        row += 1
        ttk.Label(left, text="Minimum model EV %").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(left, textvariable=self.min_ev_var, width=10).grid(row=row, column=1, sticky="w", padx=(14, 8), pady=6)
        ttk.Label(left, text="Default 4.0%. Applied to the independent V1.5 model EV, not Sportsbet de-vig probability.", style="Muted.TLabel").grid(row=row, column=2, sticky="w")

        row += 1
        ttk.Label(left, text="Minimum Polymarket volume ($)").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(left, textvariable=self.min_volume_var, width=12).grid(row=row, column=1, sticky="w", padx=(14, 8), pady=6)
        ttk.Label(left, text="0 disables the filter. Passing edges below this volume are labelled EDGE - LOW PM VOLUME.", style="Muted.TLabel").grid(row=row, column=2, sticky="w")

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

        info = ttk.LabelFrame(self.settings_tab, text="How V1.5 calculates edge", padding=16)
        info.pack(fill="x", pady=(16, 0))
        ttk.Label(
            info,
            text=(
                "1) Sportsbet is the target price. The app calculates raw implied probability and a power-method de-vig probability so we can inspect Sportsbet's margin allocation, but Sportsbet does not vote in its own fair probability.\n\n"
                "2) Polymarket executable YES asks are normalised to 100%. Pinnacle 1X2 is power-de-vigged when available. Pinnacle Asian Handicap + total-goals prices are converted into an implied Poisson score model. The Pinnacle 1X2 and AH estimates are first combined into one Pinnacle provider component so one bookmaker does not get two votes.\n\n"
                "3) The external provider components are combined into the V1.5 fair probability. EV = fair probability × Sportsbet decimal odds − 1.\n\n"
                "4) Conservative EV uses the least favourable external provider probability. This tells you whether the apparent edge survives disagreement rather than existing only because of an average.\n\n"
                "5) Favourite-longshot and away-favourite effects are research tags. They can elevate a robust edge to BIAS-ALIGNED ROBUST EDGE, but V1.5 does not add an unvalidated probability bonus."
            ),
            wraplength=1280,
            justify="left",
        ).pack(anchor="w")

        caution = ttk.LabelFrame(self.settings_tab, text="Why V1.5 does not simply 'increase' the EV", padding=16)
        caution.pack(fill="x", pady=(12, 0))
        ttk.Label(
            caution,
            text=(
                "EV only becomes more accurate if the probability estimate becomes more accurate. Arbitrarily adding 1–2 percentage points for a favourite or away favourite would manufacture an edge rather than discover one. "
                "V1.5 therefore adds information (sharp 1X2, Asian Handicap, totals, cross-market agreement and bias residuals) and stores all of it. The eventual numerical bias adjustment should be learned from completed-match and closing-price data and then tested out-of-sample."
            ),
            wraplength=1280,
            justify="left",
        ).pack(anchor="w")

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

            # Apply the existing user-configurable Polymarket volume quality
            # threshold to V1.5 edge labels before data is persisted.
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


def main() -> None:
    try:
        V15FinalApp().mainloop()
    except Exception:
        LOGGER.exception("Fatal V1.5 application error")
        raise


if __name__ == "__main__":
    main()
