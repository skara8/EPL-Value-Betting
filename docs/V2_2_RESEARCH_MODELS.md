# V2.2 Research Models — Candidate Signal, Not Assumed Edge

## Purpose

V2.2 converts several promising ideas from the public GitHub model review into explicit, storable research features while leaving the production `ROBUST +EV` decision rule unchanged.

The central question is not whether Elo, xG, lineup continuity or Poisson modelling sound sensible. It is whether each feature adds information beyond the current sharp-market probability **before kickoff** and improves future out-of-sample evidence.

## Public-model lessons implemented

### MatchLabs — `mejding/matchlabs`

The repository combines historical results, xG, fatigue, Elo, lineup stability, opponent-adjusted xG, tactical features, bootstrap uncertainty and time-based probabilistic evaluation.

The most important reusable lesson is negative as well as positive: its reported experiments show that richer feature sets can have worse test-period log loss and Brier score than simpler variants. Therefore V2.2 does not promote a feature merely because it is plausible.

Implemented from this direction:

- historical Elo feature;
- lineup continuity feature;
- recent xG context;
- recent-opponent strength context;
- explicit cross-model disagreement;
- stored feature snapshots for future time-ordered ablation.

Deferred until enough history exists:

- trained bootstrap residual model;
- production opponent-adjusted xG probability correction;
- SHAP/permutation importance on a trained residual model.

### ProphitBet — `kochlisGit/ProphitBet-Soccer-Bets-Predictor`

Useful ideas include holdout/cross-validation, sliding evaluation, variance/correlation analysis, feature selection and comparison of simpler and more complex models.

Implemented direction:

- keep candidate features separable in storage;
- preserve the market-only baseline;
- test each candidate against later evidence instead of permanently blending every feature together.

### Dixon-Coles / Poisson public implementations

Useful ideas include attack strength, defence strength, home advantage, Poisson score distributions and time decay so old matches matter less.

V2.2 implements a conservative **time-decayed goals-strength Poisson research model** rather than claiming a fully fitted Dixon-Coles maximum-likelihood model.

## V2.2 feature definitions

### Elo

Historical EPL results are processed chronologically. Each team begins near 1500 and receives standard Elo updates with a small home-advantage term. Ratings partially regress toward 1500 at season changes.

To express Elo as H/D/A for comparison with the market, V2.2 uses the sharp-market draw probability as a draw anchor and allocates the remaining probability between home and away according to the Elo strength differential.

This means the Elo residual is primarily a directional team-strength disagreement, not an independent estimate of draw frequency.

### Time-decayed goals-strength Poisson

Historical EPL results receive exponential weights with a 180-day half-life and a maximum lookback of roughly 1000 days.

The model estimates:

- weighted league home-goal average;
- weighted league away-goal average;
- home team's venue-specific attack strength;
- home team's venue-specific defensive weakness;
- away team's venue-specific attack strength;
- away team's venue-specific defensive weakness.

Team rates are shrunk toward league averages using pseudo-match priors. Expected home/away goals are then converted into H/D/A probabilities with independent Poisson score distributions.

A team needs a minimum amount of EPL history before the research model is shown. Newly promoted teams may therefore have no historical Poisson output at first; this is preferable to fabricating strength.

### Expected-XI continuity

For each expected starter:

```text
recent-start share × current start probability × player-strength weight
```

is aggregated across the expected XI.

The result is a 0–100% continuity estimate. It is intended to capture how closely the expected lineup resembles the team's recent regular lineup.

### Recent xG and opponent strength

V2.2 records recent net xG from the existing EPL football-intelligence layer and separately records the average Elo strength of those recent opponents.

It deliberately does not apply an arbitrary opponent-strength multiplier to the production probability. A later historical residual model can learn whether the combination adds predictive value.

### Cross-model spread

For any fixture with at least two available probability models:

```text
spread = maximum H/D/A probability range across available models
```

Large spread identifies unstable/disputed matches. The Research Models page also reports whether the sharp market, Elo and time-decayed Poisson model agree on the most likely outcome.

## Historical data source

EPL historical result CSVs are downloaded from football-data.co.uk and cached locally. Up to five seasons are used. Completed seasons receive a long cache TTL; the current season refreshes more frequently.

The source is optional. If unavailable, the production market model remains fully operational.

## Anti-leakage rule

Every historical model is calculated using only matches with timestamps strictly before the upcoming fixture's kickoff.

No future result is allowed into Elo, goals-strength or validation features.

## Production boundary

V2.2 does **not** alter the green recommendation because Elo or Poisson agrees with it.

Production remains:

```text
independent external market probability
→ least-favourable external stress test
→ best eligible execution price
→ ROBUST +EV threshold
```

Research becomes:

```text
market probability
vs Elo residual
vs time-decayed Poisson residual
vs lineup continuity
vs xG/opponent context
→ store
→ compare with future sharp close/result
```

## Promotion criteria for a future V2.x feature

A research feature should only be considered for production probability correction after a sufficiently large time-ordered sample demonstrates incremental improvement over the market-only baseline.

Priority evidence:

1. sharp closing-line value;
2. multiclass log loss;
3. Brier score;
4. calibration / expected calibration error;
5. realised ROI with uncertainty intervals.

A feature that increases historical ROI but worsens log loss/calibration should not automatically be promoted.

## CPU acceleration

V2.2 also parallelises the expensive per-fixture AH/totals Poisson calibration using multiple processes. This is computational only; it does not change the probability formula.

The automatic worker rule targets roughly 75% of logical processors and caps at 10 workers. Small scans and failed process creation automatically use the serial implementation.
