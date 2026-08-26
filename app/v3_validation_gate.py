from __future__ import annotations

from dataclasses import dataclass

from v3_clv import V3CLVSummary, v3_clv_summary
from v3_storage import latest_walkforward_summary


@dataclass(frozen=True)
class V3ValidationGate:
    grade: str
    forecast_grade: str
    clv_grade: str
    clv_samples: int
    explanation: str


def validation_gate() -> V3ValidationGate:
    walk = latest_walkforward_summary()
    forecast = str((walk or {}).get("forecast_grade") or "UNVALIDATED").upper()
    clv: V3CLVSummary = v3_clv_summary()

    if forecast == "FORECAST_VALIDATED" and clv.edge_grade == "CLV_VALIDATED":
        grade = "EDGE_VALIDATED"
        explanation = (
            "The football challenger passed the chronological forecast gate and the saved research prices also passed the de-vigged sharp near-close CLV gate."
        )
    elif forecast == "FORECAST_VALIDATED":
        grade = "FORECAST_VALIDATED"
        explanation = (
            "Forecast improvement passed its chronological gate, but there is not yet enough positive sharp near-close evidence to call a betting edge validated."
        )
    elif forecast in {"RESEARCH_ONLY", "INSUFFICIENT_SAMPLE"}:
        grade = forecast
        explanation = "The forecasting evidence is still research-only; edge validation cannot pass yet."
    else:
        grade = "UNVALIDATED"
        explanation = "No qualifying V3 walk-forward validation has been completed yet."

    return V3ValidationGate(
        grade=grade,
        forecast_grade=forecast,
        clv_grade=clv.edge_grade,
        clv_samples=clv.samples,
        explanation=explanation,
    )


def current_validation_grade() -> str:
    return validation_gate().grade
