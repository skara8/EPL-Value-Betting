# GitHub research ideas incorporated in V2.2

Public football prediction repositories reviewed for V2.1/V2.2 consistently reinforced a few principles that are now represented directly in the application:

1. **Dixon-Coles / Poisson structure** — team attack and defence strengths, explicit home advantage, time decay and low-score correction are useful independent views of score probability.
2. **Elo as a compact strength signal** — recent competitive results update team strength without requiring a huge feature set.
3. **Probability calibration over winner accuracy** — Brier/log-loss style probability evaluation is more useful than merely counting correct favourites.
4. **Chronological evaluation** — football data must be split in time order and every feature/prediction must use information available before kickoff.
5. **Feature ablation** — extra xG, lineup, fatigue or tactical variables only deserve production weight if they improve unseen data.
6. **Uncertainty/disagreement matters** — unstable probabilities should be treated more cautiously rather than converted into stronger EV claims.
7. **Market residuals are the real target** — the research problem is not whether a public model predicts football; it is whether the model contains information beyond a sharp market baseline.

V2.2 therefore adds time-decayed Elo and Poisson/Dixon-Coles-style residuals, lineup/context residuals, dispersion, anti-leakage tests and chronological holdout Brier validation while keeping the V2.1 market-supported Robust +EV rule as the production gate.
