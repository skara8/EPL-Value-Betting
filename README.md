# Football Value Betting

Windows desktop research application for comparing football prices, estimating fair probabilities, studying market inefficiencies and validating whether apparent edges persist.

## Current version

**V2.2.0**

## V2.2 highlights

### Multicore probability calculations

The expensive per-fixture probability stage now uses Windows worker **processes** rather than one Python interpreter. By default the app uses roughly 75% of logical CPUs, capped at 12 workers, so a 12-thread PC uses 9 probability workers while leaving capacity for Windows and the GUI.

This specifically accelerates the pure-Python de-vig, Asian-Handicap/total Poisson fitting, fair-probability and robust-EV calculations. Network stages can still show low CPU use because they are waiting on remote APIs rather than the processor.

If multiprocessing is unavailable, the app automatically falls back to the previous safe single-process calculation.

### Faster best-price shopping

Independent bookmaker feeds are now checked with up to three concurrent network workers. The cap is deliberately conservative so the app reduces latency without aggressively bursting requests against PulseScore.

### Research models from the GitHub review

A new **Analysis → Research models** page adds the strongest reusable ideas from public football-prediction projects:

- time-decayed Elo;
- time-decayed attack/defence Poisson with a bounded Dixon-Coles-style low-score correction;
- lineup-continuity/context residuals;
- model disagreement/dispersion as an uncertainty measure;
- strict pre-kickoff anti-leakage rules;
- chronological train/holdout evaluation;
- Brier-score comparison against historical market probabilities;
- small residual-blend weight selection on training data, followed by untouched holdout testing.

The central research question is **not** whether Elo or Poisson can predict football in isolation. It is whether their residual versus the market adds information that the market has not already incorporated.

If the historical residual fails to beat the market baseline on chronological holdout data, the app says so and does not promote that residual into the primary betting signal.

See `docs/V2_2_PERFORMANCE_AND_RESEARCH.md` for the detailed design.

## V2.1 robust-edge rule retained

V2.2 keeps the conservative V2.1 primary signal. A green `ROBUST +EV` requires:

1. at least two independent external market components;
2. medium/high reference-market confidence;
3. external disagreement no greater than 4 percentage points;
4. the **least-favourable** external probability still clears the configured EV threshold at an eligible fixed-odds execution price.

The average model EV remains visible, but a large average edge that disappears under the cautious external probability is a watch/research case rather than a primary recommendation.

Polymarket can contribute to the fair-probability model and remains visible in Best Prices/Dutch analysis, but it is not used as the primary execution price for a robust signal when doing so would create circularity.

## Market model

Sportsbet is a target/execution price, not the independent probability source used to assess itself.

The external fair layer can use:

- Polymarket executable YES asks normalised to 100%;
- Pinnacle/PS3838 power-de-vigged 1X2;
- Pinnacle Asian Handicap + total-goals information converted into an implied Poisson score distribution.

Pinnacle's 1X2 and AH/totals estimates are combined into one Pinnacle provider component so one provider does not receive multiple full votes.

For decimal price `O` and fair probability `p`:

```text
EV = p * O - 1
```

## Validation

The Research section stores recommendation history and V2.1/V2.2 evidence. The main early validation target remains closing-line value against a near-kickoff Pinnacle observation. Historical residual models additionally use chronological Brier-score holdout testing.

These checks are designed to stop an attractive-looking backtest or point-estimate EV from being mistaken for a demonstrated betting edge.

## Navigation

```text
Dashboard
Markets
Analysis
Tools
Research
Settings
```

Analysis now contains:

- Candidates;
- Market edge;
- Team context;
- Football model;
- Research models.

## Data and settings

Settings, logs, cached public football data and SQLite research data live under:

```text
%LOCALAPPDATA%\EPLValueBetting\
```

Existing research data is retained across upgrades.

## Scope

This is a theoretical market-efficiency and probability-modelling project. It does not automatically place bets. A displayed positive EV is an estimate, not proof of profitability; the project is designed to test those estimates against independent markets and out-of-sample evidence.
