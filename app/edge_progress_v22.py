from __future__ import annotations

import multiprocessing as mp
import os
from concurrent.futures import ProcessPoolExecutor
from typing import Callable, Optional

from edge_model import calculate_match_edge
from engine import CombinedMatch


ProgressCallback = Callable[[int, str, str], None]


def recommended_worker_count() -> int:
    """Use most, but not all, logical CPUs so the Windows UI remains responsive."""
    logical = max(1, int(os.cpu_count() or 1))
    if logical <= 2:
        return 1
    # One Python process previously looked like ~8% total CPU on a 12-thread PC.
    # Keep roughly 75% of logical CPUs busy, capped to avoid excessive spawn/RAM
    # overhead on very high-core-count machines.
    return max(2, min(12, int(round(logical * 0.75))))


def _calculate_row(payload: tuple[int, CombinedMatch, float]) -> tuple[int, CombinedMatch]:
    index, row, min_ev_pct = payload
    calculate_match_edge(row, min_ev_pct=min_ev_pct)
    return index, row


def _sequential(
    rows: list[CombinedMatch],
    min_ev_pct: float,
    progress: Optional[ProgressCallback],
    start_pct: int,
    end_pct: int,
) -> list[CombinedMatch]:
    total = max(1, len(rows))
    for index, row in enumerate(rows, start=1):
        calculate_match_edge(row, min_ev_pct=min_ev_pct)
        if progress and (index == total or index % max(10, total // 14) == 0):
            pct = start_pct + int((end_pct - start_pct) * index / total)
            progress(
                min(end_pct, pct),
                "Probability model",
                f"Calculated {index:,}/{total:,} fixtures · single-process fallback",
            )
    return rows


def enrich_edge_model_parallel(
    rows: list[CombinedMatch],
    min_ev_pct: float = 4.0,
    progress: Optional[ProgressCallback] = None,
    start_pct: int = 73,
    end_pct: int = 80,
    workers: Optional[int] = None,
) -> list[CombinedMatch]:
    """Calculate independent-market probabilities across multiple CPU cores.

    The expensive AH/total Poisson fit is pure Python and therefore does not
    benefit materially from normal threads because of the GIL.  Windows worker
    processes give each worker its own interpreter and can execute these fits on
    separate CPU cores.  If process creation is unavailable, the app falls back
    to the previous sequential calculation rather than failing the refresh.
    """
    if not rows:
        return rows

    worker_count = recommended_worker_count() if workers is None else max(1, int(workers))
    worker_count = min(worker_count, len(rows))
    total = len(rows)

    if progress:
        logical = max(1, int(os.cpu_count() or 1))
        progress(
            start_pct,
            "Probability model",
            f"Calculating {total:,} fixtures with {worker_count} worker process(es) across {logical} logical CPU(s)",
        )

    if worker_count <= 1 or total < 8:
        return _sequential(rows, min_ev_pct, progress, start_pct, end_pct)

    try:
        context = mp.get_context("spawn")
        chunksize = max(1, total // max(1, worker_count * 6))
        payloads = ((i, row, float(min_ev_pct)) for i, row in enumerate(rows))
        output: list[Optional[CombinedMatch]] = [None] * total
        completed = 0
        with ProcessPoolExecutor(max_workers=worker_count, mp_context=context) as executor:
            for index, calculated in executor.map(_calculate_row, payloads, chunksize=chunksize):
                output[index] = calculated
                completed += 1
                if progress and (completed == total or completed % max(5, total // 18) == 0):
                    pct = start_pct + int((end_pct - start_pct) * completed / total)
                    progress(
                        min(end_pct, pct),
                        "Probability model",
                        f"Calculated {completed:,}/{total:,} fixtures · {worker_count} CPU workers · Poisson/de-vig/robust EV",
                    )
        return [row if row is not None else rows[i] for i, row in enumerate(output)]
    except Exception as exc:
        if progress:
            progress(
                start_pct,
                "Probability model",
                f"Multicore calculation unavailable ({type(exc).__name__}); using safe single-process fallback",
            )
        return _sequential(rows, min_ev_pct, progress, start_pct, end_pct)
