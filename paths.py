"""
Cross-platform user data path helpers for Branchly.
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

APP_DIR_NAME = "branchly"

#: ``True`` inside the single-file executable that ``build-exe.py`` produces.
IS_FROZEN = bool(getattr(sys, "frozen", False))


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

    The executable unpacks itself into a temporary directory on every start,
    and its read-only files (locales, icons, manuals, ``VERSION``) travel there
    in the same layout a checkout has, so every path below this one is the same.

    Returns:
        Path: Directory containing ``run.py``; ``sys._MEIPASS`` in the executable.
    """

    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", "."))
    return Path(__file__).resolve().parent


def executable() -> Path:
    """
    Returns the single-file executable this process was started from.

    Only meaningful when :data:`IS_FROZEN` is set; otherwise it is the Python
    interpreter.

    Returns:
        Path: The running program file.
    """

    return Path(sys.executable).resolve()


def executable_name(version: str = "", build: int = 0) -> str:
    """
    Returns the file name of the executable for this platform and version.

    ``build-exe.py`` writes the executable under this name and the updater looks
    for a release asset with it, so both always agree. The version is part of it
    so a downloaded file says which one it is.

    Args:
        version: ``major.minor.patch``; defaults to this program's.
        build: The build number; defaults to this program's.

    Returns:
        str: For example ``branchly-linux-x86_64-0.10.2-build31`` or
            ``branchly-windows-x86_64-0.10.2-build31.exe``.
    """

    if not version:
        import version as _version

        current = _version.current()
        version, build = current.name, int(current.build or 0)
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    system = {"windows": "windows", "darwin": "macos"}.get(os_family(), "linux")
    suffix = ".exe" if is_windows() else ""
    return f"{APP_DIR_NAME}-{system}-{machine}-{version}-build{build}{suffix}"


def child_environment(env: dict | None = None) -> dict:
    """
    Returns the environment a child process should get.

    The executable runs with ``LD_LIBRARY_PATH`` - and, through PyInstaller's Qt
    hooks, further variables - pointing into its unpacked files. Inherited, they
    make git, ssh, the file manager or the browser load the program's libraries
    instead of their own, which ends anywhere between odd warnings and crashes.
    Every entry that points into the unpacked files is removed;
    ``LD_LIBRARY_PATH`` gets the value it had before the program started.

    Args:
        env: The environment to clean; defaults to this process's.

    Returns:
        dict: A new dict; a plain copy outside the executable.
    """

    source = os.environ if env is None else env
    bundle = getattr(sys, "_MEIPASS", "")
    if not IS_FROZEN or not bundle:
        return dict(source)
    clean = {}
    for key, value in source.items():
        if key.startswith("_PYI_") or key == "LD_LIBRARY_PATH_ORIG":
            continue
        if bundle in value:
            kept = [part for part in value.split(os.pathsep) if part and bundle not in part]
            if not kept:
                continue
            value = os.pathsep.join(kept)
        clean[key] = value
    original = source.get("LD_LIBRARY_PATH_ORIG")
    if original:
        clean["LD_LIBRARY_PATH"] = original
    return clean


def use_system_environment_for_children() -> None:
    """
    Starts every child process with :func:`child_environment`.

    One place instead of every ``subprocess`` call: the program starts git for
    almost everything it does, and a single forgotten ``env=`` would bring the
    problem back. Does nothing outside the executable.

    Returns:
        None
    """

    import subprocess

    if not IS_FROZEN or getattr(subprocess.Popen, "_branchly_clean_env", False):
        return

    class _SystemPopen(subprocess.Popen):  # type: ignore[misc, valid-type]
        """``Popen`` that never hands the bundled libraries to a child."""

        _branchly_clean_env = True

        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            kwargs["env"] = child_environment(kwargs.get("env"))
            super().__init__(*args, **kwargs)

    subprocess.Popen = _SystemPopen  # type: ignore[misc]


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


def update_state_path() -> Path:
    """
    Returns the file in which the executable's updater remembers a replaced file.

    Kept apart from ``settings.json`` on purpose: the window writes its settings
    back when it closes, and would drop a value it never loaded.

    Returns:
        Path: ``update.json`` inside the configuration directory.
    """

    return user_config_dir() / "update.json"


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
