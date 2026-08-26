from __future__ import annotations

import hashlib
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import mean
from typing import Iterable, Optional

import engine
from engine import CombinedMatch
from independent_model_v24 import (
    HistoricalMatch, LeagueSource, LEAGUE_SOURCES, canonical_history_team,
    dixon_coles_probabilities, load_histories_for_rows, load_league_history,
    resolve_league_source,
)

SIDES = ("HOME", "DRAW", "AWAY")
MODEL_VERSION = "3.0.0"
FEATURE_SCHEMA_VERSION = "v3.0"


@dataclass(frozen=True)
class V3HyperParameters:
    learning_rate: float = 0.045
    baseline_rate: float = 0.006
    process_decay_per_day: float = 0.00035
    season_regression: float = 0.82
    elo_k: float = 22.0
    elo_home_advantage: float = 55.0
    rho: float = -0.08


@dataclass
class DynamicTeamState:
    attack: float = 0.0
    defence: float = 0.0
    matches: int = 0
    last_seen: Optional[datetime] = None
    residual_var: float = 1.0


@dataclass
class DynamicLeagueState:
    source: LeagueSource
    params: V3HyperParameters
    log_home_base: float = field(default_factory=lambda: math.log(1.45))
    log_away_base: float = field(default_factory=lambda: math.log(1.15))
    draw_rate: float = 0.26
    teams: dict[str, DynamicTeamState] = field(default_factory=dict)
    elo: dict[str, float] = field(default_factory=dict)
    season: Optional[str] = None
    history_matches: int = 0


@dataclass
class V3Forecast:
    match_name: str
    league_key: str
    league_name: str
    home_team_history: str
    away_team_history: str
    home_probability: float
    draw_probability: float
    away_probability: float
    lower_home: float
    lower_draw: float
    lower_away: float
    upper_home: float
    upper_draw: float
    upper_away: float
    sd_home: float
    sd_draw: float
    sd_away: float
    fair_home_odds: float
    fair_draw_odds: float
    fair_away_odds: float
    dynamic_home: float
    dynamic_draw: float
    dynamic_away: float
    elo_home: float
    elo_draw: float
    elo_away: float
    lambda_home: float
    lambda_away: float
    stack_weight_dynamic: float
    calibration_temperature: float
    component_spread_pp: float
    history_matches: int
    home_history_matches: int
    away_history_matches: int
    bootstrap_samples: int
    confidence: str
    components: tuple[str, ...] = ("DYNAMIC-POISSON", "ELO")
    promotion_prior_home: bool = False
    promotion_prior_away: bool = False

    def probability(self, side: str) -> float:
        return {"HOME": self.home_probability, "DRAW": self.draw_probability, "AWAY": self.away_probability}[side]

    def lower_probability(self, side: str) -> float:
        return {"HOME": self.lower_home, "DRAW": self.lower_draw, "AWAY": self.lower_away}[side]

    def stdev(self, side: str) -> float:
        return {"HOME": self.sd_home, "DRAW": self.sd_draw, "AWAY": self.sd_away}[side]


@dataclass
class V3ModelResult:
    forecasts: dict[str, V3Forecast]
    supported_leagues: tuple[str, ...]
    unavailable_leagues: tuple[str, ...]
    notes: tuple[str, ...]
    downloaded_files: int = 0
    cache_hits: int = 0


def _norm(values: Iterable[float]) -> tuple[float, float, float]:
    x = [max(1e-10, float(v)) for v in values]
    s = sum(x)
    return x[0] / s, x[1] / s, x[2] / s


def _temperature(p: tuple[float, float, float], t: float) -> tuple[float, float, float]:
    t = max(0.5, min(2.0, float(t)))
    return _norm([max(1e-12, x) ** (1.0 / t) for x in p])


def new_dynamic_state(source: LeagueSource, params: Optional[V3HyperParameters] = None) -> DynamicLeagueState:
    return DynamicLeagueState(source=source, params=params or V3HyperParameters())


def _team(state: DynamicLeagueState, name: str) -> DynamicTeamState:
    return state.teams.setdefault(name, DynamicTeamState())


def _regress_season(state: DynamicLeagueState, season: str) -> None:
    if state.season is None:
        state.season = season
        return
    if state.season == season:
        return
    f = state.params.season_regression
    for team in state.teams.values():
        team.attack *= f
        team.defence *= f
    for name, rating in list(state.elo.items()):
        state.elo[name] = 1500.0 + f * (rating - 1500.0)
    state.season = season


