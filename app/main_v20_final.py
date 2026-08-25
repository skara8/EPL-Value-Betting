from __future__ import annotations

import queue
import time

from main_v20 import V20App, LOGGER


class V20FinalApp(V20App):
    """Production V2 shell with thread-safe live progress reporting."""

    def _create_vars(self) -> None:
        super()._create_vars()
        self._progress_queue: queue.Queue[tuple[int, str, str]] = queue.Queue()
        self._progress_drain_id = None

    def _progress_callback(self, percent: int, stage: str, detail: str) -> None:
        # Network work can run in more than one worker thread. Never call Tk
        # directly from those threads; queue updates for the main UI thread.
        self._progress_queue.put((int(percent), str(stage), str(detail)))

    def _start_progress(self) -> None:
        super()._start_progress()
        self._schedule_progress_drain()

    def _schedule_progress_drain(self) -> None:
        if self._progress_drain_id is None:
            self._progress_drain_id = self.after(80, self._drain_progress_queue)

    def _drain_progress_queue(self) -> None:
        self._progress_drain_id = None
        while True:
            try:
                percent, stage, detail = self._progress_queue.get_nowait()
            except queue.Empty:
                break
            self._apply_progress(percent, stage, detail)
        if self._fetch_started_monotonic is not None or not self._progress_queue.empty():
            self._progress_drain_id = self.after(80, self._drain_progress_queue)

    def _update_progress_clock(self) -> None:
        # Continue timing while optional football intelligence runs after the
        # core market results have already become usable.
        if self._fetch_started_monotonic is None:
            self._progress_timer_id = None
            return
        elapsed = max(0.0, time.monotonic() - self._fetch_started_monotonic)
        pct = max(1, self._progress_percent)
        if pct >= 8 and pct < 100:
            estimate = elapsed * (100.0 - pct) / pct
            if estimate < 90:
                eta = f"about {max(1, int(round(estimate / 5.0) * 5))}s remaining"
            else:
                eta = f"about {max(1, int(round(estimate / 60.0)))} min remaining"
        else:
            eta = "estimating time remaining"
        self.loading_time_var.set(f"Elapsed {int(elapsed)}s · {eta}")
        self._progress_timer_id = self.after(500, self._update_progress_clock)


def main() -> None:
    try:
        V20FinalApp().mainloop()
    except Exception:
        LOGGER.exception("Fatal V2.0 application error")
        raise


if __name__ == "__main__":
    main()
