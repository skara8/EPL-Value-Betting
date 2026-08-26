from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Iterable, Optional

from config import DB_FILE
from engine import CombinedMatch
from storage import event_key
from strategy_v3 import V3Decision
from v3_walkforward import WalkForwardResult

MODEL_VERSION = "3.0.0"
FEATURE_SCHEMA = "v3-scientific-1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS v3_model_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    event_key TEXT NOT NULL,
    kickoff_utc TEXT NOT NULL,
    league TEXT,
    league_key TEXT,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    model_version TEXT NOT NULL,
    feature_schema TEXT NOT NULL,
    p_home REAL,
    p_draw REAL,
    p_away REAL,
    stress_home REAL,
    stress_draw REAL,
    stress_away REAL,
    dc_home REAL,
    dc_draw REAL,
    dc_away REAL,
    elo_home REAL,
    elo_draw REAL,
    elo_away REAL,
    short_home REAL,
    short_draw REAL,
    short_away REAL,
    long_home REAL,
    long_draw REAL,
    long_away REAL,
    component_names TEXT,
    component_count INTEGER,
    lambda_home REAL,
    lambda_away REAL,
    model_spread_pp REAL,
    confidence TEXT,
    history_matches INTEGER,
    home_history_matches INTEGER,
    away_history_matches INTEGER,
    challenger_home REAL,
    challenger_draw REAL,
    challenger_away REAL,
    challenger_eta REAL,
    challenger_annual_shrink REAL,
    challenger_validation_logloss REAL,
    challenger_gap_pp REAL,
    market_reference_home REAL,
    market_reference_draw REAL,
    market_reference_away REAL
);
CREATE INDEX IF NOT EXISTS idx_v3_model_event ON v3_model_snapshots(event_key, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_v3_model_league ON v3_model_snapshots(league_key, captured_at DESC);

CREATE TABLE IF NOT EXISTS v3_quote_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    event_key TEXT NOT NULL,
    event_fingerprint TEXT,
    kickoff_utc TEXT NOT NULL,
    league TEXT,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    source TEXT NOT NULL,
    side TEXT NOT NULL,
    odds REAL NOT NULL,
    received_at TEXT,
    market_timestamp TEXT,
    provider_event_id TEXT,
    match_confidence REAL,
    model_probability REAL,
    model_ev_pct REAL
);
CREATE INDEX IF NOT EXISTS idx_v3_quote_event ON v3_quote_snapshots(event_key, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_v3_quote_source ON v3_quote_snapshots(source, captured_at DESC);

CREATE TABLE IF NOT EXISTS v3_decision_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at TEXT NOT NULL,
    event_key TEXT NOT NULL,
    kickoff_utc TEXT NOT NULL,
    league TEXT,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    model_version TEXT NOT NULL,
    side TEXT NOT NULL,
    selection_name TEXT NOT NULL,
    quote_source TEXT NOT NULL,
    quote_odds REAL NOT NULL,
    model_probability REAL NOT NULL,
    fair_odds REAL NOT NULL,
    model_ev_pct REAL NOT NULL,
    stress_probability REAL,
    stress_ev_pct REAL,
    component_count INTEGER,
    model_spread_pp REAL,
    confidence TEXT,
    status TEXT,
    validation_grade TEXT,
    market_probability REAL,
    market_gap_pp REAL,
    challenger_probability REAL,
    challenger_gap_pp REAL
);
CREATE INDEX IF NOT EXISTS idx_v3_decision_event ON v3_decision_snapshots(event_key, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_v3_decision_status ON v3_decision_snapshots(status, captured_at DESC);

CREATE TABLE IF NOT EXISTS v3_walkforward_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    model_version TEXT NOT NULL,
    feature_schema TEXT NOT NULL,
    league_keys TEXT NOT NULL,
    predictions INTEGER NOT NULL,
    periods INTEGER NOT NULL,
    baseline_log_loss REAL,
    baseline_brier REAL,
    baseline_rps REAL,
    baseline_ece REAL,
    challenger_log_loss REAL,
    challenger_brier REAL,
    challenger_rps REAL,
    challenger_ece REAL,
    delta_log_loss REAL,
    delta_ci_low REAL,
    delta_ci_high REAL,
    challenger_better_fraction REAL,
    forecast_grade TEXT NOT NULL,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_v3_walkforward_created ON v3_walkforward_runs(created_at DESC);

CREATE TABLE IF NOT EXISTS v3_walkforward_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    league_key TEXT NOT NULL,
    kickoff_utc TEXT NOT NULL,
    season TEXT,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    actual_index INTEGER NOT NULL,
    baseline_home REAL NOT NULL,
    baseline_draw REAL NOT NULL,
    baseline_away REAL NOT NULL,
    challenger_home REAL NOT NULL,
    challenger_draw REAL NOT NULL,
    challenger_away REAL NOT NULL,
    FOREIGN KEY(run_id) REFERENCES v3_walkforward_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_v3_walkforward_scores_run ON v3_walkforward_scores(run_id);
"""


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_FILE)
    con.executescript(SCHEMA)
    return con


def _utc_iso(dt) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def save_v3_live_snapshot(rows: Iterable[CombinedMatch], decisions: Iterable[V3Decision]) -> tuple[int, int, int]:
    rows = list(rows)
    decisions = list(decisions)
    captured = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row_map = {row.match_name: row for row in rows}
    model_values = []
    quote_values = []
    decision_values = []

    for row in rows:
        forecast = getattr(row, "independent_v3", None)
        if forecast is None:
            continue
        model_values.append((
            captured, event_key(row), _utc_iso(row.kickoff), str(getattr(row, "league", "") or ""),
            forecast.league_key, row.home_team, row.away_team, MODEL_VERSION, FEATURE_SCHEMA,
            forecast.home_probability, forecast.draw_probability, forecast.away_probability,
            forecast.stress_home, forecast.stress_draw, forecast.stress_away,
            forecast.dc_home, forecast.dc_draw, forecast.dc_away,
            forecast.elo_home, forecast.elo_draw, forecast.elo_away,
            forecast.short_home, forecast.short_draw, forecast.short_away,
            forecast.long_home, forecast.long_draw, forecast.long_away,
            "|".join(forecast.components), len(forecast.components),
            forecast.lambda_home, forecast.lambda_away, forecast.model_spread_pp, forecast.confidence,
            forecast.history_matches, forecast.home_history_matches, forecast.away_history_matches,
            forecast.challenger_home, forecast.challenger_draw, forecast.challenger_away,
            forecast.challenger_eta, forecast.challenger_annual_shrink,
            forecast.challenger_validation_logloss, forecast.challenger_gap_pp,
            getattr(row, "market_reference_home", None), getattr(row, "market_reference_draw", None), getattr(row, "market_reference_away", None),
        ))

        shop = getattr(row, "price_shop", None)
        if shop is not None:
            for side, quotes in getattr(shop, "quotes", {}).items():
                p = {"HOME": forecast.home_probability, "DRAW": forecast.draw_probability, "AWAY": forecast.away_probability}.get(side)
                for quote in quotes:
                    odds = float(getattr(quote, "decimal_odds", 0.0) or 0.0)
                    if odds <= 1:
                        continue
                    quote_values.append((
                        captured, event_key(row), str(getattr(shop, "event_fingerprint", "") or ""), _utc_iso(row.kickoff),
                        str(getattr(row, "league", "") or ""), row.home_team, row.away_team,
                        str(getattr(quote, "source", "") or ""), side, odds,
                        str(getattr(quote, "received_at", "") or ""), str(getattr(quote, "market_timestamp", "") or ""),
                        str(getattr(quote, "event_id", "") or ""), getattr(quote, "match_confidence", None),
                        p, ((p * odds - 1.0) * 100.0) if p is not None else None,
                    ))

    for decision in decisions:
        row = row_map.get(decision.match_name)
        if row is None:
            continue
        decision_values.append((
            captured, event_key(row), _utc_iso(row.kickoff), decision.league, row.home_team, row.away_team,
            MODEL_VERSION, decision.side, decision.selection, decision.quote_source, decision.quote_odds,
            decision.model_probability, decision.fair_odds, decision.model_ev_pct,
            decision.stress_probability, decision.stress_ev_pct, decision.component_count,
            decision.model_spread_pp, decision.confidence, decision.status, decision.validation_grade,
            decision.market_probability, decision.market_gap_pp, decision.challenger_probability, decision.challenger_gap_pp,
        ))

    with _connect() as con:
        if model_values:
            con.executemany(
                """INSERT INTO v3_model_snapshots (
                    captured_at,event_key,kickoff_utc,league,league_key,home_team,away_team,model_version,feature_schema,
                    p_home,p_draw,p_away,stress_home,stress_draw,stress_away,
                    dc_home,dc_draw,dc_away,elo_home,elo_draw,elo_away,
                    short_home,short_draw,short_away,long_home,long_draw,long_away,
                    component_names,component_count,lambda_home,lambda_away,model_spread_pp,confidence,
                    history_matches,home_history_matches,away_history_matches,
                    challenger_home,challenger_draw,challenger_away,challenger_eta,challenger_annual_shrink,
                    challenger_validation_logloss,challenger_gap_pp,
                    market_reference_home,market_reference_draw,market_reference_away
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                model_values,
            )
        if quote_values:
            con.executemany(
                """INSERT INTO v3_quote_snapshots (
                    captured_at,event_key,event_fingerprint,kickoff_utc,league,home_team,away_team,source,side,odds,
                    received_at,market_timestamp,provider_event_id,match_confidence,model_probability,model_ev_pct
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                quote_values,
            )
        if decision_values:
            con.executemany(
                """INSERT INTO v3_decision_snapshots (
                    captured_at,event_key,kickoff_utc,league,home_team,away_team,model_version,side,selection_name,
                    quote_source,quote_odds,model_probability,fair_odds,model_ev_pct,stress_probability,stress_ev_pct,
                    component_count,model_spread_pp,confidence,status,validation_grade,market_probability,market_gap_pp,
                    challenger_probability,challenger_gap_pp
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                decision_values,
            )
        con.commit()
    return len(model_values), len(quote_values), len(decision_values)


def save_walkforward_result(result: WalkForwardResult) -> int:
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect() as con:
        cur = con.execute(
            """INSERT INTO v3_walkforward_runs (
                created_at,model_version,feature_schema,league_keys,predictions,periods,
                baseline_log_loss,baseline_brier,baseline_rps,baseline_ece,
                challenger_log_loss,challenger_brier,challenger_rps,challenger_ece,
                delta_log_loss,delta_ci_low,delta_ci_high,challenger_better_fraction,forecast_grade,notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                created, MODEL_VERSION, FEATURE_SCHEMA, "|".join(result.leagues), len(result.records), result.periods,
                result.baseline.log_loss, result.baseline.brier, result.baseline.rps, result.baseline.ece,
                result.challenger.log_loss, result.challenger.brier, result.challenger.rps, result.challenger.ece,
                result.delta_log_loss, result.delta_log_loss_ci_low, result.delta_log_loss_ci_high,
                result.challenger_better_fraction, result.forecast_grade, "\n".join(result.notes),
            ),
        )
        run_id = int(cur.lastrowid)
        if result.records:
            con.executemany(
                """INSERT INTO v3_walkforward_scores (
                    run_id,league_key,kickoff_utc,season,home_team,away_team,actual_index,
                    baseline_home,baseline_draw,baseline_away,challenger_home,challenger_draw,challenger_away
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, r.league_key, _utc_iso(r.kickoff), r.season, r.home_team, r.away_team, r.actual_index,
                        r.baseline[0], r.baseline[1], r.baseline[2], r.challenger[0], r.challenger[1], r.challenger[2],
                    ) for r in result.records
                ],
            )
        con.commit()
    return run_id


def latest_walkforward_summary() -> Optional[dict]:
    with _connect() as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM v3_walkforward_runs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def current_validation_grade() -> str:
    """Return the V3 betting-validation grade.

    A walk-forward football forecast can earn FORECAST_VALIDATED, but that is
    not enough to call a betting edge validated. EDGE_VALIDATED is deliberately
    reserved for a later point-in-time CLV/execution gate. Therefore the current
    strategy remains research-labelled unless that stronger grade is explicitly
    present in future validation data.
    """
    summary = latest_walkforward_summary()
    if not summary:
        return "UNVALIDATED"
    grade = str(summary.get("forecast_grade") or "UNVALIDATED").upper()
    return grade if grade in {"FORECAST_VALIDATED", "RESEARCH_ONLY", "INSUFFICIENT_SAMPLE"} else "UNVALIDATED"


def v3_counts() -> tuple[int, int, int, int]:
    with _connect() as con:
        models = int(con.execute("SELECT COUNT(*) FROM v3_model_snapshots").fetchone()[0])
        quotes = int(con.execute("SELECT COUNT(*) FROM v3_quote_snapshots").fetchone()[0])
        decisions = int(con.execute("SELECT COUNT(*) FROM v3_decision_snapshots").fetchone()[0])
        runs = int(con.execute("SELECT COUNT(*) FROM v3_walkforward_runs").fetchone()[0])
    return models, quotes, decisions, runs
