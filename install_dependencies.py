#!/usr/bin/env python3
"""
Install Branchly's dependencies in a local virtual environment.

Two kinds of dependency:

* Python packages, pinned in ``requirements.txt``, installed into ``.venv`` so
  nothing lands in the system interpreter.
* System libraries Qt needs to open a window. Those cannot come from pip, so the
  script offers the right command for the detected package manager and asks
  before running anything with elevated rights.

By default a visible installer window is shown when a display is available.
Pass ``--cli`` for a terminal-only install (scripts, CI, SSH without X).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from shutil import which

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import paths  # noqa: E402
from constants import APP_NAME  # noqa: E402

VENV_DIR = _ROOT / ".venv"
REQUIREMENTS_PATH = _ROOT / "requirements.txt"

# Qt needs these to create a window; a fresh Linux install often lacks them and
# the failure mode is an unhelpful "could not load the xcb platform plugin".
SYSTEM_PACKAGES: dict[str, list[str]] = {
    "apt-get": [
        "libegl1",
        "libgl1",
        "libglib2.0-0",
        "libxcb-cursor0",
        "libxkbcommon-x11-0",
        "python3-venv",
    ],
    "dnf": [
        "mesa-libEGL",
        "mesa-libGL",
        "glib2",
        "xcb-util-cursor",
        "libxkbcommon-x11",
    ],
    "pacman": [
        "libglvnd",
        "glib2",
        "xcb-util-cursor",
        "libxkbcommon-x11",
    ],
    "zypper": [
        "libEGL1",
        "libGL1",
        "glib2",
        "xcb-util-cursor",
        "libxkbcommon-x11-0",
    ],
}

INSTALL_COMMANDS: dict[str, list[str]] = {
    "apt-get": ["sudo", "apt-get", "install", "-y"],
    "dnf": ["sudo", "dnf", "install", "-y"],
    "pacman": ["sudo", "pacman", "-S", "--needed", "--noconfirm"],
    "zypper": ["sudo", "zypper", "install", "-y"],
}

LogFn = Callable[[str], None]
_PIN_RE = re.compile(r"^([A-Za-z0-9_.\-]+)\s*==\s*([^\s#]+)")


@dataclass(frozen=True, slots=True)
class PinnedRequirement:
    """
    One pinned package from ``requirements.txt``.

    Attributes:
        name: Distribution name as written in the file.
        version: Exact version pin.
    """

    name: str
    version: str


@dataclass(frozen=True, slots=True)
class PackageStatus:
    """
    Whether one pinned package is present at the expected version.

    Attributes:
        requirement: The pin from ``requirements.txt``.
        installed_version: Version found in the venv, or empty when missing.
        ok: True when the installed version matches the pin.
    """

    requirement: PinnedRequirement
    installed_version: str
    ok: bool


@dataclass(frozen=True, slots=True)
class InstallOptions:
    """
    Controls one install / repair run.

    Attributes:
        skip_system: Do not touch system packages.
        assume_yes: Install system packages without an interactive prompt.
        recreate_venv: Delete ``.venv`` and create it again before pip install.
    """

    skip_system: bool = False
    assume_yes: bool = False
    recreate_venv: bool = False


def _default_log(line: str) -> None:
    """
    Prints one log line to stdout.

    Args:
        line: Text to print.

    Returns:
        None
    """

    print(line, flush=True)


def detect_package_manager() -> str:
    """
    Finds the system's package manager.

    Returns:
        str: Its executable name, or an empty string when none is known.
    """

    for name in SYSTEM_PACKAGES:
        if which(name):
            return name
    return ""


def display_available() -> bool:
    """
    Reports whether a graphical display looks usable.

    Returns:
        bool: True when a window can probably be shown.
    """

    if paths.is_windows():
        return True
    if paths.is_macos():
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def parse_pinned_requirements(path: Path = REQUIREMENTS_PATH) -> list[PinnedRequirement]:
    """
    Reads exact ``name==version`` pins from the requirements file.

    Args:
        path: Requirements file.

    Returns:
        list[PinnedRequirement]: Pins in file order.
    """

    pins: list[PinnedRequirement] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return pins
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN_RE.match(line)
        if match:
            pins.append(PinnedRequirement(name=match.group(1), version=match.group(2)))
    return pins


def run_command(
    args: list[str],
    *,
    log: LogFn | None = None,
    check: bool = True,
) -> int:
    """
    Runs a command, streaming output line by line.

    Args:
        args: Command and arguments.
        log: Receives each output line and the ``$ …`` header. Defaults to print.
        check: Whether a non-zero exit should raise ``SystemExit``.

    Returns:
        int: The exit code.
    """

    emit = log or _default_log
    emit("$ " + " ".join(args))
    try:
        process = subprocess.Popen(
            args,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as error:
        emit(f"command failed to start: {error}")
        if check:
            raise SystemExit(1) from error
        return 1

    assert process.stdout is not None
    for line in process.stdout:
        emit(line.rstrip("\n"))
    code = process.wait()
    if check and code != 0:
        emit(f"command failed with exit code {code}")
        raise SystemExit(code)
    return code


def create_venv(*, recreate: bool = False, log: LogFn | None = None) -> Path:
    """
    Creates the virtual environment if it is not there yet.

    Args:
        recreate: When True, delete an existing ``.venv`` first.
        log: Progress logger.

    Returns:
        Path: The interpreter inside the environment.
    """

    emit = log or _default_log
    interpreter = paths.venv_python_path(_ROOT)
    if recreate and VENV_DIR.exists():
        emit(f"removing existing virtual environment at {VENV_DIR}")
        shutil.rmtree(VENV_DIR)
        interpreter = paths.venv_python_path(_ROOT)

    if interpreter.exists():
        emit(f"virtual environment already present at {VENV_DIR}")
        return interpreter

    run_command([sys.executable, "-m", "venv", str(VENV_DIR)], log=emit)
    interpreter = paths.venv_python_path(_ROOT)
    if not interpreter.exists():
        emit("the virtual environment was created but holds no interpreter")
        raise SystemExit(1)
    return interpreter


def install_python_packages(interpreter: Path, *, log: LogFn | None = None) -> None:
    """
    Installs the pinned Python packages into the virtual environment.

    Always runs ``pip install -r requirements.txt`` so a repair brings every
    package back to the exact version written in the file.

    Args:
        interpreter: Interpreter inside the virtual environment.
        log: Progress logger.

    Returns:
        None
    """

    emit = log or _default_log
    run_command([str(interpreter), "-m", "pip", "install", "--upgrade", "pip"], log=emit)
    run_command(
        [str(interpreter), "-m", "pip", "install", "-r", str(REQUIREMENTS_PATH)],
        log=emit,
    )


def system_install_command() -> list[str] | None:
    """
    Builds the package-manager command that installs Qt's system libraries.

    Returns:
        list[str] | None: Full command, or None when not applicable.
    """

    if paths.is_windows():
        return None
    manager = detect_package_manager()
    if not manager:
        return None
    return [*INSTALL_COMMANDS[manager], *SYSTEM_PACKAGES[manager]]


def install_system_packages(
    *,
    assume_yes: bool,
    log: LogFn | None = None,
    confirm: Callable[[str], bool] | None = None,
) -> None:
    """
    Offers to install the system libraries Qt needs.

    Args:
        assume_yes: Whether to skip the confirmation prompt.
        log: Progress logger.
        confirm: Optional interactive confirmer. Receives the proposed command
            text and returns True to proceed. Defaults to a terminal prompt.

    Returns:
        None
    """

    emit = log or _default_log
    if paths.is_windows():
        emit("no system packages needed on Windows")
        return

    command = system_install_command()
    if command is None:
        emit("no known package manager found — install the Qt runtime libraries yourself")
        return

    manager = detect_package_manager()
    packages = SYSTEM_PACKAGES[manager]
    emit("Qt needs these system libraries:")
    emit("  " + " ".join(packages))
    emit("Proposed command:")
    emit("  " + " ".join(command))

    if not assume_yes:
        ask = confirm or _terminal_confirm
        if not ask(" ".join(command)):
            emit("skipped — run the command above yourself when Branchly fails to start")
            return

    # A failure here is not fatal: the libraries may already be present under
    # different package names on this distribution.
    run_command(command, log=emit, check=False)


def _terminal_confirm(_command: str) -> bool:
    """
    Asks on stdin whether to run the system-package command.

    Args:
        _command: Proposed command (already printed by the caller).

    Returns:
        bool: True when the user accepted.
    """

    try:
        answer = input("\nRun it now with sudo? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes", "j", "ja"}


def verify(interpreter: Path, *, log: LogFn | None = None) -> bool:
    """
    Checks that the installed packages actually import.

    Args:
        interpreter: Interpreter inside the virtual environment.
        log: Progress logger.

    Returns:
        bool: True when everything imports.
    """

    emit = log or _default_log
    probe = (
        "import PySide6, requests, keyring;"
        "from PySide6.QtWidgets import QApplication;"
        "print('PySide6', PySide6.__version__)"
    )
    return run_command([str(interpreter), "-c", probe], log=emit, check=False) == 0


def probe_package_status(interpreter: Path | None = None) -> list[PackageStatus]:
    """
    Compares pinned requirements against what the venv currently holds.

    Args:
        interpreter: Venv interpreter. Defaults to the project's ``.venv``.

    Returns:
        list[PackageStatus]: One entry per pin.
    """

    pins = parse_pinned_requirements()
    target = interpreter or paths.venv_python_path(_ROOT)
    if not target.exists():
        return [
            PackageStatus(requirement=pin, installed_version="", ok=False) for pin in pins
        ]

    # One subprocess: importlib.metadata is available on every supported Python.
    names = [pin.name for pin in pins]
    script = (
        "import importlib.metadata as m, json, sys\n"
        "names = json.loads(sys.argv[1])\n"
        "out = {}\n"
        "for name in names:\n"
        "    try:\n"
        "        out[name] = m.version(name)\n"
        "    except m.PackageNotFoundError:\n"
        "        out[name] = ''\n"
        "print(json.dumps(out))\n"
    )
    try:
        completed = subprocess.run(
            [str(target), "-c", script, __import__("json").dumps(names)],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return [
            PackageStatus(requirement=pin, installed_version="", ok=False) for pin in pins
        ]

    versions: dict[str, str] = {}
    if completed.returncode == 0 and completed.stdout.strip():
        try:
            import json

            parsed = json.loads(completed.stdout)
            if isinstance(parsed, dict):
                versions = {str(key): str(value) for key, value in parsed.items()}
        except ValueError:
            versions = {}

    result: list[PackageStatus] = []
    for pin in pins:
        installed = versions.get(pin.name, "")
        result.append(
            PackageStatus(
                requirement=pin,
                installed_version=installed,
                ok=bool(installed) and installed == pin.version,
            )
        )
    return result


def needs_install(interpreter: Path | None = None) -> bool:
    """
    Reports whether any pinned package is missing or at the wrong version.

    Args:
        interpreter: Venv interpreter. Defaults to the project's ``.venv``.

    Returns:
        bool: True when install / repair is needed.
    """

    target = interpreter or paths.venv_python_path(_ROOT)
    if not target.exists():
        return True
    return any(not status.ok for status in probe_package_status(target))


def run_install(
    options: InstallOptions | None = None,
    *,
    log: LogFn | None = None,
    confirm_system: Callable[[str], bool] | None = None,
) -> int:
    """
    Creates the venv (if needed), installs pinned packages, optionally system libs.

    Args:
        options: Install behaviour. Defaults to a normal first-run install.
        log: Progress logger.
        confirm_system: Confirmer for system packages when ``assume_yes`` is False.

    Returns:
        int: Process exit code (0 success, 1 verify failed, 2 git missing).
    """

    opts = options or InstallOptions()
    emit = log or _default_log

    if which("git") is None:
        emit("git is not installed. Branchly needs it — install git first.")
        return 2

    interpreter = create_venv(recreate=opts.recreate_venv, log=emit)
    install_python_packages(interpreter, log=emit)
    if not opts.skip_system:
        install_system_packages(
            assume_yes=opts.assume_yes,
            log=emit,
            confirm=confirm_system,
        )

    emit("")
    if verify(interpreter, log=emit):
        emit(f"{APP_NAME} is ready. Start it with ./branchly.sh (or .venv/bin/python run.py).")
        return 0
    emit(
        "the packages installed but do not import yet — the system libraries above are "
        "the usual reason"
    )
    return 1


def launch_gui(argv: list[str] | None = None) -> int:
    """
    Opens the visible installer window.

    Args:
        argv: Unused; kept for symmetry with ``main``.

    Returns:
        int: Process exit code.
    """

    del argv  # argparse leftover; the GUI collects its own options.
    from installer_ui import run_installer_ui

    return run_installer_ui()


def main(argv: list[str] | None = None) -> int:
    """
    Runs the installation.

    Args:
        argv: Argument list. Defaults to ``sys.argv[1:]``.

    Returns:
        int: Process exit code.
    """

    parser = argparse.ArgumentParser(description=f"Install {APP_NAME} dependencies.")
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Terminal-only install (no window).",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Force the installer window even when a display check is unsure.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Do not ask before installing system packages (CLI mode).",
    )
    parser.add_argument(
        "--skip-system",
        action="store_true",
        help="Only install the Python packages.",
    )
    parser.add_argument(
        "--recreate-venv",
        action="store_true",
        help="Delete .venv and create it again before installing.",
    )
    args = parser.parse_args(argv)

    prefer_gui = args.gui or (not args.cli and display_available())
    if prefer_gui and not args.cli:
        try:
            return launch_gui()
        except Exception as error:  # noqa: BLE001 — fall back to CLI for any GUI failure
            print(f"installer window unavailable ({error}); continuing in the terminal.", file=sys.stderr)

    return run_install(
        InstallOptions(
            skip_system=args.skip_system,
            assume_yes=args.yes,
            recreate_venv=args.recreate_venv,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
