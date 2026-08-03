"""
Handing files and folders to the desktop.

Every call goes through ``subprocess`` with an argument list, never a shell
string, and every path is checked to be inside the repository it claims to belong
to. That second check matters because the path usually comes from a diff of a
repository that may have been cloned from anywhere.

One thing is refused outright: a ``.desktop`` file. On Linux that is not a
document, it is a description of a command to run, and "open this file" must not
mean "execute whatever a cloned repository put in it".
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import paths
from gitops.remote_url import github_slug

OPEN_OK = "ok"
OPEN_MISSING = "missing"
OPEN_OUTSIDE = "outside"
OPEN_REFUSED = "refused"
OPEN_FAILED = "failed"

# Suffixes that describe a command rather than hold content.
_EXECUTABLE_DESCRIPTORS = frozenset({".desktop", ".lnk", ".url", ".appimage"})


def _resolve_inside(repo: Path | str, relative: str) -> Path | None:
    """
    Resolves a repository-relative path and refuses anything outside it.

    Args:
        repo: Working tree root.
        relative: Path relative to that root.

    Returns:
        Path | None: Absolute path, or None when it escapes the repository.
    """

    root = Path(repo).expanduser()
    try:
        resolved = (root / relative).resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def _launch(args: list[str]) -> bool:
    """
    Starts a detached helper process.

    Args:
        args: Command and arguments.

    Returns:
        bool: True when the process started.
    """

    try:
        subprocess.Popen(
            args,
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return False
    return True


def open_path(target: Path | str) -> str:
    """
    Opens a file or folder with the system's default handler.

    Args:
        target: Absolute path to open.

    Returns:
        str: One of the ``OPEN_*`` constants.
    """

    path = Path(target).expanduser()
    if not path.exists():
        return OPEN_MISSING
    if path.is_file() and path.suffix.lower() in _EXECUTABLE_DESCRIPTORS:
        return OPEN_REFUSED

    if paths.is_windows():
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606 - Windows only
        except (OSError, AttributeError):
            return OPEN_FAILED
        return OPEN_OK
    if paths.is_macos():
        return OPEN_OK if _launch(["open", str(path)]) else OPEN_FAILED
    return OPEN_OK if _launch(["xdg-open", str(path)]) else OPEN_FAILED


def open_repo_file(repo: Path | str, relative: str) -> str:
    """
    Opens a file from a repository with its default program.

    Args:
        repo: Working tree root.
        relative: Path relative to that root.

    Returns:
        str: One of the ``OPEN_*`` constants.
    """

    resolved = _resolve_inside(repo, relative)
    if resolved is None:
        return OPEN_OUTSIDE
    return open_path(resolved)


def reveal_path(target: Path | str) -> str:
    """
    Opens the file manager with a file selected.

    Args:
        target: Absolute path to reveal.

    Returns:
        str: One of the ``OPEN_*`` constants.
    """

    path = Path(target).expanduser()
    if not path.exists():
        return OPEN_MISSING

    if paths.is_windows():
        # explorer wants the switch and the path as one token.
        return OPEN_OK if _launch(["explorer", f"/select,{path}"]) else OPEN_FAILED
    if paths.is_macos():
        return OPEN_OK if _launch(["open", "-R", str(path)]) else OPEN_FAILED

    # The freedesktop interface selects the file; not every desktop provides it,
    # so fall back to just opening the containing folder.
    selected = _launch(
        [
            "dbus-send",
            "--session",
            "--dest=org.freedesktop.FileManager1",
            "--type=method_call",
            "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1.ShowItems",
            f"array:string:file://{path}",
            "string:",
        ]
    )
    if selected:
        return OPEN_OK
    parent = path if path.is_dir() else path.parent
    return OPEN_OK if _launch(["xdg-open", str(parent)]) else OPEN_FAILED


def reveal_repo_file(repo: Path | str, relative: str) -> str:
    """
    Opens the file manager with a repository file selected.

    Args:
        repo: Working tree root.
        relative: Path relative to that root.

    Returns:
        str: One of the ``OPEN_*`` constants.
    """

    resolved = _resolve_inside(repo, relative)
    if resolved is None:
        return OPEN_OUTSIDE
    return reveal_path(resolved)


def open_url(url: str) -> str:
    """
    Opens a web address in the default browser.

    Only ``http`` and ``https`` are allowed. Anything else could name a handler
    that does something other than show a page.

    Args:
        url: Address to open.

    Returns:
        str: One of the ``OPEN_*`` constants.
    """

    candidate = url.strip()
    lowered = candidate.lower()
    if not lowered.startswith(("http://", "https://")):
        return OPEN_REFUSED
    if any(ord(char) < 0x20 for char in candidate):
        return OPEN_REFUSED

    if paths.is_windows():
        try:
            os.startfile(candidate)  # type: ignore[attr-defined]  # noqa: S606 - Windows only
        except (OSError, AttributeError):
            return OPEN_FAILED
        return OPEN_OK
    if paths.is_macos():
        return OPEN_OK if _launch(["open", candidate]) else OPEN_FAILED
    return OPEN_OK if _launch(["xdg-open", candidate]) else OPEN_FAILED


def web_url_for_remote(remote: str) -> str:
    """
    Turns a git remote URL into a browsable web address.

    Args:
        remote: Remote URL as configured.

    Returns:
        str: An ``https`` address, empty when none can be derived.
    """

    slug = github_slug(remote)
    if slug is not None:
        owner, repo = slug
        return f"https://github.com/{owner}/{repo}"

    candidate = remote.strip()
    if candidate.lower().startswith("https://"):
        return candidate[:-4] if candidate.endswith(".git") else candidate
    # scp-style: git@host:owner/repo.git
    if "@" in candidate and ":" in candidate and not candidate.lower().startswith(("ssh://", "http")):
        host_part, _, path_part = candidate.partition(":")
        host = host_part.rsplit("@", 1)[-1]
        path_part = path_part[:-4] if path_part.endswith(".git") else path_part
        if host and path_part:
            return f"https://{host}/{path_part}"
    return ""
