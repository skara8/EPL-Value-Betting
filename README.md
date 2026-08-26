# Football Value Betting

Windows desktop research application for building **bookmaker-independent football probabilities**, comparing frozen probabilities with execution-eligible prices, and measuring whether apparent value survives chronological forecasting and market-evidence validation.

## Current version

**V3.1.0**

V3.1 is a research-validity upgrade. It does **not** claim that a profitable betting edge has already been demonstrated.

The independent football probability remains separated from current bookmaker and exchange prices. V3.1 strengthens everything around that probability: chronology, execution quality, event identity, provenance, closing-line evidence, outcome settlement and actual-fill economics.

## Current pipeline

```text
historical football results
        ↓
dynamic attack / defence state + separate Elo family
        ↓
chronological tuning + calibration
        ↓
P_independent(H/D/A)
        ↓
moving-block bootstrap uncertainty
        ↓
FREEZE probability
        ↓
scan every modelled fixture across all available execution feeds
        ↓
strict event identity + executable quote gates
        ↓
research candidate decision
        ↓
immutable forecast / quote / decision / event record
        ↓
date-atomic walk-forward validation + simple chronological baselines
        ↓
strict sharp-market snapshots + final-close CLV
        ↓
outcome reconciliation
        ↓
separate candidate evidence and actual-fill economics
```

## What V3.1 adds

### Date-atomic walk-forward validation

The validation engine no longer cuts folds by raw match count and then batches the test set by day. Train/test boundaries themselves are now date-atomic.

A calendar day can never appear partly in training and partly in testing.

Same-day fixtures are still all forecast before any same-day result is admitted.

### Stronger baseline discipline

Every chronological V3 validation report now compares the combined model against three simple controls calculated using only information available at that point:

- league-frequency baseline;
- Elo-only model;
- dynamic score-model-only component.

Reported validation now includes:

- multiclass log loss;
- Brier score;
- calibration error;
- Ranked Probability Score (RPS);
- home/draw/away binary log loss;
- each simple baseline log loss;
- V3 log-loss difference versus the best simple baseline.

A complex model is therefore not rewarded merely for beating an old version of itself.

### Execution quality is now binding

V3.1 does not simply choose the highest displayed decimal price.

A quote can be rejected from EV ranking when known evidence shows that it is not executable, including:

- event-match confidence below the V3 execution gate;
- provider quote age above the freshness limit when a provider timestamp is available;
- known zero available size;
- known zero liquidity;
- invalid odds;
- missing point-in-time receive evidence.

The price-shop still scans every independently modelled fixture before ranking.

### Strict market-context identity

The old broad diagnostic matcher has been replaced with V3-style identity rules for Sportsbet/Pinnacle context:

- tight kickoff tolerance;
- separate home-team and away-team matching;
- home/away orientation rejection;
- league agreement;
- ambiguity rejection.

This matters because Pinnacle observations can now be retained for closing-line research. A loose eight-hour fuzzy match is not acceptable for that purpose.

### Canonical internal event IDs

V3.1 adds an internal event registry and provider-event mapping table.

The canonical event identity is based on competition, season and canonical home/away teams, making ordinary kickoff reschedules stable within the same league season rather than silently creating unrelated research events.

Provider event IDs are mapped separately with first-seen, last-seen and match-confidence metadata.

### Provenance and experiment registry

The research database now includes explicit provenance and experiment tables.

Provenance can store:

- collection stage;
- source;
- observation count;
- payload hash when supplied;
- structured metadata;
- capture time.

Research experiments can be registered before evaluation with:

- control model;
- challenger model;
- feature set;
- training window;
- test window;
- primary metric;
- multiple-testing family;
- notes/status.

This is intended to reduce silent data leakage and repeated feature fishing.

### Sharp-market snapshots and CLV

Strictly matched Pinnacle 1X2 observations can now be stored at point-in-time horizons such as:

- early;
- T-24h;
- T-6h;
- T-1h;
- T-15m.

