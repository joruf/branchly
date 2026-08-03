"""
Tests for the repository, category and sort models.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from models.category import Category, normalize_category_name
from models.repository import RepoEntry, RepoStatus
from models.sort import (
    DEFAULT_SORT_MODE,
    SORT_CHANGES_FIRST,
    SORT_MANUAL,
    SORT_NAME_ASC,
    SORT_NAME_DESC,
    SORT_NEWEST_COMMIT,
    SORT_RECENT_OPENED,
    VALID_SORT_MODES,
    normalize_sort_mode,
    sort_entries,
)


def _entry(name: str, **kwargs) -> RepoEntry:
    """
    Builds an entry with a throwaway path.

    Args:
        name: Display name.
        **kwargs: Extra RepoEntry fields.

    Returns:
        RepoEntry: Entry for use in sort assertions.
    """

    return RepoEntry(path=Path("/tmp") / name, name=name, **kwargs)


class RepoStatusTests(unittest.TestCase):
    def test_fresh_status_is_not_scanned(self) -> None:
        status = RepoStatus()
        self.assertFalse(status.was_scanned)
        self.assertFalse(status.is_dirty)
        self.assertFalse(status.has_conflicts)

    def test_dirty_detection(self) -> None:
        self.assertTrue(RepoStatus(changed_files=1).is_dirty)
        self.assertTrue(RepoStatus(staged_files=1).is_dirty)
        self.assertTrue(RepoStatus(conflicted_files=1).is_dirty)

    def test_conflict_detection(self) -> None:
        self.assertTrue(RepoStatus(conflicted_files=2).has_conflicts)

    def test_round_trip(self) -> None:
        status = RepoStatus(branch="main", ahead=2, behind=1, incoming=3, checked_at=1.5, last_commit_at=99.0)
        restored = RepoStatus.from_dict(status.to_dict())
        self.assertEqual(status.to_dict(), restored.to_dict())

    def test_from_dict_survives_garbage(self) -> None:
        for value in (None, [], "text", 5):
            self.assertEqual("", RepoStatus.from_dict(value).branch)

    def test_negative_counts_are_dropped(self) -> None:
        status = RepoStatus.from_dict({"changed_files": -4, "ahead": "many"})
        self.assertEqual(0, status.changed_files)
        self.assertEqual(0, status.ahead)


class RepoEntryTests(unittest.TestCase):
    def test_name_defaults_to_directory_name(self) -> None:
        entry = RepoEntry(path=Path("/home/x/Applications/pmtool"))
        self.assertEqual("pmtool", entry.name)

    def test_explicit_name_wins(self) -> None:
        entry = RepoEntry(path=Path("/home/x/pmtool"), name="PM Tool")
        self.assertEqual("PM Tool", entry.name)

    def test_key_is_the_resolved_path(self) -> None:
        entry = RepoEntry(path=Path("/tmp/./demo"))
        self.assertEqual(str(Path("/tmp/demo")), entry.key)

    def test_round_trip(self) -> None:
        entry = RepoEntry(
            path=Path("/tmp/demo"),
            name="Demo",
            category="Work",
            favorite=True,
            order=3,
            remote_url="https://example.com/demo.git",
            last_opened=12.5,
        )
        restored = RepoEntry.from_dict(entry.to_dict())
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(entry.to_dict(), restored.to_dict())

    def test_entry_without_path_is_rejected(self) -> None:
        self.assertIsNone(RepoEntry.from_dict({"name": "no path"}))
        self.assertIsNone(RepoEntry.from_dict({"path": "   "}))
        self.assertIsNone(RepoEntry.from_dict("nonsense"))


class CategoryTests(unittest.TestCase):
    def test_unnamed_category_is_the_catch_all(self) -> None:
        self.assertTrue(Category(name="").is_uncategorized)
        self.assertFalse(Category(name="Work").is_uncategorized)

    def test_catch_all_sorts_last(self) -> None:
        groups = [Category(name=""), Category(name="Work", order=5), Category(name="Alpha", order=1)]
        ordered = [group.name for group in sorted(groups, key=lambda item: item.sort_key)]
        self.assertEqual(["Alpha", "Work", ""], ordered)

    def test_name_normalization_collapses_whitespace(self) -> None:
        self.assertEqual("Client Work", normalize_category_name("  Client   Work \n"))
        self.assertEqual("", normalize_category_name("   "))
        self.assertEqual("", normalize_category_name(None))

    def test_round_trip(self) -> None:
        category = Category(name="Work", collapsed=True, order=2)
        restored = Category.from_dict(category.to_dict())
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(category.to_dict(), restored.to_dict())

    def test_malformed_category_is_rejected(self) -> None:
        self.assertIsNone(Category.from_dict({"collapsed": True}))
        self.assertIsNone(Category.from_dict(None))


class SortTests(unittest.TestCase):
    def test_normalize_accepts_every_valid_mode(self) -> None:
        for mode in VALID_SORT_MODES:
            self.assertEqual(mode, normalize_sort_mode(mode))

    def test_normalize_rejects_junk(self) -> None:
        for value in (None, "", "random", 3, []):
            self.assertEqual(DEFAULT_SORT_MODE, normalize_sort_mode(value))

    def test_favorites_always_come_first(self) -> None:
        entries = [_entry("aaa"), _entry("zzz", favorite=True)]
        ordered = [item.name for item in sort_entries(entries, SORT_NAME_ASC)]
        self.assertEqual(["zzz", "aaa"], ordered)

    def test_favorites_come_first_in_descending_order_too(self) -> None:
        entries = [_entry("zzz"), _entry("aaa", favorite=True)]
        ordered = [item.name for item in sort_entries(entries, SORT_NAME_DESC)]
        self.assertEqual(["aaa", "zzz"], ordered)

    def test_name_ascending(self) -> None:
        entries = [_entry("Charlie"), _entry("alpha"), _entry("Bravo")]
        ordered = [item.name for item in sort_entries(entries, SORT_NAME_ASC)]
        self.assertEqual(["alpha", "Bravo", "Charlie"], ordered)

    def test_name_descending(self) -> None:
        entries = [_entry("alpha"), _entry("Bravo"), _entry("Charlie")]
        ordered = [item.name for item in sort_entries(entries, SORT_NAME_DESC)]
        self.assertEqual(["Charlie", "Bravo", "alpha"], ordered)

    def test_newest_commit_first(self) -> None:
        old = _entry("old")
        old.status.last_commit_at = 100.0
        new = _entry("new")
        new.status.last_commit_at = 900.0
        ordered = [item.name for item in sort_entries([old, new], SORT_NEWEST_COMMIT)]
        self.assertEqual(["new", "old"], ordered)

    def test_never_scanned_entries_sort_after_scanned_ones(self) -> None:
        scanned = _entry("scanned")
        scanned.status.last_commit_at = 10.0
        unscanned = _entry("unscanned")
        ordered = [item.name for item in sort_entries([unscanned, scanned], SORT_NEWEST_COMMIT)]
        self.assertEqual(["scanned", "unscanned"], ordered)

    def test_recently_opened_first(self) -> None:
        a = _entry("a", last_opened=5.0)
        b = _entry("b", last_opened=50.0)
        ordered = [item.name for item in sort_entries([a, b], SORT_RECENT_OPENED)]
        self.assertEqual(["b", "a"], ordered)

    def test_changes_first_ranks_conflicts_above_dirty(self) -> None:
        clean = _entry("clean")
        dirty = _entry("dirty")
        dirty.status.changed_files = 2
        conflicted = _entry("conflicted")
        conflicted.status.conflicted_files = 1
        unpushed = _entry("unpushed")
        unpushed.status.ahead = 3
        ordered = [item.name for item in sort_entries([clean, unpushed, dirty, conflicted], SORT_CHANGES_FIRST)]
        self.assertEqual(["conflicted", "dirty", "unpushed", "clean"], ordered)

    def test_manual_order(self) -> None:
        entries = [_entry("third", order=3), _entry("first", order=1), _entry("second", order=2)]
        ordered = [item.name for item in sort_entries(entries, SORT_MANUAL)]
        self.assertEqual(["first", "second", "third"], ordered)

    def test_empty_input(self) -> None:
        self.assertEqual([], sort_entries([], SORT_NAME_ASC))

    def test_sorting_does_not_mutate_the_input(self) -> None:
        entries = [_entry("b"), _entry("a")]
        sort_entries(entries, SORT_NAME_ASC)
        self.assertEqual(["b", "a"], [item.name for item in entries])


if __name__ == "__main__":
    unittest.main()
