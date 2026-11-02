"""
Tests for the right-click menu of a project and the window it opens for the
project's folder and online repository.

The menu is ordered by what its entries are about, and an entry that wanders into
the wrong group, or is offered twice, is the kind of change nobody notices until
somebody reaches for it. So the order is written down here, as the labels
the user reads, with the separators between the groups.
"""

from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QDialogButtonBox

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

import i18n
from config.app_settings import AppSettings
from tests.support import requires_git, temp_repo

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")

SEPARATOR = ""


def _expected_menu(missing: bool = False, favorite: bool = False, remote: bool = True) -> list[str]:
    """
    Writes down the menu as the user is meant to read it.

    Args:
        missing: Whether the project's folder is gone.
        favorite: Whether the project is starred.
        remote: Whether the project has a server. Not a different entry, only an
            enabled or disabled one, so it does not change the labels.

    Returns:
        list[str]: The labels, with an empty string for each separator.
    """

    connection = i18n.t("repo.connection")
    group_every_day = [
        i18n.t("repo.favorite_off" if favorite else "repo.favorite_on"),
        i18n.t("repo.check_now"),
        i18n.t("repo.update_now"),
    ]
    group_open = [
        i18n.t("repo.open_folder"),
        i18n.t("repo.open_remote"),
        i18n.t("gitignore.menu"),
    ]
    group_about = [
        *([] if missing else [connection]),
        i18n.t("action.rename"),
        i18n.t("category.move_to"),
    ]
    group_remove = [i18n.t("revert.menu_all"), i18n.t("action.remove")]
    menu = [*([connection, SEPARATOR] if missing else [])]
    for group in (group_every_day, group_open, group_about, group_remove):
        menu.extend(group)
        menu.append(SEPARATOR)
    return menu[:-1]


