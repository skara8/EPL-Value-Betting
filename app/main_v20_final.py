from __future__ import annotations

import queue
import time
import tkinter as tk
from tkinter import ttk

from main_v20 import V20App, LOGGER


class V20FinalApp(V20App):
    """Production V2 shell with thread-safe live progress reporting."""

    def _create_vars(self) -> None:
        super()._create_vars()
        self._progress_queue: queue.Queue[tuple[int, str, str]] = queue.Queue()
        self._progress_drain_id = None

    def _build_settings(self) -> None:
        super()._build_settings()
        note = ttk.LabelFrame(self.settings_tab, text="Crypto price sources", padding=12)
        note.pack(fill="x", pady=(10, 0))
        ttk.Label(
            note,
            text=(
                "The targeted best-price pass also checks Stake and Cloudbet when those PulseScore feeds are available on your plan. "
                "They are treated only as additional executable price sources. Polymarket remains fee-adjusted separately because it is an event market rather than a conventional fixed-odds bookmaker."
            ),
            wraplength=1260,
            justify="left",
            style="Muted.TLabel",
        ).pack(anchor="w")

    def _progress_callback(self, percent: int, stage: str, detail: str) -> None:
        # Network work can run in more than one worker thread. Never call Tk
        # directly from those threads; queue updates for the main UI thread.
        self._progress_queue.put((int(percent), str(stage), str(detail)))

    def _start_progress(self) -> None:
        super()._start_progress()
        self.dashboard_status_var.set("Analysis running — live progress is shown above")
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

    def _apply_v20_result(self, rows, warnings, info_notes, saved, context_saved, edge_saved) -> None:
        # Extra bookmaker feeds are opportunistic price-shopping sources. A
        # plan restriction or temporary failure should be visible on the Best
        # prices page, not interrupt a successful core analysis with a modal
        # warning dialog.
        optional_prefixes = (
            "Bet365:", "Ladbrokes:", "TAB:", "Unibet AU:", "BetRight:",
            "Stake (crypto):", "Cloudbet (crypto):", "Best-price scan:",
        )
        primary_warnings = []
        optional_notes = []
        for warning in warnings:
            if str(warning).startswith(optional_prefixes):
                optional_notes.append(str(warning))
            else:
                primary_warnings.append(warning)
        if optional_notes:
            info_notes = list(info_notes) + [
                f"Optional price sources skipped: {len(optional_notes)}. See Markets → Best prices for the sources that were successfully checked."
            ]
        super()._apply_v20_result(rows, primary_warnings, info_notes, saved, context_saved, edge_saved)

    def _normalise_visible_copy(self) -> None:
        super()._normalise_visible_copy()
        # Earlier screens also expose explanatory copy through StringVars. A
        # widget-level text sweep cannot see those values, so clean all dynamic
        # user-facing strings as well. This leaves diagnostics logs untouched.
        for value in self.__dict__.values():
            if isinstance(value, tk.StringVar):
                try:
                    current = value.get()
                    cleaned = self._clean_copy(current)
                    if cleaned != current:
                        value.set(cleaned)
                except Exception:
                    pass

        # Notebook tab captions are metadata on the Notebook rather than normal
        # child-widget text, so sweep every notebook explicitly. This removes
        # labels such as "V1.5 summary" while retaining the same technical page.
        def clean_notebooks(widget) -> None:
            if isinstance(widget, ttk.Notebook):
                try:
                    for tab_id in widget.tabs():
                        current = widget.tab(tab_id, "text")
                        cleaned = self._clean_copy(str(current))
                        if cleaned != current:
                            widget.tab(tab_id, text=cleaned)
                except Exception:
                    pass
            for child in widget.winfo_children():
                clean_notebooks(child)

        clean_notebooks(self)


def main() -> None:
    try:
        V20FinalApp().mainloop()
    except Exception:
        LOGGER.exception("Fatal V2.0 application error")
        raise


if __name__ == "__main__":
    main()