Pinnacle 1X2 is de-vigged with a documented multiplicative inverse-odds method.

Only an actually captured final pre-kickoff observation is eligible for final-close evidence. Earlier snapshots are not relabelled as a close after the event.

For research candidates with a genuine final sharp close, V3.1 can calculate:

- decision-price CLV;
- log-odds CLV;
- model probability versus de-vigged close;
- settled unit-return proxy.

The original decision quote is labelled an **observed quote proxy**, not an assumed real fill.

### Automatic outcome reconciliation

Previously stored V3 events can be reconciled against later historical results.

Because some historical feeds are date-level, reconciliation permits only a small date discrepancy and requires exact canonical home/away teams. Ambiguous matches are skipped rather than guessed.

### Actual fills are separate

V3.1 adds an explicit fill ledger.

Actual fill records can contain:

- canonical event ID;
- side;
- source;
- requested odds;
- filled odds;
- stake;
- fees;
- request/fill time;
- status;
- external reference.

Realised ROI from actual fills is kept separate from research-candidate proxy returns. This prevents backtest economics from silently assuming that every displayed quote was actually obtained.

## Independent football model

The production probability model remains deliberately conservative:

- dynamic latent attack and defence;
- evolving league scoring environment;
- draw environment;
- separate Elo family;
- chronological stack-weight selection;
- chronological probability-temperature calibration;
- moving-block bootstrap uncertainty;
- conservative promotion/relegation transfer priors.

Current bookmaker/exchange prices remain outside the independent probability calculation.

`Goal intensity` means expected scoring rate (Poisson λ). It is not shot-derived xG.

## Research-gated features

The following are intentionally **not** promoted into the production probability merely because they sound more sophisticated:

- broad shot-derived xG/xGA;
- expected-XI/player-strength features;
- market-residual or sharp-market hybrid probabilities;
- alternative score distributions;
- adaptive Kelly or other staking models;
- larger stacked model libraries.

These should enter through the experiment registry and chronological ablation. They should be promoted only when they demonstrate durable incremental information versus the simpler controls.

A classical maximum-likelihood Dixon–Coles benchmark and broader feature-ablation library remain research backlog items rather than being represented as already solved.

## Research database

Core V3/V3.1 tables include:

- `v3_forecasts`;
- `v3_quotes`;
- `v3_decisions`;
- `v3_outcomes`;
- `v3_sharp_lines`;
- `v3_validation_runs`;
- `v3_validation_predictions`;
- `v3_events`;
- `v3_provider_event_map`;
- `v3_data_provenance`;
- `v3_experiments`;
- `v3_fills`;
- `v3_economic_evidence`.

Existing V3 databases are migrated in place with additive columns/tables when opened.

## UI

The existing condensed navigation remains. V3.1 adds research-validity explanations to Settings and augments the Walk-forward status with baseline comparison and RPS.

The dashboard continues to label apparent value as a **research candidate**, not a proven betting edge.

## Run from source

Requirements:

- Python 3.12 recommended;
- Windows is the primary packaged target.

Install:

```bash
pip install -r requirements.txt
```

Run:

```bash
python app/launcher.py
```

Tests:

```bash
python -m unittest discover -s tests -v
```

Smoke test:

```bash
python app/launcher.py --self-test
```

## Build and release

The main Windows workflow:

1. installs dependencies;
2. runs the full automated test suite;
3. reads `VERSION`;
4. packages canonical `app/launcher.py` with PyInstaller;
5. runs the frozen V3 self-test;
6. builds the Inno Setup installer;
7. uploads setup and portable artefacts;
8. publishes/refreshes the release on `main`.

## Interpretation

Forecast accuracy, closing-line value and realised strategy economics are separate evidence layers.

A lower log loss does not prove profit. Positive model EV does not prove the quote was obtainable. Positive CLV does not guarantee positive realised ROI. A limited positive ROI sample does not establish persistence.

V3.1 is designed to preserve those distinctions instead of collapsing them into one headline number.
