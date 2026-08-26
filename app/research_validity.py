from __future__ import annotations

from tkinter import ttk

from main_v3 import LOGGER, V3App
from v3_storage import (
    economic_summary,
    record_provenance,
    record_sharp_snapshots,
    reconcile_outcomes_from_histories,
    refresh_economic_evidence,
)


class ResearchValidityApp(V3App):
    """Current V3 application with the research-validity evidence lifecycle.

    This layer does not change the independent football probability. It makes
    chronology, execution evidence, closing-line evidence and realised outcomes
    auditable around that model.
    """

    def _build_settings(self) -> None:
        super()._build_settings()
        frame = ttk.LabelFrame(self.settings_tab, text="Research validity", padding=12)
        frame.pack(fill="x", pady=(10, 0))
        ttk.Label(
            frame,
            text=(
                "Economic evidence is tracked separately from forecast quality. Only execution-eligible observed quotes can enter EV ranking. "
                "Strictly matched Pinnacle observations are de-vigged and stored at point-in-time horizons; only a genuine final pre-kickoff snapshot is used for CLV. "
                "Observed decision quotes are research proxies, not assumed fills. Actual fills have their own ledger."
            ),
            wraplength=1260,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text=(
                "Walk-forward folds are date-atomic and compare V3 against league-frequency, Elo-only and dynamic-score-only baselines. "
                "xG, player/XI features, hybrid market models and staking remain research-gated until pre-registered chronological ablation demonstrates durable incremental information."
            ),
            wraplength=1260,
            justify="left",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 0))

    def _apply_v3_result(self, rows, warnings, info_notes, saved, context_saved, edge_saved) -> None:
        evidence_notes = []
        try:
            record_provenance(
                "analysis",
                "current-fixture-pipeline",
                record_count=len(rows),
                metadata={
                    "supported_leagues": len(self.v3_model_result.supported_leagues) if self.v3_model_result else 0,
                    "modelled_fixtures": len(self.v3_forecasts),
                    "validation_leagues": len(self.v3_validation_reports),
                },
            )
            sharp = record_sharp_snapshots(rows)
            settled = reconcile_outcomes_from_histories(self.v3_histories)
            evidence = refresh_economic_evidence()
            summary = economic_summary()
            evidence_notes.append(
                f"Research evidence: stored {sharp} strict sharp-market snapshot(s), settled {settled} prior event(s), "
                f"and refreshed {evidence} decision/close evidence row(s)."
            )
            if summary["decisions_with_final_close"]:
                clv = summary["average_price_clv_pct"]
                positive = summary["positive_clv_rate"]
                evidence_notes.append(
                    "Final-close evidence: "
                    f"{summary['decisions_with_final_close']} decision(s) with final sharp close · "
                    f"mean price CLV {'—' if clv is None else f'{clv:+.2f}%'} · "
                    f"positive CLV {'—' if positive is None else f'{positive*100:.1f}%'} · "
                    f"actual recorded fills {summary['actual_fills']}."
                )
        except Exception as exc:
            LOGGER.exception("V3 research evidence refresh failed")
            warnings.append(f"Research evidence refresh: {exc}")

        info_notes.extend(evidence_notes)
        super()._apply_v3_result(rows, warnings, info_notes, saved, context_saved, edge_saved)

    def _fill_v3_validation(self) -> None:
        super()._fill_v3_validation()
        pooled = self.v3_pooled_report
        if pooled is None or pooled.predictions <= 0:
            return
        baseline = pooled.delta_log_loss_vs_best_baseline
        rps = pooled.rps
        if baseline is None:
            comparison = "baseline comparison unavailable"
        elif baseline < 0:
            comparison = f"log loss {abs(baseline):.4f} better than the best simple baseline"
        else:
            comparison = f"log loss {baseline:.4f} worse than the best simple baseline"
        self.v3_validation_status_var.set(
            f"{pooled.predictions} date-atomic chronological OOS predictions across {len(self.v3_validation_reports)} league(s) · "
            f"{comparison} · RPS {'—' if rps is None else f'{rps:.4f}'}. Forecast metrics are evidence, not a profitability claim."
        )


def main() -> None:
    try:
        ResearchValidityApp().mainloop()
    except Exception:
        LOGGER.exception("Fatal V3 research-validity application error")
        raise
