# Football Value Betting

Windows desktop research application for comparing football prices, estimating fair probabilities, studying market inefficiencies and validating whether apparent edges persist.

## Current version

**V2.2.0**

## What V2.2 changes

V2.2 keeps the conservative V2.1 `ROBUST +EV` production rule, but makes the expensive probability calculations use substantially more of a modern multicore CPU and adds several research-only models inspired by the strongest ideas found in public football-prediction repositories.

### 1. Multicore probability calculation

The Asian-Handicap/total Poisson calibration is independent for each fixture and is the most CPU-heavy pure-Python part of the market model. A single Python process on a 12-thread PC can therefore appear as only about 8% total CPU usage.

V2.2 distributes fixture calculations across multiple Windows worker processes:

```text
logical CPUs detected
        ↓
use about 75% of them
        ↓
maximum 10 workers
        ↓
fit fixtures in parallel
```

Examples:

- 4 logical CPUs -> 3 workers;
- 8 logical CPUs -> 6 workers;
- 12 logical CPUs -> 9 workers;
- 16+ logical CPUs -> capped at 10 workers.

The cap reduces RAM/process-startup overhead in the one-file Windows executable and leaves capacity for Windows and the GUI. Tiny scans remain single-process because multiprocessing overhead would be slower than the calculation itself.

If multiprocessing is blocked by a security product or unusual frozen-Windows environment, V2.2 automatically falls back to the proven serial calculation instead of failing the scan. Diagnostics reports whether the last run used multicore acceleration, the worker count and the stage timings.

V2.1's indexed AH/totals context matching remains in place, so the old all-catalogue repeated-scan bottleneck is also still removed.

### 2. Historical Elo research model

V2.2 downloads and caches up to five EPL seasons of public football-data.co.uk results and maintains a pre-match Elo strength estimate.

The research page shows:

- home and away Elo ratings;
- an Elo-derived H/D/A probability using the sharp-market draw probability only as the draw anchor;
- the difference between the Elo view and the sharp-market view.

The Elo number is **not** added to production fair probability yet. Its residual is stored so future validation can ask whether Elo disagreement predicts closing-line movement or improves probability calibration out of sample.

### 3. Time-decayed goals-strength Poisson research model

Public Dixon-Coles/Poisson implementations reinforced the value of attack strength, defence strength, home advantage and giving recent results more weight than old results.

V2.2 therefore adds a separate research-only goals model using:

- exponentially time-decayed historical results;
- venue-specific home/away scoring strength;
- attack and defensive-strength ratios relative to the league;
- shrinkage toward league averages to reduce small-sample extremes;
- Poisson H/D/A probabilities and implied expected goals.

This is deliberately labelled a **time-decayed goals-strength Poisson model**, not a fully fitted Dixon-Coles MLE implementation.

### 4. Expected-XI continuity

The existing player layer already estimates expected starters. V2.2 now also measures how much the expected XI resembles the recent regular XI, weighted by player strength and expected availability.

This turns the GitHub research idea of lineup continuity into a storable feature rather than another subjective manual slider.

### 5. Recent xG + opponent-strength context

For the available EPL football-intelligence data, V2.2 records each side's recent net xG and the average Elo strength of the opponents in those recent matches.

Opponent strength is kept visible as a separate feature rather than hidden inside an arbitrary probability bonus. This is intentional: any opponent-adjusted xG transformation should first prove incremental value out of sample.

### 6. Research-model agreement

The new **Research -> Research models** page compares:

```text
sharp market
Elo
 time-decayed Poisson
expected-XI continuity
recent xG context
```

It reports model spread and whether the sharp market, Elo and Poisson models agree on the most likely outcome.

Research agreement can appear beside the Dashboard confidence label, but it does **not** change the displayed EV or turn an amber/watch case into a green recommendation.

### 7. Feature snapshots for future ablation and validation

V2.2 stores the new research features in SQLite alongside the existing market and robust-signal history.

This is the foundation for the next important step from the GitHub research: **feature ablation**. Once enough settled examples and near-closing Pinnacle prices exist, each feature can be tested separately:

- market baseline only;
- + Elo residual;
- + time-decayed Poisson residual;
- + lineup continuity;
- + xG/opponent context;
- combinations of the above.

A feature should only enter the production probability model if it improves time-ordered out-of-sample CLV, log loss, Brier score or calibration rather than merely making historical EV look larger.

## Production probability architecture

Sportsbet remains a target/execution price, not the independent probability source used to assess itself.

The external fair layer can use:

- Polymarket executable YES asks normalised to 100%;
- Pinnacle/PS3838 power-de-vigged 1X2;
- Pinnacle Asian Handicap + totals converted into an implied Poisson score distribution.

Pinnacle 1X2 and AH/totals estimates are combined into one Pinnacle provider component so one provider does not receive multiple full votes.

For decimal price `O` and fair probability `p`:

```text
EV = p * O - 1
```

A primary green `ROBUST +EV` signal still requires:

1. at least two external provider components;
2. medium or high reference-market confidence;
3. external disagreement no greater than 4 percentage points;
4. the least-favourable external probability still producing EV at or above the configured threshold at the best eligible execution price.

The new V2.2 football research models do not bypass those rules.

## Best-price execution

The fair probability is calculated before price shopping. Supported observed execution sources can include Sportsbet, Bet365, Ladbrokes, TAB, Unibet AU, BetRight, Stake and Cloudbet when available through the user's PulseScore plan.

Polymarket remains visible in Best Prices and Dutch tools but is not used as the primary robust execution price when it is also contributing to the fair-probability reference.

## Validation

The Research section retains:

- first-observed robust signals;
- actual execution source and price;
- last captured near-kickoff Pinnacle price;
- sharp CLV;
- realised result/ROI;
- Brier/calibration research;
- V2.2 feature snapshots for later ablation.

Near-close Pinnacle statistics only use observations captured within six hours of kickoff.

## Why these additions

The public-model review produced a consistent lesson rather than a magic formula.

`mejding/matchlabs` includes xG, fatigue, Elo, lineup stability, opponent-adjusted xG, bootstrap uncertainty and strict time-based probabilistic evaluation. Its own reported comparisons also show that adding Elo/shot-volume or injury variants can worsen out-of-sample log loss/Brier score relative to simpler variants.

`kochlisGit/ProphitBet-Soccer-Bets-Predictor` emphasises sliding/cross-validation, holdout evaluation, variance/correlation analysis and feature-selection tools rather than assuming every available variable is useful.

Public Dixon-Coles implementations reinforce time-decayed goal-strength modelling, but do not establish a transferable betting edge by themselves.

V2.2 therefore implements the useful candidate features **as testable residuals**, while keeping the market-supported robust rule in control.

See:

- `docs/V2_1_STRATEGY.md`
- `docs/V2_2_RESEARCH_MODELS.md`

## Navigation

Top-level navigation remains compact:

```text
Dashboard
Markets
Analysis
Tools
Research
Settings
```

Research now contains:

```text
Validation
History
Diagnostics
Research models
```

## Data and settings

Settings, logs, cached public football data, historical football-data CSVs and SQLite research data live under:

```text
%LOCALAPPDATA%\EPLValueBetting\
```

Existing research data is retained across upgrades.

## Scope

This is a theoretical market-efficiency and probability-modelling project. It does not automatically place bets. A displayed positive EV is an estimate, not proof of profitability. The objective is to make candidate edges difficult to pass and increasingly easy to falsify through sharp closing-line and out-of-sample validation.
