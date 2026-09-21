"""
Data model for a repository tracked by Branchly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

UNCATEGORIZED = ""


@dataclass(slots=True)
class RepoStatus:
    """
    Last known state of a repository, as shown in the sidebar badges.

    All counts default to zero and ``checked_at`` to None, so an entry that was
    never scanned renders as "unknown" rather than as "clean".

    Attributes:
        branch: Current branch name, or a short SHA when HEAD is detached.
        detached: Whether HEAD points at a commit instead of a branch.
        changed_files: Number of modified, added, deleted and untracked files.
        staged_files: Number of files currently staged.
        conflicted_files: Number of files with unresolved merge conflicts.
        ahead: Commits the local branch has that the remote does not.
        behind: Commits the remote branch has that the local one does not.
        incoming: Commits available on the remote as seen by the last online
            check. Differs from ``behind``, which only reflects what the local
            tracking ref already knows about.
        open_pull_requests: Open pull requests reported by the GitHub API.
        check_state: Aggregated CI state of the tip commit, or None.
        last_commit_at: Author timestamp of the tip commit, used by the
            "newest first" sort order.
        checked_at: Unix timestamp of the last successful scan, or None.
        online_checked_at: Unix timestamp of the last successful online check.
        error_key: Translation key for why the last scan failed, or None. Not
            persisted: a failure is about the last attempt, not about the entry.
    """

    branch: str = ""
    detached: bool = False
    changed_files: int = 0
    staged_files: int = 0
    conflicted_files: int = 0
    ahead: int = 0
    behind: int = 0
    incoming: int = 0
    open_pull_requests: int = 0
    check_state: str | None = None
    last_commit_at: float | None = None
    checked_at: float | None = None
    online_checked_at: float | None = None
    error_key: str | None = None

    @property
    def is_dirty(self) -> bool:
        """
        Returns whether the working tree holds uncommitted work.

        Returns:
            bool: True when files are changed, staged or conflicted.
        """

        return bool(self.changed_files or self.staged_files or self.conflicted_files)

    @property
    def has_conflicts(self) -> bool:
        """
        Returns whether a merge is currently unresolved.

        Returns:
            bool: True when at least one file is conflicted.
        """

        return self.conflicted_files > 0

    @property
    def was_scanned(self) -> bool:
        """
        Returns whether this status carries real data.

        Returns:
            bool: True once a scan has completed at least once.
        """

        return self.checked_at is not None

    def to_dict(self) -> dict[str, Any]:
        """
        Serializes the status for ``repos.json``.

        Returns:
            dict[str, Any]: JSON-compatible mapping.
        """

        return {
            "branch": self.branch,
            "detached": self.detached,
            "changed_files": self.changed_files,
            "staged_files": self.staged_files,
            "conflicted_files": self.conflicted_files,
            "ahead": self.ahead,
            "behind": self.behind,
            "incoming": self.incoming,
            "open_pull_requests": self.open_pull_requests,
            "check_state": self.check_state,
            "last_commit_at": self.last_commit_at,
            "checked_at": self.checked_at,
            "online_checked_at": self.online_checked_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> RepoStatus:
        """
        Rebuilds a status from stored data, ignoring anything malformed.

        Args:
            data: Mapping read from ``repos.json``.

        Returns:
            RepoStatus: Restored status, or an empty one on bad input.
        """

        if not isinstance(data, dict):
            return cls()

        def _int(key: str) -> int:
            value = data.get(key)
            return value if isinstance(value, int) and value >= 0 else 0

        def _float(key: str) -> float | None:
            value = data.get(key)
            return float(value) if isinstance(value, (int, float)) else None

        branch = data.get("branch")
        check_state = data.get("check_state")
        return cls(
            branch=branch if isinstance(branch, str) else "",
            detached=bool(data.get("detached")),
            changed_files=_int("changed_files"),
            staged_files=_int("staged_files"),
            conflicted_files=_int("conflicted_files"),
            ahead=_int("ahead"),
            behind=_int("behind"),
            incoming=_int("incoming"),
            open_pull_requests=_int("open_pull_requests"),
            check_state=check_state if isinstance(check_state, str) else None,
            last_commit_at=_float("last_commit_at"),
            checked_at=_float("checked_at"),
            online_checked_at=_float("online_checked_at"),
        )


@dataclass(slots=True)
class RepoEntry:
    """
    A repository the user added to Branchly.

    Attributes:
        path: Absolute path of the working tree.
        name: Display name, defaulting to the directory name.
        category: Category name, or an empty string for uncategorized.
        favorite: Whether the entry is pinned to the top of its category.
        order: Manual position inside its category, used as a sort tiebreaker.
        remote_url: Fetch URL of ``origin``, or an empty string.
        last_opened: Unix timestamp of the last time it was selected.
        status: Last known scan result.
        deselected_paths: Files the user unticked in the commit box. Stored
            rather than the ticked ones, because everything is ticked by default
            and only the exceptions are worth remembering: a file added to this
            set stays out of the next commit across a restart, and a new file
            appearing in the working tree is ticked without having to be listed.
    """

    path: Path
    name: str = ""
    category: str = UNCATEGORIZED
    favorite: bool = False
    order: int = 0
    remote_url: str = ""
    last_opened: float | None = None
    status: RepoStatus = field(default_factory=RepoStatus)
    deselected_paths: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        """
        Normalizes the path and fills in a display name.

        Returns:
            None
        """

        self.path = Path(self.path).expanduser()
        if not self.name:
            self.name = self.path.name or str(self.path)

    @property
    def key(self) -> str:
        """
        Returns the identity of this entry inside the registry.

        The resolved path is the identity: the same working tree must not be
        addable twice under two display names.

        Returns:
            str: Absolute path as a string.
        """

        try:
            return str(self.path.resolve())
        except OSError:
            return str(self.path)

    def forget_selection(self, paths: list[str]) -> None:
        """
        Drops paths from the remembered deselection.

        Called after a commit: whatever went in is settled, and a stale entry
        would keep unticking a file that has nothing to do with the old change.

        Args:
            paths: Repository-relative paths to forget.

        Returns:
            None
        """

        self.deselected_paths.difference_update(paths)

    @property
    def exists(self) -> bool:
        """
        Returns whether the working tree is still present on disk.

        Returns:
            bool: True when the path holds a ``.git`` entry.
        """

        return (self.path / ".git").exists()

    def to_dict(self) -> dict[str, Any]:
        """
        Serializes the entry for ``repos.json``.

        Returns:
            dict[str, Any]: JSON-compatible mapping.
        """

        return {
            "path": str(self.path),
            "name": self.name,
            "category": self.category,
            "favorite": self.favorite,
            "order": self.order,
            "remote_url": self.remote_url,
            "last_opened": self.last_opened,
            "status": self.status.to_dict(),
            "deselected_paths": sorted(self.deselected_paths),
        }

    @classmethod
    def from_dict(cls, data: Any) -> RepoEntry | None:
        """
        Rebuilds an entry from stored data.

        Args:
            data: Mapping read from ``repos.json``.

        Returns:
            RepoEntry | None: Restored entry, or None when the path is missing
                or unusable, so a corrupt file drops entries instead of failing.
        """

        if not isinstance(data, dict):
            return None
        raw_path = data.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None

        name = data.get("name")
        category = data.get("category")
        order = data.get("order")
        remote_url = data.get("remote_url")
        last_opened = data.get("last_opened")
        raw_deselected = data.get("deselected_paths")
        deselected = (
            {item for item in raw_deselected if isinstance(item, str) and item}
            if isinstance(raw_deselected, list)
            else set()
        )
        return cls(
            path=Path(raw_path),
            name=name if isinstance(name, str) else "",
            category=category if isinstance(category, str) else UNCATEGORIZED,
            favorite=bool(data.get("favorite")),
            order=order if isinstance(order, int) else 0,
            remote_url=remote_url if isinstance(remote_url, str) else "",
            last_opened=float(last_opened) if isinstance(last_opened, (int, float)) else None,
            status=RepoStatus.from_dict(data.get("status")),
            deselected_paths=deselected,
        )
