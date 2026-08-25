from __future__ import annotations

import multiprocessing as mp
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Optional

from edge_model import calculate_match_edge
from engine import CombinedMatch


ProgressCallback = Callable[[int, str, str], None]


@dataclass
class EdgeAccelerationStats:
    workers: int
    parallel: bool
    completed: int
    fallback_reason: str = ""


def recommended_workers(logical_cpus: Optional[int] = None) -> int:
    """Use materially more than one core without monopolising the PC.

    The V2.1 AH/total fit is pure Python numerical work. On a 12-thread CPU a
    single Python process often appears as roughly 8% total utilisation. V2.2
    therefore targets about 75% of logical CPUs, capped at 10 workers to avoid
    excessive RAM/process-startup cost in the one-file Windows executable.
    """
    logical = int(logical_cpus or os.cpu_count() or 2)
    if logical <= 2:
        return 1
    target = max(2, int(round(logical * 0.75)))
    return max(1, min(10, logical - 1, target))


def _edge_worker(index: int, row: CombinedMatch, min_ev_pct: float) -> tuple[int, CombinedMatch]:
    calculate_match_edge(row, min_ev_pct=min_ev_pct)
    return index, row


def _serial(
    rows: list[CombinedMatch],
    min_ev_pct: float,
    progress: Optional[ProgressCallback],
    start_pct: int,
    end_pct: int,
    detail_prefix: str,
) -> list[CombinedMatch]:
    total = max(1, len(rows))
    for index, row in enumerate(rows, start=1):
        calculate_match_edge(row, min_ev_pct=min_ev_pct)
        if progress and (index == total or index % max(5, total // 18) == 0):
            pct = start_pct + int((end_pct - start_pct) * index / total)
            progress(
                min(end_pct, pct),
                "Probability model",
                f"{detail_prefix}: {index:,}/{total:,} fixtures calculated",
            )
    return rows


def enrich_edge_model_parallel(
    rows: list[CombinedMatch],
    min_ev_pct: float = 4.0,
    progress: Optional[ProgressCallback] = None,
    start_pct: int = 73,
    end_pct: int = 81,
    workers: Optional[int] = None,
) -> tuple[list[CombinedMatch], EdgeAccelerationStats]:
    """Calculate independent fair probabilities across multiple CPU processes.

    Each fixture is independent, so the expensive AH/total Poisson calibration
    can be distributed safely. If multiprocessing is unavailable for any reason
    (including an unusual frozen-Windows environment), the function falls back
    to the proven serial calculation instead of failing the market scan.
    """
    worker_count = int(workers or recommended_workers())
    total = len(rows)
    if progress:
        progress(
            start_pct,
            "Probability model",
            f"Preparing {total:,} fixture calculations · {worker_count} CPU worker(s) available",
        )

    # Process start-up costs more than it saves for tiny refreshes.
    if worker_count <= 1 or total < 12:
        result = _serial(rows, min_ev_pct, progress, start_pct, end_pct, "Single-process model")
        return result, EdgeAccelerationStats(workers=1, parallel=False, completed=total)

    try:
        context = mp.get_context("spawn")
        output: list[Optional[CombinedMatch]] = [None] * total
        completed = 0
        if progress:
            progress(
                min(end_pct, start_pct + 1),
                "Probability model",
                f"Multicore acceleration active · {worker_count} worker processes",
            )

        with ProcessPoolExecutor(max_workers=worker_count, mp_context=context) as pool:
            futures = {
                pool.submit(_edge_worker, index, row, min_ev_pct): index
                for index, row in enumerate(rows)
            }
            for future in as_completed(futures):
                index, calculated = future.result()
                output[index] = calculated
                completed += 1
                if progress and (completed == total or completed % max(4, total // 24) == 0):
                    pct = start_pct + int((end_pct - start_pct) * completed / max(1, total))
                    progress(
                        min(end_pct, pct),
                        "Probability model",
                        f"Multicore model: {completed:,}/{total:,} fixtures · {worker_count} CPU workers",
                    )

        result = [row for row in output if row is not None]
        if len(result) != total:
            raise RuntimeError(f"parallel model returned {len(result)} of {total} rows")
        return result, EdgeAccelerationStats(
            workers=worker_count,
            parallel=True,
            completed=completed,
        )
    except Exception as exc:
        # Keep the application usable even if process creation is blocked by a
        # security product or an edge-case Windows/PyInstaller environment.
        reason = f"{type(exc).__name__}: {exc}"
        if progress:
            progress(
                start_pct,
                "Probability model",
                "Multicore acceleration unavailable; automatically retrying with the safe single-process model",
            )
        result = _serial(rows, min_ev_pct, progress, start_pct, end_pct, "Fallback model")
        return result, EdgeAccelerationStats(
            workers=1,
            parallel=False,
            completed=total,
            fallback_reason=reason,
        )
