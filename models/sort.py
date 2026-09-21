"""
Sidebar sort orders.

Favorites always come first inside their category — that is the point of the
star and is not something a sort order may override. The chosen order only
decides the sequence *within* the favorite block and within the rest.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.repository import RepoEntry

SORT_NAME_ASC = "name_asc"
SORT_NAME_DESC = "name_desc"
SORT_NEWEST_COMMIT = "newest_commit"
SORT_RECENT_OPENED = "recent_opened"
SORT_CHANGES_FIRST = "changes_first"
SORT_MANUAL = "manual"

DEFAULT_SORT_MODE = SORT_NAME_ASC

VALID_SORT_MODES: tuple[str, ...] = (
    SORT_NAME_ASC,
    SORT_NAME_DESC,
    SORT_NEWEST_COMMIT,
    SORT_RECENT_OPENED,
    SORT_CHANGES_FIRST,
    SORT_MANUAL,
)

# Translation keys, so the settings dialog and the sidebar dropdown stay in sync
# with whatever is actually implemented here.
SORT_MODE_LABEL_KEYS: dict[str, str] = {
    SORT_NAME_ASC: "sort.name_asc",
    SORT_NAME_DESC: "sort.name_desc",
    SORT_NEWEST_COMMIT: "sort.newest_commit",
    SORT_RECENT_OPENED: "sort.recent_opened",
    SORT_CHANGES_FIRST: "sort.changes_first",
    SORT_MANUAL: "sort.manual",
}


def normalize_sort_mode(mode: str | None) -> str:
    """
    Maps arbitrary input onto a known sort mode.

    Args:
        mode: Candidate mode, possibly from a stale config file.

    Returns:
        str: A valid sort mode, falling back to the default.
    """

    if isinstance(mode, str):
        candidate = mode.strip().lower()
        if candidate in VALID_SORT_MODES:
            return candidate
    return DEFAULT_SORT_MODE


def _name_key(entry: RepoEntry) -> str:
    """
    Returns the case-insensitive display name used for alphabetic ordering.

    Args:
        entry: Repository entry.

    Returns:
        str: Lowercased display name.
    """

    return entry.name.lower()


def _sort_key(entry: RepoEntry, mode: str) -> tuple:
    """
    Builds the ordering key for one entry under a given mode.

    Entries that were never scanned sort after scanned ones for the
    activity-based modes instead of pretending to be the oldest, which would
    push fresh additions to the bottom for no reason the user can see.

    Args:
        entry: Repository entry.
        mode: Normalized sort mode.

    Returns:
        tuple: Comparable sort key.
    """

    name = _name_key(entry)
    if mode == SORT_NAME_DESC:
        # Negating a string is impossible, so descending is handled by the
        # caller via `reverse` on a name-only key.
        return (name,)
    if mode == SORT_NEWEST_COMMIT:
        stamp = entry.status.last_commit_at
        return (0 if stamp is not None else 1, -(stamp or 0.0), name)
    if mode == SORT_RECENT_OPENED:
        stamp = entry.last_opened
        return (0 if stamp is not None else 1, -(stamp or 0.0), name)
    if mode == SORT_CHANGES_FIRST:
        status = entry.status
        # Conflicts first, then dirty trees, then unpushed work, then the rest.
        rank = 3
        if status.has_conflicts:
            rank = 0
        elif status.is_dirty:
            rank = 1
        elif status.ahead or status.incoming or status.behind:
            rank = 2
        pending = status.changed_files + status.staged_files + status.conflicted_files
        return (rank, -pending, name)
    if mode == SORT_MANUAL:
        return (entry.order, name)
    return (name,)


def sort_entries(entries: Iterable[RepoEntry], mode: str | None = None) -> list[RepoEntry]:
    """
    Sorts repository entries for display inside a single category.

    Args:
        entries: Entries belonging to one category.
        mode: Sort mode to apply. Invalid values fall back to the default.

    Returns:
        list[RepoEntry]: Favorites first, each block ordered by ``mode``.
    """

    normalized = normalize_sort_mode(mode)
    reverse = normalized == SORT_NAME_DESC
    favorites: list[RepoEntry] = []
    others: list[RepoEntry] = []
    for entry in entries:
        (favorites if entry.favorite else others).append(entry)

    def _ordered(items: list[RepoEntry]) -> list[RepoEntry]:
        return sorted(items, key=lambda item: _sort_key(item, normalized), reverse=reverse)

    return _ordered(favorites) + _ordered(others)
