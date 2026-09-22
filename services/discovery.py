"""
Finding git repositories on disk.

Branchly's project list is normally filled one repository at a time. On a fresh
install that is a lot of clicking for something the filesystem already knows, so
this walks a folder and reports every working tree it finds.

Three decisions shape the whole module:

* **The walk never starts git.** Asking git about every directory would mean one
  process per candidate, and a home directory holds tens of thousands. A working
  tree is recognised by its ``.git`` entry, and the branch and the remote are read
  straight out of ``.git/HEAD`` and ``.git/config``, which is what those files are
  for. A scan of a large home directory stays a few seconds instead of minutes.
* **A repository is not descended into.** Once a working tree is found, the walk
  does not look inside it. Submodules and vendored checkouts would otherwise show
  up as projects of their own, and nobody wants forty entries for one project.
* **What cannot be read is skipped, not reported.** A folder without permission,
  a broken symlink, a directory that vanished mid-walk: none of those are the
  user's problem and none of them may stop the scan.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

# Directories that never hold a project worth listing and often hold thousands of
# files. Skipping them by name is what keeps a scan of a home directory quick.
SKIP_NAMES = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".gradle",
        ".m2",
        ".npm",
        ".cargo",
        ".rustup",
        ".cache",
        ".local",
        ".steam",
        ".wine",
        "snap",
        "Trash",
        "site-packages",
        "vendor",
        "target",
        "build",
        "dist",
    }
)

# How deep below a chosen folder the walk goes. Projects live near the top; a
# repository twenty levels down is a vendored copy, not something to register.
DEFAULT_MAX_DEPTH = 6


@dataclass(frozen=True, slots=True)
class Candidate:
    """
    One working tree the scan found.

    Attributes:
        path: Working tree root, the folder holding the ``.git`` entry.
        name: Folder name, used as the display name.
        branch: Branch the repository is on, or a short commit id when it is
            detached. Empty when it could not be read.
        remote_url: Fetch URL of ``origin``, empty when there is none.
        known: Whether Branchly already has this repository.
    """

    path: Path
    name: str = ""
    branch: str = ""
    remote_url: str = ""
    known: bool = False

    @property
    def path_key(self) -> str:
        """
        Returns the identity this candidate would have in the registry.

        Returns:
            str: Absolute path as a string, matching ``RepoEntry.key``.
        """

        return str(self.path)

    @property
    def display_path(self) -> str:
        """
        Returns the path with the home directory shortened.

        Returns:
            str: The path, with ``~`` standing in for the home directory.
        """

        text = str(self.path)
        home = str(Path.home())
        return f"~{text[len(home):]}" if text.startswith(home) else text


def resolve_git_dir(root: Path) -> Path | None:
    """
    Finds the git directory belonging to a working tree.

    ``.git`` is usually a directory. For a submodule or a linked worktree it is a
    file holding ``gitdir: <path>``, and the branch and the remote live there
    instead.

    Args:
        root: Working tree root.

    Returns:
        Path | None: The git directory, or None when it cannot be resolved.
    """

    marker = root / ".git"
    try:
        if marker.is_dir():
            return marker
        if not marker.is_file():
            return None
        content = marker.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None

    if not content.startswith("gitdir:"):
        return None
    target = Path(content[len("gitdir:"):].strip())
    if not target.is_absolute():
        target = (root / target).resolve()
    return target if target.is_dir() else None


def read_branch(git_dir: Path) -> str:
    """
    Reads the checked-out branch without starting git.

    Args:
        git_dir: The repository's git directory.

    Returns:
        str: Branch name, a short commit id when detached, or an empty string.
    """

    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if head.startswith("ref:"):
        reference = head[4:].strip()
        # Only the "refs/heads/" prefix goes. Splitting on the last slash would
        # turn "feature/login" into "login", which is a different branch name.
        prefix = "refs/heads/"
        if reference.startswith(prefix):
            return reference[len(prefix):]
        return reference
    # A detached HEAD holds the commit id itself.
    return head[:8] if head else ""


def read_origin_url(git_dir: Path) -> str:
    """
    Reads the fetch URL of ``origin`` out of the repository's config.

    A hand-rolled section scan rather than ``configparser``: a git config may
    hold keys that ``configparser`` refuses, and failing to show a remote must
    not fail the scan.

    Args:
        git_dir: The repository's git directory.

    Returns:
        str: The URL, empty when there is no ``origin``.
    """

    try:
        lines = (git_dir / "config").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""

    inside = False
    for raw in lines:
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            inside = line.replace(" ", "").lower() in {'[remote"origin"]', "[remoteorigin]"}
            continue
        if not inside or "=" not in line:
            continue
        key, _sep, value = line.partition("=")
        if key.strip().lower() == "url":
            return value.strip()
    return ""


def is_working_tree(entry_path: Path) -> bool:
    """
    Reports whether a directory is a working tree.

    Args:
        entry_path: Directory to test.

    Returns:
        bool: True when it holds a ``.git`` entry.
    """

    marker = entry_path / ".git"
    try:
        return marker.is_dir() or marker.is_file()
    except OSError:
        return False


def scan(
    roots: Iterable[Path | str],
    known_keys: set[str] | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    on_progress: Callable[[int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[Candidate]:
    """
    Walks folders and reports every working tree below them.

    Args:
        roots: Folders to search.
        known_keys: Registry keys Branchly already has, so the result can mark
            them rather than offering them again.
        max_depth: How many levels below each root to look.
        on_progress: Called with the number of folders looked at and the folder
            currently being looked at, so a dialog can show something moving.
        should_cancel: Asked regularly; the walk stops when it returns True.

    Returns:
        list[Candidate]: Working trees, sorted by path. Already known ones are
            included and marked, because a list missing them looks like the scan
            failed to find projects the user can see in the sidebar.
    """

    already = known_keys or set()
    found: dict[str, Candidate] = {}
    looked_at = 0

    # An explicit stack rather than recursion: a deep tree must not depend on the
    # interpreter's recursion limit.
    stack: list[tuple[Path, int]] = []
    seen_dirs: set[str] = set()
    for root in roots:
        start = Path(root).expanduser()
        if start.is_dir():
            stack.append((start, 0))

    while stack:
        if should_cancel is not None and should_cancel():
            break
        current, depth = stack.pop()

        try:
            key = str(current.resolve())
        except OSError:
            continue
        # A symlink pointing back up would otherwise walk the same tree twice.
        if key in seen_dirs:
            continue
        seen_dirs.add(key)

        looked_at += 1
        if on_progress is not None and looked_at % 25 == 0:
            on_progress(looked_at, str(current))

        if is_working_tree(current):
            candidate = describe(current, already)
            if candidate is not None:
                found[candidate.path_key] = candidate
            # Nothing below a working tree is its own project.
            continue

        if depth >= max_depth:
            continue

        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.name in SKIP_NAMES:
                        continue
                    try:
                        # follow_symlinks=False keeps the walk inside one tree.
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    stack.append((Path(entry.path), depth + 1))
        except (OSError, PermissionError):
            # Unreadable folders are skipped rather than reported: a home
            # directory always holds a few and none of them is the user's doing.
            continue

    if on_progress is not None:
        on_progress(looked_at, "")
    return sorted(found.values(), key=lambda item: str(item.path).lower())


def describe(root: Path, known_keys: set[str]) -> Candidate | None:
    """
    Builds the entry for one working tree.

    Args:
        root: Working tree root.
        known_keys: Registry keys Branchly already has.

    Returns:
        Candidate | None: The candidate, or None when the path is unusable.
    """

    try:
        resolved = root.resolve()
    except OSError:
        return None

    git_dir = resolve_git_dir(root)
    branch = read_branch(git_dir) if git_dir is not None else ""
    remote = read_origin_url(git_dir) if git_dir is not None else ""
    return Candidate(
        path=resolved,
        name=resolved.name or str(resolved),
        branch=branch,
        remote_url=remote,
        known=str(resolved) in known_keys,
    )


def default_roots() -> list[Path]:
    """
    Returns the folders a scan starts from when the user names none.

    Returns:
        list[Path]: The home directory. Everything worth finding is below it, and
            starting anywhere higher would walk system folders that hold no
            projects of the user's.
    """

    return [Path.home()]
