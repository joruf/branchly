"""
Tests for the registry, the scanner and the desktop integration.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from models.repository import RepoStatus
from models.sort import SORT_NAME_ASC
from services import open_with, scanner
from services.registry import (
    ADD_DUPLICATE,
    ADD_NOT_A_REPOSITORY,
    ADD_OK,
    ADD_UNREADABLE,
    Registry,
    load_registry,
)
from tests.support import requires_git, temp_repo, temp_repo_pair


class RegistryPersistenceTests(unittest.TestCase):
    def test_missing_file_yields_an_empty_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = load_registry(Path(tmp) / "absent.json")
            self.assertEqual([], registry.entries)
            self.assertEqual([], registry.categories)

    def test_corrupt_file_yields_an_empty_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repos.json"
            target.write_text("{ broken", encoding="utf-8")
            self.assertEqual([], load_registry(target).entries)

    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "repos.json"
            registry = Registry(target)
            registry.add_category("Work")
            self.assertTrue(registry.save())

            reloaded = load_registry(target)
            self.assertEqual(["Work"], [item.name for item in reloaded.categories])

    def test_no_temp_file_is_left_behind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repos.json"
            Registry(target).save()
            leftovers = [item.name for item in Path(tmp).iterdir() if item.name.endswith(".tmp")]
            self.assertEqual([], leftovers)

    def test_written_file_is_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repos.json"
            registry = Registry(target)
            registry.add_category("Work")
            registry.save()
            data = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(1, data["version"])
            self.assertEqual("Work", data["categories"][0]["name"])

    def test_category_referenced_by_a_repository_is_restored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repos.json"
            # A file whose category list lost an entry must not hide the project.
            target.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "categories": [],
                        "repositories": [{"path": "/tmp/demo", "category": "Orphaned"}],
                    }
                ),
                encoding="utf-8",
            )
            registry = load_registry(target)
            self.assertEqual(["Orphaned"], [item.name for item in registry.categories])

    def test_duplicate_entries_in_the_file_are_collapsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repos.json"
            target.write_text(
                json.dumps(
                    {
                        "repositories": [{"path": "/tmp/demo"}, {"path": "/tmp/demo"}],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(1, len(load_registry(target).entries))


@requires_git
class RegistryEntryTests(unittest.TestCase):
    def test_adding_a_repository(self) -> None:
        with temp_repo() as repo:
            registry = Registry(Path(repo.root) / "registry.json")
            outcome, entry = registry.add(repo.root)
            self.assertEqual(ADD_OK, outcome)
            self.assertIsNotNone(entry)
            assert entry is not None
            self.assertEqual(repo.root.resolve(), entry.path.resolve())

    def test_adding_a_subdirectory_registers_the_root(self) -> None:
        with temp_repo() as repo:
            nested = repo.root / "src" / "deep"
            nested.mkdir(parents=True)
            registry = Registry()
            outcome, entry = registry.add(nested)
            self.assertEqual(ADD_OK, outcome)
            assert entry is not None
            self.assertEqual(repo.root.resolve(), entry.path.resolve())

    def test_adding_a_plain_folder_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outcome, entry = Registry().add(tmp)
            self.assertEqual(ADD_NOT_A_REPOSITORY, outcome)
            self.assertIsNone(entry)

    def test_adding_a_missing_folder_is_refused(self) -> None:
        outcome, entry = Registry().add("/nonexistent/branchly")
        self.assertEqual(ADD_UNREADABLE, outcome)
        self.assertIsNone(entry)

    def test_adding_twice_returns_the_existing_entry(self) -> None:
        with temp_repo() as repo:
            registry = Registry()
            _outcome, first = registry.add(repo.root)
            outcome, second = registry.add(repo.root)
            self.assertEqual(ADD_DUPLICATE, outcome)
            self.assertIs(first, second)
            self.assertEqual(1, len(registry.entries))

    def test_removing_an_entry(self) -> None:
        with temp_repo() as repo:
            registry = Registry()
            _outcome, entry = registry.add(repo.root)
            assert entry is not None
            self.assertTrue(registry.remove(entry))
            self.assertEqual([], registry.entries)
            self.assertTrue(repo.root.exists(), "removing must not touch the disk")

    def test_favorite_toggle(self) -> None:
        with temp_repo() as repo:
            registry = Registry()
            _outcome, entry = registry.add(repo.root)
            assert entry is not None
            self.assertTrue(registry.toggle_favorite(entry))
            self.assertFalse(registry.toggle_favorite(entry))

    def test_rename_keeps_the_path(self) -> None:
        with temp_repo() as repo:
            registry = Registry()
            _outcome, entry = registry.add(repo.root)
            assert entry is not None
            before = entry.path
            self.assertTrue(registry.rename(entry, "  My Project  "))
            self.assertEqual("My Project", entry.name)
            self.assertEqual(before, entry.path)

    def test_empty_rename_is_refused(self) -> None:
        with temp_repo() as repo:
            registry = Registry()
            _outcome, entry = registry.add(repo.root)
            assert entry is not None
            self.assertFalse(registry.rename(entry, "   "))


class RegistryCategoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = Registry(Path(tempfile.mkdtemp()) / "repos.json")

    def test_adding_a_category(self) -> None:
        created = self.registry.add_category("  Client   Work ")
        self.assertIsNotNone(created)
        assert created is not None
        self.assertEqual("Client Work", created.name)

    def test_duplicate_and_empty_names_are_refused(self) -> None:
        self.registry.add_category("Work")
        self.assertIsNone(self.registry.add_category("Work"))
        self.assertIsNone(self.registry.add_category("   "))

    def test_renaming_moves_the_repositories_along(self) -> None:
        self.registry.add_category("Old")
        entry = self.registry._entries  # noqa: SLF001 - direct setup for the test
        from models.repository import RepoEntry

        entry.append(RepoEntry(path=Path("/tmp/demo"), category="Old"))
        self.assertTrue(self.registry.rename_category("Old", "New"))
        self.assertEqual("New", self.registry.entries[0].category)

    def test_renaming_onto_an_existing_name_is_refused(self) -> None:
        self.registry.add_category("A")
        self.registry.add_category("B")
        self.assertFalse(self.registry.rename_category("A", "B"))

    def test_deleting_a_category_keeps_its_repositories(self) -> None:
        from models.repository import RepoEntry

        self.registry.add_category("Doomed")
        self.registry._entries.append(RepoEntry(path=Path("/tmp/demo"), category="Doomed"))  # noqa: SLF001
        self.assertTrue(self.registry.remove_category("Doomed"))
        self.assertEqual([], self.registry.categories)
        self.assertEqual("", self.registry.entries[0].category)

    def test_collapsed_state_is_remembered(self) -> None:
        self.registry.add_category("Work")
        self.registry.set_collapsed("Work", True)
        self.assertTrue(self.registry.is_collapsed("Work"))
        self.registry.set_collapsed("", True)
        self.assertTrue(self.registry.is_collapsed(""))
        self.registry.set_all_collapsed(False)
        self.assertFalse(self.registry.is_collapsed("Work"))
        self.assertFalse(self.registry.is_collapsed(""))


class RegistryGroupingTests(unittest.TestCase):
    def setUp(self) -> None:
        from models.repository import RepoEntry

        self.registry = Registry(Path(tempfile.mkdtemp()) / "repos.json")
        self.registry.add_category("Work")
        self.registry.add_category("Private")
        self.registry._entries.extend(  # noqa: SLF001
            [
                RepoEntry(path=Path("/tmp/pmtool"), name="pmtool", category="Work"),
                RepoEntry(path=Path("/tmp/snappix"), name="snappix", category="Work", favorite=True),
                RepoEntry(path=Path("/tmp/notes"), name="notes", category=""),
            ]
        )

    def test_groups_follow_the_category_order(self) -> None:
        groups = self.registry.grouped(SORT_NAME_ASC)
        names = [category.name for category, _entries in groups]
        self.assertEqual(["Work", "Private", ""], names)

    def test_favorites_come_first_inside_a_group(self) -> None:
        groups = dict((category.name, entries) for category, entries in self.registry.grouped(SORT_NAME_ASC))
        self.assertEqual(["snappix", "pmtool"], [item.name for item in groups["Work"]])

    def test_empty_named_categories_are_kept(self) -> None:
        groups = dict((category.name, entries) for category, entries in self.registry.grouped(SORT_NAME_ASC))
        self.assertIn("Private", groups)
        self.assertEqual([], groups["Private"])

    def test_empty_catch_all_group_is_dropped(self) -> None:
        self.registry.assign_category(self.registry.entries[2], "Work")
        names = [category.name for category, _entries in self.registry.grouped(SORT_NAME_ASC)]
        self.assertNotIn("", names)

    def test_search_filters_by_name_and_path(self) -> None:
        groups = self.registry.grouped(SORT_NAME_ASC, query="snap")
        found = [item.name for _category, entries in groups for item in entries]
        self.assertEqual(["snappix"], found)

    def test_search_looks_inside_collapsed_categories(self) -> None:
        self.registry.set_collapsed("Work", True)
        groups = self.registry.grouped(SORT_NAME_ASC, query="pmtool")
        found = [item.name for _category, entries in groups for item in entries]
        self.assertEqual(["pmtool"], found)

    def test_summary_counts_what_needs_attention(self) -> None:
        entries = self.registry.entries
        entries[0].status = RepoStatus(changed_files=3, checked_at=1.0)
        entries[1].status = RepoStatus(conflicted_files=1, ahead=2, checked_at=1.0)
        entries[2].status = RepoStatus(incoming=1, checked_at=1.0)
        summary = self.registry.summary()
        self.assertEqual(3, summary["total"])
        self.assertEqual(2, summary["changed"])
        self.assertEqual(1, summary["conflicts"])
        self.assertEqual(1, summary["incoming"])
        self.assertEqual(1, summary["unpushed"])


@requires_git
class ScannerTests(unittest.TestCase):
    def test_clean_repository(self) -> None:
        with temp_repo() as repo:
            result = scanner.scan(scanner.ScanRequest(path=repo.root, key="k", check_online=False))
            self.assertFalse(result.missing)
            self.assertEqual("main", result.status.branch)
            self.assertFalse(result.status.is_dirty)
            self.assertIsNotNone(result.status.checked_at)
            self.assertIsNotNone(result.status.last_commit_at)

    def test_dirty_repository(self) -> None:
        with temp_repo() as repo:
            repo.write("README.md", "# changed\n")
            repo.write("new.txt", "fresh\n")
            result = scanner.scan(scanner.ScanRequest(path=repo.root, key="k", check_online=False))
            self.assertEqual(2, result.status.changed_files)
            self.assertTrue(result.status.is_dirty)

    def test_missing_folder(self) -> None:
        result = scanner.scan(scanner.ScanRequest(path=Path("/nonexistent/branchly"), key="k"))
        self.assertTrue(result.missing)
        self.assertEqual("repo.missing", result.status.error_key)

    def test_online_check_sees_news(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("news.txt", "fresh\n")
            first.git("push", "origin", "main")
            result = scanner.scan(scanner.ScanRequest(path=second.root, key="k", check_online=True))
            self.assertTrue(result.status.incoming >= 1)
            self.assertIsNotNone(result.status.online_checked_at)

    def test_offline_mode_skips_the_server(self) -> None:
        with temp_repo_pair() as (_first, second, _origin):
            with mock.patch("services.scanner.remote_mod.check_remote_state") as checked:
                scanner.scan(scanner.ScanRequest(path=second.root, key="k", check_online=False))
                checked.assert_not_called()

    def test_github_remote_is_recognised(self) -> None:
        with temp_repo() as repo:
            repo.git("remote", "add", "origin", "https://github.com/user/project.git")
            result = scanner.scan(scanner.ScanRequest(path=repo.root, key="k", check_online=False))
            self.assertEqual(("user", "project"), result.github)

    def test_non_github_remote_yields_no_slug(self) -> None:
        with temp_repo() as repo:
            repo.git("remote", "add", "origin", "https://gitlab.com/user/project.git")
            result = scanner.scan(scanner.ScanRequest(path=repo.root, key="k", check_online=False))
            self.assertIsNone(result.github)

    def test_apply_result_keeps_the_github_numbers(self) -> None:
        from models.repository import RepoEntry

        entry = RepoEntry(path=Path("/tmp/demo"))
        entry.status = RepoStatus(open_pull_requests=4, check_state="success")
        fresh = scanner.ScanResult(key=entry.key, status=RepoStatus(changed_files=1))
        scanner.apply_result(entry, fresh)
        self.assertEqual(1, entry.status.changed_files)
        self.assertEqual(4, entry.status.open_pull_requests)
        self.assertEqual("success", entry.status.check_state)


class BadgeTests(unittest.TestCase):
    def test_clean_status_has_no_badges(self) -> None:
        self.assertEqual([], scanner.describe(RepoStatus(checked_at=1.0)))

    def test_conflicts_come_first(self) -> None:
        badges = scanner.describe(RepoStatus(conflicted_files=1, changed_files=2, ahead=1, incoming=3))
        self.assertEqual(["conflict", "changed", "incoming", "ahead"], [kind for kind, _token, _count in badges])

    def test_changed_counts_staged_and_unstaged_together(self) -> None:
        badges = scanner.describe(RepoStatus(changed_files=2, staged_files=3))
        self.assertEqual(("changed", "status_modified", 5), badges[0])


class OpenWithTests(unittest.TestCase):
    def test_missing_path_is_reported(self) -> None:
        self.assertEqual(open_with.OPEN_MISSING, open_with.open_path("/nonexistent/branchly/file"))

    def test_desktop_files_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "trap.desktop"
            target.write_text("[Desktop Entry]\nExec=rm -rf ~\n", encoding="utf-8")
            self.assertEqual(open_with.OPEN_REFUSED, open_with.open_path(target))

    def test_path_escaping_the_repository_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(open_with.OPEN_OUTSIDE, open_with.open_repo_file(tmp, "../../etc/passwd"))
            self.assertEqual(open_with.OPEN_OUTSIDE, open_with.reveal_repo_file(tmp, "../outside"))

    def test_opening_a_file_uses_a_helper_without_a_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "doc.txt"
            target.write_text("content", encoding="utf-8")
            with mock.patch("services.open_with.subprocess.Popen") as popen:
                with mock.patch("services.open_with.paths.is_windows", return_value=False):
                    with mock.patch("services.open_with.paths.is_macos", return_value=False):
                        self.assertEqual(open_with.OPEN_OK, open_with.open_path(target))
            args, kwargs = popen.call_args
            self.assertEqual(["xdg-open", str(target)], args[0])
            self.assertFalse(kwargs["shell"])

    def test_only_web_urls_are_opened(self) -> None:
        for url in ("file:///etc/passwd", "javascript:alert(1)", "ftp://host/x", ""):
            self.assertEqual(open_with.OPEN_REFUSED, open_with.open_url(url), url)

    def test_url_with_a_control_character_is_refused(self) -> None:
        self.assertEqual(open_with.OPEN_REFUSED, open_with.open_url("https://example.com/\rx"))

    def test_web_url_from_github_remotes(self) -> None:
        self.assertEqual(
            "https://github.com/user/project",
            open_with.web_url_for_remote("git@github.com:user/project.git"),
        )
        self.assertEqual(
            "https://github.com/user/project",
            open_with.web_url_for_remote("https://github.com/user/project.git"),
        )

    def test_web_url_from_a_self_hosted_remote(self) -> None:
        self.assertEqual(
            "https://git.example.com/group/tool",
            open_with.web_url_for_remote("git@git.example.com:group/tool.git"),
        )

    def test_local_remote_has_no_web_url(self) -> None:
        self.assertEqual("", open_with.web_url_for_remote("/srv/git/project.git"))


if __name__ == "__main__":
    unittest.main()
