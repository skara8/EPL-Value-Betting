from __future__ import annotations

import csv
import io
import math
import re
import time
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean
from typing import Callable, Iterable, Optional

import requests

import edge_model
import engine
from config import DATA_DIR
from engine import CombinedMatch


ProgressCallback = Callable[[int, str, str], None]
FOOTBALL_DATA_BASE = "https://www.football-data.co.uk"
CACHE_DIR = DATA_DIR / "cache" / "independent_model_v24"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
HISTORICAL_SEASONS = 5
CURRENT_TTL_SECONDS = 6 * 60 * 60
ARCHIVE_TTL_SECONDS = 30 * 24 * 60 * 60
EXTRA_TTL_SECONDS = 6 * 60 * 60
DC_RHO = -0.10
SIDES = ("HOME", "DRAW", "AWAY")


@dataclass(frozen=True)
class LeagueSource:
    key: str
    name: str
    code: str
    aliases: tuple[str, ...]
    kind: str = "seasonal"  # seasonal => mmz4281/{season}/{code}.csv, extra => new/{code}.csv
    country: str = ""


# Main Football-Data divisions plus the 16 worldwide top divisions.  Sportsbet
# league names are matched to these aliases; a league is never guessed from a
# bookmaker price or team strength.
LEAGUE_SOURCES: tuple[LeagueSource, ...] = (
    LeagueSource("ENG-PL", "Premier League", "E0", ("england premier league", "english premier league", "premier league", "epl"), country="England"),
    LeagueSource("ENG-CH", "Championship", "E1", ("england championship", "efl championship", "english championship", "championship"), country="England"),
    LeagueSource("ENG-L1", "League One", "E2", ("england league one", "efl league one", "english league one"), country="England"),
    LeagueSource("ENG-L2", "League Two", "E3", ("england league two", "efl league two", "english league two"), country="England"),
    LeagueSource("ENG-NL", "National League", "EC", ("england national league", "english national league", "conference premier", "national league"), country="England"),
    LeagueSource("SCO-PL", "Scottish Premiership", "SC0", ("scotland premiership", "scottish premiership", "scotland premier league"), country="Scotland"),
    LeagueSource("SCO-CH", "Scottish Championship", "SC1", ("scotland championship", "scottish championship"), country="Scotland"),
    LeagueSource("SCO-L1", "Scottish League One", "SC2", ("scotland league one", "scottish league one"), country="Scotland"),
    LeagueSource("SCO-L2", "Scottish League Two", "SC3", ("scotland league two", "scottish league two"), country="Scotland"),
    LeagueSource("GER-B1", "Bundesliga", "D1", ("germany bundesliga", "german bundesliga", "bundesliga"), country="Germany"),
    LeagueSource("GER-B2", "2. Bundesliga", "D2", ("germany 2 bundesliga", "german 2 bundesliga", "2 bundesliga", "bundesliga 2"), country="Germany"),
    LeagueSource("ITA-A", "Serie A", "I1", ("italy serie a", "italian serie a", "serie a"), country="Italy"),
    LeagueSource("ITA-B", "Serie B", "I2", ("italy serie b", "italian serie b", "serie b"), country="Italy"),
    LeagueSource("ESP-LL", "La Liga", "SP1", ("spain la liga", "spanish la liga", "la liga", "primera division spain"), country="Spain"),
    LeagueSource("ESP-SD", "Segunda Division", "SP2", ("spain segunda", "spanish segunda", "segunda division", "la liga 2"), country="Spain"),
    LeagueSource("FRA-L1", "Ligue 1", "F1", ("france ligue 1", "french ligue 1", "ligue 1"), country="France"),
    LeagueSource("FRA-L2", "Ligue 2", "F2", ("france ligue 2", "french ligue 2", "ligue 2"), country="France"),
    LeagueSource("NED-ER", "Eredivisie", "N1", ("netherlands eredivisie", "dutch eredivisie", "eredivisie"), country="Netherlands"),
    LeagueSource("BEL-PL", "Belgian Pro League", "B1", ("belgium first division a", "belgian pro league", "jupiler pro league", "belgium jupiler"), country="Belgium"),
    LeagueSource("POR-PL", "Primeira Liga", "P1", ("portugal primeira liga", "portuguese primeira liga", "liga portugal", "primeira liga"), country="Portugal"),
    LeagueSource("TUR-SL", "Super Lig", "T1", ("turkey super lig", "turkish super lig", "super lig"), country="Turkey"),
    LeagueSource("GRE-SL", "Greek Super League", "G1", ("greece super league", "greek super league", "super league greece"), country="Greece"),
    LeagueSource("ARG-PR", "Argentina Primera Division", "ARG", ("argentina primera division", "argentina liga profesional", "liga profesional argentina", "argentina primera"), "extra", "Argentina"),
    LeagueSource("AUT-BL", "Austrian Bundesliga", "AUT", ("austria bundesliga", "austrian bundesliga"), "extra", "Austria"),
    LeagueSource("BRA-A", "Brazil Serie A", "BRA", ("brazil serie a", "brazilian serie a", "brasileirao serie a", "campeonato brasileiro serie a"), "extra", "Brazil"),
    LeagueSource("CHN-SL", "Chinese Super League", "CHN", ("china super league", "chinese super league", "china csl"), "extra", "China"),
    LeagueSource("DEN-SL", "Danish Superliga", "DNK", ("denmark superliga", "danish superliga", "superligaen"), "extra", "Denmark"),
    LeagueSource("FIN-VL", "Veikkausliiga", "FIN", ("finland veikkausliiga", "veikkausliiga"), "extra", "Finland"),
    LeagueSource("IRL-PD", "Ireland Premier Division", "IRL", ("ireland premier division", "irish premier division"), "extra", "Ireland"),
    LeagueSource("JPN-J1", "J1 League", "JPN", ("japan j1 league", "japanese j1 league", "j league", "j1 league"), "extra", "Japan"),
    LeagueSource("MEX-LMX", "Liga MX", "MEX", ("mexico liga mx", "mexican liga mx", "liga mx"), "extra", "Mexico"),
    LeagueSource("NOR-EL", "Eliteserien", "NOR", ("norway eliteserien", "norwegian eliteserien", "eliteserien"), "extra", "Norway"),
    LeagueSource("POL-EK", "Ekstraklasa", "POL", ("poland ekstraklasa", "polish ekstraklasa", "ekstraklasa"), "extra", "Poland"),
    LeagueSource("ROU-L1", "Romania Liga I", "ROU", ("romania liga 1", "romania liga i", "romanian liga 1", "superliga romania"), "extra", "Romania"),
    LeagueSource("RUS-PL", "Russian Premier League", "RUS", ("russia premier league", "russian premier league"), "extra", "Russia"),
    LeagueSource("SWE-AL", "Allsvenskan", "SWE", ("sweden allsvenskan", "swedish allsvenskan", "allsvenskan"), "extra", "Sweden"),
    LeagueSource("SUI-SL", "Swiss Super League", "SUI", ("switzerland super league", "swiss super league"), "extra", "Switzerland"),
    LeagueSource("USA-MLS", "Major League Soccer", "USA", ("usa major league soccer", "united states major league soccer", "major league soccer", "mls"), "extra", "USA"),
)


