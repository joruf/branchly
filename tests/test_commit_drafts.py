"""
Tests for keeping a half written commit message per project.

Typing a title and then going to look at another project before committing is
common. The message must still be there on the way back, must not appear in the
other project, and must be gone once it was committed. It lives in memory only.
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
from gitops.commit import CommitDraft
from tests.support import requires_git, temp_repo

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")


@requires_git
@requires_qt
class DraftTests(unittest.TestCase):
    """
    The commit box follows the project it was typed for.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def setUp(self) -> None:
        self._base = TemporaryDirectory()
        self.addCleanup(self._base.cleanup)
        os.environ["XDG_CONFIG_HOME"] = str(Path(self._base.name) / "config")
        self._repos = []
        for _index in range(3):
            context = temp_repo()
            repo = context.__enter__()
            self.addCleanup(context.__exit__, None, None, None)
            repo.write("change.txt", "not committed\n")
            self._repos.append(repo)

        from ui.main_window import MainWindow

        self.window = MainWindow(
            AppSettings(auto_check_minutes=0, github_enabled=False, language="en")
        )
        self.addCleanup(self.window.close)
        self.entries = []
        for repo in self._repos:
            _outcome, entry = self.window._registry.add(repo.root)
            self.entries.append(entry)
        self.window._sidebar.refresh()
        self._open(0)

    def _open(self, index: int) -> None:
        """
        Switches to one of the projects.

        Args:
            index: Which project.

        Returns:
            None
        """

        self.window._activate(self.entries[index])
        self.window._switch_scan_timer.stop()

    def _box(self) -> CommitDraft:
        """
        Returns what the commit box shows.

        Returns:
            CommitDraft: The fields.
        """

        return self.window._changes.commit_draft()

    def test_each_project_gets_its_own_message_back(self) -> None:
        self.window._changes.set_draft(CommitDraft("Title A", "Body A"))
        self._open(1)
        self.assertEqual(CommitDraft(""), self._box())
        self.window._changes.set_draft(CommitDraft("Title B"))
        self._open(2)
        self.assertEqual(CommitDraft(""), self._box())
        self._open(0)
        self.assertEqual(CommitDraft("Title A", "Body A"), self._box())
        self._open(1)
        self.assertEqual(CommitDraft("Title B"), self._box())

    def test_the_amend_tick_is_kept_too(self) -> None:
        self.window._changes.set_draft(CommitDraft("Fix", amend=True))
        self._open(1)
        self.assertFalse(self._box().amend)
        self._open(0)
        self.assertTrue(self._box().amend)

    def test_opening_the_same_project_again_keeps_what_is_typed(self) -> None:
        self.window._changes.set_draft(CommitDraft("Still typing"))
        self._open(0)
        self.assertEqual("Still typing", self._box().summary)

    def test_an_emptied_box_forgets_the_old_message(self) -> None:
        self.window._changes.set_draft(CommitDraft("Title A"))
        self._open(1)
        self._open(0)
        self.window._changes.clear_draft()
        self._open(1)
        self._open(0)
        self.assertEqual(CommitDraft(""), self._box())

    def test_a_commit_ends_the_draft(self) -> None:
        self.window._changes.set_draft(CommitDraft("Commit this"))
        self.window._do_commit(self._box(), ["change.txt"])
        self.assertEqual(CommitDraft(""), self._box())
        self._open(1)
        self._open(0)
        self.assertEqual(CommitDraft(""), self._box())
        self.assertNotIn(self.entries[0].key, self.window._drafts)

    def test_a_removed_project_takes_its_draft_along(self) -> None:
        self.window._changes.set_draft(CommitDraft("Gone soon"))
        self._open(1)
        self.window._confirm = lambda *_args: True
        self.window._prompt_remove_repo(self.entries[0].key)
        self.assertNotIn(self.entries[0].key, self.window._drafts)


if __name__ == "__main__":
    unittest.main()
