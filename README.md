# Football Value Betting

Windows desktop research application for independently forecasting football H/D/A probabilities, comparing those frozen probabilities with observed prices, and testing whether apparent value survives chronological out-of-sample validation.

## Current version

**V3.0.0 — Scientific Forecasting Laboratory**

V3 was rebuilt from a PhD-level audit of V2.4. The most important change is philosophical: **the application no longer treats a large displayed EV as evidence that an edge exists.**

```text
FOOTBALL DATA
    ↓
INDEPENDENT H/D/A PROBABILITY
    ↓
FREEZE PROBABILITY
    ↓
ALL-BOOK QUOTE MATRIX
    ↓
THEORETICAL EV
    ↓
CHRONOLOGICAL VALIDATION
    ↓
ONLY THEN: evidence for or against an edge
```

## The V3 rules

### 1. Football probability and price are separate

Current Sportsbet, Pinnacle, Polymarket, Asian Handicap and other bookmaker prices do not enter the independent football probability.

For independent probability `p` and observed decimal odds `O`:

```text
fair odds = 1 / p
EV = p * O - 1
```

Changing a bookmaker quote changes EV. It must not change `p`.

### 2. Every model component must be genuine

V2.4 could copy its main score-model probability into a missing short/long-decay slot and still count the copied value as another component.

V3 removes that behaviour. Missing means missing. `component_count` counts only genuinely available forecasts.

### 3. Model spread is not a confidence interval

V3 retains the least-optimistic component only as a transparent **stress scenario** for research compatibility.

It is not a posterior interval, confidence bound or proof of robustness.

### 4. New statistical models are challengers first

V3 includes an adaptive dynamic attack/defence state model. Its learning rate and shrinkage are selected chronologically from earlier results.

It appears in the Independent Model and V3 Laboratory pages but cannot alter the live production probability until it proves an out-of-sample improvement.

### 5. Price shopping happens before ranking

V2.4 checked additional bookmakers only for a limited pre-ranked set of fixtures. That could miss a valuable quote simply because Sportsbet's price was poor.

V3 instead does:

```text
all independently modelled fixtures
        ↓
scan available execution books
        ↓
strict identity validation
        ↓
calculate EV for every observed side/price
        ↓
rank
```

The price scanner uses the existing PulseScore-accessible sources, currently including where available:

- Sportsbet
- Bet365
- Ladbrokes
- TAB
- Unibet AU
- BetRight
- Stake
- Cloudbet
- Polymarket / exchange context

### 6. Wrong-match quotes are treated as more dangerous than missing quotes

V3 tightens additional-bookmaker identity matching substantially:

- kickoff difference no greater than 45 minutes;
- strong home-team and away-team similarity independently;
- strong pair similarity;
- strict home/away orientation;
- youth/women/reserve markers must be compatible;
- stronger competition matching;
- ambiguous near-ties are rejected rather than guessed.

This is designed to prevent a wrongly attached long price from manufacturing a huge fake edge.

## Independent league coverage

The independent historical layer retains V2.4's broad Football-Data coverage and only models a Sportsbet competition where league/team history can be resolved confidently.

Coverage includes major divisions in:

- England
- Scotland
- Germany
- Italy
- Spain
- France
- Netherlands
- Belgium
- Portugal
- Turkey
- Greece
- Argentina
- Austria
- Brazil
- China
- Denmark
- Finland
- Ireland
- Japan
- Mexico
- Norway
- Poland
- Romania
- Russia
- Sweden
- Switzerland
- USA / MLS

Unsupported competitions display **Independent model unavailable** rather than substituting bookmaker consensus and calling it an independent probability.

## Current production baseline

V3 deliberately keeps the production baseline relatively transparent while the research laboratory determines what should replace it.

Available baseline components are:

1. time-decayed Dixon-Coles-style score model;
2. league-local Elo;
3. short-decay score model when genuinely available;
4. long-decay score model when genuinely available.

The central probability is currently the equal average of the genuinely available baseline components.

This is a baseline, not a claim that equal weighting is optimal.

## Dynamic-state challenger

The first V3 challenger evolves latent team attack and defence sequentially.

It:

- uses only earlier results;
- shrinks stale team state back toward league average;
- tunes update speed and shrinkage on an earlier chronological validation segment;
- produces a separate H/D/A forecast;
- is compared against the production baseline;
- is not promoted automatically.

The research question is straightforward:

> Does this dynamic representation predict unseen football results better than the frozen baseline?

