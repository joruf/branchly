#!/usr/bin/env python3
"""
Install Branchly's dependencies in a local virtual environment.

Two kinds of dependency:

* Python packages, pinned in ``requirements.txt``, installed into ``.venv`` so
  nothing lands in the system interpreter.
* System libraries Qt needs to open a window. Those cannot come from pip, so the
  script offers the right command for the detected package manager and asks
  before running anything with elevated rights.

Nothing is installed silently: every command is printed before it runs.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from shutil import which

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import paths  # noqa: E402
from constants import APP_NAME  # noqa: E402

VENV_DIR = _ROOT / ".venv"

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


def run(args: list[str], check: bool = True) -> int:
    """
    Runs a command, printing it first.

    Args:
        args: Command and arguments.
        check: Whether a non-zero exit should stop the script.

    Returns:
        int: The exit code.
    """

    print("$", " ".join(args))
    completed = subprocess.run(args, shell=False, check=False)
    if check and completed.returncode != 0:
        print(f"command failed with exit code {completed.returncode}", file=sys.stderr)
        raise SystemExit(completed.returncode)
    return completed.returncode


def create_venv() -> Path:
    """
    Creates the virtual environment if it is not there yet.

    Returns:
        Path: The interpreter inside the environment.
    """

    interpreter = paths.venv_python_path(_ROOT)
    if interpreter.exists():
        print(f"virtual environment already present at {VENV_DIR}")
        return interpreter
    run([sys.executable, "-m", "venv", str(VENV_DIR)])
    interpreter = paths.venv_python_path(_ROOT)
    if not interpreter.exists():
        print("the virtual environment was created but holds no interpreter", file=sys.stderr)
        raise SystemExit(1)
    return interpreter


def install_python_packages(interpreter: Path) -> None:
    """
    Installs the pinned Python packages.

    Args:
        interpreter: Interpreter inside the virtual environment.

    Returns:
        None
    """

    run([str(interpreter), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(interpreter), "-m", "pip", "install", "-r", str(_ROOT / "requirements.txt")])


def install_system_packages(assume_yes: bool) -> None:
    """
    Offers to install the system libraries Qt needs.

    Args:
        assume_yes: Whether to skip the confirmation prompt.

    Returns:
        None
    """

    if paths.is_windows():
        print("no system packages needed on Windows")
        return
    manager = detect_package_manager()
    if not manager:
        print("no known package manager found — install the Qt runtime libraries yourself")
        return

    command = [*INSTALL_COMMANDS[manager], *SYSTEM_PACKAGES[manager]]
    print("\nQt needs these system libraries:")
    print("  " + " ".join(SYSTEM_PACKAGES[manager]))
    print("Proposed command:")
    print("  " + " ".join(command))

    if not assume_yes:
        answer = input("\nRun it now with sudo? [y/N] ").strip().lower()
        if answer not in {"y", "yes", "j", "ja"}:
            print("skipped — run the command above yourself when Branchly fails to start")
            return
    # A failure here is not fatal: the libraries may already be present under
    # different package names on this distribution.
    run(command, check=False)


def verify(interpreter: Path) -> bool:
    """
    Checks that the installed packages actually import.

    Args:
        interpreter: Interpreter inside the virtual environment.

    Returns:
        bool: True when everything imports.
    """

    probe = (
        "import PySide6, requests, keyring;"
        "from PySide6.QtWidgets import QApplication;"
        "print('PySide6', PySide6.__version__)"
    )
    return run([str(interpreter), "-c", probe], check=False) == 0


def main(argv: list[str] | None = None) -> int:
    """
    Runs the installation.

    Args:
        argv: Argument list. Defaults to ``sys.argv[1:]``.

    Returns:
        int: Process exit code.
    """

    parser = argparse.ArgumentParser(description=f"Install {APP_NAME} dependencies.")
    parser.add_argument("--yes", action="store_true", help="Do not ask before installing system packages.")
    parser.add_argument("--skip-system", action="store_true", help="Only install the Python packages.")
    args = parser.parse_args(argv)

    if which("git") is None:
        print("git is not installed. Branchly needs it — install git first.", file=sys.stderr)
        return 2

    interpreter = create_venv()
    install_python_packages(interpreter)
    if not args.skip_system:
        install_system_packages(args.yes)

    print()
    if verify(interpreter):
        print(f"{APP_NAME} is ready. Start it with ./branchly.sh (or .venv/bin/python run.py).")
        return 0
    print(
        "the packages installed but do not import yet — the system libraries above are "
        "the usual reason",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