def _decay_factor(team: DynamicTeamState, at: datetime, rate: float) -> float:
    if team.last_seen is None:
        return 1.0
    days = max(0.0, (at - team.last_seen).total_seconds() / 86400.0)
    return math.exp(-rate * min(days, 730.0))


def score_intensities(state: DynamicLeagueState, home: str, away: str, at: datetime) -> tuple[float, float]:
    # Forecasting is side-effect free: later current fixtures cannot change
    # simply because an earlier row happened to be iterated first.
    hs, as_ = _team(state, home), _team(state, away)
    hf = _decay_factor(hs, at, state.params.process_decay_per_day)
    af = _decay_factor(as_, at, state.params.process_decay_per_day)
    lh = math.exp(state.log_home_base + hs.attack * hf - as_.defence * af)
    la = math.exp(state.log_away_base + as_.attack * af - hs.defence * hf)
    return max(0.15, min(4.75, lh)), max(0.12, min(4.25, la))


def elo_probabilities(state: DynamicLeagueState, home: str, away: str) -> tuple[float, float, float]:
    # All unseen teams use the same prior; V2.4's first-team ordering bug is gone.
    rh, ra = state.elo.get(home, 1500.0), state.elo.get(away, 1500.0)
    diff = rh + state.params.elo_home_advantage - ra
    decisive = 1.0 / (1.0 + 10.0 ** (-diff / 400.0))
    draw = max(0.13, min(0.35, state.draw_rate * math.exp(-abs(diff) / 950.0)))
    return _norm(((1 - draw) * decisive, draw, (1 - draw) * (1 - decisive)))


def component_probabilities(state: DynamicLeagueState, home: str, away: str, at: datetime):
    lh, la = score_intensities(state, home, away, at)
    dynamic = dixon_coles_probabilities(lh, la, rho=state.params.rho)
    return dynamic, elo_probabilities(state, home, away), lh, la


def combined_probability(dynamic, elo, weight: float, temperature: float):
    w = max(0.0, min(1.0, weight))
    return _temperature(_norm([w * dynamic[i] + (1 - w) * elo[i] for i in range(3)]), temperature)


def update_dynamic_state(state: DynamicLeagueState, match: HistoricalMatch) -> None:
    _regress_season(state, match.season)
    home, away = match.home_team, match.away_team
    hs, as_ = _team(state, home), _team(state, away)
    # Apply state decay once when an observed match arrives. Forecast calls only
    # read a decayed view and never mutate the stored state.
    hf = _decay_factor(hs, match.kickoff, state.params.process_decay_per_day)
    af = _decay_factor(as_, match.kickoff, state.params.process_decay_per_day)
    hs.attack *= hf; hs.defence *= hf
    as_.attack *= af; as_.defence *= af
    hs.last_seen = match.kickoff; as_.last_seen = match.kickoff
    lh, la = score_intensities(state, home, away, match.kickoff)
    eh, ea = match.home_goals - lh, match.away_goals - la
    lr = state.params.learning_rate / math.sqrt(1.0 + min(hs.matches, as_.matches) / 45.0)
    hs.attack = max(-1.35, min(1.35, hs.attack + lr * eh))
    as_.defence = max(-1.35, min(1.35, as_.defence - lr * eh))
    as_.attack = max(-1.35, min(1.35, as_.attack + lr * ea))
    hs.defence = max(-1.35, min(1.35, hs.defence - lr * ea))
    state.log_home_base = max(math.log(.75), min(math.log(2.25), state.log_home_base + state.params.baseline_rate * eh))
    state.log_away_base = max(math.log(.65), min(math.log(2.0), state.log_away_base + state.params.baseline_rate * ea))
    state.draw_rate = max(.16, min(.34, .992 * state.draw_rate + .008 * (match.home_goals == match.away_goals)))
    for team, residual in ((hs, eh), (as_, ea)):
        team.matches += 1
        team.last_seen = match.kickoff
        team.residual_var = .96 * team.residual_var + .04 * residual * residual
    rh, ra = state.elo.get(home, 1500.0), state.elo.get(away, 1500.0)
    expected = 1.0 / (1.0 + 10.0 ** ((ra - (rh + state.params.elo_home_advantage)) / 400.0))
    score = 1.0 if match.home_goals > match.away_goals else 0.0 if match.home_goals < match.away_goals else .5
    k = state.params.elo_k * (1 + .10 * min(3, abs(match.home_goals - match.away_goals)))
    delta = k * (score - expected)
    state.elo[home], state.elo[away] = rh + delta, ra - delta
    state.history_matches += 1