@requires_git
@requires_qt
class ContextMenuTests(unittest.TestCase):
    """
    The entries, their order and their state.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _window(self, base: Path, repo):  # noqa: ANN001, ANN202 - Qt at import time
        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = str(base / "config")
        window = MainWindow(AppSettings(auto_check_minutes=0, github_enabled=False, language="en"))
        self.addCleanup(window.close)
        _outcome, entry = window._registry.add(repo.root)
        window._sidebar.refresh()
        window._switch_scan_timer.stop()
        return window, entry

    def _labels(self, window, key: str) -> list[str]:  # noqa: ANN001
        menu = window._sidebar.repo_menu(key)
        return [action.text() for action in menu.actions()]

    def test_the_entries_stand_in_their_groups(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as repo:
            window, entry = self._window(Path(base), repo)
            self.assertEqual(_expected_menu(), self._labels(window, entry.key))

    def test_a_missing_folder_puts_the_connection_first_and_only_once(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as repo:
            window, entry = self._window(Path(base), repo)
            shutil.move(str(repo.root), str(Path(base) / "gone"))
            self.assertEqual(_expected_menu(missing=True), self._labels(window, entry.key))

    def test_a_starred_project_offers_to_take_the_star_off(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as repo:
            window, entry = self._window(Path(base), repo)
            window._registry.set_favorite(entry, True)
            self.assertEqual(_expected_menu(favorite=True), self._labels(window, entry.key))

    def test_the_connection_entry_asks_for_that_project(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as repo:
            window, entry = self._window(Path(base), repo)
            asked: list[str] = []
            window._sidebar.connection_requested.disconnect(window._edit_connection)
            window._sidebar.connection_requested.connect(asked.append)
            menu = window._sidebar.repo_menu(entry.key)
            action = next(a for a in menu.actions() if a.text() == i18n.t("repo.connection"))
            self.assertEqual(i18n.t("tip.repo_connection"), action.toolTip())
            action.trigger()
            self.assertEqual([entry.key], asked)

    def test_states_that_cannot_apply_are_greyed_out_not_removed(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as repo:
            window, entry = self._window(Path(base), repo)
            menu = window._sidebar.repo_menu(entry.key)
            by_text = {action.text(): action for action in menu.actions()}
            # No server and nothing to revert.
            self.assertFalse(by_text[i18n.t("repo.open_remote")].isEnabled())
            self.assertFalse(by_text[i18n.t("revert.menu_all")].isEnabled())
            self.assertTrue(by_text[i18n.t("repo.open_folder")].isEnabled())

            shutil.move(str(repo.root), str(Path(base) / "gone"))
            menu = window._sidebar.repo_menu(entry.key)
            by_text = {action.text(): action for action in menu.actions()}
            self.assertFalse(by_text[i18n.t("gitignore.menu")].isEnabled())
            self.assertFalse(by_text[i18n.t("repo.update_now")].isEnabled())
            self.assertTrue(by_text[i18n.t("repo.connection")].isEnabled())

    def test_every_entry_has_a_label_in_both_languages(self) -> None:
        keys = (
            "repo.connection",
            "tip.repo_connection",
            "link.title_both",
            "link.intro_both",
            "link.folder_label",
            "link.folder_not_repo",
            "link.folder_not_repo_hint",
            "tip.link_folder",
        )
        for language in ("en", "de"):
            i18n.set_language(language)
            self.addCleanup(i18n.set_language, "en")
            for key in keys:
                with self.subTest(language=language, key=key):
                    self.assertNotEqual(key, i18n.t(key))


@requires_git
@requires_qt
class FolderAndServerWindowTests(unittest.TestCase):
    """
    The window itself, without the main window around it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _dialog(self, repo, current_url: str = "", with_folder: bool = True):  # noqa: ANN001, ANN202
        from ui.link_remote_dialog import LinkRemoteDialog

        dialog = LinkRemoteDialog(
            repo.root,
            "demo",
            current_url=current_url,
            folder=repo.root if with_folder else None,
        )
        self.addCleanup(dialog.deleteLater)
        return dialog

    def _ok(self, dialog):  # noqa: ANN001, ANN202
        return dialog._buttons.button(QDialogButtonBox.StandardButton.Ok)

    def test_without_a_folder_it_is_the_old_window_for_the_address_alone(self) -> None:
        with temp_repo() as repo:
            dialog = self._dialog(repo, with_folder=False)
            self.assertIsNone(dialog._folder_edit)
            self.assertEqual(i18n.t("link.title"), dialog.windowTitle())
            self.assertIsNone(dialog.folder)
            self.assertFalse(dialog.folder_changed)

    def test_with_a_folder_the_title_names_both(self) -> None:
        with temp_repo() as repo:
            dialog = self._dialog(repo)
            self.assertEqual(i18n.t("link.title_both"), dialog.windowTitle())
            self.assertEqual(str(repo.root), dialog._folder_edit.text())
            self.assertFalse(dialog.folder_changed)

    def test_a_folder_inside_the_project_names_the_project(self) -> None:
        with temp_repo() as repo:
            (repo.root / "sub").mkdir()
            dialog = self._dialog(repo)
            dialog._folder_edit.setText(str(repo.root / "sub"))
            self.assertEqual(repo.root.resolve(), dialog.folder.resolve())
            self.assertFalse(dialog.folder_changed)

    def test_a_folder_that_does_not_exist_names_nothing_and_blocks_accepting(self) -> None:
        with temp_repo() as repo:
            dialog = self._dialog(repo)
            dialog._folder_edit.setText(str(repo.root / "nowhere"))
            self.assertIsNone(dialog.folder)
            self.assertFalse(self._ok(dialog).isEnabled())
            self.assertFalse(dialog._check_button.isEnabled())
            self.assertEqual(i18n.t("link.folder_not_repo"), dialog._notice._title.text())

    def test_another_project_as_folder_is_a_change_and_can_be_accepted(self) -> None:
        with temp_repo() as repo, temp_repo() as other:
            dialog = self._dialog(repo)
            dialog._folder_edit.setText(str(other.root))
            self.assertTrue(dialog.folder_changed)
            self.assertTrue(self._ok(dialog).isEnabled())

    def test_a_new_address_alone_can_be_accepted_and_an_empty_one_cannot(self) -> None:
        with temp_repo() as repo:
            dialog = self._dialog(repo, current_url="https://github.com/joruf/old.git")
            self.assertFalse(self._ok(dialog).isEnabled())
            dialog._url.setText("https://github.com/joruf/new.git")
            self.assertTrue(self._ok(dialog).isEnabled())
            dialog._url.setText("   ")
            self.assertFalse(self._ok(dialog).isEnabled())

    def test_the_server_check_asks_about_the_folder_that_is_chosen(self) -> None:
        with temp_repo() as repo, temp_repo() as other:
            dialog = self._dialog(repo)
            dialog._folder_edit.setText(str(other.root))
            self.assertEqual(other.root.resolve(), Path(dialog._repo).resolve())

    def test_an_empty_folder_field_names_nothing(self) -> None:
        with temp_repo() as repo:
            dialog = self._dialog(repo)
            dialog._folder_edit.setText("")
            self.assertIsNone(dialog.folder)
            self.assertFalse(self._ok(dialog).isEnabled())


if __name__ == "__main__":
    unittest.main()
