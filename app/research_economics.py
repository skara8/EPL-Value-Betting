from __future__ import annotations

from typing import Optional

from v3_storage import _db

CANDIDATE_STATUSES = (
    "V3 HIGH-CONFIDENCE CANDIDATE",
    "V3 +EV CANDIDATE — UNCERTAINTY",
)


def candidate_economic_summary() -> dict[str, Optional[float]]:
    """Summarise only strategy-selected research candidates.

    v3_economic_evidence intentionally stores close evidence for every decision
    so calibration/residual research remains possible. Strategy-level summary
    must not average the mutually exclusive HOME/DRAW/AWAY rows that were never
    selected by the candidate rule.
    """
    placeholders = ",".join("?" for _ in CANDIDATE_STATUSES)
    with _db() as con:
        row = con.execute(
            f"""SELECT COUNT(*),AVG(e.price_clv_pct),AVG(CASE WHEN e.price_clv_pct>0 THEN 1.0 ELSE 0.0 END),
                       SUM(e.settled),AVG(CASE WHEN e.settled=1 THEN e.unit_return END)
                FROM v3_economic_evidence e
                JOIN v3_decisions d ON d.id=e.decision_id
                WHERE d.status IN ({placeholders})""",
            CANDIDATE_STATUSES,
        ).fetchone()
    return {
        "candidates_with_final_close": int(row[0] or 0),
        "average_price_clv_pct": None if row[1] is None else float(row[1]),
        "positive_clv_rate": None if row[2] is None else float(row[2]),
        "settled_candidates": int(row[3] or 0),
        "unit_stake_roi": None if row[4] is None else float(row[4]),
    }


def actual_fill_summary() -> dict[str, Optional[float]]:
    """Compute realised economics from explicitly recorded fills only."""
    with _db() as con:
        fills = con.execute(
            """SELECT f.canonical_event_id,f.side,f.filled_odds,f.stake,f.fees,o.outcome,
                      (SELECT CASE f.side WHEN 'HOME' THEN s.odds_home WHEN 'DRAW' THEN s.odds_draw ELSE s.odds_away END
                       FROM v3_sharp_lines s
                       WHERE s.canonical_event_id=f.canonical_event_id AND s.is_final_pre_kickoff=1
                       ORDER BY s.captured_at DESC LIMIT 1) AS close_odds
               FROM v3_fills f
               LEFT JOIN v3_outcomes o ON o.canonical_event_id=f.canonical_event_id
               WHERE f.status='FILLED'"""
        ).fetchall()

    total_stake = 0.0
    total_profit = 0.0
    settled = 0
    clv_values = []
    for _event, side, filled_odds, stake, fees, outcome, close_odds in fills:
        stake = float(stake)
        fees = float(fees or 0.0)
        total_stake += stake
        if close_odds is not None and float(close_odds) > 1.0:
            clv_values.append((float(filled_odds) / float(close_odds) - 1.0) * 100.0)
        if outcome is None:
            continue
        settled += 1
        total_profit += stake * (float(filled_odds) - 1.0) - fees if str(outcome) == str(side) else -stake - fees

    return {
        "actual_fills": len(fills),
        "settled_fills": settled,
        "realised_roi": None if total_stake <= 0 or settled <= 0 else total_profit / total_stake,
        "average_fill_clv_pct": None if not clv_values else sum(clv_values) / len(clv_values),
    }
