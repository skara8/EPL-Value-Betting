# Football Value Betting V3 — Scientific Forecasting Laboratory

V3 is a direct response to the independent PhD-level audit of V2.4.

## Core principle

V3 separates four questions that earlier betting applications often mix together:

1. **Football forecasting** — what are the Home / Draw / Away probabilities using football information only?
2. **Market comparison** — how does that estimate differ from a sharp market available at the same decision time?
3. **Execution** — what prices are actually observable across books/exchanges for the same event?
4. **Validation** — did the forecast and apparent edge survive future, genuinely unseen data?

A larger displayed EV is not itself evidence that the model improved.

## Production baseline

The live V3 baseline remains bookmaker-independent.

```text
historical football data
        ↓
league/team state
        ↓
genuinely available independent model components
        ↓
H / D / A probability
        ↓
FREEZE probability
        ↓
complete quote matrix
        ↓
EV at each quote = probability × decimal odds - 1
```

Current bookmaker prices do not vote in this probability.

### V3 corrections to V2.4

- Every unseen Elo team starts from the same prior; there is no order-dependent first-team exception.
- Missing short/long-decay models stay **missing**. They are not silently replaced by the main score model and counted as new evidence.
- Component count means the number of genuinely available model variants.
- The minimum component probability is retained only as a **stress scenario** for compatibility/research. It is not called a confidence interval and cannot validate an edge.

## Dynamic-state challenger

V3 adds an adaptive attack/defence state model as a research challenger.

It updates latent team attack and defence sequentially from earlier match residuals and shrinks stale team states toward league average through time.

The learning rate and annual shrink factor are selected on a chronological internal validation segment, never from current bookmaker prices.

The challenger is visible in the V3 Laboratory and Independent Model pages, but **does not alter the production probability**.

A new statistical model must first demonstrate better out-of-sample forecasting before it can be considered for production.

## Complete quote matrix

V2.4 price-shopped only a pre-ranked subset of fixtures. This could miss an attractive quote simply because Sportsbet's own price made the fixture look uninteresting.

V3 changes the order:

```text
independent model
      ↓
ALL independently priced fixtures
      ↓
scan available execution books
      ↓
strict event identity validation
      ↓
quote matrix
      ↓
calculate EV
      ↓
rank
```

This is an execution improvement, not a probability-model change.

## Strict event matching

A false match can create a spectacular but imaginary edge. V3 therefore prefers a missing quote to an uncertain quote.

Additional-bookmaker quotes require:

- home and away team identities in the correct orientation;
- strong individual team-name similarity;
- strong pair similarity;
- compatible senior/youth/women/reserve markers;
- strong league match;
- kickoff within 45 minutes;
- rejection when two provider events are almost equally plausible.

Each stored quote includes an event identifier when supplied by the provider, observation timestamp, provider market timestamp and match-confidence score.

## Immutable research snapshots

Every saved V3 refresh appends three point-in-time datasets:

### Model snapshot

Contains:

- stable event key;
- kickoff UTC;
- model/version schema;
- independent H/D/A;
- individual model components;
- component count;
- model spread;
- dynamic challenger probabilities and tuning parameters;
- separate market-reference probability.

### Quote snapshot

Contains every observed quote for the independently modelled fixture, including:

- source;
- side;
- odds;
- observation timestamp;
- provider market timestamp when available;
- provider event ID;
- match-confidence score;
- independent probability and EV at that quote.

### Decision snapshot

Contains the exact price and independent probability used for each decision-time research signal.

Historical observations are append-only. A later price does not overwrite what V3 actually observed earlier.

## Walk-forward validation

The V3 Laboratory can run a chronological replay against the league histories represented in the current scan.

Football-Data history is often date-based rather than precise intra-day timestamp data. Therefore V3 uses a conservative rule:

> Every fixture on a date is predicted before any result from that date updates the model.

This prevents a later same-day match leaking into an earlier prediction.

### Metrics

V3 evaluates:

- multiclass log loss;
- Brier score;
- Ranked Probability Score;
- pooled calibration ECE;
- paired challenger-minus-baseline log-loss difference;
- league-month cluster-bootstrap 95% confidence interval.

Negative delta log loss means the challenger is better.

### Forecast gate

A challenger can earn `FORECAST_VALIDATED` only when there are at least 1,000 chronological predictions, the clustered 95% CI for its log-loss improvement is entirely below zero, and Brier/calibration do not materially deteriorate.

This is intentionally not a betting-edge certificate.

## Two validation gates

V3 distinguishes:

### Forecast validation

Does a football model predict unseen match outcomes better?

### Betting-edge validation

Do point-in-time research signals subsequently demonstrate economically meaningful evidence such as positive after-cost sharp closing-line value, sensible EV calibration and execution availability?

A model can be `FORECAST_VALIDATED` and still have no demonstrated betting edge.

Accordingly, the Dashboard uses:

- `RESEARCH +EV — UNVALIDATED` for threshold-clearing theoretical EV while the edge gate is unproven;
- `VALIDATED +EV` only for a future `EDGE_VALIDATED` state after the stronger point-in-time market/CLV test exists and passes.

## Market intelligence

Pinnacle, Asian Handicap, Polymarket, Sportsbet de-vig probabilities and bookmaker consensus remain valuable research information.

They are stored/displayed separately from `P_independent`.

Future V3 research can test a second probability:

```text
P_production = sharp decision-time market prior + validated football residual
```

but this hybrid must beat a sharp decision-time market out of sample before it is allowed to replace the independent research baseline.

## Research-first upgrades not promoted automatically

The audit identified several high-potential additions. V3 deliberately treats them as experiments rather than unquestioned production features:

- genuinely fitted dynamic/hierarchical score model;
- shot-derived xG/xGA;
- promotion/relegation cross-division priors;
- expected-XI/player strength;
- out-of-fold constrained stacking;
- chronological calibration;
- posterior/bootstrap probability uncertainty;
- sharp-market residual model;
- execution-aware dynamic thresholds.

Each should be admitted only after a pre-specified chronological ablation demonstrates incremental information.

## Acceptance philosophy

For a candidate model, the central question is not:

> Did historical ROI increase?

It is:

> Did the candidate reduce future proper scoring loss relative to the frozen baseline, with uncertainty excluding zero, without worsening calibration or collapsing in individual leagues?

Only after that comes the economic question:

> Does the information create positive, executable, after-cost value relative to a strong point-in-time market benchmark?

That is the scientific definition of V3.
