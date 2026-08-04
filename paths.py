"""
Cross-platform user data path helpers for Branchly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "branchly"


def os_family() -> str:
    """
    Returns the current OS family identifier.

    Returns:
        str: ``linux``, ``windows``, ``darwin``, or the raw ``sys.platform`` value.
    """

    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return sys.platform


def is_windows() -> bool:
    """
    Returns whether the current process runs on Windows.

    Returns:
        bool: True on Windows.
    """

    return os_family() == "windows"


def is_linux() -> bool:
    """
    Returns whether the current process runs on Linux.

    Returns:
        bool: True on Linux.
    """

    return os_family() == "linux"


def is_macos() -> bool:
    """
    Returns whether the current process runs on macOS.

    Returns:
        bool: True on macOS.
    """

    return os_family() == "darwin"


def project_root() -> Path:
    """
    Returns the Branchly source tree root.

    Returns:
        Path: Directory containing ``run.py``.
    """

    return Path(__file__).resolve().parent


def user_config_dir() -> Path:
    """
    Returns the Branchly configuration directory.

    Returns:
        Path: ``%APPDATA%\\branchly`` on Windows, ``$XDG_CONFIG_HOME/branchly``
            or ``~/.config/branchly`` elsewhere.
    """

    if is_windows():
        base = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(base) / APP_DIR_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_DIR_NAME


def user_cache_dir() -> Path:
    """
    Returns the Branchly cache directory (avatars, ETag payloads).

    Returns:
        Path: ``%LOCALAPPDATA%\\branchly\\cache`` on Windows,
            ``$XDG_CACHE_HOME/branchly`` or ``~/.cache/branchly`` elsewhere.
    """

    if is_windows():
        base = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(base) / APP_DIR_NAME / "cache"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / APP_DIR_NAME


def settings_path() -> Path:
    """
    Returns the path of the application settings file.

    Returns:
        Path: ``settings.json`` inside the configuration directory.
    """

    return user_config_dir() / "settings.json"


def registry_path() -> Path:
    """
    Returns the path of the tracked-repository registry file.

    Returns:
        Path: ``repos.json`` inside the configuration directory.
    """

    return user_config_dir() / "repos.json"


def avatar_cache_dir() -> Path:
    """
    Returns the directory holding downloaded author avatars.

    Returns:
        Path: ``avatars`` inside the cache directory.
    """

    return user_cache_dir() / "avatars"


def default_clone_parent() -> Path:
    """
    Returns the directory the clone dialog starts in.

    Returns:
        Path: The user home directory, or ``~/Applications`` when that exists.
    """

    preferred = Path.home() / "Applications"
    if preferred.is_dir():
        return preferred
    return Path.home()


def ensure_dir(path: Path) -> bool:
    """
    Creates a directory including parents, tolerating failures.

    A missing config directory must never take the application down; callers
    fall back to in-memory defaults when this returns False.

    Args:
        path: Directory to create.

    Returns:
        bool: True when the directory exists afterwards.
    """

    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return path.is_dir()


def venv_python_path(root: Path | None = None, gui: bool = False) -> Path:
    """
    Resolves the project virtualenv interpreter for the current OS.

    Args:
        root: Project root to look in. Defaults to the Branchly source tree.
        gui: Whether the interpreter will start a window rather than a script.
            Only matters on Windows, where ``python.exe`` opens a console the
            user never asked for; ``pythonw.exe`` does not.

    Returns:
        Path: Preferred interpreter path, which may not exist yet.
    """

    base = root or project_root()
    if is_windows():
        scripts = base / ".venv" / "Scripts"
        order = ("pythonw.exe", "python.exe") if gui else ("python.exe", "pythonw.exe")
        for name in order:
            candidate = scripts / name
            if candidate.exists():
                return candidate
        return scripts / order[0]
    return base / ".venv" / "bin" / "python"
