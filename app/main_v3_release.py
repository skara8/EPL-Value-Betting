from __future__ import annotations

import main_v3
from main_v3 import LOGGER, V3App
from v3_clv import refresh_v3_clv_evaluations, v3_clv_summary
from v3_validation_gate import current_validation_grade, validation_gate

# Replace the preliminary forecast-only grade used while main_v3 was being
# developed with the final two-stage V3 gate. All inherited methods resolve
# this module-global at runtime.
main_v3.current_validation_grade = current_validation_grade


class V3ReleaseApp(V3App):
    def _apply_v3_result(self, rows, warnings, info_notes, saved, context_saved, edge_saved) -> None:
        super()._apply_v3_result(rows, warnings, info_notes, saved, context_saved, edge_saved)
        try:
            added = refresh_v3_clv_evaluations()
            if added:
                LOGGER.info("V3 added %d de-vigged sharp near-close CLV evaluation(s)", added)
        except Exception:
            LOGGER.exception("V3 CLV refresh failed")
        self._fill_v3_lab()
        self._fill_dashboard()

    def _fill_v3_lab(self) -> None:
        super()._fill_v3_lab()
        gate = validation_gate()
        clv = v3_clv_summary()
        self.v3_validation_grade_var.set(gate.grade.replace("_", " ").title())
        if clv.samples:
            avg = f"{clv.average_price_clv_pct:+.2f}%" if clv.average_price_clv_pct is not None else "—"
            ci = (
                f"[{clv.ci_low_pct:+.2f}%, {clv.ci_high_pct:+.2f}%]"
                if clv.ci_low_pct is not None and clv.ci_high_pct is not None else "—"
            )
            self.v3_lab_status_var.set(
                f"{gate.explanation} Sharp near-close samples: {clv.samples}; average price CLV {avg}; bootstrap 95% CI {ci}."
            )
        else:
            self.v3_lab_status_var.set(
                gate.explanation + " No qualifying Pinnacle H/D/A observation within 60 minutes of kickoff has been captured for a V3 research signal yet."
            )

    def _diagnostic_text(self) -> str:
        gate = validation_gate()
        clv = v3_clv_summary()
        return (
            f"V3 combined validation grade: {gate.grade}\n"
            f"V3 forecast grade: {gate.forecast_grade}\n"
            f"V3 sharp near-close grade: {gate.clv_grade}\n"
            f"V3 sharp near-close samples: {clv.samples}\n"
            + super()._diagnostic_text()
        )


def main() -> None:
    try:
        V3ReleaseApp().mainloop()
    except Exception:
        LOGGER.exception("Fatal V3 release application error")
        raise


if __name__ == "__main__":
    main()
