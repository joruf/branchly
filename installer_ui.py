"""
Visible installer for Branchly.

Uses the standard library Tk toolkit so the window can appear before PySide6 is
installed. The same window also repairs a broken or outdated ``.venv`` by
reinstalling every pin from ``requirements.txt``.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import i18n  # noqa: E402
import install_dependencies as install_mod  # noqa: E402
import paths  # noqa: E402
from constants import APP_NAME, APP_SLUG, APP_VERSION  # noqa: E402

# Match Branchly's dark theme tokens so the bootstrap window feels native.
_BG = "#1f2430"
_SURFACE = "#242833"
_SURFACE_ALT = "#222938"
_TEXT = "#e7ecf2"
_MUTED = "#9fb2c9"
_BORDER = "#3e4657"
_ACCENT = "#2f7dd1"
_ACCENT_HOVER = "#4591e4"
_BUTTON = "#2f3543"
_SUCCESS = "#4ec98a"
_WARNING = "#e0b341"
_DANGER = "#e8705f"
_INPUT = "#2f3543"


def _prefer_language() -> str:
    """
    Picks a language for the installer from settings or the environment.

    Returns:
        str: Language code.
    """

    try:
        from config.app_settings import load_settings

        return i18n.set_language(load_settings().language)
    except Exception:  # noqa: BLE001 - any settings problem falls back to the locale
        env = (os.environ.get("LANG") or os.environ.get("LC_ALL") or "en").lower()
        if env.startswith("de"):
            return i18n.set_language("de")
        return i18n.set_language("en")


def _status_label(status: install_mod.PackageStatus) -> str:
    """
    Formats one package row for the checklist.

    Args:
        status: Probe result.

    Returns:
        str: Human-readable line.
    """

    pin = status.requirement
    if status.ok:
        return f"✓  {pin.name} == {pin.version}"
    if not status.installed_version:
        return f"✗  {pin.name} == {pin.version}  — {i18n.t('installer.status_missing')}"
    return (
        f"✗  {pin.name} == {pin.version}  — "
        f"{i18n.t('installer.status_wrong', version=status.installed_version)}"
    )


class InstallerApp:
    """
    Tk window that installs or repairs Branchly's pinned dependencies.
    """

    def __init__(self) -> None:
        _prefer_language()
        self._root = tk.Tk()
        self._root.title(i18n.t("installer.title", app=APP_NAME))
        self._root.minsize(640, 520)
        self._root.configure(bg=_BG)
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

        icon = paths.project_root() / "resources" / f"{APP_SLUG}.png"
        if icon.exists():
            try:
                self._root.iconphoto(True, tk.PhotoImage(file=str(icon)))
            except tk.TclError:
                pass

        self._log_queue: queue.Queue[str | None] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._busy = False
        self._exit_code = 1
        self._success = False

        self._build()
        self._refresh_status()
        self._root.after(100, self._drain_log)

    def _build(self) -> None:
        """
        Builds the window contents.

        Returns:
            None
        """

        pad = {"padx": 16, "pady": (12, 0)}
        outer = tk.Frame(self._root, bg=_BG)
        outer.pack(fill=tk.BOTH, expand=True)

        heading = tk.Label(
            outer,
            text=i18n.t("installer.heading", app=APP_NAME),
            bg=_BG,
            fg=_TEXT,
            font=("Sans", 14, "bold"),
            anchor="w",
        )
        heading.pack(fill=tk.X, **pad)

        subtitle = tk.Label(
            outer,
            text=i18n.t("installer.subtitle", version=APP_VERSION),
            bg=_BG,
            fg=_MUTED,
            font=("Sans", 10),
            anchor="w",
            justify=tk.LEFT,
            wraplength=600,
        )
        subtitle.pack(fill=tk.X, padx=16, pady=(4, 0))

        status_frame = tk.LabelFrame(
            outer,
            text=i18n.t("installer.packages"),
            bg=_SURFACE,
            fg=_MUTED,
            bd=1,
            relief=tk.SOLID,
            labelanchor="nw",
            highlightbackground=_BORDER,
            highlightcolor=_BORDER,
        )
        status_frame.pack(fill=tk.X, padx=16, pady=(12, 0))

        self._status_list = tk.Listbox(
            status_frame,
            height=7,
            bg=_INPUT,
            fg=_TEXT,
            selectbackground=_ACCENT,
            selectforeground="#ffffff",
            activestyle="none",
            relief=tk.FLAT,
            highlightthickness=0,
            font=("Consolas", 10),
            borderwidth=0,
        )
        self._status_list.pack(fill=tk.X, padx=8, pady=8)

        options = tk.Frame(outer, bg=_BG)
        options.pack(fill=tk.X, padx=16, pady=(10, 0))

        self._system_var = tk.BooleanVar(value=not paths.is_windows())
        self._recreate_var = tk.BooleanVar(value=False)

        system_check = tk.Checkbutton(
            options,
            text=i18n.t("installer.option_system"),
            variable=self._system_var,
            bg=_BG,
            fg=_TEXT,
            activebackground=_BG,
            activeforeground=_TEXT,
            selectcolor=_INPUT,
            highlightthickness=0,
            anchor="w",
        )
        system_check.pack(fill=tk.X)
        if paths.is_windows():
            system_check.configure(state=tk.DISABLED)

        recreate_check = tk.Checkbutton(
            options,
            text=i18n.t("installer.option_recreate"),
            variable=self._recreate_var,
            bg=_BG,
            fg=_TEXT,
            activebackground=_BG,
            activeforeground=_TEXT,
            selectcolor=_INPUT,
            highlightthickness=0,
            anchor="w",
        )
        recreate_check.pack(fill=tk.X, pady=(4, 0))

        self._summary = tk.Label(
            outer,
            text="",
            bg=_BG,
            fg=_MUTED,
            font=("Sans", 10),
            anchor="w",
            justify=tk.LEFT,
            wraplength=600,
        )
        self._summary.pack(fill=tk.X, padx=16, pady=(8, 0))

        style = ttk.Style(self._root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Install.Horizontal.TProgressbar",
            troughcolor=_SURFACE_ALT,
            background=_ACCENT,
            bordercolor=_BORDER,
            lightcolor=_ACCENT,
            darkcolor=_ACCENT,
        )
        self._progress = ttk.Progressbar(
            outer,
            mode="indeterminate",
            style="Install.Horizontal.TProgressbar",
        )
        self._progress.pack(fill=tk.X, padx=16, pady=(10, 0))

        log_label = tk.Label(
            outer,
            text=i18n.t("installer.log"),
            bg=_BG,
            fg=_MUTED,
            font=("Sans", 10),
            anchor="w",
        )
        log_label.pack(fill=tk.X, padx=16, pady=(10, 0))

        self._log = scrolledtext.ScrolledText(
            outer,
            height=12,
            bg=_INPUT,
            fg=_TEXT,
            insertbackground=_TEXT,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=_BORDER,
            highlightcolor=_ACCENT,
            font=("Consolas", 9),
            state=tk.DISABLED,
            wrap=tk.WORD,
        )
        self._log.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 0))

        buttons = tk.Frame(outer, bg=_BG)
        buttons.pack(fill=tk.X, padx=16, pady=16)

        self._quit_btn = tk.Button(
            buttons,
            text=i18n.t("installer.quit"),
            command=self._on_close,
            bg=_BUTTON,
            fg=_TEXT,
            activebackground=_BORDER,
            activeforeground=_TEXT,
            relief=tk.FLAT,
            padx=14,
            pady=6,
            cursor="hand2",
        )
        self._quit_btn.pack(side=tk.LEFT)

        self._start_btn = tk.Button(
            buttons,
            text=i18n.t("installer.start_app", app=APP_NAME),
            command=self._start_app,
            bg=_BUTTON,
            fg=_TEXT,
            activebackground=_BORDER,
            activeforeground=_TEXT,
            relief=tk.FLAT,
            padx=14,
            pady=6,
            cursor="hand2",
            state=tk.DISABLED,
        )
        self._start_btn.pack(side=tk.RIGHT)

        self._install_btn = tk.Button(
            buttons,
            text=i18n.t("installer.install"),
            command=self._start_install,
            bg=_ACCENT,
            fg="#ffffff",
            activebackground=_ACCENT_HOVER,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            padx=16,
            pady=6,
            cursor="hand2",
        )
        self._install_btn.pack(side=tk.RIGHT, padx=(0, 8))

    def _append_log(self, line: str) -> None:
        """
        Appends one line to the log view.

        Args:
            line: Text to show.

        Returns:
            None
        """

        self._log.configure(state=tk.NORMAL)
        self._log.insert(tk.END, line + "\n")
        self._log.see(tk.END)
        self._log.configure(state=tk.DISABLED)

    def _drain_log(self) -> None:
        """
        Moves queued worker lines onto the UI thread.

        Returns:
            None
        """

        try:
            while True:
                item = self._log_queue.get_nowait()
                if item is None:
                    break
                self._append_log(item)
        except queue.Empty:
            pass
        if self._root.winfo_exists():
            self._root.after(100, self._drain_log)

    def _refresh_status(self) -> None:
        """
        Re-probes the venv and updates the checklist.

        Returns:
            None
        """

        statuses = install_mod.probe_package_status()
        self._status_list.delete(0, tk.END)
        missing = 0
        for status in statuses:
            self._status_list.insert(tk.END, _status_label(status))
            index = self._status_list.size() - 1
            if status.ok:
                self._status_list.itemconfig(index, foreground=_SUCCESS)
            else:
                missing += 1
                color = _WARNING if status.installed_version else _DANGER
                self._status_list.itemconfig(index, foreground=color)

        venv = paths.venv_python_path(_ROOT)
        if not venv.exists():
            self._summary.configure(
                text=i18n.t("installer.summary_no_venv"),
                fg=_WARNING,
            )
            self._install_btn.configure(text=i18n.t("installer.install"))
        elif missing:
            self._summary.configure(
                text=i18n.t("installer.summary_repair", count=missing),
                fg=_WARNING,
            )
            self._install_btn.configure(text=i18n.t("installer.repair"))
        else:
            self._summary.configure(
                text=i18n.t("installer.summary_ok"),
                fg=_SUCCESS,
            )
            self._install_btn.configure(text=i18n.t("installer.reinstall"))
            self._success = True
            self._start_btn.configure(state=tk.NORMAL)
            self._exit_code = 0

    def _set_busy(self, busy: bool) -> None:
        """
        Enables or disables controls while work runs.

        Args:
            busy: Whether an install is in progress.

        Returns:
            None
        """

        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self._install_btn.configure(state=state)
        if busy:
            self._start_btn.configure(state=tk.DISABLED)
            self._progress.start(12)
        else:
            self._progress.stop()
            if self._success:
                self._start_btn.configure(state=tk.NORMAL)

    def _confirm_system(self, command: str) -> bool:
        """
        Asks whether to run the system-package command (on the UI thread).

        Args:
            command: Proposed shell command.

        Returns:
            bool: True when the user accepted.
        """

        event = threading.Event()
        answer: dict[str, bool] = {"ok": False}

        def ask() -> None:
            answer["ok"] = messagebox.askyesno(
                i18n.t("installer.system_title"),
                i18n.t("installer.system_prompt", command=command),
                parent=self._root,
            )
            event.set()

        self._root.after(0, ask)
        event.wait()
        return answer["ok"]

    def _start_install(self) -> None:
        """
        Starts install / repair on a background thread.

        Returns:
            None
        """

        if self._busy:
            return
        self._set_busy(True)
        self._success = False
        self._append_log("")
        self._append_log(i18n.t("installer.log_start"))

        options = install_mod.InstallOptions(
            skip_system=not self._system_var.get(),
            assume_yes=False,
            recreate_venv=self._recreate_var.get(),
        )

        def worker() -> None:
            def log(line: str) -> None:
                self._log_queue.put(line)

            code = install_mod.run_install(
                options,
                log=log,
                confirm_system=self._confirm_system,
            )
            self._log_queue.put(None)
            self._root.after(0, lambda: self._install_finished(code))

        self._worker = threading.Thread(target=worker, name="branchly-install", daemon=True)
        self._worker.start()

    def _install_finished(self, code: int) -> None:
        """
        Handles completion of the background install.

        Args:
            code: Exit code from ``run_install``.

        Returns:
            None
        """

        self._set_busy(False)
        self._exit_code = code
        self._refresh_status()
        if code == 0:
            self._success = True
            self._start_btn.configure(state=tk.NORMAL)
            self._summary.configure(text=i18n.t("installer.summary_done"), fg=_SUCCESS)
            messagebox.showinfo(
                APP_NAME,
                i18n.t("installer.done", app=APP_NAME),
                parent=self._root,
            )
        elif code == 2:
            messagebox.showerror(
                APP_NAME,
                i18n.t("error.git_missing") + "\n\n" + i18n.t("error.git_missing_hint"),
                parent=self._root,
            )
        else:
            messagebox.showerror(
                APP_NAME,
                i18n.t("installer.failed"),
                parent=self._root,
            )

    def _start_app(self) -> None:
        """
        Launches Branchly with the venv interpreter and closes the installer.

        Returns:
            None
        """

        interpreter = paths.venv_python_path(_ROOT)
        if not interpreter.exists():
            messagebox.showerror(APP_NAME, i18n.t("installer.summary_no_venv"), parent=self._root)
            return
        run_py = _ROOT / "run.py"
        env = dict(os.environ)
        env["BRANCHLY_REEXEC"] = "1"
        try:
            subprocess.Popen([str(interpreter), str(run_py)], env=env, cwd=str(_ROOT))
        except OSError as error:
            messagebox.showerror(APP_NAME, str(error), parent=self._root)
            return
        self._exit_code = 0
        self._root.destroy()

    def _on_close(self) -> None:
        """
        Closes the window, refusing while an install is running.

        Returns:
            None
        """

        if self._busy and not messagebox.askyesno(
            APP_NAME,
            i18n.t("installer.close_busy"),
            parent=self._root,
        ):
            return
        self._root.destroy()

    def run(self) -> int:
        """
        Shows the window until it is closed.

        Returns:
            int: Process exit code.
        """

        self._root.mainloop()
        return self._exit_code


def run_installer_ui() -> int:
    """
    Opens the installer window.

    Returns:
        int: Process exit code.
    """

    return InstallerApp().run()


def launch_installer_subprocess() -> None:
    """
    Starts the installer as a separate process (safe while Branchly is running).

    Returns:
        None
    """

    script = _ROOT / "install_dependencies.py"
    subprocess.Popen([sys.executable, str(script), "--gui"], cwd=str(_ROOT))
