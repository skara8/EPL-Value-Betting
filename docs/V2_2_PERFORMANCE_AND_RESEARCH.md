# V2.2 performance and research model notes

## Why CPU usage was low

The V2.1 application did the most expensive probability calculation in one Python process. On a 12-logical-CPU machine, one saturated core appears as roughly 8% total CPU. Normal Python threads would not materially improve this particular pure-Python numerical work because of the interpreter lock.

V2.2 therefore uses Windows worker **processes**, not ordinary threads, for the expensive per-fixture probability stage. The default worker count is about 75% of logical CPUs, capped at 12 and leaving capacity for the GUI/OS. A 12-thread machine therefore uses 9 probability workers. The app falls back to the previous single-process path if multiprocessing cannot start.

This optimisation targets the de-vig / Asian-Handicap-total Poisson / fair-probability / robust-EV stage. Network stages will still show lower CPU use because they are waiting for remote APIs rather than waiting for the processor.

## Network-bound price shopping

V2.2 also checks independent bookmaker feeds with up to three concurrent network workers. This is deliberately much lower than the CPU worker count because the bottleneck is network latency and API allowance, not arithmetic. The cap avoids issuing all supported provider requests at once.

## Research models added from the GitHub review

The new **Analysis → Research models** page implements useful ideas found repeatedly across public football-prediction projects while keeping them separate from the production Robust +EV rule until they prove incremental value.

### Time-decayed Elo

Historical results are processed in chronological order. Older matches have less indirect relevance because Elo updates continuously, goal-margin information modestly scales each update and home advantage is explicit. The resulting rating difference is converted to a three-way probability through a Poisson score layer.

### Time-decayed low-score-corrected Poisson

Recent goals for/against are exponentially downweighted with a 180-day half-life. Team attack/defence strengths are shrunk towards the league average so sparse/newly promoted teams do not create extreme estimates. A bounded Dixon-Coles-style low-score correction adjusts the 0-0, 0-1, 1-0 and 1-1 cells.

This component is deliberately labelled Poisson/Dixon-Coles-style rather than claiming a full maximum-likelihood Dixon-Coles implementation.

### Market residual, not market replacement

For each EPL outcome the app shows:

- live independent-market fair probability;
- Elo probability;
- time-decayed Poisson probability;
- small football/context shift;
- experimental residual probability;
- residual versus the market;
- model dispersion;
- whether the historical models agree on the direction of the residual.

The residual is the research object. The historical models are not automatically assumed to be more accurate than Pinnacle/Polymarket.

### Lineup continuity and football context

The existing expected-XI data is converted into a small continuity measure using start probability, availability and recent starts. Recent-form and rest signals contribute only a tightly capped research shift. This cannot rescue a price that fails the market-supported Robust +EV test.

### Strict anti-leakage rule

Every historical prediction uses only matches with kickoff times earlier than the target match. Tests explicitly add an impossible future 20-0 result and verify that an earlier prediction is unchanged.

### Chronological holdout validation

Where historical bookmaker prices are available, V2.2 de-vigs the reference price and compares probability quality using three-way Brier score. The data is ordered chronologically, the first 70% chooses a small blend weight, and the final 30% is untouched holdout data.

The Research Models page reports:

- market holdout Brier;
- Elo holdout Brier;
- Poisson holdout Brier;
- residual-blend holdout Brier;
- selected historical-model weight;
- holdout improvement versus the market baseline.

If the historical residual fails to improve the holdout Brier score, the app explicitly says so and leaves it out of the primary betting signal. This is the feature-ablation/validation discipline highlighted by the GitHub research.

## Current strategy rule

V2.2 keeps V2.1's primary Robust +EV rule unchanged:

1. at least two independent market components;
2. medium/high market confidence;
3. external disagreement <= 4 percentage points;
4. least-favourable external market probability must still clear the configured EV threshold at an eligible fixed-odds execution price.

Elo, Poisson, xG/context and lineup-continuity residuals are visible and stored as research evidence. They are candidates for a future production residual correction only after chronological calibration and live closing-line validation show that they add information beyond the market.