@dataclass(frozen=True)
class HistoricalMatch:
    kickoff: datetime
    season: str
    league_key: str
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int


@dataclass
class TeamVenueStats:
    home_gf: float = 0.0
    home_ga: float = 0.0
    home_w: float = 0.0
    away_gf: float = 0.0
    away_ga: float = 0.0
    away_w: float = 0.0
    matches: int = 0


@dataclass
class LeagueState:
    source: LeagueSource
    cutoff: datetime
    half_life_days: float
    league_home_goals: float
    league_away_goals: float
    draw_rate: float
    teams: dict[str, TeamVenueStats]
    ratings: dict[str, float]
    team_names: tuple[str, ...]
    history_matches: int


@dataclass
class IndependentForecast:
    match_name: str
    league_key: str
    league_name: str
    home_team_history: str
    away_team_history: str
    home_probability: float
    draw_probability: float
    away_probability: float
    conservative_home: float
    conservative_draw: float
    conservative_away: float
    fair_home_odds: float
    fair_draw_odds: float
    fair_away_odds: float
    dc_home: float
    dc_draw: float
    dc_away: float
    elo_home: float
    elo_draw: float
    elo_away: float
    short_home: float
    short_draw: float
    short_away: float
    long_home: float
    long_draw: float
    long_away: float
    lambda_home: float
    lambda_away: float
    model_spread_pp: float
    history_matches: int
    home_history_matches: int
    away_history_matches: int
    confidence: str
    components: tuple[str, ...] = ("DIXON-COLES", "ELO", "SHORT-DECAY", "LONG-DECAY")


