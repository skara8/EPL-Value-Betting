# Football Value Betting

Windows desktop research application for independently estimating football probabilities, scanning executable prices and testing whether apparent value survives chronological out-of-sample validation.

## Current version

**V3.0.0**

V3 is a research-first rebuild of the V2.4 independent-probability architecture. It deliberately separates three questions:

```text
1. FOOTBALL FORECAST
   historical results only
          ↓
   dynamic football state
          ↓
   calibrated independent H / D / A probability + uncertainty

2. EXECUTION
   freeze probability
          ↓
   scan every supported fixture across available books/exchanges
          ↓
   validate event identity + quote freshness
          ↓
   calculate EV at every observed price

3. VALIDATION
   point-in-time forecast/quote snapshots
          ↓
   chronological walk-forward replay
          ↓
   log loss / Brier / calibration + later closing-line/outcome evidence
```

A displayed positive EV is **not described as a demonstrated betting edge**. V3 calls it a research candidate until the separate forecasting and betting-edge validation gates have enough evidence.

## What V3 changes

### Dynamic independent football model

The old 90/180/360-day pseudo-ensemble is replaced by two genuinely different independent model families:

- a dynamic Poisson/Dixon-Coles score model with evolving team attack and defence;
- a league-local Elo rating model.

The dynamic model updates chronologically from results, estimates the league scoring environment and applies season regression. Current bookmaker prices never enter either independent component.

All unseen Elo teams now start from the same 1500 prior, removing the ordering-dependent V2.4 initialisation anomaly.

### Chronologically learned stacking and calibration

V3 no longer equal-weights several correlated goal-model variants.

For each league, a small regularised candidate grid is selected using only earlier chronological validation data. V3 learns:

- dynamic-state learning/decay settings;
- Elo K/home-advantage settings;
- the convex Dynamic/Elo stack weight;
- a probability-temperature calibration parameter.

These settings are selected on past data only. Current prices and future results are excluded.

### Proper model uncertainty

V2.4's `conservative_probability = minimum(component probability)` is removed from V3 decision logic.

V3 instead uses moving-block bootstrap refits of the chronological match history and reports:

- central probability;
- 5th percentile probability;
- 95th percentile probability;
- bootstrap standard deviation;
- central EV;
- 5th-percentile EV;
- estimated `P(EV > 0)`.

The block bootstrap preserves local stretches of match history rather than treating every fixture as an independent random observation.

### Promotion/relegation strength transfer

For supported connected divisions, a club can carry a shrinkage-weighted prior from the lower division instead of being reset to an entirely generic top-flight prior.

The current transfer graph covers the available paired divisions in England, Scotland, Germany, Italy, Spain and France. Transferred priors are deliberately uncertain and cannot automatically receive the highest confidence label.

### Price-shop every modelled fixture before ranking

V3 removes the V2.4 top-N execution filter.

The old path was effectively:

```text
independent probability
    ↓
rank using Sportsbet EV
    ↓
scan only leading fixtures at other books
```

V3 is:

```text
independent probability
    ↓
ALL independently modelled fixtures
    ↓
complete available quote matrix
    ↓
identity/freshness checks
    ↓
EV for every side/book
    ↓
rank only now
```

This means a fixture is not excluded merely because Sportsbet has a poor price while another execution source has a good one.

### Hardened event matching

A quote cannot enter V3 EV merely because two fuzzy names look approximately similar.

The V3 execution matcher uses:

- league resolution before event matching;
- home and away identity checks separately;
- home/away orientation validation;
- a 90-minute kickoff tolerance instead of the old eight-hour window;
- ambiguity rejection where multiple candidate events are too close;
- rejection of in-play events from the pre-match scan.

False negatives are preferred to false positive event matches.

### Quote microstructure

Where supplied by the source, V3 quote records can store:

- source;
- side;
- decimal odds;
- receive time;
- market timestamp;
- quote age;
- liquidity;
- available size;
- commission;
- line;
- provider event ID;
- event-match confidence.

Sportsbet and fee-adjusted Polymarket prices remain base execution comparisons. The additional PulseScore bookmaker feeds are opportunistic and depend on the user's API access.

## Chronological walk-forward validation

V3 adds an expanding-window replay engine.

```text
train history
    ↓
predict untouched period A
    ↓
train history + A
    ↓
predict untouched period B
    ↓
repeat
```

There is no random train/test shuffle.

Matches on the same calendar day are predicted as a batch before any result from that day is allowed to update the state. This prevents an earlier row on a date from leaking its result into another fixture on the same date when historical data lacks precise decision timestamps.

The primary forecasting metrics are:

- multiclass log loss;
- multiclass Brier score;
- probability calibration error;
- outcome-specific binary log loss.

The live application runs a bounded recent-fold diagnostic so refreshes remain usable. The underlying validator can run a longer historical study separately.

## Immutable V3 research records

