# V2.2 changelog summary

- Multicore process pool for CPU-bound probability/Poisson calculations.
- Automatic worker count of roughly 75% of logical CPUs, capped at 12.
- Safe sequential fallback.
- Three-way concurrent bookmaker price-feed network scan.
- New Analysis > Research models page.
- Time-decayed Elo.
- Time-decayed Poisson / Dixon-Coles-style low-score correction.
- Experimental lineup/context residual.
- Model residual and dispersion display.
- Chronological anti-leakage historical evaluation.
- Brier-score holdout comparison against historical market probabilities.
- V2.1 Robust +EV remains the production decision gate.
