# Football Value Betting V2.2.0

V2.2 increases computation throughput and adds an experimental, validation-first football residual research layer.

## Performance

- Uses roughly 75% of logical CPU cores for the expensive probability/Poisson stage, capped at 12 worker processes.
- Leaves headroom for Windows and the GUI.
- Falls back safely to the previous single-process model if multiprocessing is unavailable.
- Checks independent bookmaker feeds with up to three concurrent network workers to reduce API wait time without aggressive request bursts.

## Research models

- Time-decayed Elo.
- Time-decayed attack/defence Poisson with bounded Dixon-Coles-style low-score correction.
- Lineup-continuity/context residual.
- Model disagreement/dispersion measurement.
- Strict pre-kickoff anti-leakage.
- Chronological train/holdout Brier-score evaluation against historical market probabilities.
- Residual blend weights are selected on the earlier sample and judged only on the later holdout sample.

## Strategy safety

The V2.1 Robust +EV rule remains the primary betting signal. Experimental Elo/Poisson/context models are shown as residual research and do not create a green recommendation unless future validation justifies a production correction.
