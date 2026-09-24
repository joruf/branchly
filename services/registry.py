"""
The list of repositories the user added, and their categories.

Persisted as ``repos.json`` next to the settings. Writes are atomic, and a
corrupt file degrades to an empty list rather than stopping the application: a
lost list is annoying, a program that will not start is worse.

Nothing here touches git. The registry only remembers *which* folders the user
cares about and how they want them arranged.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import paths
from gitops.runner import repository_root
from models.category import Category, normalize_category_name
from models.repository import UNCATEGORIZED, RepoEntry, RepoStatus
from models.sort import sort_entries
from services.discovery import read_origin_url, resolve_git_dir

ADD_OK = "ok"
ADD_NOT_A_REPOSITORY = "not_a_repository"
ADD_DUPLICATE = "duplicate"
ADD_UNREADABLE = "unreadable"


class Registry:
    """
    Holds the tracked repositories and their categories.

    The in-memory state is the source of truth while the app runs; ``save`` writes
    it out. Callers mutate through the methods here so that ordering and category
    bookkeeping stay consistent.
    """

    def __init__(self, path: Path | None = None) -> None:
        """
        Args:
            path: File to read and write. Defaults to the standard registry path.
        """

        self._path = path or paths.registry_path()
        self._entries: list[RepoEntry] = []
        self._categories: list[Category] = []
        # The catch-all group has no Category object of its own, so its folded
        # state is kept here.
        self._uncategorized_collapsed = False

    # ------------------------------------------------------------- persistence

    @property
    def path(self) -> Path:
        """
        Returns the file this registry is stored in.

        Returns:
            Path: Registry file path.
        """

        return self._path

    def load(self) -> None:
        """
        Reads the registry from disk, replacing the in-memory state.

        Returns:
            None
        """

        self._entries = []
        self._categories = []
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return
        try:
            data = json.loads(raw)
        except ValueError:
            return
        if not isinstance(data, dict):
            return

        for item in data.get("repositories", []) or []:
            entry = RepoEntry.from_dict(item)
            if entry is not None and not self._has(entry.key):
                self._entries.append(entry)
        for item in data.get("categories", []) or []:
            category = Category.from_dict(item)
            if (
                category is not None
                and not category.is_uncategorized
                and not self._find_category(category.name)
            ):
                self._categories.append(category)

        # A category referenced by a repository but missing from the list would
        # make that repository disappear from the sidebar.
        for entry in self._entries:
            if entry.category and not self._find_category(entry.category):
                self._categories.append(Category(name=entry.category, order=len(self._categories)))

    def save(self) -> bool:
        """
        Writes the registry to disk atomically.

        Returns:
            bool: True on success.
        """

        if not paths.ensure_dir(self._path.parent):
            return False
        payload = {
            "version": 1,
            "categories": [item.to_dict() for item in self._categories],
            "repositories": [item.to_dict() for item in self._entries],
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        temp = self._path.with_name(self._path.name + ".tmp")
        try:
            temp.write_text(text, encoding="utf-8")
            os.replace(temp, self._path)
        except OSError:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        return True

    # ---------------------------------------------------------------- entries

    @property
    def entries(self) -> list[RepoEntry]:
        """
        Returns every tracked repository.

        Returns:
            list[RepoEntry]: Entries in insertion order.
        """

        return list(self._entries)

    def _has(self, key: str) -> bool:
        """
        Reports whether a repository is already tracked.

        Args:
            key: Resolved path string.

        Returns:
            bool: True when present.
        """

        return any(entry.key == key for entry in self._entries)

    def find(self, path: Path | str) -> RepoEntry | None:
        """
        Looks up an entry by path.

        Args:
            path: Working tree path.

        Returns:
            RepoEntry | None: The entry, or None when not tracked.
        """

        try:
            key = str(Path(path).expanduser().resolve())
        except OSError:
            key = str(path)
        for entry in self._entries:
            if entry.key == key:
                return entry
        return None

    def add(self, path: Path | str, category: str = UNCATEGORIZED) -> tuple[str, RepoEntry | None]:
        """
        Adds a repository, resolving a subdirectory to its working tree root.

        Args:
            path: Any directory inside the repository.
            category: Category to file it under.

        Returns:
            tuple[str, RepoEntry | None]: One of the ``ADD_*`` constants and the
                entry — the existing one when it was already tracked, so the
                caller can select it instead of showing an error.
        """

        candidate = Path(path).expanduser()
        if not candidate.is_dir():
            return ADD_UNREADABLE, None
        root = repository_root(candidate)
        if root is None:
            return ADD_NOT_A_REPOSITORY, None

        existing = self.find(root)
        if existing is not None:
            return ADD_DUPLICATE, existing

        name = normalize_category_name(category)
        if name and not self._find_category(name):
            self.add_category(name)
        entry = RepoEntry(path=root, category=name, order=len(self._entries))
        # Read straight from the config rather than waiting for the first scan.
        # The sidebar tooltip names the server a project belongs to, and a
        # freshly added project would otherwise claim to have none until a scan
        # got round to it.
        git_dir = resolve_git_dir(root)
        if git_dir is not None:
            entry.remote_url = read_origin_url(git_dir)
        self._entries.append(entry)
        return ADD_OK, entry

    def remove(self, entry: RepoEntry) -> bool:
        """
        Forgets a repository. Nothing on disk is touched.

        Args:
            entry: Entry to drop.

        Returns:
            bool: True when it was present.
        """

        before = len(self._entries)
        self._entries = [item for item in self._entries if item.key != entry.key]
        return len(self._entries) != before

    def set_favorite(self, entry: RepoEntry, favorite: bool) -> None:
        """
        Pins or unpins an entry inside its category.

        Args:
            entry: Entry to change.
            favorite: Whether it should be pinned.

        Returns:
            None
        """

        entry.favorite = favorite

    def toggle_favorite(self, entry: RepoEntry) -> bool:
        """
        Flips the pinned state.

        Args:
            entry: Entry to change.

        Returns:
            bool: The new state.
        """

        entry.favorite = not entry.favorite
        return entry.favorite

    def rename(self, entry: RepoEntry, name: str) -> bool:
        """
        Changes the display name. The folder is not renamed.

        Args:
            entry: Entry to change.
            name: New display name.

        Returns:
            bool: True when a usable name was given.
        """

        cleaned = " ".join(name.split())
        if not cleaned:
            return False
        entry.name = cleaned
        return True

    def assign_category(self, entry: RepoEntry, category: str) -> None:
        """
        Moves an entry into a category, creating it when necessary.

        Args:
            entry: Entry to move.
            category: Category name, empty for the catch-all group.

        Returns:
            None
        """

        name = normalize_category_name(category)
        if name and not self._find_category(name):
            self.add_category(name)
        entry.category = name

    def touch(self, entry: RepoEntry) -> None:
        """
        Records that an entry was just opened.

        Args:
            entry: Entry that was selected.

        Returns:
            None
        """

        entry.last_opened = time.time()

    def update_status(self, entry: RepoEntry, status: RepoStatus) -> None:
        """
        Stores a fresh scan result on an entry.

        Args:
            entry: Entry to update.
            status: New status.

        Returns:
            None
        """

        entry.status = status

    def set_manual_order(self, ordered: Iterable[RepoEntry]) -> None:
        """
        Records a hand-arranged order for the manual sort mode.

        Args:
            ordered: Entries in the order the user dragged them into.

        Returns:
            None
        """

        for position, entry in enumerate(ordered):
            found = self.find(entry.path)
            if found is not None:
                found.order = position

    # ------------------------------------------------------------- categories

    @property
    def categories(self) -> list[Category]:
        """
        Returns the named categories, in display order.

        The catch-all group is not in this list — it is implicit and always last.

        Returns:
            list[Category]: Named categories.
        """

        return sorted(self._categories, key=lambda item: item.sort_key)

    def _find_category(self, name: str) -> Category | None:
        """
        Looks up a category by name.

        Args:
            name: Category name.

        Returns:
            Category | None: The category, or None when absent.
        """

        cleaned = normalize_category_name(name)
        for category in self._categories:
            if category.name == cleaned:
                return category
        return None

    def add_category(self, name: str) -> Category | None:
        """
        Creates a category.

        Args:
            name: Category name.

        Returns:
            Category | None: The new category, or None when the name was empty or
                already taken.
        """

        cleaned = normalize_category_name(name)
        if not cleaned or self._find_category(cleaned):
            return None
        category = Category(name=cleaned, order=len(self._categories))
        self._categories.append(category)
        return category

    def rename_category(self, old_name: str, new_name: str) -> bool:
        """
        Renames a category and moves its repositories along with it.

        Args:
            old_name: Current name.
            new_name: New name.

        Returns:
            bool: True when the rename happened.
        """

        category = self._find_category(old_name)
        cleaned = normalize_category_name(new_name)
        if category is None or not cleaned:
            return False
        if cleaned != category.name and self._find_category(cleaned):
            return False
        previous = category.name
        category.name = cleaned
        for entry in self._entries:
            if entry.category == previous:
                entry.category = cleaned
        return True

    def remove_category(self, name: str) -> bool:
        """
        Deletes a category. Its repositories stay, moving to the catch-all group.

        Args:
            name: Category name.

        Returns:
            bool: True when the category existed.
        """

        category = self._find_category(name)
        if category is None:
            return False
        self._categories = [item for item in self._categories if item.name != category.name]
        for entry in self._entries:
            if entry.category == category.name:
                entry.category = UNCATEGORIZED
        return True

    def set_collapsed(self, name: str, collapsed: bool) -> None:
        """
        Remembers whether a category is folded shut.

        Args:
            name: Category name, empty for the catch-all group.
            collapsed: Whether it is folded.

        Returns:
            None
        """

        cleaned = normalize_category_name(name)
        if not cleaned:
            self._uncategorized_collapsed = collapsed
            return
        category = self._find_category(cleaned)
        if category is not None:
            category.collapsed = collapsed

    def is_collapsed(self, name: str) -> bool:
        """
        Reports whether a category is folded shut.

        Args:
            name: Category name, empty for the catch-all group.

        Returns:
            bool: True when folded.
        """

        cleaned = normalize_category_name(name)
        if not cleaned:
            return self._uncategorized_collapsed
        category = self._find_category(cleaned)
        return bool(category and category.collapsed)

    def set_all_collapsed(self, collapsed: bool) -> None:
        """
        Folds or unfolds every category at once.

        Args:
            collapsed: Whether everything should be folded.

        Returns:
            None
        """

        for category in self._categories:
            category.collapsed = collapsed
        self._uncategorized_collapsed = collapsed

    # ------------------------------------------------------------- grouping

    def grouped(self, sort_mode: str, query: str = "") -> list[tuple[Category, list[RepoEntry]]]:
        """
        Arranges entries into the groups the sidebar renders.

        Args:
            sort_mode: Sort order applied within each group.
            query: Case-insensitive filter on the display name and path. A search
                looks through every group, including folded ones — hiding a match
                because its category happens to be shut would be baffling.

        Returns:
            list[tuple[Category, list[RepoEntry]]]: Groups in display order.
                Empty named categories are kept so the user can drop entries into
                them; the catch-all group is dropped when it holds nothing.
        """

        needle = query.strip().lower()

        def _matches(entry: RepoEntry) -> bool:
            if not needle:
                return True
            return needle in entry.name.lower() or needle in str(entry.path).lower()

        buckets: dict[str, list[RepoEntry]] = {"": []}
        for category in self._categories:
            buckets.setdefault(category.name, [])
        for entry in self._entries:
            if not _matches(entry):
                continue
            buckets.setdefault(entry.category, []).append(entry)

        groups: list[tuple[Category, list[RepoEntry]]] = []
        for category in self.categories:
            groups.append((category, sort_entries(buckets.get(category.name, []), sort_mode)))
        catch_all = sort_entries(buckets.get("", []), sort_mode)
        if catch_all:
            groups.append((Category(name=UNCATEGORIZED), catch_all))
        return groups

    def summary(self) -> dict[str, int]:
        """
        Counts what needs attention across every repository.

        Returns:
            dict[str, int]: Keys ``changed``, ``incoming``, ``conflicts``,
                ``unpushed`` and ``total``.
        """

        counts = {"changed": 0, "incoming": 0, "conflicts": 0, "unpushed": 0, "total": len(self._entries)}
        for entry in self._entries:
            status = entry.status
            if status.has_conflicts:
                counts["conflicts"] += 1
            if status.is_dirty:
                counts["changed"] += 1
            if status.incoming or status.behind:
                counts["incoming"] += 1
            if status.ahead:
                counts["unpushed"] += 1
        return counts


def load_registry(path: Path | None = None) -> Registry:
    """
    Builds a registry and loads it from disk.

    Args:
        path: File to read. Defaults to the standard registry path.

    Returns:
        Registry: Loaded registry.
    """

    registry = Registry(path)
    registry.load()
    return registry


def registry_payload(registry: Registry) -> dict[str, Any]:
    """
    Returns what the registry would write, without writing it.

    Used by tests and by the export in the settings dialog.

    Args:
        registry: Registry to serialize.

    Returns:
        dict[str, Any]: JSON-compatible payload.
    """

    return {
        "version": 1,
        "categories": [item.to_dict() for item in registry.categories],
        "repositories": [item.to_dict() for item in registry.entries],
    }
