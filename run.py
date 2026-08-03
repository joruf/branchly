#!/usr/bin/env python3
"""
Branchly application entry point.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Allow running as `python run.py` from anywhere: the project root must be
# importable before the first `import config...` below.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Set in the child process so a re-exec can never turn into a loop.
_REEXEC_MARKER = "BRANCHLY_REEXEC"

import i18n  # noqa: E402
import paths  # noqa: E402
from config.app_settings import AppSettings, load_settings, save_settings  # noqa: E402
from config.theme import available_themes, build_application_stylesheet, set_current_theme  # noqa: E402
from constants import APP_NAME, APP_SLUG, APP_VERSION  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """
    Parses the command line.

    Args:
        argv: Argument list. Defaults to ``sys.argv[1:]``.

    Returns:
        argparse.Namespace: Parsed options.
    """

    parser = argparse.ArgumentParser(
        prog=APP_SLUG,
        description=f"{APP_NAME} — manage your Git projects without the command line.",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument(
        "--language",
        metavar="CODE",
        help="Override the stored language for this run (e.g. de, en).",
    )
    parser.add_argument(
        "--theme",
        metavar="NAME",
        choices=sorted(available_themes()),
        help="Override the stored theme for this run.",
    )
    parser.add_argument(
        "--repo",
        metavar="PATH",
        help="Select this repository on startup, adding it to the list if needed.",
    )
    return parser.parse_args(argv)


def apply_preferences(settings: AppSettings, args: argparse.Namespace) -> AppSettings:
    """
    Applies language and theme, letting command-line overrides win.

    An override is applied for this run only — it is not written back, so
    ``--theme light`` for a screenshot does not silently change the stored
    preference.

    Args:
        settings: Settings loaded from disk.
        args: Parsed command line.

    Returns:
        AppSettings: Settings with the effective language and theme filled in.
    """

    language = args.language or settings.language
    theme = args.theme or settings.theme
    effective_language = i18n.set_language(language)
    effective_theme = set_current_theme(theme)
    settings.language = effective_language
    settings.theme = effective_theme
    return settings


def reexec_into_venv_if_available() -> None:
    """
    Restarts this script with the project's own interpreter when there is one.

    ``python3 run.py`` is the natural thing to type, and it fails, because the
    dependencies live in ``.venv`` rather than in the system interpreter. Rather
    than answer that with a traceback, hand the script over to the right
    interpreter and carry on.

    Returns:
        None
    """

    if os.environ.get(_REEXEC_MARKER):
        return
    interpreter = paths.venv_python_path(_ROOT)
    if not interpreter.exists():
        return
    # Compare environments, not interpreter paths: `.venv/bin/python` is usually a
    # symlink to the system interpreter, so resolving both makes them look equal
    # and the re-exec would never happen. `sys.prefix` is the environment.
    try:
        if Path(sys.prefix).resolve() == (_ROOT / ".venv").resolve():
            return
    except OSError:
        return

    env = dict(os.environ)
    env[_REEXEC_MARKER] = "1"
    argv = [str(interpreter), str(_ROOT / "run.py"), *sys.argv[1:]]
    if paths.is_windows():
        # os.execv is unreliable on Windows; run the child and pass its code on.
        completed = subprocess.run(argv, env=env, shell=False, check=False)
        raise SystemExit(completed.returncode)
    try:
        os.execve(str(interpreter), argv, env)
    except OSError:
        # Falling through is fine: the import check below reports the real problem.
        return


def _report_missing_dependencies(error: ImportError) -> int:
    """
    Opens the installer window when possible; otherwise explains how to fix it.

    Args:
        error: The import failure, shown as the technical detail.

    Returns:
        int: Process exit code.
    """

    interpreter = paths.venv_python_path(_ROOT)
    launcher = "branchly.bat" if paths.is_windows() else "./branchly.sh"
    installer = "python install_dependencies.py" if paths.is_windows() else "./install_dependencies.py"

    # Prefer a visible installer: desktop users never see stderr from the launcher.
    try:
        from install_dependencies import display_available, launch_gui

        if display_available():
            print(
                f"{APP_NAME}: {error.name or error} is missing — opening the installer.",
                file=sys.stderr,
            )
            return launch_gui()
    except Exception as gui_error:  # noqa: BLE001 — fall through to the text hint
        print(f"{APP_NAME}: installer window unavailable ({gui_error})", file=sys.stderr)

    lines = [
        f"{APP_NAME} cannot start: its GUI toolkit is not installed for this interpreter.",
        f"  interpreter: {sys.executable}",
        f"  missing:     {error.name or error}",
        "",
    ]
    if interpreter.exists():
        lines.extend(
            [
                "The dependencies are in this project's virtual environment. Start it with:",
                f"  {launcher}",
                f"  {interpreter} run.py",
                "",
                "Or repair the environment with the installer:",
                f"  {installer}",
            ]
        )
    else:
        lines.extend(
            [
                "No virtual environment yet. Create it with the installer:",
                f"  {installer}",
                f"then start {APP_NAME} with:",
                f"  {launcher}",
            ]
        )
    print("\n".join(lines), file=sys.stderr)
    return 3


def git_is_available() -> bool:
    """
    Reports whether a git binary can be found.

    Returns:
        bool: True when ``git`` is on PATH.
    """

    return shutil.which("git") is not None


def _report_missing_git() -> int:
    """
    Tells the user that git is missing, graphically when possible.

    Returns:
        int: Process exit code.
    """

    message = f"{i18n.t('error.git_missing')}\n\n{i18n.t('error.git_missing_hint')}"
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv)
        assert isinstance(app, QApplication)
        QMessageBox.critical(None, APP_NAME, message)
    except Exception:
        print(message, file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    """
    Starts the application.

    Args:
        argv: Argument list. Defaults to ``sys.argv[1:]``.

    Returns:
        int: Process exit code.
    """

    args = parse_args(argv)
    settings = apply_preferences(load_settings(), args)

    if not git_is_available():
        return _report_missing_git()

    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication

        from ui.main_window import MainWindow
    except ImportError as error:
        return _report_missing_dependencies(error)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_NAME)
    # Wayland reads the desktop file name to attach the window to its icon.
    app.setDesktopFileName(APP_SLUG)

    icon_path = paths.project_root() / "resources" / f"{APP_SLUG}.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    app.setStyleSheet(build_application_stylesheet(settings.theme))

    window = MainWindow(settings)
    if args.repo:
        window.select_startup_repo(args.repo)
    window.show()

    exit_code = app.exec()
    save_settings(window.collect_settings())
    return exit_code


if __name__ == "__main__":
    # Only when started as a script: importing this module must never re-exec.
    reexec_into_venv_if_available()
    sys.exit(main())
