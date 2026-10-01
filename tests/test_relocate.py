"""
Tests for pointing a project at the folder it moved to.

A project whose folder was moved or renamed shows as missing. Removing it and
adding it again loses everything arranged around it, so the right-click menu
offers to change its path instead. What has to hold:

* **The entry is offered only when the folder is gone**, and then first.
* **Everything arranged stays**: category, star, manual position, the files left
  out of the next commit, a display name the user chose. A name that was only
  the old folder's name follows the new folder.
* **A wrong pick is caught**: a folder that is no project, one already in the
  list, and one whose server differs, which is asked about.
"""

from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

import i18n
from config.app_settings import AppSettings
from gitops.commit import CommitDraft
from services.registry import ADD_DUPLICATE, ADD_NOT_A_REPOSITORY, ADD_OK, Registry
from tests.support import requires_git, temp_repo

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")


def moved(repo, base: Path, name: str = "moved") -> Path:  # noqa: ANN001 - TempRepo
    """
    Moves a fixture's working tree somewhere else, as a user would.

    Args:
        repo: The fixture.
        base: Folder to move it into.
        name: New folder name.

    Returns:
        Path: The new location.
    """

    target = base / name
    shutil.move(str(repo.root), str(target))
    return target


@requires_git
class RegistryTests(unittest.TestCase):
    """
    Moving the entry itself.
    """

    def test_what_was_arranged_stays(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as repo:
            registry = Registry(Path(base) / "registry.json")
            _outcome, entry = registry.add(repo.root, category="Work")
            registry.set_favorite(entry, True)
            entry.order = 7
            entry.deselected_paths = {"notes.txt"}
            entry.status.changed_files = 3
            target = moved(repo, Path(base))
            outcome, result = registry.relocate(entry, target)
            self.assertEqual(ADD_OK, outcome)
            self.assertIs(entry, result)
            self.assertEqual(target.resolve(), entry.path.resolve())
            self.assertEqual(
                ("Work", True, 7, {"notes.txt"}),
                (entry.category, entry.favorite, entry.order, entry.deselected_paths),
            )
            # The last scan described the folder that is gone.
            self.assertEqual(0, entry.status.changed_files)
            self.assertEqual(1, len(registry.entries))

    def test_a_folder_name_follows_and_a_chosen_name_stays(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as first, temp_repo() as second:
            registry = Registry(Path(base) / "registry.json")
            _o, plain = registry.add(first.root)
            _o, named = registry.add(second.root)
            registry.rename(named, "My project")
            registry.relocate(plain, moved(first, Path(base), "renamed-folder"))
            registry.relocate(named, moved(second, Path(base), "elsewhere"))
            self.assertEqual("renamed-folder", plain.name)
            self.assertEqual("My project", named.name)

    def test_a_folder_inside_the_project_means_the_project(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as repo:
            registry = Registry(Path(base) / "registry.json")
            _o, entry = registry.add(repo.root)
            target = moved(repo, Path(base))
            (target / "sub").mkdir()
            registry.relocate(entry, target / "sub")
            self.assertEqual(target.resolve(), entry.path.resolve())

    def test_the_server_is_read_from_the_new_folder(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as repo:
            registry = Registry(Path(base) / "registry.json")
            _o, entry = registry.add(repo.root)
            target = moved(repo, Path(base))
            repo.root = target
            repo.git("remote", "add", "origin", "https://github.com/joruf/example.git")
            registry.relocate(entry, target)
            self.assertEqual("https://github.com/joruf/example.git", entry.remote_url)

    def test_a_folder_that_is_no_project_is_refused(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as repo:
            registry = Registry(Path(base) / "registry.json")
            _o, entry = registry.add(repo.root)
            empty = Path(base) / "empty"
            empty.mkdir()
            self.assertEqual(ADD_NOT_A_REPOSITORY, registry.relocate(entry, empty)[0])
            self.assertEqual(repo.root.resolve(), entry.path.resolve())

    def test_a_folder_already_listed_is_refused(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as first, temp_repo() as second:
            registry = Registry(Path(base) / "registry.json")
            _o, entry = registry.add(first.root)
            _o, other = registry.add(second.root)
            outcome, found = registry.relocate(entry, second.root)
            self.assertEqual(ADD_DUPLICATE, outcome)
            self.assertIs(other, found)


@requires_git
@requires_qt
class WindowTests(unittest.TestCase):
    """
    The menu entry and what the window does with it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _window(self, base: Path, repo):  # noqa: ANN001, ANN202 - Qt at import time
        """
        Builds a window holding one project.

        Args:
            base: Directory for the configuration.
            repo: The fixture.

        Returns:
            tuple: The window and the entry.
        """

        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = str(base / "config")
        window = MainWindow(AppSettings(auto_check_minutes=0, github_enabled=False, language="en"))
        self.addCleanup(window.close)
        _outcome, entry = window._registry.add(repo.root)
        window._sidebar.refresh()
        window._activate(entry)
        window._switch_scan_timer.stop()
        return window, entry

    def _entries(self, window, key: str) -> list[str]:  # noqa: ANN001
        """
        Returns the labels of a project's right-click menu.

        Args:
            window: The main window.
            key: Registry key.

        Returns:
            list[str]: The entries, separators left out.
        """

        menu = window._sidebar.repo_menu(key)
        return [action.text() for action in menu.actions() if action.text()]

    def test_the_entry_shows_only_for_a_missing_folder_and_then_first(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as repo:
            window, entry = self._window(Path(base), repo)
            self.assertNotIn(i18n.t("repo.relocate"), self._entries(window, entry.key))
            moved(repo, Path(base))
            self.assertEqual(i18n.t("repo.relocate"), self._entries(window, entry.key)[0])

    def test_choosing_the_new_folder_moves_the_project(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as repo:
            window, entry = self._window(Path(base), repo)
            window._changes.set_draft(CommitDraft("Half written"))
            old_key = entry.key
            target = moved(repo, Path(base))
            window.choose_relocation = lambda _entry: str(target)
            window._prompt_relocate_repo(old_key)
            self.assertTrue(entry.exists)
            self.assertEqual(target.resolve(), entry.path.resolve())
            self.assertIs(entry, window._entry)
            self.assertEqual("Half written", window._changes.commit_draft().summary)
            self.assertNotIn(old_key, window._drafts)

    def test_cancelling_the_picker_changes_nothing(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as repo:
            window, entry = self._window(Path(base), repo)
            before = entry.path
            moved(repo, Path(base))
            window.choose_relocation = lambda _entry: ""
            window._prompt_relocate_repo(entry.key)
            self.assertEqual(before, entry.path)

    def test_a_different_server_is_asked_about(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as repo, temp_repo() as other:
            window, entry = self._window(Path(base), repo)
            entry.remote_url = "https://github.com/joruf/one.git"
            other.git("remote", "add", "origin", "https://github.com/joruf/two.git")
            before = entry.path
            moved(repo, Path(base))
            asked: list[str] = []

            def refuse(_title: str, message: str, *_rest: object, **_kw: object) -> bool:
                asked.append(message)
                return False

            window._confirm = refuse
            window.choose_relocation = lambda _entry: str(other.root)
            window._prompt_relocate_repo(entry.key)
            self.assertEqual(1, len(asked))
            self.assertIn("two.git", asked[0])
            self.assertEqual(before, entry.path)

    def test_a_folder_that_is_no_project_is_explained(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as repo:
            window, entry = self._window(Path(base), repo)
            moved(repo, Path(base))
            empty = Path(base) / "empty"
            empty.mkdir()
            window.choose_relocation = lambda _entry: str(empty)
            window._prompt_relocate_repo(entry.key)
            self.assertEqual(i18n.t("repo.relocate_not_repo"), window._notice._title.text())
            self.assertFalse(entry.exists)


if __name__ == "__main__":
    unittest.main()
