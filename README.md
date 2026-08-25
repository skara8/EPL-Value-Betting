# EPL Value Betting

Windows desktop research application for analysing EPL bookmaker prices, exchange probabilities, Asian Handicap markets and market-efficiency hypotheses.

## Current version

**V1.6.0**

## Core market model

V1.6 retains the V1.5 architecture:

- Sportsbet is the **target price**, not the fair-probability source used to assess itself.
- Sportsbet 1X2 is de-vigged with the power method for bookmaker-shading diagnostics.
- Polymarket executable YES asks are normalised to 100%.
- Pinnacle/PS3838 1X2 is power-de-vigged when available.
- Pinnacle Asian Handicap + total-goals prices can be converted into an implied Poisson score distribution.
- Pinnacle 1X2 and Pinnacle AH information are combined into one provider component before combining with Polymarket.

For Sportsbet decimal price `O` and independent fair probability `p`:

```text
break-even probability = 1 / O
EV = p * O - 1
price edge = p - (1 / O)
```

V1.5/V1.6 also calculates conservative EV using the least favourable external provider probability.

## V1.6 Context Lab

V1.6 adds a second, experimental contextual probability estimate **without replacing the base market model**.

The contextual layer captures:

- player / expected line-up;
- recent underlying performance;
- tactical / style matchup;
- manager / coaching matchup;
- transfer / squad-change assessment;
- schedule / rest / travel.

The default research weights are deliberately conservative and **not fitted coefficients**:

```text
Player / expected line-up            35%
Recent underlying performance        25%
Tactical / style matchup             20%
Manager / coaching matchup           10%
Transfer / squad-change assessment    5%
Schedule / rest / travel              5%
```

Each factor is rated from `-3` (away advantage) to `+3` (home advantage). The resulting context layer is converted into a small softmax probability tilt. By default, the largest H/D/A probability movement is capped at **1.50 percentage points**.

The GUI always shows:

- base V1.5 fair probability;
- context-adjusted fair probability;
- probability shift;
- base EV;
- context-adjusted EV.

This makes the contextual model a transparent sensitivity analysis rather than an opaque way to manufacture positive EV.

### Automatic player availability

After a successful EPL odds fetch, V1.6 makes one optional request to the free public Fantasy Premier League bootstrap feed. It uses current player status and chance-of-playing fields to construct an availability proxy for each club.

FPL player price/current performance fields are used only as rough player-importance proxies. The GUI displays the flagged players so the user can decide whether the signal is relevant to the selected fixture.

### Transfers and managers

V1.6 records transfer spending and manager names, but:

- **transfer spend itself does not move the probability**;
- raw manager-v-manager H2H does not automatically move the probability.

The user separately rates whether the actual squad change or coaching/tactical matchup provides evidence of an advantage. Those judgements are saved for later backtesting.

## V1.6 Dutch Calculator

The new Dutch Calculator supports 2–6 selections and can use:

- Sportsbet decimal odds;
- Polymarket YES prices;
- other decimal odds.

For effective decimal odds `O_i` and total stake `T`:

```text
S = sum(1 / O_i)
combined Dutch odds = 1 / S
stake_i = T * (1 / O_i) / S
equal return = T / S
equal profit = T / S - T
```

If the selected outcomes cover the full mutually exclusive market:

- `S < 1` → arbitrage / positive locked return;
- `S = 1` → break-even;
- `S > 1` → negative Dutch / locked loss.

The GUI can automatically load:

- Sportsbet H/D/A;
- Polymarket H/D/A;
- the best effective H/D/A price across Sportsbet and Polymarket.

### Polymarket fees

V1.6 can account for the current Polymarket sports taker-fee formula. For a YES price `p` and fee parameter `f`, the approximate effective winner decimal odds on a taker buy are:

```text
(1 - f * (1-p)) / p
```

The default sports `feeRate` parameter is 5%, but it is editable because market fee settings can change. Maker mode uses zero trading fee.

The calculator does not model order-book slippage beyond the entered price. Arbitrage research requires genuinely executable prices and identical settlement rules across the selected markets.

## Existing edge research

V1.6 continues to track:

- favourite-longshot bias;
- away-favourite / possible home-field overpricing;
- market agreement/disagreement;
- base model EV;
- conservative EV;
- Asian Handicap-implied expected goals;
- closing-line research snapshots.

Bias hypotheses remain tags rather than arbitrary probability bonuses.

## Research database

User settings, logs and SQLite research data are stored under:

```text
%LOCALAPPDATA%\EPLValueBetting\
```

Existing V1.3–V1.5 history is retained.

V1.6 adds `context_research_snapshots`, storing factor ratings, transfer-spend inputs, manager names, notes, automatic availability, base probabilities, context probabilities and context EV.

## Detailed methodology

See:

```text
docs/MODEL_V1_5.md
docs/MODEL_V1_6.md
```

for the modelling rationale, formulas and research references.

## Windows releases

Every successful build produces:

- `EPL-Value-Betting-vX.Y.Z-Setup.exe`
- `EPL-Value-Betting-vX.Y.Z-Portable.exe`

When a tested version reaches `main`, GitHub Actions publishes the versioned Release. The installed application can detect and launch the newer installer.

## Important scope

This project is a theoretical market-efficiency and probability-modelling application. It does not place bets automatically. Positive model EV is not proof of a persistent edge; the historical database exists so the model can ultimately be judged on calibration, closing-line value and out-of-sample performance.
