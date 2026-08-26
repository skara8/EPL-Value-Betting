from __future__ import annotations

"""Point-in-time sharp near-close validation for V3.

A desktop app cannot honestly claim a closing price unless it actually captured
one. V3 therefore accepts only a Pinnacle H/D/A snapshot observed no more than
60 minutes before kickoff and labels it *near-close*. The Pinnacle margin is
removed before the originally observed execution quote is evaluated.
"""

import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Optional

from config import DB_FILE
from edge_model import power_devig

MAX_NEAR_CLOSE_MINUTES = 60.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS v3_clv_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER NOT NULL UNIQUE,
    event_key TEXT NOT NULL,
    side TEXT NOT NULL,
    kickoff_utc TEXT NOT NULL,
    decision_at TEXT NOT NULL,
    quote_source TEXT NOT NULL,
    quote_odds REAL NOT NULL,
    model_probability REAL NOT NULL,
    close_captured_at TEXT NOT NULL,
    close_minutes_before_kickoff REAL NOT NULL,
    pin_home REAL NOT NULL,
    pin_draw REAL NOT NULL,
    pin_away REAL NOT NULL,
    close_fair_home REAL NOT NULL,
    close_fair_draw REAL NOT NULL,
    close_fair_away REAL NOT NULL,
    close_side_probability REAL NOT NULL,
    close_fair_odds REAL NOT NULL,
    price_clv_pct REAL NOT NULL,
    model_minus_close_pp REAL NOT NULL,
    evaluated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v3_clv_event ON v3_clv_evaluations(event_key, side);
"""


@dataclass
class V3CLVSummary:
    samples: int = 0
    average_price_clv_pct: Optional[float] = None
    positive_price_clv_pct: Optional[float] = None
    ci_low_pct: Optional[float] = None
    ci_high_pct: Optional[float] = None
    average_model_minus_close_pp: Optional[float] = None
    edge_grade: str = "INSUFFICIENT_CLV"


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def _dt(value: str) -> Optional[datetime]:
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _close_probability(con: sqlite3.Connection, event_key: str, kickoff: str, side: str):
    kickoff_dt = _dt(kickoff)
    if kickoff_dt is None:
        return None
    try:
        rows = con.execute(
            """SELECT captured_at,pin_home,pin_draw,pin_away
               FROM market_context_snapshots
               WHERE event_key=? AND pin_home IS NOT NULL AND pin_draw IS NOT NULL AND pin_away IS NOT NULL
               ORDER BY id DESC""",
            (event_key,),
        ).fetchall()
    except sqlite3.OperationalError:
        return None

    side_idx = {"HOME": 0, "DRAW": 1, "AWAY": 2}.get(side)
    if side_idx is None:
        return None
    for row in rows:
        captured = _dt(row["captured_at"])
        if captured is None or captured > kickoff_dt:
            continue
        minutes = (kickoff_dt - captured).total_seconds() / 60.0
        if minutes < 0 or minutes > MAX_NEAR_CLOSE_MINUTES:
            # Rows are reverse chronological. Once we're beyond the window,
            # older rows will also be too early.
            if minutes > MAX_NEAR_CLOSE_MINUTES:
                break
            continue
        odds = (float(row["pin_home"]), float(row["pin_draw"]), float(row["pin_away"]))
        fair = power_devig(*odds)
        if fair is None:
            continue
        return captured, minutes, odds, fair, fair[side_idx]
    return None


def refresh_v3_clv_evaluations() -> int:
    """Evaluate first threshold-clearing V3 observations once a sharp near-close exists."""
    changed = 0
    evaluated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect() as con:
        # First point-in-time threshold-clearing research observation for each
        # event/side. Using MIN(id) prevents later prices from rewriting history.
        try:
            decisions = con.execute(
                """SELECT d.* FROM v3_decision_snapshots d
                   JOIN (
                       SELECT event_key,side,MIN(id) AS first_id
                       FROM v3_decision_snapshots
                       WHERE status IN ('RESEARCH +EV — UNVALIDATED','VALIDATED +EV')
                       GROUP BY event_key,side
                   ) firsts ON d.id=firsts.first_id
                   WHERE d.id NOT IN (SELECT decision_id FROM v3_clv_evaluations)"""
            ).fetchall()
        except sqlite3.OperationalError:
            return 0

        for decision in decisions:
            close = _close_probability(con, decision["event_key"], decision["kickoff_utc"], decision["side"])
            if close is None:
                continue
            captured, minutes, pin_odds, fair, close_p = close
            quote_odds = float(decision["quote_odds"])
            model_p = float(decision["model_probability"])
            close_fair_odds = 1.0 / close_p
            # Positive means the original execution price was better than the
            # later de-vigged sharp fair price for the same outcome.
            price_clv = (quote_odds * close_p - 1.0) * 100.0
            model_gap = (model_p - close_p) * 100.0
            con.execute(
                """INSERT INTO v3_clv_evaluations (
                    decision_id,event_key,side,kickoff_utc,decision_at,quote_source,quote_odds,model_probability,
                    close_captured_at,close_minutes_before_kickoff,pin_home,pin_draw,pin_away,
                    close_fair_home,close_fair_draw,close_fair_away,close_side_probability,close_fair_odds,
                    price_clv_pct,model_minus_close_pp,evaluated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    decision["id"], decision["event_key"], decision["side"], decision["kickoff_utc"],
                    decision["captured_at"], decision["quote_source"], quote_odds, model_p,
                    captured.isoformat(timespec="seconds"), minutes,
                    pin_odds[0], pin_odds[1], pin_odds[2], fair[0], fair[1], fair[2], close_p, close_fair_odds,
                    price_clv, model_gap, evaluated_at,
                ),
            )
            changed += 1
        con.commit()
    return changed


def _bootstrap_mean_ci(values: list[float], samples: int = 1000) -> tuple[float, float]:
    if not values:
        return float("nan"), float("nan")
    if len(values) < 3:
        avg = mean(values)
        return avg, avg
    rng = random.Random(300026)
    estimates = []
    for _ in range(max(200, samples)):
        estimates.append(mean(rng.choice(values) for _ in values))
    estimates.sort()
    return estimates[int(0.025 * (len(estimates) - 1))], estimates[int(0.975 * (len(estimates) - 1))]


def v3_clv_summary() -> V3CLVSummary:
    with _connect() as con:
        rows = con.execute("SELECT * FROM v3_clv_evaluations ORDER BY id").fetchall()
    if not rows:
        return V3CLVSummary()

    clv = [float(row["price_clv_pct"]) for row in rows]
    gaps = [float(row["model_minus_close_pp"]) for row in rows]
    low, high = _bootstrap_mean_ci(clv)
    positive = sum(1 for value in clv if value > 0) / len(clv) * 100.0

    # Deliberately demanding and only about execution/price evidence. The app
    # also requires the separate forecast-quality gate before combining this
    # into EDGE_VALIDATED.
    if len(clv) >= 200 and low > 0.0 and positive > 52.0:
        grade = "CLV_VALIDATED"
    elif len(clv) >= 50:
        grade = "CLV_RESEARCH"
    else:
        grade = "INSUFFICIENT_CLV"

    return V3CLVSummary(
        samples=len(clv),
        average_price_clv_pct=mean(clv),
        positive_price_clv_pct=positive,
        ci_low_pct=low,
        ci_high_pct=high,
        average_model_minus_close_pp=mean(gaps),
        edge_grade=grade,
    )
