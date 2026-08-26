# Football Value Betting

Windows desktop research application for building **bookmaker-independent football probabilities**, comparing those frozen probabilities with executable prices, and measuring whether apparent value survives chronological out-of-sample validation.

## Current version

**V3.0.0**

V3 is a research architecture, not a claim that a profitable betting edge has already been proven. The application deliberately separates forecasting evidence from betting-edge evidence.

## What changed in V3

V3 uses a single current architecture with a clean forecast → execution → validation pipeline:

```text
historical football results
        ↓
chronological dynamic attack / defence state
        +
separate Elo model family
        ↓
chronological stack-weight selection
        ↓
chronological probability calibration
        ↓
P_independent(H/D/A)
        ↓
moving-block bootstrap uncertainty
        ↓
FREEZE probability
        ↓
scan EVERY independently modelled fixture across ALL available execution feeds
        ↓
strict event/entity/freshness checks
        ↓
EV + uncertainty-aware research candidate
        ↓
append immutable forecast / quote / decision record
        ↓
walk-forward validation + outcome / sharp-close research
```

### 1. Dynamic football state instead of 90/180/360 duplicate models

The primary football component is now a score-driven dynamic model. Each league maintains evolving latent:

- attacking strength;
- defensive strength;
- home scoring environment;
- away scoring environment;
- draw environment.

Parameters update sequentially from results. Recent information enters through state evolution rather than a separate last-five form score.

### 2. Genuinely distinct auxiliary model

Elo remains as a second model family. Every unseen team starts from the same league-average Elo prior.

V3 does **not** count short/long copies of the same goal model as extra independent evidence.

### 3. Chronological stack tuning and calibration

The dynamic model and Elo model are combined with a non-negative two-model stack. The dynamic weight and calibration temperature are selected on a trailing chronological validation period built only from matches before the current decision time.

No random train/test shuffle is used.

### 4. Real uncertainty layer

V3 refits the football state across moving-block bootstrap samples and stores coherent H/D/A uncertainty summaries:

- central probability;
- 5th percentile;
- 95th percentile;
- bootstrap standard deviation.

The decision layer reports central EV, 5th-percentile EV and an estimated `P(EV > 0)`.

### 5. Promotion/relegation transfer prior

Where an adjacent lower-division history is available, a newly promoted or sparsely observed club can retain a strongly shrunk prior from that previous division rather than being treated as a completely generic new team. The transfer remains deliberately conservative and increases uncertainty until enough V3 transition history exists for a fully learned cross-division mapping.

### 6. Price-shop every modelled fixture before ranking

V3 price-shops every independently modelled fixture before any EV ranking, preventing execution-source censoring.

The rule is now:

```text
independent probability
        ↓
ALL supported current fixtures
        ↓
ALL available execution sources
        ↓
validate event identity
        ↓
calculate every side / book EV
        ↓
rank only now
```

The all-book scan currently uses the additional bookmaker feeds exposed through the user's PulseScore access, plus Sportsbet and fee-adjusted Polymarket where available.

### 7. Strict event matching

Execution matching now rejects rather than guesses when identity is uncertain.

V3 requires:

- a tight 90-minute kickoff tolerance;
- strong home-team and away-team identity agreement;
- correct home/away orientation;
- unambiguous league resolution;
- no near-tied event candidate.

A false negative is preferable to a phantom +EV price attached to the wrong fixture.

### 8. Quote microstructure is stored

V3 quote records can retain:

- source;
- side;
- decimal odds;
- receive timestamp;
- provider market timestamp;
- quote age when derivable;
- liquidity/available size where supplied;
- commission/line fields where supplied;
- provider event ID;
- match-confidence score;
- whether the quote was the best observed price.

### 9. Walk-forward validation

The new **Research → Walk-forward** page runs expanding-window chronological replay.

Each fold:

1. uses only earlier matches;
2. tunes the model on prior information;
3. forecasts the next untouched period;
4. updates only after outcomes become available;
5. predicts same-day fixtures as a batch before admitting any same-day result.

Reported metrics include:

- multiclass log loss (primary forecasting score);
- Brier score;
- calibration error;
- home/draw/away binary log loss;
- out-of-sample sample size and fold count.

### 10. Forecast gate and betting-edge gate are separate

A strong current price is labelled a **V3 research candidate**, not a proven bet.

A candidate may require:

- configured central EV threshold;
- positive 5th-percentile EV;
- high estimated probability that EV is positive;
- medium/high football-model confidence;
- high execution-event match confidence.

That still does **not** prove a sustainable edge. V3 stores the data needed to later test closing-line value and realised outcomes separately.

## Research database

V3 adds append-only SQLite research tables for:

- `v3_forecasts` — model version, feature schema/hash, independent probabilities, uncertainty and component outputs;
- `v3_quotes` — all observed execution quotes and available microstructure;
- `v3_decisions` — point-in-time candidate decisions;
- `v3_outcomes` — settled scores/outcomes;
- `v3_sharp_lines` — sharp-market observations such as T-24h, T-6h, T-1h, T-10m and final pre-kickoff references;
- `v3_validation_runs` and `v3_validation_predictions` — reproducible chronological OOS model evidence.

The storage module exposes explicit outcome and sharp-line recording functions so later collection jobs can join those observations without rewriting the original forecast.

## UI

The mature condensed navigation remains:

- **Dashboard** — clearest current research candidate and live progress;
- **Markets** — fixtures, league coverage and all-book best prices;
- **Analysis** — candidates, market diagnostics, team context and the V3 independent model;
- **Tools** — Dutch calculator;
- **Research** — walk-forward validation, history and diagnostics;
- **Settings** — operational controls and one consolidated V3 model explanation.

The Dashboard loading panel continues to show stage, detail, percentage, elapsed time and estimated time remaining while network/model work runs in background threads/processes.

## Important terminology

`Goal intensity` means the model's expected scoring rate (Poisson λ). It is **not shot-derived expected goals (xG)**.

Shot-based xG/xGA and player/expected-XI data remain research inputs to be added to the V3 state only after point-in-time coverage is sufficiently complete to run a clean chronological ablation. V3 deliberately does not fabricate those inputs from goals or market prices.

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

Run tests:

```bash
python -m unittest discover -s tests -v
```

Run the packaged/frozen self-test path from source:

```bash
python app/launcher.py --self-test
```

## Build and release

GitHub Actions:

1. installs dependencies;
2. runs the complete unit-test suite;
3. builds the canonical `app/launcher.py` with PyInstaller;
4. runs the frozen V3 self-test;
5. builds the Inno Setup installer;
6. uploads portable/setup artefacts;
7. publishes the release when the build runs on `main`.

## Current research limitations

V3 removes several objective architectural errors, but it should not be described as a statistically proven profitable system yet.

In particular:

- historical Football-Data rows are often date-level rather than true decision-time records; V3 mitigates same-day leakage by batching same-day replay but exact archived kickoff timestamps are preferable;
- block-bootstrap uncertainty is materially stronger than component minima, but its coverage still needs empirical OOS checking;
- promotion priors are partially pooled, not yet a fully estimated multi-division Bayesian transition model;
- shot-derived xG and expected-XI/player strength are not yet broad, point-in-time production inputs;
- the sharp-market residual/hybrid production model is intentionally not switched on until sufficient decision-time sharp snapshots exist;
- no current label should be interpreted as evidence of guaranteed profit.

These are research backlog items, not reasons to leak current bookmaker prices back into the independent football model.
