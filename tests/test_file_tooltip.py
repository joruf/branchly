"""
Tests for the tooltip of a file in the list of changes.

Its first line says what happened to the file and where it is, and has since
the list existed. The second line says when the file was last written, which is
what somebody looking at a list of new and changed files wants to know and could
only find out by opening each one. What has to hold:

* **The first line stays exactly as it was**, with the date underneath it and
  never in its place.
* **The date is the file's own**, read from the disk, in the same form the rest
  of the program writes dates and times.
* **A file that is gone has no date**, and no placeholder pretending otherwise.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime
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
from gitops.status import read_state
from tests.support import requires_git, temp_repo

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")

STAMP = datetime(2026, 3, 14, 9, 26, 53).timestamp()
STAMP_TEXT = "2026-03-14 09:26"


@requires_git
@requires_qt
class FileTooltipTests(unittest.TestCase):
    """
    The tooltip as the list builds it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _panel(self):  # noqa: ANN202 - Qt at import time
        from ui.changes_panel import ChangesPanel

        panel = ChangesPanel()
        self.addCleanup(panel.deleteLater)
        return panel

    def _tip(self, panel, name: str) -> str:  # noqa: ANN001 - ChangesPanel
        for row in range(panel._list.count()):
            item = panel._list.item(row)
            if item.text().endswith(name):
                return item.toolTip()
        self.fail(f"{name} is not in the list")

    def _stamped(self, path: Path, stamp: float = STAMP) -> None:
        os.utime(path, (stamp, stamp))

    def test_a_new_file_shows_its_date_on_a_second_line(self) -> None:
        with temp_repo() as repo:
            self._stamped(repo.write("fresh.txt", "x\n"))
            panel = self._panel()
            panel.set_state(read_state(repo.root), "main", root=repo.root)

            lines = self._tip(panel, "fresh.txt").split("\n")

            self.assertEqual(2, len(lines))
            self.assertEqual(i18n.t("changes.file_changed_at", time=STAMP_TEXT), lines[1])

    def test_the_first_line_is_what_it_always_was(self) -> None:
        with temp_repo() as repo:
            repo.write("fresh.txt", "x\n")
            state = read_state(repo.root)
            panel = self._panel()
            panel.set_state(state, "main", root=repo.root)
            change = next(c for c in state.files if c.path == "fresh.txt")

            first = self._tip(panel, "fresh.txt").split("\n")[0]

            self.assertEqual(f"{i18n.t(change.label_key)} · fresh.txt", first)

    def test_a_changed_file_shows_when_it_was_last_edited(self) -> None:
        with temp_repo() as repo:
            repo.commit_file("old.txt", "one\n", "Add old")
            self._stamped(repo.write("old.txt", "two\n"), STAMP)
            panel = self._panel()
            panel.set_state(read_state(repo.root), "main", root=repo.root)

            self.assertTrue(
                self._tip(panel, "old.txt").endswith(
                    i18n.t("changes.file_changed_at", time=STAMP_TEXT)
                )
            )

    def test_each_file_has_its_own_date(self) -> None:
        with temp_repo() as repo:
            self._stamped(repo.write("a.txt", "a\n"), STAMP)
            self._stamped(repo.write("b.txt", "b\n"), STAMP + 3 * 86400)
            panel = self._panel()
            panel.set_state(read_state(repo.root), "main", root=repo.root)

            self.assertIn("2026-03-14 09:26", self._tip(panel, "a.txt"))
            self.assertIn("2026-03-17 09:26", self._tip(panel, "b.txt"))

    def test_a_deleted_file_has_no_date_and_no_placeholder(self) -> None:
        with temp_repo() as repo:
            repo.commit_file("gone.txt", "bye\n", "Add gone")
            (repo.root / "gone.txt").unlink()
            panel = self._panel()
            panel.set_state(read_state(repo.root), "main", root=repo.root)

            tip = self._tip(panel, "gone.txt")

            self.assertNotIn("\n", tip)
            self.assertNotIn(i18n.t("changes.file_changed_at", time=""), tip)

    def test_without_the_folder_the_tooltip_is_the_one_line_it_was(self) -> None:
        with temp_repo() as repo:
            repo.write("fresh.txt", "x\n")
            panel = self._panel()
            panel.set_state(read_state(repo.root), "main")

            self.assertNotIn("\n", self._tip(panel, "fresh.txt"))

    def test_a_file_in_a_subfolder_is_found_by_its_path_in_the_project(self) -> None:
        with temp_repo() as repo:
            self._stamped(repo.write("docs/deep/note.txt", "x\n"))
            panel = self._panel()
            panel.set_state(read_state(repo.root), "main", root=repo.root)

            self.assertIn(STAMP_TEXT, self._tip(panel, "note.txt"))

    def test_it_is_in_both_languages(self) -> None:
        for language in ("en", "de"):
            i18n.set_language(language)
            self.addCleanup(i18n.set_language, "en")
            with self.subTest(language=language):
                text = i18n.t("changes.file_changed_at", time=STAMP_TEXT)
                self.assertIn(STAMP_TEXT, text)
                self.assertNotEqual("changes.file_changed_at", text.split(":")[0])


@requires_git
@requires_qt
class FileTooltipInTheWindowTests(unittest.TestCase):
    """
    The same, as the main window hands the list its folder.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def test_the_window_passes_the_project_folder_on(self) -> None:
        from ui.main_window import MainWindow

        with TemporaryDirectory() as base, temp_repo() as repo:
            os.environ["XDG_CONFIG_HOME"] = str(Path(base) / "config")
            window = MainWindow(
                AppSettings(auto_check_minutes=0, github_enabled=False, check_updates=False, language="en")
            )
            self.addCleanup(window.close)
            os.utime(repo.write("fresh.txt", "x\n"), (STAMP, STAMP))
            _outcome, entry = window._registry.add(repo.root)
            window._sidebar.refresh()
            window._activate(entry)
            window._switch_scan_timer.stop()

            panel = window._changes
            tips = [panel._list.item(row).toolTip() for row in range(panel._list.count())]

            self.assertEqual(1, len(tips))
            self.assertTrue(tips[0].endswith(i18n.t("changes.file_changed_at", time=STAMP_TEXT)))
            self.assertEqual(2, len(tips[0].split("\n")))

    def test_a_refresh_after_an_edit_shows_the_new_time(self) -> None:
        from ui.main_window import MainWindow

        with TemporaryDirectory() as base, temp_repo() as repo:
            os.environ["XDG_CONFIG_HOME"] = str(Path(base) / "config")
            window = MainWindow(
                AppSettings(auto_check_minutes=0, github_enabled=False, check_updates=False, language="en")
            )
            self.addCleanup(window.close)
            path = repo.write("fresh.txt", "x\n")
            os.utime(path, (STAMP, STAMP))
            _outcome, entry = window._registry.add(repo.root)
            window._sidebar.refresh()
            window._activate(entry)
            window._switch_scan_timer.stop()

            os.utime(path, (STAMP + 86400, STAMP + 86400))
            window._reload_current()

            tip = window._changes._list.item(0).toolTip()
            self.assertIn("2026-03-15 09:26", tip)


if __name__ == "__main__":
    unittest.main()
