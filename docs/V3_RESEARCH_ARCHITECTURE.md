# V3 Research Architecture

## Design objective

V3 exists to answer two different questions without conflating them:

1. **Forecasting:** does football-derived information predict outcomes well out of sample?
2. **Betting:** when the model disagrees with an executable market, does that disagreement produce positive closing-line value and eventually positive returns after costs?

The architecture therefore keeps an independent football probability separate from decision-time market information.

## Production flow

1. Fetch current Sportsbet catalogue and reference-market diagnostics.
2. Resolve leagues conservatively.
3. Load only historical football results required by the current supported leagues, plus adjacent lower divisions used for promotion priors.
4. Fit the dynamic goal state chronologically.
5. Fit the separate Elo family.
6. Select stack weight and calibration temperature using past chronological validation only.
7. Generate independent H/D/A probabilities.
8. Refit moving-block bootstrap states and derive probability uncertainty.
9. Freeze the probability.
10. Scan every independently modelled fixture across every available execution feed.
11. Reject ambiguous league/event/team matches.
12. Calculate EV at every valid quote.
13. Rank only after the quote matrix is complete.
14. Persist forecasts, quotes and decisions append-only.
15. Run recent expanding-window validation folds and persist OOS predictions.

## Independent model

The dynamic score model uses likelihood-score residuals from observed goals to update latent attack and defence. This is a fitted dynamic state process rather than a fixed rolling goals average with several manually selected half-lives.

Elo remains deliberately separate. V3 counts two genuinely distinct model families rather than counting the same goal model at several parameter settings as independent votes.

The stack is constrained to a convex combination of the two families. Candidate dynamic weights and a probability-temperature calibration parameter are selected on a trailing chronological validation segment using log loss.

## Uncertainty

For each league, V3 creates moving-block bootstrap resamples of pre-decision history, refits the state, and re-prices the current fixture. The resulting H/D/A triplets remain coherent within each draw.

Stored summaries include 5th/95th percentiles and standard deviation. The 5th percentile is used as a downside probability check in the candidate decision layer.

## Walk-forward protocol

Random cross-validation is forbidden.

The validation engine uses an expanding training window and untouched chronological test folds. Because some historical sources provide dates without exact kickoff times, all fixtures sharing a date are predicted before any result from that date updates the state.

Primary metric: multiclass log loss.

Secondary metrics: Brier score, calibration error, outcome-specific binary log loss.

## Immutable research record

A live forecast must be reproducible from:

- event identity and kickoff;
- decision timestamp;
- model version and git commit (when available);
- feature schema version and feature snapshot hash;
- component and stacked probabilities;
- uncertainty summaries;
- all observed execution quotes with timestamps/metadata;
- later outcome;
- later sharp-market trajectory.

Outcomes and closing lines are stored in separate append-only tables so they do not mutate the original forecast observation.

## Deferred research layers

The following are deliberately not faked in V3.0:

- shot-derived xG/xGA across all leagues;
- expected-XI/player-strength production effects;
- a learned sharp-market residual production model;
- adaptive Kelly staking.

Interfaces and persistence now exist to test these additions properly. They should enter production only after chronological ablation demonstrates incremental value.
