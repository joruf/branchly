"""
Tests for the one tick that stands for all the files.

It replaced two buttons, "select all" and "select none", and the reason it can
is that it also answers a question the buttons could not: which of the three
states the list is currently in. That is the part worth testing, because it is
the part that can be silently wrong.

Two things in particular:

* **Half ticked has to mean half.** Not only "some files are unticked" but also
  "one file is only partly in", because both are the same answer to "is all of
  this going into the commit". A tick claiming everything while a block sits
  outside the commit would be a lie the user only finds out about afterwards.
* **A click means all of it, unless it already is.** Qt's own tristate box
  cycles through the middle state as you click it. Partly ticked is something
  the file list produces, never something anybody asks for, so that cycle is
  overridden and stays overridden.
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QPushButton

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

from gitops import diff as diff_mod
from tests.support import requires_git

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")

FILES = ("one.txt", "two.txt", "three.txt")


def build_repo(root: Path) -> Path:
    """
    Creates three changed files, each with two separate blocks.

    Args:
        root: Folder to build in.

    Returns:
        Path: The working tree.
    """

    repo = root / "work"
    repo.mkdir(parents=True)
    environment = dict(os.environ)
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": str(root / ".gitconfig"),
            "GIT_CONFIG_SYSTEM": str(root / ".gitconfig-system"),
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        }
    )

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, env=environment, check=True, capture_output=True)

    lines = [f"line {number:03d}" for number in range(1, 61)]
    for name in FILES:
        (repo / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    git("init", "-b", "main")
    git("add", "-A")
    git("commit", "-m", "first")

    edited = list(lines)
    edited[4] = "line 005 TOP"
    edited[54] = "line 055 BOTTOM"
    for name in FILES:
        (repo / name).write_text("\n".join(edited) + "\n", encoding="utf-8")
    return repo


@requires_qt
@requires_git
class SelectAllTests(unittest.TestCase):
    """
    The header tick, in every state it can reach.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, base: Path):  # noqa: ANN202 - Qt at import time
        """
        Builds a window on a project with three changed files.

        Args:
            base: Directory to work in.

        Returns:
            tuple: The window and the repository path.
        """

        from config.app_settings import AppSettings
        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = str(base / "config")
        repo = build_repo(base)
        window = MainWindow(
            AppSettings(auto_check_minutes=0, github_enabled=False, language="en")
        )
        self.addCleanup(window.close)
        _outcome, entry = window._registry.add(repo)
        window._sidebar.refresh()
        window._activate(entry)
        window._switch_scan_timer.stop()
        return window, repo

    def test_the_two_buttons_are_gone(self) -> None:
        with TemporaryDirectory() as base:
            window, _repo = self._window(Path(base))
            labels = [b.text() for b in window._changes._header.findChildren(QPushButton)]
            self.assertEqual([], labels)

    def test_it_carries_the_count_itself(self) -> None:
        # One fact in one place, and the whole line becomes the click target.
        with TemporaryDirectory() as base:
            window, _repo = self._window(Path(base))
            self.assertIn("3", window._changes._select_all.text())

    def test_everything_is_ticked_to_begin_with(self) -> None:
        with TemporaryDirectory() as base:
            window, _repo = self._window(Path(base))
            self.assertEqual(Qt.CheckState.Checked, window._changes._select_all.checkState())
            self.assertEqual(3, len(window._changes.checked_paths()))

    def test_a_click_empties_it(self) -> None:
        with TemporaryDirectory() as base:
            window, _repo = self._window(Path(base))
            window._changes._select_all.click()

            self.assertEqual(Qt.CheckState.Unchecked, window._changes._select_all.checkState())
            self.assertEqual([], window._changes.checked_paths())

    def test_a_second_click_fills_it_again(self) -> None:
        with TemporaryDirectory() as base:
            window, _repo = self._window(Path(base))
            window._changes._select_all.click()
            window._changes._select_all.click()

            self.assertEqual(Qt.CheckState.Checked, window._changes._select_all.checkState())
            self.assertEqual(3, len(window._changes.checked_paths()))

    def test_unticking_one_file_shows_the_middle_state(self) -> None:
        with TemporaryDirectory() as base:
            window, _repo = self._window(Path(base))
            window._changes._list.item(0).setCheckState(Qt.CheckState.Unchecked)

            self.assertEqual(
                Qt.CheckState.PartiallyChecked, window._changes._select_all.checkState()
            )

    def test_from_the_middle_a_click_means_all_of_it(self) -> None:
        # Qt's own tristate box would step to unchecked here. Partly ticked is
        # something the list produces, not something anybody asks for.
        with TemporaryDirectory() as base:
            window, _repo = self._window(Path(base))
            window._changes._list.item(0).setCheckState(Qt.CheckState.Unchecked)

            window._changes._select_all.click()
            self.assertEqual(Qt.CheckState.Checked, window._changes._select_all.checkState())
            self.assertEqual(3, len(window._changes.checked_paths()))

    def test_a_partly_included_file_also_reads_as_the_middle_state(self) -> None:
        # Every file is ticked, but one is only half in. The tick has to say so.
        with TemporaryDirectory() as base:
            window, repo = self._window(Path(base))
            window._changes._list.setCurrentRow(0)
            path = window._changes.current_path()
            diff = diff_mod.file_diff(repo, path, diff_mod.TARGET_WORKTREE_HEAD)
            self.assertEqual(2, len(diff.hunks))

            window._on_hunk_toggled(diff.hunks[1].key)
            self.assertEqual(3, len(window._changes.checked_paths()))
            self.assertEqual(
                Qt.CheckState.PartiallyChecked, window._changes._select_all.checkState()
            )

    def test_asking_for_all_of_it_also_clears_a_block_selection(self) -> None:
        with TemporaryDirectory() as base:
            window, repo = self._window(Path(base))
            window._changes._list.setCurrentRow(0)
            path = window._changes.current_path()
            diff = diff_mod.file_diff(repo, path, diff_mod.TARGET_WORKTREE_HEAD)
            window._on_hunk_toggled(diff.hunks[1].key)
            self.assertIn(path, window._hunk_selection)

            window._changes._select_all.click()
            self.assertNotIn(path, window._hunk_selection)
            self.assertEqual(Qt.CheckState.Checked, window._changes._select_all.checkState())

    def test_the_three_states_look_different(self) -> None:
        # The whole point of one control instead of two buttons: it says which
        # state the list is in. Three states that render the same say nothing.
        from config.theme import THEME_DARK, build_application_stylesheet

        with TemporaryDirectory() as base:
            window, _repo = self._window(Path(base))
            self.app.setStyleSheet(build_application_stylesheet(THEME_DARK))
            box = window._changes._select_all

            pictures = {}
            for name, state in (
                ("all", Qt.CheckState.Checked),
                ("some", Qt.CheckState.PartiallyChecked),
                ("none", Qt.CheckState.Unchecked),
            ):
                box.blockSignals(True)
                box.setCheckState(state)
                box.blockSignals(False)
                self.app.processEvents()
                image = box.grab().toImage()
                pictures[name] = [
                    image.pixelColor(x, y).name()
                    for y in range(0, image.height(), 2)
                    for x in range(0, min(20, image.width()), 2)
                ]

            self.assertNotEqual(pictures["all"], pictures["some"])
            self.assertNotEqual(pictures["some"], pictures["none"])
            self.assertNotEqual(pictures["all"], pictures["none"])

    def test_it_hides_itself_when_there_is_nothing_to_tick(self) -> None:
        with TemporaryDirectory() as base:
            window, _repo = self._window(Path(base))
            window._changes.set_state(None)
            self.assertFalse(window._changes._select_all.isVisibleTo(window._changes._header))


if __name__ == "__main__":
    unittest.main()
