# V1.7 — Decision-first dashboard

V1.7 changes presentation rather than hiding or removing the underlying model.

## User-facing rule

The dashboard answers three questions first:

1. Is there a current theoretical +EV single-outcome option?
2. What is the price and estimated EV?
3. In simple language, why does the model think the price may be too high?

The detailed market, Asian Handicap, contextual and research calculations remain available in the other tabs.

## Recommendation guardrail

A contextual adjustment is not allowed to turn a clearly negative independent-market outcome into a dashboard recommendation.

For an outcome to appear on the simplified +EV shortlist:

- the V1.5 independent-market base EV must be at least 0%; and
- the context-adjusted EV must clear the user's configured minimum EV threshold.

This prevents subjective context from manufacturing a pick from a market view that was already negative.

## Ranking

The shortlist ranks robustness before headline EV:

1. whether conservative external-reference EV remains positive;
2. external-model confidence;
3. conservative EV;
4. context-adjusted EV.

The highest ranked result becomes the green dashboard card.

If no result qualifies, the dashboard says **No +EV option found** rather than forcing a selection.

## Plain-English explanation

For each shortlisted outcome the dashboard explains:

- the model's estimated probability;
- the break-even probability implied by the Sportsbet price;
- the approximate probability gap;
- whether current context meaningfully helps or hurts the selection.

Example:

> Our model gives Arsenal about 60.5% chance. At $1.78, the price only needs about 56.2% to break even. That leaves about +4.3 percentage points of model edge. Current team context barely changes the estimate.

## Dutch scan

V1.7 automatically checks each fetched H/D/A market using the best effective price available between Sportsbet and Polymarket.

It surfaces:

- full-market arbitrage when the inverse-odds sum is below 1 after Polymarket taker fees; or
- a two-outcome partial Dutch when model-based expected return clears a research threshold.

Partial Dutch ideas are explicitly labelled as non-arbitrage because the uncovered third outcome can lose the full outlay.

## FPL availability correction

V1.6 exposed a data-quality issue: the FPL bootstrap feed can retain players who have left permanently or gone out on loan. V1.7 filters clear transfer/loan departure news from injury/availability penalties.

It also stores availability penalty by position group:

- goalkeeper;
- defence;
- midfield;
- attack.

This is the first step towards a proper position-v-position player model.

## Next modelling stages

The next high-value additions should focus on data that can be validated against closing prices and results:

1. expected-XI and position-group strength;
2. rolling xG/xGA and chance-quality performance;
3. style/tactical profiles and style-v-style interaction;
4. schedule/rest/travel automation;
5. manager/system implementation features;
6. closing-line and outcome ingestion;
7. out-of-sample calibration of contextual coefficients.

Until those variables demonstrate incremental predictive value, V1.7 keeps the contextual probability move small and transparent.
