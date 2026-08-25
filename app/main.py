from __future__ import annotations

import os
import platform
import threading
import traceback
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkcalendar import DateEntry

from config import APP_NAME, DATA_DIR, DB_FILE, VERSION, AppSettings
from engine import (
    BRISBANE,
    CombinedMatch,
    combine_sources,
    fetch_polymarket_epl,
    fetch_sportsbet_epl,
    fmt_money,
    fmt_odds,
    fmt_pct,
    fmt_probability,
)
from logging_setup import LOG_FILE, configure_logging
from credential_store import get_api_key, save_api_key
from storage import clear_history, export_history, history_summary, recent_snapshots, save_snapshot
from updater import download_installer, latest_release, launch_installer, update_available

LOGGER = configure_logging()


def open_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        webbrowser.open(path.as_uri())


def safe_float(value: str, default: float) -> float:
    try:
        return float(value.strip())
    except Exception:
        return default


def safe_int(value: str, default: int) -> int:
    try:
        return int(value.strip())
    except Exception:
        return default


class SummaryCard(ttk.Frame):
    def __init__(self, parent, title: str, value_var: tk.StringVar, subtitle: str = ""):
        super().__init__(parent, padding=14, relief="solid")
        ttk.Label(self, text=title, style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(self, textvariable=value_var, style="CardValue.TLabel").pack(anchor="w", pady=(4, 0))
        if subtitle:
            ttk.Label(self, text=subtitle, style="Muted.TLabel").pack(anchor="w", pady=(3, 0))


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.settings = AppSettings.load()
        self.rows: list[CombinedMatch] = []
        self.last_fetch_at: Optional[datetime] = None
        self._busy = False
        self._latest_release = None

        self.title(f"{APP_NAME} v{VERSION}")
        self.geometry("1500x900")
        self.minsize(1180, 720)

        self._configure_style()
        self._create_vars()
        self._build_header()
        self._build_tabs()
        self._build_statusbar()
        self._load_settings_into_ui()
        self.refresh_history()
        self._refresh_diagnostics()

        LOGGER.info("Application started v%s", VERSION)
        self.after(600, self._initialise_startup_checks)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", font=("Segoe UI", 9, "bold"))
        style.configure("CardValue.TLabel", font=("Segoe UI", 19, "bold"))
        style.configure("Muted.TLabel", font=("Segoe UI", 9))
        style.configure("Treeview", rowheight=25, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.configure("TNotebook.Tab", padding=(16, 8))

    def _create_vars(self) -> None:
        self.status_var = tk.StringVar(value="Ready")
        self.api_key_var = tk.StringVar(value="")
        self.min_ev_var = tk.StringVar(value=f"{self.settings.min_ev_pct:.1f}")
        self.min_volume_var = tk.StringVar(value=f"{self.settings.min_pm_volume:.0f}")
        self.days_ahead_var = tk.StringVar(value=str(self.settings.default_days_ahead))
        self.remember_key_var = tk.BooleanVar(value=self.settings.remember_api_key)
        self.save_snapshots_var = tk.BooleanVar(value=self.settings.save_snapshots)
        self.candidates_only_var = tk.BooleanVar(value=self.settings.candidates_only)
        self.check_updates_var = tk.BooleanVar(value=self.settings.check_updates_on_start)

        self.summary_matches = tk.StringVar(value="0")
        self.summary_matched = tk.StringVar(value="0")
        self.summary_candidates = tk.StringVar(value="0")
        self.summary_away = tk.StringVar(value="0")
        self.summary_best = tk.StringVar(value="—")
        self.summary_history = tk.StringVar(value="0")
        self.update_banner_var = tk.StringVar(value=f"Version {VERSION}")

    def _build_header(self) -> None:
        header = ttk.Frame(self, padding=(18, 14, 18, 8))
        header.pack(fill="x")

        left = ttk.Frame(header)
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            left,
            text="EPL market comparison, value screening and research capture",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        right = ttk.Frame(header)
        right.pack(side="right")
        ttk.Label(right, textvariable=self.update_banner_var, style="Muted.TLabel").pack(side="left", padx=(0, 10))
        ttk.Button(right, text="Check for updates", command=self.check_for_updates).pack(side="left")

    def _build_tabs(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        self.dashboard_tab = ttk.Frame(self.notebook, padding=14)
        self.matches_tab = ttk.Frame(self.notebook, padding=12)
        self.candidates_tab = ttk.Frame(self.notebook, padding=12)
        self.history_tab = ttk.Frame(self.notebook, padding=12)
        self.settings_tab = ttk.Frame(self.notebook, padding=14)
        self.diagnostics_tab = ttk.Frame(self.notebook, padding=12)

        self.notebook.add(self.dashboard_tab, text="Dashboard")
        self.notebook.add(self.matches_tab, text="Matches")
        self.notebook.add(self.candidates_tab, text="Candidates")
        self.notebook.add(self.history_tab, text="History")
        self.notebook.add(self.settings_tab, text="Settings")
        self.notebook.add(self.diagnostics_tab, text="Diagnostics")

        self._build_dashboard()
        self._build_matches()
        self._build_candidates()
        self._build_history()
        self._build_settings()
        self._build_diagnostics()

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self, padding=(14, 5, 14, 8))
        bar.pack(fill="x")
        ttk.Label(bar, textvariable=self.status_var).pack(side="left")
        ttk.Label(bar, text=f"Data: {DATA_DIR}", style="Muted.TLabel").pack(side="right")

    def _build_dashboard(self) -> None:
        cards = ttk.Frame(self.dashboard_tab)
        cards.pack(fill="x")
        for col in range(6):
            cards.columnconfigure(col, weight=1)

        card_specs = [
            ("EPL fixtures", self.summary_matches, "Current fetch"),
            ("Matched markets", self.summary_matched, "Sportsbet + Polymarket"),
            ("Value candidates", self.summary_candidates, "Above EV threshold"),
            ("Away-fav value", self.summary_away, "Research flag"),
            ("Best current EV", self.summary_best, "Strategy preview"),
            ("Saved snapshots", self.summary_history, "Research database"),
        ]
        for idx, (title, var, subtitle) in enumerate(card_specs):
            card = SummaryCard(cards, title, var, subtitle)
            card.grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 6, 0), pady=(0, 12))

        actions = ttk.LabelFrame(self.dashboard_tab, text="Quick analysis", padding=12)
        actions.pack(fill="x", pady=(0, 12))
        ttk.Button(actions, text="Fetch current EPL odds", command=self.fetch_matches).pack(side="left")
        ttk.Button(actions, text="Show candidates", command=lambda: self.notebook.select(self.candidates_tab)).pack(side="left", padx=8)
        ttk.Button(actions, text="Open research folder", command=lambda: open_path(DATA_DIR)).pack(side="left")
        ttk.Label(
            actions,
            text="V1.3 uses normalised Polymarket executable asks as the comparison baseline. Away-favourite status is a flag only.",
            style="Muted.TLabel",
        ).pack(side="right")

        ttk.Label(self.dashboard_tab, text="Top current candidates", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(4, 6))
        self.dashboard_tree = self._make_tree(
            self.dashboard_tab,
            [
                ("kickoff", "Kick-off", 130),
                ("match", "Match", 270),
                ("selection", "Selection", 90),
                ("sb", "Sportsbet", 85),
                ("fair", "PM fair", 85),
                ("ev", "EV", 80),
                ("signal", "Signal", 130),
                ("volume", "PM volume", 95),
            ],
            height=15,
        )

    def _build_matches(self) -> None:
        controls = ttk.LabelFrame(self.matches_tab, text="Fetch EPL market snapshot", padding=10)
        controls.pack(fill="x", pady=(0, 10))

        today = datetime.now(BRISBANE).date()
        end = today + timedelta(days=self.settings.default_days_ahead)

        ttk.Label(controls, text="Start date").pack(side="left")
        self.start_date = DateEntry(
            controls, width=11, date_pattern="dd/mm/yy",
            year=today.year, month=today.month, day=today.day,
        )
        self.start_date.pack(side="left", padx=(5, 14))

        ttk.Label(controls, text="End date").pack(side="left")
        self.end_date = DateEntry(
            controls, width=11, date_pattern="dd/mm/yy",
            year=end.year, month=end.month, day=end.day,
        )
        self.end_date.pack(side="left", padx=(5, 14))

        ttk.Label(controls, text="Min EV %").pack(side="left")
        ttk.Entry(controls, textvariable=self.min_ev_var, width=7).pack(side="left", padx=(5, 12))

        ttk.Checkbutton(
            controls,
            text="Candidates only",
            variable=self.candidates_only_var,
            command=self.refresh_match_tables,
        ).pack(side="left", padx=(0, 12))

        self.fetch_button = ttk.Button(controls, text="Fetch odds", command=self.fetch_matches)
        self.fetch_button.pack(side="left")
        ttk.Button(controls, text="Clear view", command=self.clear_current_view).pack(side="left", padx=8)

        self.matches_tree = self._make_tree(
            self.matches_tab,
            [
                ("kickoff", "Kick-off", 125),
                ("match", "Match", 235),
                ("sbh", "SB H", 65), ("sbd", "SB D", 65), ("sba", "SB A", 65),
                ("pmh", "PM H", 65), ("pmd", "PM D", 65), ("pma", "PM A", 65),
                ("fh", "PM fair H", 80), ("fd", "PM fair D", 80), ("fa", "PM fair A", 80),
                ("evh", "EV H", 70), ("evd", "EV D", 70), ("eva", "EV A", 70),
                ("away", "Away fav?", 78),
                ("best", "Best", 70), ("bestev", "Best EV", 75),
                ("signal", "Strategy", 130),
                ("overround", "PM ask O/R", 85),
                ("volume", "PM volume", 90),
                ("status", "Matched?", 105),
            ],
            height=23,
        )

    def _build_candidates(self) -> None:
        top = ttk.Frame(self.candidates_tab)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Current strategy candidates", font=("Segoe UI", 12, "bold")).pack(side="left")
        ttk.Label(
            top,
            text="VALUE = comparison edge. AWAY-FAV VALUE = same edge plus away-favourite research flag.",
            style="Muted.TLabel",
        ).pack(side="right")

        self.candidates_tree = self._make_tree(
            self.candidates_tab,
            [
                ("kickoff", "Kick-off", 130),
                ("match", "Match", 280),
                ("selection", "Selection", 90),
                ("sb", "Sportsbet odds", 100),
                ("fair", "PM fair", 90),
                ("ev", "Estimated EV", 100),
                ("away", "Away fav?", 85),
                ("signal", "Signal", 140),
                ("volume", "PM volume", 100),
                ("pmor", "PM ask O/R", 100),
            ],
            height=24,
        )

    def _build_history(self) -> None:
        controls = ttk.Frame(self.history_tab)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Label(controls, text="Saved research snapshots", font=("Segoe UI", 12, "bold")).pack(side="left")
        ttk.Button(controls, text="Refresh", command=self.refresh_history).pack(side="right")
        ttk.Button(controls, text="Export CSV", command=self.export_history_ui).pack(side="right", padx=8)
        ttk.Button(controls, text="Open data folder", command=lambda: open_path(DATA_DIR)).pack(side="right")
        ttk.Button(controls, text="Clear history", command=self.clear_history_ui).pack(side="right", padx=8)

        self.history_summary_var = tk.StringVar(value="No history yet")
        ttk.Label(self.history_tab, textvariable=self.history_summary_var, style="Muted.TLabel").pack(anchor="w", pady=(0, 8))

        self.history_tree = self._make_tree(
            self.history_tab,
            [
                ("captured", "Captured", 145),
                ("kickoff", "Kick-off", 135),
                ("match", "Match", 250),
                ("sbh", "SB H", 65), ("sbd", "SB D", 65), ("sba", "SB A", 65),
                ("pmh", "PM H", 65), ("pmd", "PM D", 65), ("pma", "PM A", 65),
                ("best", "Best", 70), ("ev", "Best EV", 80),
                ("signal", "Signal", 130),
                ("volume", "PM volume", 90),
            ],
            height=23,
        )

    def _build_settings(self) -> None:
        left = ttk.LabelFrame(self.settings_tab, text="Data and strategy", padding=16)
        left.pack(fill="x", pady=(0, 12))
        left.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(left, text="PulseScore API key").grid(row=row, column=0, sticky="w", pady=6)
        self.api_key_entry = ttk.Entry(left, textvariable=self.api_key_var, show="•", width=48)
        self.api_key_entry.grid(row=row, column=1, sticky="ew", padx=(14, 8), pady=6)
        ttk.Checkbutton(left, text="Remember in Windows Credential Manager", variable=self.remember_key_var).grid(row=row, column=2, sticky="w", pady=6)

        row += 1
        ttk.Label(left, text="Minimum EV %").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(left, textvariable=self.min_ev_var, width=10).grid(row=row, column=1, sticky="w", padx=(14, 8), pady=6)
        ttk.Label(left, text="Default 4.0%. This is a screening threshold, not a proven edge.", style="Muted.TLabel").grid(row=row, column=2, sticky="w")

        row += 1
        ttk.Label(left, text="Minimum Polymarket volume ($)").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(left, textvariable=self.min_volume_var, width=12).grid(row=row, column=1, sticky="w", padx=(14, 8), pady=6)
        ttk.Label(left, text="0 = do not filter. Useful later for liquidity-quality testing.", style="Muted.TLabel").grid(row=row, column=2, sticky="w")

        row += 1
        ttk.Label(left, text="Default days ahead").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(left, textvariable=self.days_ahead_var, width=10).grid(row=row, column=1, sticky="w", padx=(14, 8), pady=6)
        ttk.Label(left, text="1–60 days", style="Muted.TLabel").grid(row=row, column=2, sticky="w")

        row += 1
        ttk.Checkbutton(left, text="Save each successful fetch to the research database", variable=self.save_snapshots_var).grid(row=row, column=0, columnspan=2, sticky="w", pady=6)
        ttk.Checkbutton(left, text="Check for application updates on start", variable=self.check_updates_var).grid(row=row, column=2, sticky="w", pady=6)

        buttons = ttk.Frame(self.settings_tab)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Save settings", command=self.save_settings_ui).pack(side="left")
        ttk.Button(buttons, text="Test data sources", command=self.fetch_matches).pack(side="left", padx=8)
        ttk.Button(buttons, text="Open data folder", command=lambda: open_path(DATA_DIR)).pack(side="left")

        info = ttk.LabelFrame(self.settings_tab, text="How V1.3 interprets the market", padding=16)
        info.pack(fill="x", pady=(16, 0))
        ttk.Label(
            info,
            text=(
                "Sportsbet is the target price. Polymarket's executable YES asks are converted to implied probabilities and normalised so Home + Draw + Away = 100%. "
                "Sportsbet EV is then calculated against that market baseline. The away-favourite result is only a research flag; V1.3 does not add an arbitrary probability boost."
            ),
            wraplength=1250,
            justify="left",
        ).pack(anchor="w")

    def _build_diagnostics(self) -> None:
        controls = ttk.Frame(self.diagnostics_tab)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Button(controls, text="Refresh log", command=self._refresh_diagnostics).pack(side="left")
        ttk.Button(controls, text="Copy diagnostics", command=self.copy_diagnostics).pack(side="left", padx=8)
        ttk.Button(controls, text="Open log folder", command=lambda: open_path(LOG_FILE.parent)).pack(side="left")
        ttk.Button(controls, text="Check for updates", command=self.check_for_updates).pack(side="right")

        wrapper = ttk.Frame(self.diagnostics_tab)
        wrapper.pack(fill="both", expand=True)
        self.diagnostics_text = tk.Text(wrapper, wrap="none", font=("Consolas", 9), height=30)
        vs = ttk.Scrollbar(wrapper, orient="vertical", command=self.diagnostics_text.yview)
        hs = ttk.Scrollbar(wrapper, orient="horizontal", command=self.diagnostics_text.xview)
        self.diagnostics_text.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.diagnostics_text.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        wrapper.rowconfigure(0, weight=1)
        wrapper.columnconfigure(0, weight=1)

    def _make_tree(self, parent, columns, height=18):
        wrapper = ttk.Frame(parent)
        wrapper.pack(fill="both", expand=True)
        keys = [x[0] for x in columns]
        tree = ttk.Treeview(wrapper, columns=keys, show="headings", height=height)
        for key, title, width in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=55, anchor="w" if key == "match" else "center")
        vs = ttk.Scrollbar(wrapper, orient="vertical", command=tree.yview)
        hs = ttk.Scrollbar(wrapper, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        wrapper.rowconfigure(0, weight=1)
        wrapper.columnconfigure(0, weight=1)
        return tree

    def _load_settings_into_ui(self) -> None:
        if self.settings.remember_api_key:
            self.api_key_var.set(get_api_key())

    def save_settings_ui(self, quiet: bool = False) -> bool:
        min_ev = safe_float(self.min_ev_var.get(), -999)
        if not (-50 <= min_ev <= 100):
            if not quiet:
                messagebox.showerror("Invalid minimum EV", "Enter a minimum EV between -50 and 100.")
            return False

        days = safe_int(self.days_ahead_var.get(), 0)
        if not (1 <= days <= 60):
            if not quiet:
                messagebox.showerror("Invalid date range", "Default days ahead must be between 1 and 60.")
            return False

        min_volume = safe_float(self.min_volume_var.get(), -1)
        if min_volume < 0:
            if not quiet:
                messagebox.showerror("Invalid volume", "Minimum Polymarket volume cannot be negative.")
            return False

        self.settings.min_ev_pct = min_ev
        self.settings.default_days_ahead = days
        self.settings.save_snapshots = bool(self.save_snapshots_var.get())
        self.settings.remember_api_key = bool(self.remember_key_var.get())
        self.settings.candidates_only = bool(self.candidates_only_var.get())
        self.settings.min_pm_volume = min_volume
        self.settings.check_updates_on_start = bool(self.check_updates_var.get())
        self.settings.save()

        if self.settings.remember_api_key:
            if not save_api_key(self.api_key_var.get().strip()):
                if not quiet:
                    messagebox.showwarning(
                        "API key not saved",
                        "Windows Credential Manager could not be used. The API key will remain in this session only.",
                    )
            else:
                LOGGER.info("PulseScore API key saved to system credential store")
        else:
            save_api_key("")

        if not quiet:
            self.status_var.set("Settings saved")
            messagebox.showinfo("Settings", "Settings saved successfully.")
        return True

    def fetch_matches(self) -> None:
        if self._busy:
            return

        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showerror(
                "PulseScore key required",
                "Enter your free PulseScore API key under Settings first.",
            )
            self.notebook.select(self.settings_tab)
            return

        start = self.start_date.get_date()
        end = self.end_date.get_date()
        if end < start:
            messagebox.showerror("Invalid dates", "End date must be on or after start date.")
            return
        if (end - start).days > 60:
            messagebox.showerror("Range too large", "Choose a range of 60 days or less.")
            return

        min_ev = safe_float(self.min_ev_var.get(), self.settings.min_ev_pct)
        if not (-50 <= min_ev <= 100):
            messagebox.showerror("Invalid EV threshold", "Enter a minimum EV between -50% and 100%.")
            return

        self._set_busy(True, "Fetching Sportsbet and Polymarket EPL markets…")
        LOGGER.info("Fetch started %s to %s; min EV %.2f", start, end, min_ev)

        threading.Thread(
            target=self._fetch_worker,
            args=(api_key, start, end, min_ev),
            daemon=True,
        ).start()

    def _fetch_worker(self, api_key, start, end, min_ev) -> None:
        warnings: list[str] = []
        try:
            try:
                sb_rows = fetch_sportsbet_epl(api_key, start, end)
            except Exception as exc:
                sb_rows = []
                warnings.append(f"Sportsbet: {exc}")
                LOGGER.exception("Sportsbet fetch failed")

            try:
                pm_rows = fetch_polymarket_epl(start, end)
            except Exception as exc:
                pm_rows = []
                warnings.append(f"Polymarket: {exc}")
                LOGGER.exception("Polymarket fetch failed")

            rows = combine_sources(sb_rows, pm_rows, min_ev_pct=min_ev)

            min_volume = max(0.0, safe_float(self.min_volume_var.get(), 0.0))
            if min_volume > 0:
                for row in rows:
                    if row.strategy_flag in {"VALUE", "AWAY-FAV VALUE"}:
                        if row.polymarket_volume is None or row.polymarket_volume < min_volume:
                            row.strategy_flag = "LOW VOLUME"

            saved = 0
            if self.save_snapshots_var.get() and rows:
                saved = save_snapshot(rows)
                LOGGER.info("Saved %d snapshot rows", saved)

            LOGGER.info(
                "Fetch completed: Sportsbet=%d Polymarket=%d combined=%d warnings=%d",
                len(sb_rows), len(pm_rows), len(rows), len(warnings),
            )
            self.after(0, lambda: self._apply_fetch_result(rows, warnings, saved))
        except Exception as exc:
            LOGGER.exception("Unexpected fetch worker failure")
            self.after(0, lambda: self._fatal_fetch_error(exc))

    def _apply_fetch_result(self, rows, warnings, saved) -> None:
        self.rows = rows
        self.last_fetch_at = datetime.now().astimezone()
        self.refresh_match_tables()
        self.refresh_history()
        self._set_busy(False)

        matched = sum(1 for r in rows if r.match_status == "Matched")
        candidates = sum(1 for r in rows if r.strategy_flag in {"VALUE", "AWAY-FAV VALUE"})
        self.status_var.set(
            f"Loaded {len(rows)} EPL fixture(s); matched {matched}; candidates {candidates}"
            + (f"; saved {saved}" if saved else "")
        )
        if warnings:
            messagebox.showwarning("Some data could not be fetched", "\n\n".join(warnings))

    def _fatal_fetch_error(self, exc: Exception) -> None:
        self._set_busy(False)
        self.status_var.set("Fetch failed")
        messagebox.showerror("Fetch failed", str(exc))

    def _set_busy(self, busy: bool, text: Optional[str] = None) -> None:
        self._busy = busy
        try:
            self.fetch_button.configure(state="disabled" if busy else "normal")
        except Exception:
            pass
        if text:
            self.status_var.set(text)
        self.update_idletasks()

    def clear_current_view(self) -> None:
        self.rows = []
        self.refresh_match_tables()
        self.status_var.set("Current view cleared; saved history is unchanged.")

    def refresh_match_tables(self) -> None:
        self._fill_matches_tree()
        self._fill_candidates_tree()
        self._fill_dashboard()

    def _candidate_rows(self) -> list[CombinedMatch]:
        return [r for r in self.rows if r.strategy_flag in {"VALUE", "AWAY-FAV VALUE"}]

    def _fill_matches_tree(self) -> None:
        for item in self.matches_tree.get_children():
            self.matches_tree.delete(item)
        rows = self._candidate_rows() if self.candidates_only_var.get() else self.rows
        for r in rows:
            self.matches_tree.insert("", "end", values=(
                r.kickoff.strftime("%d/%m/%y %H:%M"), r.match_name,
                fmt_odds(r.sb_home), fmt_odds(r.sb_draw), fmt_odds(r.sb_away),
                fmt_odds(r.pm_home), fmt_odds(r.pm_draw), fmt_odds(r.pm_away),
                fmt_probability(r.pm_fair_home), fmt_probability(r.pm_fair_draw), fmt_probability(r.pm_fair_away),
                fmt_pct(r.ev_home_pct), fmt_pct(r.ev_draw_pct), fmt_pct(r.ev_away_pct),
                r.away_favourite, r.best_selection, fmt_pct(r.best_ev_pct), r.strategy_flag,
                fmt_pct(r.polymarket_sum_minus_100_pct), fmt_money(r.polymarket_volume), r.match_status,
            ))

    def _fill_candidates_tree(self) -> None:
        for item in self.candidates_tree.get_children():
            self.candidates_tree.delete(item)
        rows = sorted(self._candidate_rows(), key=lambda r: r.best_ev_pct if r.best_ev_pct is not None else -999, reverse=True)
        for r in rows:
            sb = {"HOME": r.sb_home, "DRAW": r.sb_draw, "AWAY": r.sb_away}.get(r.best_selection)
            fair = {"HOME": r.pm_fair_home, "DRAW": r.pm_fair_draw, "AWAY": r.pm_fair_away}.get(r.best_selection)
            self.candidates_tree.insert("", "end", values=(
                r.kickoff.strftime("%d/%m/%y %H:%M"), r.match_name, r.best_selection,
                fmt_odds(sb), fmt_probability(fair), fmt_pct(r.best_ev_pct), r.away_favourite,
                r.strategy_flag, fmt_money(r.polymarket_volume), fmt_pct(r.polymarket_sum_minus_100_pct),
            ))

    def _fill_dashboard(self) -> None:
        for item in self.dashboard_tree.get_children():
            self.dashboard_tree.delete(item)
        matched = sum(1 for r in self.rows if r.match_status == "Matched")
        candidates = self._candidate_rows()
        away = sum(1 for r in candidates if r.strategy_flag == "AWAY-FAV VALUE")
        best = max((r.best_ev_pct for r in candidates if r.best_ev_pct is not None), default=None)
        self.summary_matches.set(str(len(self.rows)))
        self.summary_matched.set(str(matched))
        self.summary_candidates.set(str(len(candidates)))
        self.summary_away.set(str(away))
        self.summary_best.set(fmt_pct(best))
        for r in sorted(candidates, key=lambda x: x.best_ev_pct or -999, reverse=True)[:15]:
            sb = {"HOME": r.sb_home, "DRAW": r.sb_draw, "AWAY": r.sb_away}.get(r.best_selection)
            fair = {"HOME": r.pm_fair_home, "DRAW": r.pm_fair_draw, "AWAY": r.pm_fair_away}.get(r.best_selection)
            self.dashboard_tree.insert("", "end", values=(
                r.kickoff.strftime("%d/%m/%y %H:%M"), r.match_name, r.best_selection,
                fmt_odds(sb), fmt_probability(fair), fmt_pct(r.best_ev_pct),
                r.strategy_flag, fmt_money(r.polymarket_volume),
            ))

    def refresh_history(self) -> None:
        try:
            summary = history_summary()
            self.summary_history.set(str(summary["snapshots"]))
            self.history_summary_var.set(
                f"{summary['snapshots']} saved rows across {summary['events']} fixtures • "
                f"{summary['candidates']} value observations • {summary['away_candidates']} away-favourite value observations"
            )
            for item in self.history_tree.get_children():
                self.history_tree.delete(item)
            for row in recent_snapshots(500):
                match = f"{row['home_team']} v {row['away_team']}"
                self.history_tree.insert("", "end", values=(
                    row["captured_at"], row["kickoff"], match,
                    fmt_odds(row["sb_home"]), fmt_odds(row["sb_draw"]), fmt_odds(row["sb_away"]),
                    fmt_odds(row["pm_home"]), fmt_odds(row["pm_draw"]), fmt_odds(row["pm_away"]),
                    row["best_selection"] or "—", fmt_pct(row["best_ev_pct"]),
                    row["strategy_flag"] or "—", fmt_money(row["pm_volume"]),
                ))
        except Exception:
            LOGGER.exception("Could not refresh history")

    def export_history_ui(self) -> None:
        default_name = f"epl-odds-history-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        path = filedialog.asksaveasfilename(
            title="Export EPL odds history",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV files", "*.csv")],
        )
        if not path:
            return
        try:
            result = export_history(Path(path))
            self.status_var.set(f"History exported: {result}")
            messagebox.showinfo("Export complete", f"Saved:\n{result}")
        except Exception as exc:
            LOGGER.exception("History export failed")
            messagebox.showerror("Export failed", str(exc))

    def clear_history_ui(self) -> None:
        if not messagebox.askyesno(
            "Clear history",
            "Delete all saved odds snapshots from the local research database?\n\nThis cannot be undone.",
        ):
            return
        try:
            count = clear_history()
            self.refresh_history()
            self.status_var.set(f"Deleted {count} saved snapshot rows")
        except Exception as exc:
            messagebox.showerror("Could not clear history", str(exc))

    def _initialise_startup_checks(self) -> None:
        if not self.api_key_var.get().strip():
            self.status_var.set("Add your PulseScore API key under Settings, then fetch EPL odds.")
        if self.settings.check_updates_on_start:
            threading.Thread(target=self._background_update_check, daemon=True).start()

    def _background_update_check(self) -> None:
        try:
            release = latest_release()
            self._latest_release = release
            if release and update_available(release):
                self.after(0, lambda: self.update_banner_var.set(f"Update v{release.version} available"))
        except Exception:
            LOGGER.info("Background update check unavailable", exc_info=True)

    def check_for_updates(self) -> None:
        if self._busy:
            return
        self.status_var.set("Checking GitHub Releases…")
        threading.Thread(target=self._update_check_worker, daemon=True).start()

    def _update_check_worker(self) -> None:
        try:
            release = latest_release()
            self._latest_release = release
            self.after(0, lambda: self._show_update_result(release))
        except Exception as exc:
            LOGGER.exception("Update check failed")
            self.after(0, lambda: messagebox.showwarning("Update check failed", str(exc)))

    def _show_update_result(self, release) -> None:
        if release is None:
            self.status_var.set("No published GitHub Release is available yet.")
            messagebox.showinfo("Updates", "No published GitHub Release is available yet.")
            return
        if not update_available(release):
            self.status_var.set(f"Version {VERSION} is current")
            self.update_banner_var.set(f"Version {VERSION} — up to date")
            messagebox.showinfo("Updates", f"You are using the latest version ({VERSION}).")
            return

        self.update_banner_var.set(f"Update v{release.version} available")
        self.status_var.set(f"Update v{release.version} is available")
        prompt = f"Version {release.version} is available.\n\nDownload and run the Windows installer now?"
        if release.notes:
            prompt += "\n\nRelease notes:\n" + release.notes[:700]
        if messagebox.askyesno("Update available", prompt):
            self._download_update(release)

    def _download_update(self, release) -> None:
        if not release.installer_url:
            webbrowser.open(release.page_url)
            return
        self._set_busy(True, f"Downloading update v{release.version}…")

        def worker():
            try:
                def progress(done, total):
                    if total:
                        pct = done / total * 100
                        self.after(0, lambda: self.status_var.set(f"Downloading update… {pct:.0f}%"))
                path = download_installer(release, progress=progress)
                self.after(0, lambda: self._launch_downloaded_update(path))
            except Exception as exc:
                LOGGER.exception("Update download failed")
                self.after(0, lambda: self._update_download_failed(exc, release.page_url))

        threading.Thread(target=worker, daemon=True).start()

    def _launch_downloaded_update(self, path: Path) -> None:
        self._set_busy(False)
        if messagebox.askyesno(
            "Install update",
            f"Update downloaded to:\n{path}\n\nClose EPL Value Betting and start the installer?",
        ):
            try:
                launch_installer(path)
                self.destroy()
            except Exception as exc:
                messagebox.showerror("Could not launch installer", str(exc))

    def _update_download_failed(self, exc: Exception, page_url: str) -> None:
        self._set_busy(False)
        if messagebox.askyesno(
            "Update download failed",
            f"{exc}\n\nOpen the GitHub Release page instead?",
        ):
            webbrowser.open(page_url)

    def _diagnostic_text(self) -> str:
        lines = [
            f"{APP_NAME} v{VERSION}",
            f"Python: {platform.python_version()}",
            f"OS: {platform.platform()}",
            f"Data directory: {DATA_DIR}",
            f"Database: {DB_FILE}",
            f"Log: {LOG_FILE}",
            f"PulseScore key stored: {'Yes' if bool(get_api_key()) else 'No'}",
            f"Current rows: {len(self.rows)}",
            f"Last fetch: {self.last_fetch_at or 'Never'}",
            "",
            "--- Recent application log ---",
        ]
        try:
            log_lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            lines.extend(log_lines[-250:])
        except Exception as exc:
            lines.append(f"Could not read log: {exc}")
        return "\n".join(lines)

    def _refresh_diagnostics(self) -> None:
        try:
            text = self._diagnostic_text()
        except Exception:
            text = traceback.format_exc()
        self.diagnostics_text.delete("1.0", "end")
        self.diagnostics_text.insert("1.0", text)

    def copy_diagnostics(self) -> None:
        text = self._diagnostic_text()
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_var.set("Diagnostics copied to clipboard")


def main() -> None:
    try:
        App().mainloop()
    except Exception:
        LOGGER.exception("Fatal application error")
        raise


if __name__ == "__main__":
    main()
