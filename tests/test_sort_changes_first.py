"""
Tests for "projects with changes first" as a second sort criterion.

It used to be one of the orders, which made "what needs me" and "alphabetical"
an either-or. Now it is a tick next to the order. What has to hold:

* **An old settings file keeps meaning the same**: the order it named becomes the
  default one, with the tick set.
* **The tick and the order are independent** and both survive a restart and a
  trip through the settings dialog.
* **The settings dialog loses nothing it does not show.** It used to rebuild the
  settings field by field, which dropped the download folder.
"""

from __future__ import annotations

import os
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
from models.sort import DEFAULT_SORT_MODE, LEGACY_CHANGES_FIRST, SORT_NAME_DESC
from tests.support import requires_git, temp_repo

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")


class SettingsTests(unittest.TestCase):
    """
    The stored choice.
    """

    def test_the_old_order_becomes_the_tick(self) -> None:
        settings = AppSettings.from_dict({"sort_mode": LEGACY_CHANGES_FIRST})
        self.assertEqual(DEFAULT_SORT_MODE, settings.sort_mode)
        self.assertTrue(settings.sort_changes_first)

    def test_order_and_tick_are_kept_apart(self) -> None:
        settings = AppSettings(sort_mode=SORT_NAME_DESC, sort_changes_first=True)
        again = AppSettings.from_dict(settings.to_dict())
        self.assertEqual((SORT_NAME_DESC, True), (again.sort_mode, again.sort_changes_first))

    def test_the_tick_starts_off(self) -> None:
        self.assertFalse(AppSettings().sort_changes_first)