V3 adds dedicated SQLite tables for:

### Forecasts

- event identity and kickoff;
- decision/capture time;
- V3 model version;
- Git commit when available;
- feature-schema version;
- feature snapshot hash;
- independent H/D/A probabilities;
- bootstrap intervals and standard deviation;
- dynamic-model and Elo component probabilities;
- goal intensities;
- stack weight and calibration temperature;
- history/sample evidence;
- promotion-prior flags;
- decision-time market reference.

### Quotes

Every observed execution quote can be retained with its microstructure fields rather than only saving the winning price.

### Decisions

V3 stores the exact probability, uncertainty, execution price, central/lower EV, `P(EV>0)`, confidence and research-candidate status used at the decision timestamp.

### Validation

Out-of-sample predictions and aggregate validation runs are persisted separately from current forecasts.

### Outcomes and sharp lines

V3 also provides dedicated stores for settled outcomes and point-in-time sharp-market observations so repeated collection can build T-24h / T-6h / T-1h / T-10m / final-pre-kickoff trajectories and later calculate genuine CLV.

## Decision labels

V3 deliberately avoids the old implication that model agreement proves robustness.

### V3 HIGH-CONFIDENCE CANDIDATE

Requires, at the observed price:

- central EV at or above the configured threshold;
- 5th-percentile EV above zero;
- estimated `P(EV > 0)` at least 90%;
- medium/high football-model confidence;
- high-confidence event matching.

This is still a **research candidate**, not a claim that the strategy has demonstrated a sustainable edge.

### V3 +EV CANDIDATE — UNCERTAINTY

The point estimate clears the threshold but the uncertainty evidence is weaker.

### POSITIVE — BELOW V3 THRESHOLD / NEGATIVE EV

Shown explicitly rather than hidden.

## Dashboard and navigation

V3 keeps the condensed main navigation:

- Dashboard
- Markets
- Analysis
- Tools
- Research
- Settings

Older version-specific analysis/validation pages still exist in the source for backwards compatibility but are hidden from the V3 navigation where they duplicate the new V3 pages.

The active **Analysis → Independent model** page shows:

- V3 H/D/A probability;
- 5th-percentile H/D/A probability;
- fair odds;
- Dynamic and Elo components;
- model goal intensities;
- learned stack weight;
- calibration temperature;
- decision-time market reference and residual;
- data/confidence evidence.

The active **Research → Walk-forward** page shows the recent chronological out-of-sample log loss, Brier and calibration metrics by league and pooled across supported leagues.

The live Dashboard progress panel continues to show the current analysis stage, detail, progress percentage and elapsed/estimated remaining time while API calls, modelling, quote scanning, validation and persistence run.

## What V3 intentionally does not fake

Several high-potential ideas remain research-gated rather than being forced into production without suitable point-in-time data:

- genuine shot-derived xG/xGA across all covered leagues;
- expected-XI/player-value modelling;
- a sharp-market residual/hybrid production probability;
- adaptive uncertainty-aware staking/Kelly sizing.

The repository already contains earlier EPL football-intelligence research scaffolding, but V3 does not silently promote incomplete or inconsistently timestamped xG/player inputs into the headline independent probability.

The next scientific admission rule is simple: a new component should enter the production probability only if it improves untouched chronological forecasts, and a betting rule should only be promoted after its point-in-time EV also produces credible positive closing-line/economic evidence after costs.

## Model terminology

`Goal intensity` means the model-implied expected scoring rate (lambda). It is **not** shot-derived expected goals (xG).

## Data sources

The application currently relies on the sources already supported by the repository and the user's access, including:

- Football-Data historical result files for independent model history;
- Sportsbet via PulseScore for current target/execution prices;
- Pinnacle/PS3838 and Asian lines where available for market diagnostics;
- Polymarket where matched and liquid enough to be useful;
- Bet365, Ladbrokes, TAB, Unibet AU, BetRight, Stake and Cloudbet feeds where the user's PulseScore plan exposes them.

Current market information remains outside `P_independent`.

## Running from source

```bash
python -m pip install -r requirements.txt
python app/launcher_v3.py
```

Run the automated test suite:

```bash
python -m unittest discover -s tests -v
```

Run the V3 frozen-compatible self-test path:

```bash
python app/launcher_v3.py --self-test
```

## Windows build

GitHub Actions builds:

- `EPL-Value-Betting-v3.0.0-Setup.exe`
- `EPL-Value-Betting-v3.0.0-Portable.exe`

The workflow runs the full unit-test suite and then launches the frozen executable with `--self-test` before producing the installer artefacts.

## Research warning

This project is an analysis/research tool. Estimated probabilities and positive expected value are model outputs, not guarantees of future performance. Prices move, markets suspend, liquidity and stake limits matter, and a probability model can be wrong or miscalibrated even when its mathematics and software are functioning as designed.