def fit_state(source: LeagueSource, matches: list[HistoricalMatch], cutoff: datetime, params: V3HyperParameters) -> DynamicLeagueState:
    state = new_dynamic_state(source, params)
    for match in sorted(matches, key=lambda m: m.kickoff):
        if match.kickoff >= cutoff:
            break
        update_dynamic_state(state, match)
    return state


def _validation_rows(source: LeagueSource, matches: list[HistoricalMatch], params: V3HyperParameters):
    if len(matches) < 80:
        return []
    split = min(len(matches) - 20, max(45, int(len(matches) * .78)))
    state = new_dynamic_state(source, params)
    output = []
    by_day = defaultdict(list)
    for match in sorted(matches, key=lambda m: m.kickoff):
        by_day[match.kickoff.date()].append(match)
    index = 0
    for day in sorted(by_day):
        predicted = []
        for match in by_day[day]:
            d, e, _, _ = component_probabilities(state, match.home_team, match.away_team, match.kickoff)
            predicted.append((match, d, e, index))
            index += 1
        for match, d, e, idx in predicted:
            if idx >= split:
                y = 0 if match.home_goals > match.away_goals else 1 if match.home_goals == match.away_goals else 2
                output.append((d, e, y))
        for match in by_day[day]:
            update_dynamic_state(state, match)
    return output


def tune_model(source: LeagueSource, matches: list[HistoricalMatch]):
    if len(matches) < 100:
        return V3HyperParameters(), .65, 1.0, float("nan")
    candidates = (
        V3HyperParameters(learning_rate=.030, process_decay_per_day=.00020, elo_k=18, elo_home_advantage=50),
        V3HyperParameters(learning_rate=.040, process_decay_per_day=.00030, elo_k=20, elo_home_advantage=55),
        V3HyperParameters(learning_rate=.050, process_decay_per_day=.00040, elo_k=22, elo_home_advantage=55),
        V3HyperParameters(learning_rate=.060, process_decay_per_day=.00055, elo_k=24, elo_home_advantage=60),
    )
    best = (float("inf"), candidates[1], .65, 1.0)
    for params in candidates:
        rows = _validation_rows(source, matches, params)
        for weight in (.50, .65, .80, 1.0):
            for temp in (.85, 1.0, 1.15, 1.30):
                if not rows:
                    continue
                loss = mean(-math.log(max(1e-12, combined_probability(d, e, weight, temp)[y])) for d, e, y in rows)
                if loss < best[0]:
                    best = (loss, params, weight, temp)
    return best[1], best[2], best[3], best[0]


def _block_sample(matches: list[HistoricalMatch], rng: random.Random) -> list[HistoricalMatch]:
    if len(matches) < 40:
        return list(matches)
    block = max(8, min(30, int(math.sqrt(len(matches)))))
    selected = []
    while len(selected) < len(matches):
        start = rng.randrange(0, len(matches) - block + 1)
        selected.extend((i, matches[i]) for i in range(start, min(len(matches), start + block)))
    selected = selected[:len(matches)]
    selected.sort(key=lambda pair: pair[0])
    return [m for _, m in selected]