## Walk-forward validation

Under **Research → V3 laboratory**, click **Run walk-forward validation**.

The engine:

1. loads the cached historical leagues represented in the current fixture scan;
2. predicts chronologically;
3. withholds every result on a calendar date until all matches on that date have been predicted;
4. compares the production baseline with the dynamic challenger;
5. calculates proper probability scores;
6. estimates uncertainty around the paired improvement using a league-month cluster bootstrap.

### Reported metrics

- multiclass log loss — primary model-selection metric;
- Brier score;
- Ranked Probability Score;
- calibration ECE;
- challenger minus baseline log loss;
- cluster-bootstrap 95% confidence interval;
- bootstrap probability the challenger improves log loss.

A challenger can earn **FORECAST VALIDATED** only when the evidence threshold is met.

That still does **not** mean a betting edge is proven.

## Two separate validation gates

### Forecast gate

> Does the football model predict future outcomes better?

### Betting-edge gate

> Does the point-in-time information subsequently create positive, executable after-cost value relative to a strong market benchmark such as a sharp close?

V3 will not call a threshold-clearing EV a validated edge merely because the forecast gate passes.

Until the second gate exists and passes, the Dashboard uses labels such as:

**RESEARCH +EV — UNVALIDATED**

rather than automatically presenting a green proven-edge signal.

If every available outcome is negative EV, the Dashboard still shows the highest EV available, clearly labelled as not a +EV signal.

## Immutable research storage

Each saved V3 refresh appends:

### Model snapshots

- event identity and kickoff UTC;
- version/schema;
- independent H/D/A;
- all available baseline components;
- dynamic challenger;
- model spread and confidence metadata;
- separate market-reference probabilities.

### Quote snapshots

- every observed quote from the complete scan;
- source and side;
- odds;
- observation time;
- provider market timestamp where available;
- provider event ID;
- event-match confidence;
- independent probability and EV at that quote.

### Decision snapshots

The exact model probability, fair odds, execution source/price and status observed at decision time.

Later data never overwrites an earlier point-in-time observation.

## Market intelligence remains separate

V3 still displays and stores where available:

- Sportsbet implied/de-vigged probability;
- Pinnacle/PS3838;
- Asian Handicap and totals;
- Polymarket;
- broader bookmaker references;
- football-model-versus-market residuals.

These are important research benchmarks, but they do not redefine `P_independent`.

A future challenger may test a separate market-anchored production model:

```text
P_production = sharp decision-time market prior + validated football residual
```

That hybrid will only be promoted if it beats the sharp decision-time market chronologically.

## What V3 deliberately does not pretend is solved

The audit identified several high-potential research upgrades that require evidence before production deployment:

- a genuinely fitted dynamic hierarchical/Bayesian score model;
- shot-derived xG/xGA state;
- promotion/relegation cross-division priors;
- expected-XI/player strength;
- out-of-fold constrained stacking;
- chronological probability calibration;
- posterior/bootstrap probability uncertainty;
- sharp-market residual modelling;
- execution-aware dynamic EV thresholds;
- proper closing-line and after-cost economic validation.

These are experiments, not hard-coded probability bonuses.

## User interface

Top-level navigation remains deliberately compact:

- **Dashboard** — simplest current theoretical comparison;
- **Markets** — matches, league coverage and best observed prices;
- **Analysis** — detailed independent probability and football/market context;
- **Tools** — Dutch calculator;
- **Research** — V3 laboratory, validation, history and diagnostics;
- **Settings**.

The Dashboard is designed for quick reading. The detailed pages explain how the result was constructed.

## Data location

Research data, settings and logs are stored outside Program Files under:

```text
%LOCALAPPDATA%\EPLValueBetting\
```

Historical football files are cached locally so repeat runs avoid unnecessary downloads.

## Windows releases

Successful production builds publish:

- `EPL-Value-Betting-vX.Y.Z-Setup.exe`
- `EPL-Value-Betting-vX.Y.Z-Portable.exe`

The installed application can check GitHub Releases and launch the newer installer.

## Detailed V3 methodology

See:

```text
docs/V3_SCIENTIFIC_MODEL.md
```

## Scope

This is a theoretical sports-market research application. It does not automatically place bets.

The objective is not to maximise the number of displayed positive-EV selections. The objective is to discover whether any forecasting or execution signal produces **repeatable out-of-sample information gain** and, separately, whether that information survives real market prices, costs and execution constraints.