@requires_qt
class DialogTests(unittest.TestCase):
    """
    The settings dialog.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _dialog(self, settings: AppSettings):  # noqa: ANN202 - Qt at import time
        """
        Opens the settings dialog without showing it.

        Args:
            settings: Settings to start from.

        Returns:
            SettingsDialog: The dialog.
        """

        from ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(settings)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_the_tick_is_offered_and_carried(self) -> None:
        dialog = self._dialog(AppSettings(sort_changes_first=False))
        dialog._sort_changes_first.setChecked(True)
        self.assertTrue(dialog.result_settings().sort_changes_first)

    def test_settings_it_does_not_show_survive(self) -> None:
        original = AppSettings(
            download_folder="/home/x/Downloads",
            discovery_offered=True,
            diff_context_lines=20,
            update_remote_commit="a" * 40,
        )
        result = self._dialog(original).result_settings()
        self.assertEqual("/home/x/Downloads", result.download_folder)
        self.assertTrue(result.discovery_offered)
        self.assertEqual(20, result.diff_context_lines)
        self.assertEqual("a" * 40, result.update_remote_commit)


@requires_git
@requires_qt
class SidebarTests(unittest.TestCase):
    """
    The tick next to the order in the project list.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def test_the_list_keeps_the_dropdown_and_gains_the_tick(self) -> None:
        from models.sort import VALID_SORT_MODES

        with TemporaryDirectory() as base:
            window = self._window(Path(base))
            offered = [window._sidebar._sort.itemData(i) for i in range(window._sidebar._sort.count())]
            self.assertEqual(list(VALID_SORT_MODES), offered)
            self.assertEqual(i18n.t("sort.changes_first"), window._sidebar._changes_first_box.text())

    def test_ticking_it_is_remembered(self) -> None:
        with TemporaryDirectory() as base:
            window = self._window(Path(base))
            window._sidebar._changes_first_box.setChecked(True)
            self.assertTrue(window._sidebar.sort_changes_first)
            self.assertTrue(window.collect_settings().sort_changes_first)

    def test_ticking_it_moves_the_project_with_changes_up(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as alpha, temp_repo() as bravo:
            window = self._window(Path(base))
            _outcome, entry_a = window._registry.add(alpha.root)
            window._registry.rename(entry_a, "alpha")
            _outcome, entry_b = window._registry.add(bravo.root)
            window._registry.rename(entry_b, "bravo")
            entry_b.status.changed_files = 3

            def order() -> list[str]:
                groups = window._registry.grouped(
                    window._sidebar.sort_mode, "", window._sidebar.sort_changes_first
                )
                return [entry.name for _category, items in groups for entry in items]

            self.assertEqual(["alpha", "bravo"], order())
            window._sidebar._changes_first_box.setChecked(True)
            self.assertEqual(["bravo", "alpha"], order())

    def _window(self, base: Path):  # noqa: ANN202 - Qt at import time
        """
        Builds a main window with an isolated configuration.

        Args:
            base: Directory for the configuration.

        Returns:
            MainWindow: The window.
        """

        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = str(base / "config")
        window = MainWindow(AppSettings(auto_check_minutes=0, github_enabled=False, language="en"))
        self.addCleanup(window.close)
        return window


@requires_git
@requires_qt
class ShownOrderTests(unittest.TestCase):
    """
    The order the list actually shows, after the check has told what changed.

    The earlier test asks the registry for the order, which was right all along.
    The list on screen is built before the first check and redraws rows in place
    afterwards, so it kept the order of a time when nothing was known.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _window(self, base: Path, changes_first: bool = True):  # noqa: ANN202 - Qt at import time
        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = str(base / "config")
        window = MainWindow(
            AppSettings(
                auto_check_minutes=0,
                github_enabled=False,
                check_updates=False,
                language="en",
                sort_changes_first=changes_first,
            )
        )
        self.addCleanup(window.close)
        window._switch_scan_timer.stop()
        return window

    def _add(self, window, repo, name: str):  # noqa: ANN001, ANN202 - fixtures
        _outcome, entry = window._registry.add(repo.root)
        window._registry.rename(entry, name)
        return entry

    def _shown(self, window) -> list[str]:  # noqa: ANN001
        """
        Reads the names top to bottom, as the tree shows them.

        Args:
            window: The main window.

        Returns:
            list[str]: Project names in display order, categories flattened.
        """

        tree = window._sidebar._tree
        names: list[str] = []
        for index in range(tree.topLevelItemCount()):
            parent = tree.topLevelItem(index)
            for row in range(parent.childCount()):
                names.append(tree.itemWidget(parent.child(row), 0)._name.text())
        return names

    def _check_everything(self, window) -> None:  # noqa: ANN001
        import time

        window._start_scan("")
        deadline = time.time() + 15
        while time.time() < deadline:
            self.app.processEvents()
            if not window._scans.is_running and window._pending_scan_key is None:
                break
            time.sleep(0.01)
        self.app.processEvents()

    def test_a_check_moves_the_project_with_changes_to_the_top(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as a, temp_repo() as b, temp_repo() as c:
            window = self._window(Path(base))
            self._add(window, a, "alpha")
            self._add(window, b, "bravo")
            self._add(window, c, "charlie")
            window._sidebar.refresh()
            b.write("dirty.txt", "x\n")
            self.assertEqual(["alpha", "bravo", "charlie"], self._shown(window))

            self._check_everything(window)

            self.assertEqual(["bravo", "alpha", "charlie"], self._shown(window))

    def test_projects_with_changes_are_alphabetical_among_themselves(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as a, temp_repo() as b, temp_repo() as c, temp_repo() as d:
            window = self._window(Path(base))
            self._add(window, a, "alpha")
            self._add(window, b, "bravo")
            self._add(window, c, "charlie")
            self._add(window, d, "delta")
            window._sidebar.refresh()
            d.write("dirty.txt", "x\n")
            b.write("dirty.txt", "x\n")

            self._check_everything(window)

            self.assertEqual(["bravo", "delta", "alpha", "charlie"], self._shown(window))

    def test_without_the_tick_a_check_leaves_the_order_alone(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as a, temp_repo() as b:
            window = self._window(Path(base), changes_first=False)
            self._add(window, a, "alpha")
            self._add(window, b, "bravo")
            window._sidebar.refresh()
            b.write("dirty.txt", "x\n")

            self._check_everything(window)

            self.assertEqual(["alpha", "bravo"], self._shown(window))

    def test_saving_the_changes_moves_the_project_back_down(self) -> None:
        from gitops.commit import CommitDraft

        with TemporaryDirectory() as base, temp_repo() as a, temp_repo() as b:
            window = self._window(Path(base))
            self._add(window, a, "alpha")
            entry_b = self._add(window, b, "bravo")
            window._sidebar.refresh()
            b.write("dirty.txt", "x\n")
            self._check_everything(window)
            self.assertEqual(["bravo", "alpha"], self._shown(window))

            b.git("add", "-A")
            b.git("commit", "-q", "-m", "Save it")
            window._start_scan(entry_b.key)
            self._check_everything(window)

            self.assertEqual(["alpha", "bravo"], self._shown(window))

    def test_the_list_is_rebuilt_only_when_something_moves(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as a, temp_repo() as b:
            window = self._window(Path(base))
            self._add(window, a, "alpha")
            self._add(window, b, "bravo")
            window._sidebar.refresh()
            rebuilt: list[bool] = []
            real = window._sidebar.refresh
            window._sidebar.refresh = lambda: (rebuilt.append(True), real())[1]

            self._check_everything(window)
            self.assertEqual([], rebuilt)

            b.write("dirty.txt", "x\n")
            self._check_everything(window)
            self.assertEqual([True], rebuilt)

    def test_the_selection_stays_on_the_same_project_when_the_list_moves(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as a, temp_repo() as b:
            window = self._window(Path(base))
            entry_a = self._add(window, a, "alpha")
            self._add(window, b, "bravo")
            window._sidebar.refresh()
            window._sidebar.select_key(entry_a.key)
            b.write("dirty.txt", "x\n")

            self._check_everything(window)

            self.assertEqual(["bravo", "alpha"], self._shown(window))
            self.assertEqual(entry_a.key, window._sidebar.selected_key)

    def test_a_category_keeps_its_own_projects(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as a, temp_repo() as b, temp_repo() as c:
            window = self._window(Path(base))
            entry_a = self._add(window, a, "alpha")
            entry_b = self._add(window, b, "bravo")
            self._add(window, c, "charlie")
            window._registry.add_category("Work")
            entry_a.category = "Work"
            entry_b.category = "Work"
            window._sidebar.refresh()
            b.write("dirty.txt", "x\n")

            self._check_everything(window)

            from ui.sidebar import _ROLE_CATEGORY

            tree = window._sidebar._tree
            by_category = {}
            for index in range(tree.topLevelItemCount()):
                parent = tree.topLevelItem(index)
                by_category[str(parent.data(0, _ROLE_CATEGORY))] = [
                    tree.itemWidget(parent.child(row), 0)._name.text()
                    for row in range(parent.childCount())
                ]
            self.assertEqual(["bravo", "alpha"], by_category["Work"])
            self.assertEqual(["charlie"], by_category[""])


if __name__ == "__main__":
    unittest.main()
