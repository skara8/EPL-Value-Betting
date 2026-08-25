from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from config import DB_FILE
from engine import CombinedMatch
from football_intelligence import LeagueMatchRef, MatchIntelligence
from storage import event_key


SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL,
    kickoff TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    side TEXT NOT NULL,
    selection_name TEXT NOT NULL,
    first_detected_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    first_odds REAL NOT NULL,
    last_seen_odds REAL NOT NULL,
    first_model_probability REAL,
    first_base_ev_pct REAL,
    first_decision_ev_pct REAL,
    first_conservative_ev_pct REAL,
    confidence TEXT,
    signal TEXT,
    intelligence_quality TEXT,
    xi_rating REAL,
    recent_form_rating REAL,
    tactical_rating REAL,
    rest_rating REAL,
    context_shift_pp REAL,
    UNIQUE(event_key, side)
);
CREATE INDEX IF NOT EXISTS idx_decision_event ON decision_recommendations(event_key);
CREATE INDEX IF NOT EXISTS idx_decision_detected ON decision_recommendations(first_detected_at DESC);

CREATE TABLE IF NOT EXISTS match_results (
    event_key TEXT PRIMARY KEY,
    kickoff TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    home_score INTEGER NOT NULL,
    away_score INTEGER NOT NULL,
    result_side TEXT NOT NULL,
    source TEXT NOT NULL,
    synced_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_result_kickoff ON match_results(kickoff DESC);
"""


@dataclass
class ValidationRow:
    event_key: str
    kickoff: str
    match_name: str
    selection: str
    side: str
    first_odds: float
    closing_odds: Optional[float]
    clv_pct: Optional[float]
    close_minutes_before: Optional[float]
    close_quality: str
    model_probability: Optional[float]
    decision_ev_pct: Optional[float]
    result: str
    realised_profit_pct: Optional[float]
    intelligence_quality: str
    xi_rating: Optional[float]
    recent_form_rating: Optional[float]
    tactical_rating: Optional[float]


@dataclass
class ValidationSummary:
    recommendations: int = 0
    settled: int = 0
    wins: int = 0
    roi_pct: Optional[float] = None
    average_clv_pct: Optional[float] = None
    positive_clv_pct: Optional[float] = None
    calibration_gap_pp: Optional[float] = None
    binary_brier: Optional[float] = None
    close_quality_count: int = 0



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


def save_recommendations(rows: Iterable[CombinedMatch], ideas: Iterable[object], intelligence: Optional[dict[str, MatchIntelligence]] = None) -> int:
    intelligence = intelligence or {}
    row_map = {row.match_name: row for row in rows}
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    inserted = 0
    with _connect() as con:
        for idea in ideas:
            match_name = getattr(idea, "match_name", "")
            row = row_map.get(match_name)
            if row is None:
                continue
            side = str(getattr(idea, "side", "")).upper()
            if side not in {"HOME", "DRAW", "AWAY"}:
                continue
            intel = intelligence.get(match_name)
            values = (
                event_key(row), row.kickoff.isoformat(), row.home_team, row.away_team,
                side, str(getattr(idea, "selection", side)), now, now,
                float(getattr(idea, "sportsbet_odds")), float(getattr(idea, "sportsbet_odds")),
                getattr(idea, "adjusted_probability", None),
                getattr(idea, "base_ev_pct", None),
                getattr(idea, "decision_ev_pct", None),
                getattr(idea, "conservative_ev_pct", None),
                str(getattr(idea, "confidence", "")),
                str(getattr(idea, "signal", "")),
                getattr(intel, "data_quality", "") if intel else "",
                getattr(intel, "xi_rating", None) if intel else None,
                getattr(intel, "recent_form_rating", None) if intel else None,
                getattr(intel, "tactical_rating", None) if intel else None,
                getattr(intel, "rest_rating", None) if intel else None,
                getattr(idea, "context_shift_pp", None),
            )
            before = con.total_changes
            con.execute(
                """
                INSERT INTO decision_recommendations (
                    event_key,kickoff,home_team,away_team,side,selection_name,
                    first_detected_at,last_seen_at,first_odds,last_seen_odds,
                    first_model_probability,first_base_ev_pct,first_decision_ev_pct,
                    first_conservative_ev_pct,confidence,signal,intelligence_quality,
                    xi_rating,recent_form_rating,tactical_rating,rest_rating,context_shift_pp
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_key,side) DO UPDATE SET
                    last_seen_at=excluded.last_seen_at,
                    last_seen_odds=excluded.last_seen_odds,
                    confidence=excluded.confidence,
                    signal=excluded.signal
                """,
                values,
            )
            if con.total_changes > before:
                inserted += 1
        con.commit()
    return inserted


def _find_event_key_for_result(con: sqlite3.Connection, ref: LeagueMatchRef) -> Optional[str]:
    candidates = con.execute(
        """
        SELECT event_key,kickoff
        FROM snapshots
        WHERE home_team=? AND away_team=?
        GROUP BY event_key,kickoff
        """,
        (ref.home_team, ref.away_team),
    ).fetchall()
    if not candidates:
        candidates = con.execute(
            """
            SELECT event_key,kickoff
            FROM decision_recommendations
            WHERE home_team=? AND away_team=?
            GROUP BY event_key,kickoff
            """,
            (ref.home_team, ref.away_team),
        ).fetchall()
    if not candidates:
        return None
    if ref.kickoff is None:
        return str(candidates[0]["event_key"])
    best_key = None
    best_hours = 9999.0
    target = ref.kickoff.astimezone(timezone.utc)
    for row in candidates:
        dt = _dt(row["kickoff"])
        if dt is None:
            continue
        hours = abs((dt - target).total_seconds()) / 3600.0
        if hours < best_hours:
            best_hours = hours
            best_key = str(row["event_key"])
    return best_key if best_hours <= 12.0 else None


def sync_results(refs: Iterable[LeagueMatchRef]) -> int:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    synced = 0
    with _connect() as con:
        for ref in refs:
            if not ref.finished or ref.home_score is None or ref.away_score is None:
                continue
            key = _find_event_key_for_result(con, ref)
            if not key:
                continue
            if ref.home_score > ref.away_score:
                result_side = "HOME"
            elif ref.home_score < ref.away_score:
                result_side = "AWAY"
            else:
                result_side = "DRAW"
            before = con.total_changes
            con.execute(
                """
                INSERT INTO match_results (
                    event_key,kickoff,home_team,away_team,home_score,away_score,
                    result_side,source,synced_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_key) DO UPDATE SET
                    home_score=excluded.home_score,
                    away_score=excluded.away_score,
                    result_side=excluded.result_side,
                    source=excluded.source,
                    synced_at=excluded.synced_at
                """,
                (
                    key,
                    ref.kickoff.isoformat() if ref.kickoff else "",
                    ref.home_team, ref.away_team,
                    int(ref.home_score), int(ref.away_score), result_side,
                    "FotMob", now,
                ),
            )
            if con.total_changes > before:
                synced += 1
        con.commit()
    return synced


def backfill_recommendations_from_edge_history(min_ev_pct: float = 4.0) -> int:
    """Create research decisions from old V1.5+ history when no live alert existed.

    The first historical snapshot where a side crossed the EV threshold is used.
    These rows are labelled HISTORICAL-BACKFILL and should be interpreted as a
    research reconstruction rather than a contemporaneous V1.8 dashboard call.
    """
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    inserted = 0
    with _connect() as con:
        try:
            rows = con.execute(
                """
                SELECT e.*
                FROM edge_model_snapshots e
                JOIN (
                    SELECT event_key, side, MIN(id) AS first_id
                    FROM edge_model_snapshots
                    WHERE model_ev_pct >= ? AND sportsbet_odds IS NOT NULL
                    GROUP BY event_key, side
                ) x ON x.first_id=e.id
                ORDER BY e.id ASC
                """,
                (float(min_ev_pct),),
            ).fetchall()
        except sqlite3.OperationalError:
            return 0
        for r in rows:
            before = con.total_changes
            con.execute(
                """
                INSERT OR IGNORE INTO decision_recommendations (
                    event_key,kickoff,home_team,away_team,side,selection_name,
                    first_detected_at,last_seen_at,first_odds,last_seen_odds,
                    first_model_probability,first_base_ev_pct,first_decision_ev_pct,
                    first_conservative_ev_pct,confidence,signal,intelligence_quality,
                    xi_rating,recent_form_rating,tactical_rating,rest_rating,context_shift_pp
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    r["event_key"], r["kickoff"], r["home_team"], r["away_team"],
                    r["side"], r["selection_name"], r["captured_at"], r["captured_at"],
                    r["sportsbet_odds"], r["sportsbet_odds"], r["model_probability"],
                    r["model_ev_pct"], r["model_ev_pct"], r["conservative_ev_pct"],
                    r["confidence"], "HISTORICAL-BACKFILL", "", None, None, None, None, 0.0,
                ),
            )
            if con.total_changes > before:
                inserted += 1
        con.commit()
    return inserted