def _quantile(values: list[float], q: float) -> float:
    values = sorted(values)
    pos = max(0.0, min(1.0, q)) * (len(values) - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return values[lo]
    f = pos - lo
    return values[lo] * (1 - f) + values[hi] * f


def _bands(draws, central):
    output = []
    for i in range(3):
        values = [p[i] for p in draws]
        m = mean(values)
        sd = math.sqrt(mean((x - m) ** 2 for x in values)) if len(values) > 1 else 0.0
        output.append((min(_quantile(values, .05), central[i]), max(_quantile(values, .95), central[i]), sd))
    return output


def _lower_key(key: str) -> Optional[str]:
    return {"ENG-PL":"ENG-CH","ENG-CH":"ENG-L1","ENG-L1":"ENG-L2","GER-B1":"GER-B2","ITA-A":"ITA-B","ESP-LL":"ESP-SD","FRA-L1":"FRA-L2","SCO-PL":"SCO-CH","SCO-CH":"SCO-L1","SCO-L1":"SCO-L2"}.get(key)


def _apply_transfer(state: DynamicLeagueState, team: str, lower: Optional[DynamicLeagueState]) -> bool:
    if lower is None or team not in lower.teams or lower.teams[team].matches < 8:
        return False
    current = state.teams.get(team)
    if current is not None and current.matches >= 10:
        return False
    target, prior = _team(state, team), lower.teams[team]
    r = .55
    target.attack = r * prior.attack + (1-r) * target.attack
    target.defence = r * prior.defence + (1-r) * target.defence
    target.residual_var = max(target.residual_var, prior.residual_var + .35)
    state.elo[team] = 1500 + r * (lower.elo.get(team, 1500)-1500) + (1-r) * (state.elo.get(team,1500)-1500)
    return True


def build_v3_forecasts(rows: list[CombinedMatch], histories: dict[str, list[HistoricalMatch]], progress=None, bootstrap_samples: int = 28):
    grouped = defaultdict(list)
    sources = {s.key:s for s in LEAGUE_SOURCES}
    for row in rows:
        source = resolve_league_source(str(getattr(row,"league","") or ""))
        if source and source.key in histories:
            grouped[source.key].append(row)
    total, done, forecasts = sum(map(len, grouped.values())), 0, {}
    for key, league_rows in grouped.items():
        source = sources[key]
        cutoff = min(r.kickoff.astimezone(timezone.utc) for r in league_rows)
        history = sorted([m for m in histories[key] if m.kickoff < cutoff], key=lambda m:m.kickoff)
        params, weight, temp, _ = tune_model(source, history)
        state = fit_state(source, history, cutoff, params)
        rng = random.Random(int(hashlib.sha256(f"{key}:{cutoff.isoformat()}:{MODEL_VERSION}".encode()).hexdigest()[:16],16))
        bootstrap = [fit_state(source, _block_sample(history,rng), cutoff, params) for _ in range(bootstrap_samples)] if len(history)>=40 else []
        lower = None
        lk = _lower_key(key)
        if lk and histories.get(lk):
            lh = [m for m in histories[lk] if m.kickoff < cutoff]
            lp,_,_,_ = tune_model(sources[lk],lh)
            lower = fit_state(sources[lk],lh,cutoff,lp)

        # Apply promotion/transfer priors exactly once to each fitted state. This
        # keeps current forecasts invariant to fixture iteration order.
        current_teams = {canonical_history_team(name) for row in league_rows for name in (row.home_team,row.away_team)}
        transfer_flags = {team:_apply_transfer(state,team,lower) for team in current_teams}
        for bs in bootstrap:
            for team in current_teams:
                _apply_transfer(bs,team,lower)

        for row in league_rows:
            home, away = canonical_history_team(row.home_team), canonical_history_team(row.away_team)
            ph, pa = transfer_flags.get(home,False), transfer_flags.get(away,False)
            dynamic, elo, lh, la = component_probabilities(state,home,away,row.kickoff.astimezone(timezone.utc))
            central = combined_probability(dynamic,elo,weight,temp)
            draws=[]
            for bs in bootstrap:
                bd,be,_,_ = component_probabilities(bs,home,away,row.kickoff.astimezone(timezone.utc))
                draws.append(combined_probability(bd,be,weight,temp))
            draws.append(central)
            bands = _bands(draws,central)
            hs,as_ = state.teams.get(home,DynamicTeamState()), state.teams.get(away,DynamicTeamState())
            max_sd=max(b[2] for b in bands); n=min(hs.matches,as_.matches)
            conf="HIGH" if n>=30 and max_sd<=.045 else "MEDIUM" if n>=10 and max_sd<=.075 else "LOW"
            if (ph or pa) and conf=="HIGH": conf="MEDIUM"
            forecasts[row.match_name]=V3Forecast(
                row.match_name,key,source.name,home,away,*central,
                bands[0][0],bands[1][0],bands[2][0],bands[0][1],bands[1][1],bands[2][1],
                bands[0][2],bands[1][2],bands[2][2],1/central[0],1/central[1],1/central[2],
                *dynamic,*elo,lh,la,weight,temp,max(abs(dynamic[i]-elo[i])*100 for i in range(3)),
                state.history_matches,hs.matches,as_.matches,len(draws),conf,
                promotion_prior_home=ph,promotion_prior_away=pa,
            )
            done+=1
            if progress and (done==total or done%8==0):
                progress(82+int(6*done/max(1,total)),"V3 independent model",f"Fitted {done}/{total} fixture(s) with dynamic states and block-bootstrap uncertainty")
    return forecasts


def apply_v3_forecasts(rows: list[CombinedMatch], forecasts: dict[str,V3Forecast], min_ev_pct: float=4.0):
    for row in rows:
        market=(getattr(row,"model_fair_home",None),getattr(row,"model_fair_draw",None),getattr(row,"model_fair_away",None))
        if not all(v is not None for v in market): market=(None,None,None)
        row.market_reference_home,row.market_reference_draw,row.market_reference_away=market
        f=forecasts.get(row.match_name); outcomes=getattr(row,"edge_outcomes",{})
        if f is None:
            row.model_fair_home=row.model_fair_draw=row.model_fair_away=None
            row.edge_source_names=tuple(); row.edge_source_count=0; row.edge_signal="NO V3 INDEPENDENT MODEL"; row.edge_confidence="LOW"; row.reference_tier="V3 MODEL UNAVAILABLE"
            for edge in outcomes.values():
                edge.model_probability=edge.conservative_probability=edge.model_fair_odds=edge.model_ev_pct=edge.conservative_ev_pct=None
                edge.source_count=0; edge.confidence="LOW"; edge.signal="NO V3 INDEPENDENT MODEL"
            continue
        row.independent_v3=f
        row.model_fair_home,row.model_fair_draw,row.model_fair_away=f.home_probability,f.draw_probability,f.away_probability
        row.edge_source_names=f.components; row.edge_source_count=len(f.components); row.edge_disagreement_pp=f.component_spread_pp; row.reference_tier="V3 — DYNAMIC INDEPENDENT FOOTBALL MODEL"
        probs=(f.home_probability,f.draw_probability,f.away_probability); lows=(f.lower_home,f.lower_draw,f.lower_away); odds=(row.sb_home,row.sb_draw,row.sb_away)
        for i,side in enumerate(SIDES):
            edge=outcomes.get(side)
            if edge is None: continue
            edge.model_probability=probs[i]; edge.conservative_probability=lows[i]; edge.model_fair_odds=1/probs[i]
            edge.model_ev_pct=engine.expected_value_pct(probs[i],odds[i]); edge.conservative_ev_pct=engine.expected_value_pct(lows[i],odds[i])
            edge.required_odds_for_threshold=(1+min_ev_pct/100)/probs[i]; edge.external_disagreement_pp=f.component_spread_pp; edge.source_count=len(f.components); edge.confidence=f.confidence
            edge.signal="V3 RESEARCH CANDIDATE" if edge.model_ev_pct is not None and edge.model_ev_pct>=min_ev_pct else "PASS"
    return rows


def build_and_apply_v3_model(rows: list[CombinedMatch], min_ev_pct: float=4.0, progress=None, bootstrap_samples: int=28):
    histories,base=load_histories_for_rows(rows,progress=progress)
    sources={s.key:s for s in LEAGUE_SOURCES}; notes=list(base.notes); downloads,hits=base.downloaded_files,base.cache_hits
    requested=set()
    for row in rows:
        source=resolve_league_source(str(getattr(row,"league","") or ""))
        if source: requested.add(source.key)
    for key in sorted(requested):
        lower=_lower_key(key)
        if not lower or lower in histories or lower not in sources: continue
        try:
            matches,n,dl,ch=load_league_history(sources[lower]); histories[lower]=matches; notes.extend(n); downloads+=dl; hits+=ch
        except Exception as exc:
            notes.append(f"{sources[lower].name}: promotion-prior history unavailable ({exc})")
    forecasts=build_v3_forecasts(rows,histories,progress,bootstrap_samples)
    apply_v3_forecasts(rows,forecasts,min_ev_pct)
    return V3ModelResult(forecasts,base.supported_leagues,base.unavailable_leagues,tuple(notes),downloads,hits),histories