@dataclass
class IndependentModelResult:
    forecasts: dict[str, IndependentForecast]
    supported_leagues: tuple[str, ...]
    unavailable_leagues: tuple[str, ...]
    notes: tuple[str, ...]
    downloaded_files: int = 0
    cache_hits: int = 0


def _emit(cb: Optional[ProgressCallback], percent: int, stage: str, detail: str) -> None:
    if cb:
        cb(max(0, min(100, int(percent))), stage, detail)


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = text.lower().replace("&", " and ")
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _club_key(value: str) -> str:
    text = _norm(value)
    tokens = text.split()
    removable = {"fc", "afc", "cf", "sc", "fk", "ac", "cd", "club", "deportivo"}
    trimmed = [t for t in tokens if t not in removable]
    return " ".join(trimmed or tokens)


TEAM_ALIASES = {
    "man utd": "manchester united",
    "man united": "manchester united",
    "man city": "manchester city",
    "nottm forest": "nottingham forest",
    "spurs": "tottenham hotspur",
    "wolves": "wolverhampton wanderers",
    "inter": "inter milan",
    "internazionale": "inter milan",
    "ac milan": "milan",
    "ath madrid": "atletico madrid",
    "atletico de madrid": "atletico madrid",
    "paris sg": "paris saint germain",
    "psg": "paris saint germain",
    "bayern munich": "bayern munchen",
    "bayern munchen": "bayern munchen",
    "borussia monchengladbach": "monchengladbach",
    "gladbach": "monchengladbach",
    "sporting lisbon": "sporting cp",
    "ath bilbao": "athletic bilbao",
    "real sociedad san sebastian": "real sociedad",
    "nycfc": "new york city",
    "la galaxy": "los angeles galaxy",
}


def canonical_history_team(value: str) -> str:
    key = _club_key(value)
    return TEAM_ALIASES.get(key, key)


def _league_score(value: str, source: LeagueSource) -> float:
    target = _norm(value)
    if not target:
        return 0.0
    best = 0.0
    for alias in source.aliases:
        a = _norm(alias)
        if target == a:
            return 1.0
        if a and (a in target or target in a):
            # Generic names such as Premier League need country evidence when
            # the Sportsbet label supplies it; exact aliases above still work.
            score = 0.91
        else:
            score = SequenceMatcher(None, target, a).ratio()
        if source.country and _norm(source.country) in target:
            score += 0.05
        best = max(best, min(1.0, score))
    return best


def resolve_league_source(league_name: str) -> Optional[LeagueSource]:
    ranked = sorted((( _league_score(league_name, s), s) for s in LEAGUE_SOURCES), key=lambda x: x[0], reverse=True)
    if not ranked or ranked[0][0] < 0.72:
        return None
    # Avoid accepting an ambiguous generic label when two candidates are nearly tied.
    if len(ranked) > 1 and ranked[0][0] < 0.96 and ranked[0][0] - ranked[1][0] < 0.035:
        return None
    return ranked[0][1]