def _closing_snapshot(con: sqlite3.Connection, event_key_value: str, side: str, kickoff: str) -> tuple[Optional[float], Optional[float]]:
    column = {"HOME": "sb_home", "DRAW": "sb_draw", "AWAY": "sb_away"}.get(side)
    if not column:
        return None, None
    kickoff_dt = _dt(kickoff)
    rows = con.execute(
        f"SELECT captured_at,{column} AS odds FROM snapshots WHERE event_key=? AND {column} IS NOT NULL ORDER BY id DESC",
        (event_key_value,),
    ).fetchall()
    for row in rows:
        captured = _dt(row["captured_at"])
        if captured is None:
            continue
        if kickoff_dt is None or captured <= kickoff_dt:
            minutes = None if kickoff_dt is None else max(0.0, (kickoff_dt - captured).total_seconds() / 60.0)
            return float(row["odds"]), minutes
    return None, None


def _close_quality(minutes: Optional[float]) -> str:
    if minutes is None:
        return "UNKNOWN"
    if minutes <= 90:
        return "CLOSE"
    if minutes <= 360:
        return "NEAR CLOSE"
    return "LAST OBSERVED"


def validation_rows(limit: int = 500) -> list[ValidationRow]:
    output: list[ValidationRow] = []
    with _connect() as con:
        rows = con.execute(
            """
            SELECT d.*, r.home_score, r.away_score, r.result_side
            FROM decision_recommendations d
            LEFT JOIN match_results r ON r.event_key=d.event_key
            ORDER BY d.kickoff DESC, d.id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        for row in rows:
            close_odds, minutes = _closing_snapshot(con, row["event_key"], row["side"], row["kickoff"])
            clv = None
            if close_odds and close_odds > 1 and row["first_odds"]:
                # Positive = the originally flagged price was better than the
                # last observed pre-kickoff Sportsbet price.
                clv = (float(row["first_odds"]) / float(close_odds) - 1.0) * 100.0
            settled = row["result_side"] is not None
            won = settled and row["side"] == row["result_side"]
            realised = None
            result_text = "Pending"
            if settled:
                realised = (float(row["first_odds"]) - 1.0) * 100.0 if won else -100.0
                result_text = f"WIN {row['home_score']}-{row['away_score']}" if won else f"LOSS {row['home_score']}-{row['away_score']}"
            output.append(
                ValidationRow(
                    event_key=row["event_key"],
                    kickoff=row["kickoff"],
                    match_name=f"{row['home_team']} v {row['away_team']}",
                    selection=row["selection_name"],
                    side=row["side"],
                    first_odds=float(row["first_odds"]),
                    closing_odds=close_odds,
                    clv_pct=clv,
                    close_minutes_before=minutes,
                    close_quality=_close_quality(minutes),
                    model_probability=float(row["first_model_probability"]) if row["first_model_probability"] is not None else None,
                    decision_ev_pct=float(row["first_decision_ev_pct"]) if row["first_decision_ev_pct"] is not None else None,
                    result=result_text,
                    realised_profit_pct=realised,
                    intelligence_quality=row["intelligence_quality"] or "",
                    xi_rating=float(row["xi_rating"]) if row["xi_rating"] is not None else None,
                    recent_form_rating=float(row["recent_form_rating"]) if row["recent_form_rating"] is not None else None,
                    tactical_rating=float(row["tactical_rating"]) if row["tactical_rating"] is not None else None,
                )
            )
    return output


def validation_summary(rows: Optional[list[ValidationRow]] = None) -> ValidationSummary:
    rows = validation_rows() if rows is None else rows
    settled = [r for r in rows if r.realised_profit_pct is not None]
    wins = [r for r in settled if r.realised_profit_pct is not None and r.realised_profit_pct > 0]
    clv = [r.clv_pct for r in settled if r.clv_pct is not None]
    close_quality = [r for r in settled if r.close_quality in {"CLOSE", "NEAR CLOSE"}]
    roi = sum(r.realised_profit_pct for r in settled if r.realised_profit_pct is not None) / len(settled) if settled else None
    avg_clv = sum(clv) / len(clv) if clv else None
    positive_clv = sum(1 for x in clv if x > 0) / len(clv) * 100.0 if clv else None

    probability_rows = [r for r in settled if r.model_probability is not None]
    calibration_gap = None
    brier = None
    if probability_rows:
        avg_p = sum(float(r.model_probability) for r in probability_rows) / len(probability_rows)
        actual = sum(1.0 for r in probability_rows if r.realised_profit_pct and r.realised_profit_pct > 0) / len(probability_rows)
        calibration_gap = (actual - avg_p) * 100.0
        brier = sum((float(r.model_probability) - (1.0 if r.realised_profit_pct and r.realised_profit_pct > 0 else 0.0)) ** 2 for r in probability_rows) / len(probability_rows)

    return ValidationSummary(
        recommendations=len(rows),
        settled=len(settled),
        wins=len(wins),
        roi_pct=roi,
        average_clv_pct=avg_clv,
        positive_clv_pct=positive_clv,
        calibration_gap_pp=calibration_gap,
        binary_brier=brier,
        close_quality_count=len(close_quality),
    )
