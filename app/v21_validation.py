from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from config import DB_FILE
from engine import CombinedMatch
from storage import event_key
from strategy_v21 import V21Decision


SCHEMA = """
CREATE TABLE IF NOT EXISTS v21_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL,
    kickoff TEXT NOT NULL,
    league TEXT,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    side TEXT NOT NULL,
    selection_name TEXT NOT NULL,
    first_detected_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    first_quote_source TEXT NOT NULL,
    first_quote_odds REAL NOT NULL,
    last_quote_source TEXT NOT NULL,
    last_quote_odds REAL NOT NULL,
    model_probability REAL NOT NULL,
    conservative_probability REAL NOT NULL,
    first_model_ev_pct REAL NOT NULL,
    first_robust_ev_pct REAL NOT NULL,
    last_model_ev_pct REAL NOT NULL,
    last_robust_ev_pct REAL NOT NULL,
    source_count INTEGER NOT NULL,
    disagreement_pp REAL,
    confidence TEXT,
    status TEXT,
    UNIQUE(event_key, side)
);
CREATE INDEX IF NOT EXISTS idx_v21_decisions_kickoff ON v21_decisions(kickoff DESC);
CREATE INDEX IF NOT EXISTS idx_v21_decisions_status ON v21_decisions(status, first_detected_at DESC);
"""

# A manually refreshed desktop application cannot claim a true closing price
# unless the captured Pinnacle quote is reasonably near kickoff. Six hours is
# labelled a near-close benchmark; later versions can tighten this when an
# automated scheduled snapshot collector exists.
MAX_SHARP_CLOSE_MINUTES = 360.0


@dataclass
class V21ValidationSummary:
    decisions: int = 0
    robust_decisions: int = 0
    with_sharp_close: int = 0
    average_sharp_clv_pct: Optional[float] = None
    positive_sharp_clv_pct: Optional[float] = None
    average_first_robust_ev_pct: Optional[float] = None


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
        d = datetime.fromisoformat(text)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def save_v21_decisions(rows: Iterable[CombinedMatch], decisions: Iterable[V21Decision]) -> int:
    row_map = {row.match_name: row for row in rows}
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    changed = 0
    with _connect() as con:
        for d in decisions:
            row = row_map.get(d.match_name)
            if row is None:
                continue
            values = (
                event_key(row), row.kickoff.isoformat(), d.league,
                row.home_team, row.away_team, d.side, d.selection,
                now, now, d.quote_source, d.quote_odds, d.quote_source, d.quote_odds,
                d.model_probability, d.conservative_probability,
                d.model_ev_pct, d.robust_ev_pct, d.model_ev_pct, d.robust_ev_pct,
                d.source_count, d.disagreement_pp, d.confidence, d.status,
            )
            before = con.total_changes
            con.execute(
                """
                INSERT INTO v21_decisions (
                    event_key,kickoff,league,home_team,away_team,side,selection_name,
                    first_detected_at,last_seen_at,first_quote_source,first_quote_odds,
                    last_quote_source,last_quote_odds,model_probability,conservative_probability,
                    first_model_ev_pct,first_robust_ev_pct,last_model_ev_pct,last_robust_ev_pct,
                    source_count,disagreement_pp,confidence,status
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_key,side) DO UPDATE SET
                    last_seen_at=excluded.last_seen_at,
                    last_quote_source=excluded.last_quote_source,
                    last_quote_odds=excluded.last_quote_odds,
                    last_model_ev_pct=excluded.last_model_ev_pct,
                    last_robust_ev_pct=excluded.last_robust_ev_pct,
                    source_count=excluded.source_count,
                    disagreement_pp=excluded.disagreement_pp,
                    confidence=excluded.confidence,
                    status=excluded.status
                """,
                values,
            )
            if con.total_changes > before:
                changed += 1
        con.commit()
    return changed


def _closing_pinnacle_quote(
    con: sqlite3.Connection,
    key: str,
    side: str,
    kickoff: str,
) -> tuple[Optional[float], Optional[float]]:
    column = {"HOME": "pin_home", "DRAW": "pin_draw", "AWAY": "pin_away"}.get(side)
    if not column:
        return None, None
    kickoff_dt = _dt(kickoff)
    try:
        rows = con.execute(
            f"SELECT captured_at,{column} AS odds FROM market_context_snapshots "
            f"WHERE event_key=? AND {column} IS NOT NULL ORDER BY id DESC",
            (key,),
        ).fetchall()
    except sqlite3.OperationalError:
        return None, None
    for row in rows:
        captured = _dt(row["captured_at"])
        if captured is None:
            continue
        if kickoff_dt is None or captured <= kickoff_dt:
            odds = row["odds"]
            if odds is None or float(odds) <= 1:
                continue
            minutes = None if kickoff_dt is None else max(0.0, (kickoff_dt - captured).total_seconds() / 60.0)
            return float(odds), minutes
    return None, None


def v21_validation_summary() -> V21ValidationSummary:
    with _connect() as con:
        rows = con.execute("SELECT * FROM v21_decisions ORDER BY id DESC").fetchall()
        if not rows:
            return V21ValidationSummary()

        robust = [r for r in rows if r["status"] == "ROBUST +EV"]
        clvs: list[float] = []
        for row in robust:
            close, minutes = _closing_pinnacle_quote(con, row["event_key"], row["side"], row["kickoff"])
            if close is None or minutes is None or minutes > MAX_SHARP_CLOSE_MINUTES:
                continue
            # Positive means the originally observed non-reference execution
            # price was higher than a near-close Pinnacle quote on the same side.
            clvs.append((float(row["first_quote_odds"]) / close - 1.0) * 100.0)

        avg_clv = sum(clvs) / len(clvs) if clvs else None
        positive = sum(1 for x in clvs if x > 0) / len(clvs) * 100.0 if clvs else None
        avg_robust = (
            sum(float(r["first_robust_ev_pct"]) for r in robust) / len(robust)
            if robust else None
        )
        return V21ValidationSummary(
            decisions=len(rows),
            robust_decisions=len(robust),
            with_sharp_close=len(clvs),
            average_sharp_clv_pct=avg_clv,
            positive_sharp_clv_pct=positive,
            average_first_robust_ev_pct=avg_robust,
        )