def _current_season_start_year(now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    return now.year if now.month >= 7 else now.year - 1


def _season_code(start_year: int) -> str:
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def _season_codes(count: int = HISTORICAL_SEASONS, now: Optional[datetime] = None) -> list[str]:
    current = _current_season_start_year(now)
    return [_season_code(y) for y in range(current - count + 1, current + 1)]


def _cache_path(source: LeagueSource, season: Optional[str]) -> Path:
    suffix = season or "all"
    return CACHE_DIR / f"{source.key}_{suffix}.csv"


def _download_text(url: str, path: Path, ttl: float) -> tuple[Optional[str], str]:
    if path.exists():
        try:
            if time.time() - path.stat().st_mtime <= ttl:
                return path.read_text(encoding="utf-8"), "cache"
        except Exception:
            pass
    try:
        response = requests.get(url, timeout=20, headers={"User-Agent": "Football-Value-Betting/2.4", "Accept": "text/csv,*/*;q=0.8"})
        if response.status_code >= 400:
            if path.exists():
                return path.read_text(encoding="utf-8"), f"stale-cache HTTP {response.status_code}"
            return None, f"HTTP {response.status_code}"
        # Football-Data's archive contains Windows-1252 files in several leagues.
        text = response.content.decode("cp1252", errors="replace")
        if "," not in text or ("Home" not in text and "HomeTeam" not in text):
            return None, "unexpected CSV"
        path.write_text(text, encoding="utf-8")
        return text, "download"
    except Exception as exc:
        if path.exists():
            try:
                return path.read_text(encoding="utf-8"), f"stale-cache {type(exc).__name__}"
            except Exception:
                pass
        return None, f"{type(exc).__name__}: {exc}"


def _parse_date(value: str) -> Optional[datetime]:
    text = str(value or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _field(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return str(value)
    return ""


def _parse_csv(source: LeagueSource, text: str, season_hint: str = "") -> list[HistoricalMatch]:
    matches: list[HistoricalMatch] = []
    for row in csv.DictReader(io.StringIO(text)):
        dt = _parse_date(_field(row, "Date", "date"))
        home = canonical_history_team(_field(row, "HomeTeam", "Home", "home_team", "Home Team"))
        away = canonical_history_team(_field(row, "AwayTeam", "Away", "away_team", "Away Team"))
        try:
            hg = int(float(_field(row, "FTHG", "HG", "HomeGoals", "home_goals")))
            ag = int(float(_field(row, "FTAG", "AG", "AwayGoals", "away_goals")))
        except (TypeError, ValueError):
            continue
        if not dt or not home or not away or home == away:
            continue
        season = _field(row, "Season", "season") or season_hint or str(dt.year)
        matches.append(HistoricalMatch(dt, season, source.key, home, away, hg, ag))
    return matches


def load_league_history(source: LeagueSource, count: int = HISTORICAL_SEASONS) -> tuple[list[HistoricalMatch], tuple[str, ...], int, int]:
    notes: list[str] = []
    matches: list[HistoricalMatch] = []
    downloads = hits = 0
    if source.kind == "extra":
        path = _cache_path(source, None)
        text, status = _download_text(f"{FOOTBALL_DATA_BASE}/new/{source.code}.csv", path, EXTRA_TTL_SECONDS)
        if text:
            parsed = _parse_csv(source, text)
            # Extra-league files contain all seasons in one CSV. Limit the model
            # horizon so very old team identities do not dominate current form.
            cutoff = datetime.now(timezone.utc).timestamp() - 6.2 * 365.25 * 86400
            parsed = [m for m in parsed if m.kickoff.timestamp() >= cutoff]
            matches.extend(parsed)
            notes.append(f"{source.name}: {len(parsed)} results ({status})")
        else:
            notes.append(f"{source.name}: unavailable ({status})")
        downloads += int(status == "download")
        hits += int("cache" in status)
    else:
        codes = _season_codes(count)
        current = codes[-1]
        for code in codes:
            ttl = CURRENT_TTL_SECONDS if code == current else ARCHIVE_TTL_SECONDS
            path = _cache_path(source, code)
            url = f"{FOOTBALL_DATA_BASE}/mmz4281/{code}/{source.code}.csv"
            text, status = _download_text(url, path, ttl)
            if text:
                parsed = _parse_csv(source, text, code)
                matches.extend(parsed)
                notes.append(f"{source.name} {code}: {len(parsed)} ({status})")
            else:
                notes.append(f"{source.name} {code}: unavailable ({status})")
            downloads += int(status == "download")
            hits += int("cache" in status)
    matches.sort(key=lambda m: m.kickoff)
    return matches, tuple(notes), downloads, hits


def load_histories_for_rows(
    rows: list[CombinedMatch],
    progress: Optional[ProgressCallback] = None,
    max_workers: int = 8,
) -> tuple[dict[str, list[HistoricalMatch]], IndependentModelResult]:
    requested: dict[str, LeagueSource] = {}
    unavailable: set[str] = set()
    for row in rows:
        league = str(getattr(row, "league", "") or "")
        source = resolve_league_source(league)
        if source:
            requested[source.key] = source
        elif league:
            unavailable.add(league)

    histories: dict[str, list[HistoricalMatch]] = {}
    notes: list[str] = []
    downloads = hits = 0
    sources = list(requested.values())
    if not sources:
        result = IndependentModelResult({}, (), tuple(sorted(unavailable)), ("No supported historical leagues matched the current Sportsbet catalogue.",), 0, 0)
        return histories, result

    _emit(progress, 74, "Independent football data", f"Loading historical results for {len(sources)} supported league(s)")
    workers = max(1, min(int(max_workers), len(sources)))
    completed = 0
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="v24-history") as pool:
        futures = {pool.submit(load_league_history, source): source for source in sources}
        for future in as_completed(futures):
            source = futures[future]
            try:
                matches, source_notes, dl, ch = future.result()
                if matches:
                    histories[source.key] = matches
                notes.extend(source_notes)
                downloads += dl
                hits += ch
            except Exception as exc:
                notes.append(f"{source.name}: {type(exc).__name__}: {exc}")
            completed += 1
            _emit(progress, 74 + int(6 * completed / max(1, len(sources))), "Independent football data", f"Loaded {completed}/{len(sources)} league histories")

    result = IndependentModelResult({}, tuple(sorted(histories)), tuple(sorted(unavailable)), tuple(notes), downloads, hits)
    return histories, result


def _normalise_triplet(values: Iterable[float]) -> tuple[float, float, float]:
    vals = [max(1e-10, float(v)) for v in values]
    total = sum(vals)
    return vals[0] / total, vals[1] / total, vals[2] / total


def _dc_tau(h: int, a: int, lh: float, la: float, rho: float = DC_RHO) -> float:
    if h == 0 and a == 0:
        return 1.0 - lh * la * rho
    if h == 0 and a == 1:
        return 1.0 + lh * rho
    if h == 1 and a == 0:
        return 1.0 + la * rho
    if h == 1 and a == 1:
        return 1.0 - rho
    return 1.0


def dixon_coles_probabilities(lambda_home: float, lambda_away: float, rho: float = DC_RHO, max_goals: int = 10) -> tuple[float, float, float]:
    hp = [math.exp(-lambda_home)]
    ap = [math.exp(-lambda_away)]
    for i in range(1, max_goals + 1):
        hp.append(hp[-1] * lambda_home / i)
        ap.append(ap[-1] * lambda_away / i)
    home = draw = away = 0.0
    for h, p_h in enumerate(hp):
        for a, p_a in enumerate(ap):
            p = max(0.0, p_h * p_a * _dc_tau(h, a, lambda_home, lambda_away, rho))
            if h > a:
                home += p
            elif h == a:
                draw += p
            else:
                away += p
    return _normalise_triplet((home, draw, away))


def _build_state(source: LeagueSource, matches: list[HistoricalMatch], cutoff: datetime, half_life_days: float) -> LeagueState:
    cutoff = cutoff.astimezone(timezone.utc)
    relevant: list[tuple[HistoricalMatch, float]] = []
    teams: dict[str, TeamVenueStats] = defaultdict(TeamVenueStats)
    total_w = home_goals = away_goals = draws = 0.0
    max_age = 1300.0
    for m in matches:
        if m.kickoff >= cutoff:
            break
        age = (cutoff - m.kickoff).total_seconds() / 86400.0
        if age < 0 or age > max_age:
            continue
        w = 0.5 ** (age / max(30.0, half_life_days))
        relevant.append((m, w))
        total_w += w
        home_goals += m.home_goals * w
        away_goals += m.away_goals * w
        draws += (1.0 if m.home_goals == m.away_goals else 0.0) * w
        hs = teams[m.home_team]
        hs.home_gf += m.home_goals * w
        hs.home_ga += m.away_goals * w
        hs.home_w += w
        hs.matches += 1
        as_ = teams[m.away_team]
        as_.away_gf += m.away_goals * w
        as_.away_ga += m.home_goals * w
        as_.away_w += w
        as_.matches += 1

    league_home = home_goals / total_w if total_w else 1.45
    league_away = away_goals / total_w if total_w else 1.15
    draw_rate = draws / total_w if total_w else 0.26
    draw_rate = max(0.17, min(0.34, draw_rate))

    # Elo is league-local and uses only matches available before the cutoff.
    ratings: dict[str, float] = {}
    last_season: Optional[str] = None
    for m, _ in relevant:
        if last_season is not None and m.season != last_season:
            for team in list(ratings):
                ratings[team] = 1500.0 + (ratings[team] - 1500.0) * 0.82
        last_season = m.season
        # New/promoted teams begin slightly below league average until results
        # establish their level; previous participants retain their rating.
        rh = ratings.setdefault(m.home_team, 1475.0 if ratings else 1500.0)
        ra = ratings.setdefault(m.away_team, 1475.0 if ratings else 1500.0)
        expected = 1.0 / (1.0 + 10.0 ** ((ra - (rh + 60.0)) / 400.0))
        score = 1.0 if m.home_goals > m.away_goals else 0.0 if m.home_goals < m.away_goals else 0.5
        gd = abs(m.home_goals - m.away_goals)
        k = 22.0 * (1.0 + 0.12 * min(3, gd))
        delta = k * (score - expected)
        ratings[m.home_team] = rh + delta
        ratings[m.away_team] = ra - delta

    return LeagueState(source, cutoff, half_life_days, league_home, league_away, draw_rate, dict(teams), ratings, tuple(teams), len(relevant))


def _resolve_team(name: str, state: LeagueState) -> Optional[str]:
    key = canonical_history_team(name)
    if key in state.teams:
        return key
    best = None
    best_score = 0.0
    key_tokens = set(key.split())
    for candidate in state.team_names:
        if not candidate:
            continue
        cand_tokens = set(candidate.split())
        overlap = len(key_tokens & cand_tokens) / max(1, len(key_tokens | cand_tokens))
        seq = SequenceMatcher(None, key, candidate).ratio()
        score = 0.65 * seq + 0.35 * overlap
        if key in candidate or candidate in key:
            score = max(score, 0.88)
        if score > best_score:
            best, best_score = candidate, score
    return best if best_score >= 0.78 else None


def _team_lambdas(state: LeagueState, home_team: str, away_team: str, prior_matches: float = 7.0) -> Optional[tuple[float, float]]:
    hs = state.teams.get(home_team)
    as_ = state.teams.get(away_team)
    if not hs or not as_ or hs.matches < 2 or as_.matches < 2:
        return None
    lh, la = state.league_home_goals, state.league_away_goals
    home_gf = (hs.home_gf + prior_matches * lh) / (hs.home_w + prior_matches)
    home_ga = (hs.home_ga + prior_matches * la) / (hs.home_w + prior_matches)
    away_gf = (as_.away_gf + prior_matches * la) / (as_.away_w + prior_matches)
    away_ga = (as_.away_ga + prior_matches * lh) / (as_.away_w + prior_matches)
    home_attack = home_gf / max(0.2, lh)
    away_def_weak = away_ga / max(0.2, lh)
    away_attack = away_gf / max(0.2, la)
    home_def_weak = home_ga / max(0.2, la)
    lambda_home = max(0.20, min(4.5, lh * home_attack * away_def_weak))
    lambda_away = max(0.15, min(4.0, la * away_attack * home_def_weak))
    return lambda_home, lambda_away


def _elo_probabilities(state: LeagueState, home_team: str, away_team: str) -> tuple[float, float, float]:
    rh = state.ratings.get(home_team, 1475.0)
    ra = state.ratings.get(away_team, 1475.0)
    diff = (rh + 60.0) - ra
    decisive_home = 1.0 / (1.0 + 10.0 ** (-diff / 400.0))
    # Draw probability is learned from the league's own historical results,
    # then gently reduced for very unequal teams. No current bookmaker price is
    # used anywhere in this calculation.
    draw = state.draw_rate * math.exp(-abs(diff) / 950.0)
    draw = max(0.14, min(0.34, draw))
    home = (1.0 - draw) * decisive_home
    away = (1.0 - draw) * (1.0 - decisive_home)
    return _normalise_triplet((home, draw, away))


def _forecast_from_states(row: CombinedMatch, source: LeagueSource, states: dict[float, LeagueState]) -> Optional[IndependentForecast]:
    main = states[180.0]
    home = _resolve_team(row.home_team, main)
    away = _resolve_team(row.away_team, main)
    if not home or not away or home == away:
        return None

    component_probs: list[tuple[str, tuple[float, float, float]]] = []
    main_lambdas = _team_lambdas(main, home, away)
    if main_lambdas is None:
        return None
    dc = dixon_coles_probabilities(main_lambdas[0], main_lambdas[1])
    component_probs.append(("DIXON-COLES", dc))

    elo = _elo_probabilities(main, home, away)
    component_probs.append(("ELO", elo))

    short_state = states[90.0]
    short_home = _resolve_team(row.home_team, short_state)
    short_away = _resolve_team(row.away_team, short_state)
    short_lambdas = _team_lambdas(short_state, short_home or home, short_away or away)
    short = dixon_coles_probabilities(*short_lambdas) if short_lambdas else dc
    component_probs.append(("SHORT-DECAY", short))

    long_state = states[360.0]
    long_home = _resolve_team(row.home_team, long_state)
    long_away = _resolve_team(row.away_team, long_state)
    long_lambdas = _team_lambdas(long_state, long_home or home, long_away or away)
    long = dixon_coles_probabilities(*long_lambdas) if long_lambdas else dc
    component_probs.append(("LONG-DECAY", long))

    # V2.4 intentionally uses equal model-family votes rather than fitted
    # weights. The stored historical snapshots are designed so later releases
    # can learn walk-forward weights without using the evaluation period twice.
    central = _normalise_triplet(tuple(mean(p[i] for _, p in component_probs) for i in range(3)))
    conservative = tuple(min(p[i] for _, p in component_probs) for i in range(3))
    spread = max((max(p[i] for _, p in component_probs) - min(p[i] for _, p in component_probs)) * 100.0 for i in range(3))

    h_count = main.teams[home].matches
    a_count = main.teams[away].matches
    min_count = min(h_count, a_count)
    confidence = "HIGH" if min_count >= 30 and spread <= 5.0 else "MEDIUM" if min_count >= 12 and spread <= 8.0 else "LOW"

    return IndependentForecast(
        match_name=row.match_name,
        league_key=source.key,
        league_name=source.name,
        home_team_history=home,
        away_team_history=away,
        home_probability=central[0], draw_probability=central[1], away_probability=central[2],
        conservative_home=conservative[0], conservative_draw=conservative[1], conservative_away=conservative[2],
        fair_home_odds=1.0 / central[0], fair_draw_odds=1.0 / central[1], fair_away_odds=1.0 / central[2],
        dc_home=dc[0], dc_draw=dc[1], dc_away=dc[2],
        elo_home=elo[0], elo_draw=elo[1], elo_away=elo[2],
        short_home=short[0], short_draw=short[1], short_away=short[2],
        long_home=long[0], long_draw=long[1], long_away=long[2],
        lambda_home=main_lambdas[0], lambda_away=main_lambdas[1],
        model_spread_pp=spread,
        history_matches=main.history_matches,
        home_history_matches=h_count,
        away_history_matches=a_count,
        confidence=confidence,
        components=tuple(name for name, _ in component_probs),
    )


def build_independent_forecasts(
    rows: list[CombinedMatch],
    histories: dict[str, list[HistoricalMatch]],
    progress: Optional[ProgressCallback] = None,
) -> dict[str, IndependentForecast]:
    grouped: dict[str, list[CombinedMatch]] = defaultdict(list)
    source_by_key = {s.key: s for s in LEAGUE_SOURCES}
    for row in rows:
        source = resolve_league_source(str(getattr(row, "league", "") or ""))
        if source and source.key in histories:
            grouped[source.key].append(row)

    forecasts: dict[str, IndependentForecast] = {}
    total_rows = sum(len(v) for v in grouped.values())
    done = 0
    for key, league_rows in grouped.items():
        source = source_by_key[key]
        history = histories[key]
        if not history:
            continue
        # All queried fixtures are future/current fixtures, so one league state
        # can safely be built at the earliest kickoff using only earlier results.
        cutoff = min(r.kickoff.astimezone(timezone.utc) for r in league_rows)
        states = {half: _build_state(source, history, cutoff, half) for half in (90.0, 180.0, 360.0)}
        for row in league_rows:
            forecast = _forecast_from_states(row, source, states)
            if forecast:
                forecasts[row.match_name] = forecast
            done += 1
            if done == total_rows or done % 10 == 0:
                _emit(progress, 81 + int(5 * done / max(1, total_rows)), "Independent probability model", f"Priced {done}/{total_rows} supported fixtures from football data only")
    return forecasts


def _market_reference_triplet(row: CombinedMatch) -> Optional[tuple[float, float, float]]:
    values = (
        getattr(row, "model_fair_home", None),
        getattr(row, "model_fair_draw", None),
        getattr(row, "model_fair_away", None),
    )
    if all(v is not None for v in values):
        return float(values[0]), float(values[1]), float(values[2])
    return None


def apply_independent_forecasts(
    rows: list[CombinedMatch],
    forecasts: dict[str, IndependentForecast],
    min_ev_pct: float = 4.0,
) -> list[CombinedMatch]:
    for row in rows:
        # Preserve the market-derived estimate strictly as a diagnostic before
        # replacing the production fair probability.
        market_ref = _market_reference_triplet(row)
        setattr(row, "market_reference_home", market_ref[0] if market_ref else None)
        setattr(row, "market_reference_draw", market_ref[1] if market_ref else None)
        setattr(row, "market_reference_away", market_ref[2] if market_ref else None)

        forecast = forecasts.get(row.match_name)
        outcomes = getattr(row, "edge_outcomes", {})
        if forecast is None:
            row.model_fair_home = row.model_fair_draw = row.model_fair_away = None
            row.edge_source_names = tuple()
            row.edge_source_count = 0
            row.edge_disagreement_pp = None
            row.edge_best_selection = "—"
            row.edge_best_ev_pct = None
            row.edge_best_conservative_ev_pct = None
            row.edge_signal = "NO INDEPENDENT MODEL"
            row.edge_confidence = "LOW"
            row.reference_tier = "INDEPENDENT MODEL UNAVAILABLE"
            for side in SIDES:
                edge = outcomes.get(side)
                if edge is None:
                    continue
                edge.model_probability = None
                edge.conservative_probability = None
                edge.model_fair_odds = None
                edge.price_edge_pp = None
                edge.model_ev_pct = None
                edge.conservative_ev_pct = None
                edge.required_odds_for_threshold = None
                edge.external_disagreement_pp = None
                edge.source_count = 0
                edge.confidence = "LOW"
                edge.signal = "NO INDEPENDENT MODEL"
            continue

        setattr(row, "independent_v24", forecast)
        row.model_fair_home = forecast.home_probability
        row.model_fair_draw = forecast.draw_probability
        row.model_fair_away = forecast.away_probability
        row.edge_source_names = forecast.components
        row.edge_source_count = len(forecast.components)
        row.edge_disagreement_pp = forecast.model_spread_pp
        row.reference_tier = "V2.4 — INDEPENDENT FOOTBALL MODEL"
        row.reference_execution_exclusions = tuple()

        probs = (forecast.home_probability, forecast.draw_probability, forecast.away_probability)
        conservative = (forecast.conservative_home, forecast.conservative_draw, forecast.conservative_away)
        sb = (row.sb_home, row.sb_draw, row.sb_away)
        sb_devig = (
            getattr(row, "sb_devig_power_home", None),
            getattr(row, "sb_devig_power_draw", None),
            getattr(row, "sb_devig_power_away", None),
        )
        for i, side in enumerate(SIDES):
            edge = outcomes.get(side)
            if edge is None:
                continue
            odds = sb[i]
            p = probs[i]
            c = conservative[i]
            break_even = 1.0 / odds if odds is not None and odds > 1 else None
            edge.model_probability = p
            edge.conservative_probability = c
            edge.model_fair_odds = 1.0 / p
            edge.price_edge_pp = (p - break_even) * 100.0 if break_even is not None else None
            edge.sportsbet_residual_pp = (p - sb_devig[i]) * 100.0 if sb_devig[i] is not None else None
            edge.model_ev_pct = engine.expected_value_pct(p, odds)
            edge.conservative_ev_pct = engine.expected_value_pct(c, odds)
            edge.required_odds_for_threshold = (1.0 + min_ev_pct / 100.0) / p
            edge.external_disagreement_pp = forecast.model_spread_pp
            edge.source_count = len(forecast.components)
            edge.confidence = forecast.confidence
            if edge.model_ev_pct is None:
                edge.signal = "NO PRICE"
            elif edge.model_ev_pct < min_ev_pct:
                edge.signal = "PASS"
            elif edge.conservative_ev_pct is not None and edge.conservative_ev_pct >= min_ev_pct and forecast.confidence in {"HIGH", "MEDIUM"}:
                edge.signal = "ROBUST INDEPENDENT +EV"
            elif edge.conservative_ev_pct is not None and edge.conservative_ev_pct > 0:
                edge.signal = "INDEPENDENT +EV — MODEL SPREAD"
            else:
                edge.signal = "INDEPENDENT +EV — NOT ROBUST"

        best = max(
            (outcomes[s] for s in SIDES if outcomes.get(s) is not None),
            key=lambda x: x.model_ev_pct if x.model_ev_pct is not None else -99999.0,
            default=None,
        )
        if best is not None:
            row.edge_best_selection = best.side
            row.edge_best_ev_pct = best.model_ev_pct
            row.edge_best_conservative_ev_pct = best.conservative_ev_pct
            row.edge_signal = best.signal
            row.edge_confidence = best.confidence
    return rows


def build_and_apply_independent_model(
    rows: list[CombinedMatch],
    min_ev_pct: float = 4.0,
    progress: Optional[ProgressCallback] = None,
) -> IndependentModelResult:
    histories, initial = load_histories_for_rows(rows, progress=progress)
    forecasts = build_independent_forecasts(rows, histories, progress=progress)
    apply_independent_forecasts(rows, forecasts, min_ev_pct=min_ev_pct)
    return IndependentModelResult(
        forecasts=forecasts,
        supported_leagues=initial.supported_leagues,
        unavailable_leagues=initial.unavailable_leagues,
        notes=initial.notes,
        downloaded_files=initial.downloaded_files,
        cache_hits=initial.cache_hits,
    )
