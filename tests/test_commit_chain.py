"""
Tests for the commit chain: save, upload, or both in one click.

Most of the time a commit is followed straight away by an upload, which took two
clicks in two places. The commit box now shows the two steps side by side, with
an arrow between them, and the button that does both underneath. What has to
hold:

* **Each button is only on when it can do something.** Saving needs ticked files
  and a summary, uploading needs something waiting (or a branch the server has
  not seen yet) and a server, and both together need all of it.
* **"Save and upload" saves first and uploads only what was saved.** A failed
  commit uploads nothing; a refused upload keeps the commit.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

import i18n
from config.app_settings import AppSettings
from gitops.commit import CommitDraft
from tests.support import requires_git, temp_repo, temp_repo_pair

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")


@requires_git
@requires_qt
class ChainTests(unittest.TestCase):
    """
    The three buttons, on a real project.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _window(self, root: Path):  # noqa: ANN202 - Qt at import time
        """
        Builds a window on one project.

        Args:
            root: The project.

        Returns:
            MainWindow: The window.
        """

        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="branchly-test-")
        window = MainWindow(AppSettings(auto_check_minutes=0, github_enabled=False, language="en"))
        self.addCleanup(window.close)
        _outcome, entry = window._registry.add(root)
        window._sidebar.refresh()
        window._activate(entry)
        window._switch_scan_timer.stop()
        return window

    def test_nothing_to_do_leaves_every_button_off(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            panel = self._window(first.root)._changes
            self.assertFalse(panel._commit_button.isEnabled())
            self.assertFalse(panel._push_button.isEnabled())
            self.assertFalse(panel._both_button.isEnabled())

    def test_a_summary_and_a_file_switch_on_saving_and_both(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            first.write("new.txt", "x\n")
            window = self._window(first.root)
            window._changes.set_draft(CommitDraft("Add new file"))
            panel = window._changes
            self.assertTrue(panel._commit_button.isEnabled())
            self.assertTrue(panel._both_button.isEnabled())
            self.assertFalse(panel._push_button.isEnabled())
            self.assertEqual(i18n.t("changes.commit_step", count=1), panel._commit_button.text())

    def test_without_a_server_only_saving_is_possible(self) -> None:
        with temp_repo() as repo:
            repo.write("new.txt", "x\n")
            window = self._window(repo.root)
            window._changes.set_draft(CommitDraft("Add new file"))
            panel = window._changes
            self.assertTrue(panel._commit_button.isEnabled())
            self.assertFalse(panel._both_button.isEnabled())
            self.assertEqual(i18n.t("changes.push_no_remote"), panel._both_button.toolTip())

    def test_a_waiting_commit_switches_on_uploading_with_its_count(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            first.commit_file("a.txt", "a\n", "one")
            first.commit_file("b.txt", "b\n", "two")
            panel = self._window(first.root)._changes
            self.assertTrue(panel._push_button.isEnabled())
            self.assertEqual(i18n.t("changes.push_step_count", count=2), panel._push_button.text())

    def test_save_and_upload_puts_the_commit_on_the_server(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            first.write("new.txt", "x\n")
            window = self._window(first.root)
            window._changes.set_draft(CommitDraft("Add new file"))
            window._changes._both_button.click()
            remote = first.git("ls-remote", "origin", "main").stdout.split()[0]
            self.assertEqual(first.head(), remote)
            self.assertEqual("Add new file", first.git("log", "-1", "--format=%s").stdout.strip())

    def test_a_failed_commit_uploads_nothing(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            window = self._window(first.root)
            pushed: list[bool] = []
            window._do_push = lambda: pushed.append(True)
            window._do_commit_and_push(CommitDraft("Nothing ticked"), [])
            self.assertEqual([], pushed)

    def test_a_refused_upload_keeps_the_commit(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            second.commit_file("theirs.txt", "y\n", "theirs")
            second.git("push", "-q", "origin", "main")
            first.write("mine.txt", "x\n")
            window = self._window(first.root)
            window._changes.set_draft(CommitDraft("Mine"))
            window._changes._both_button.click()
            self.assertEqual("Mine", first.git("log", "-1", "--format=%s").stdout.strip())
            self.assertTrue(window._notice.isVisibleTo(window))

    def test_the_upload_button_asks_for_the_push(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            first.commit_file("a.txt", "a\n", "one")
            panel = self._window(first.root)._changes
            asked: list[bool] = []
            panel.push_requested.connect(lambda: asked.append(True))
            panel._push_button.click()
            self.assertEqual([True], asked)



@requires_qt
@requires_git
class UploadAllButtonTests(unittest.TestCase):
    """
    The button at the top right uploads every project.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def test_it_is_labelled_for_every_project_and_counts_their_commits(self) -> None:
        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="branchly-test-")
        with temp_repo() as one, temp_repo() as two:
            window = MainWindow(AppSettings(auto_check_minutes=0, github_enabled=False, language="en"))
            self.addCleanup(window.close)
            _o, first = window._registry.add(one.root)
            _o, second = window._registry.add(two.root)
            first.status.ahead = 2
            second.status.ahead = 3
            window._update_push_all_button()
            self.assertEqual(i18n.t("push_all.button_count", count=5), window._push_button.text())
            self.assertEqual(i18n.t("tip.push_all_button"), window._push_button.toolTip())
            self.assertTrue(window._push_button.isEnabled())

    def test_a_click_starts_the_run_over_every_project(self) -> None:
        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="branchly-test-")
        with temp_repo() as one:
            window = MainWindow(AppSettings(auto_check_minutes=0, github_enabled=False, language="en"))
            self.addCleanup(window.close)
            window._registry.add(one.root)
            window._update_push_all_button()
            started: list[object] = []
            window._run_bulk = lambda mode: started.append(mode)
            window._push_button.click()
            self.assertEqual(1, len(started))
            self.assertEqual("push_all.title", started[0].title)


@requires_qt
class BulkSummaryTests(unittest.TestCase):
    """
    The totals of a run over every project outlive the reload after it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def test_the_summary_is_still_on_screen_after_the_reload(self) -> None:
        import ui.main_window as module
        from services.puller import RESULT_PULLED, PullResult
        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="branchly-test-")
        with temp_repo() as repo:
            window = MainWindow(AppSettings(auto_check_minutes=0, github_enabled=False, language="en"))
            self.addCleanup(window.close)
            _outcome, entry = window._registry.add(repo.root)
            window._activate(entry)
            window._switch_scan_timer.stop()
            window._start_scan = lambda _key: None

            class Stub:
                """Stands in for the window, as if one project had moved."""

                def __init__(self, *_args: object, **_kwargs: object) -> None:
                    self.results = [PullResult(key="a", state=RESULT_PULLED, commits=1)]
                    self.summary = ("Update all projects", "1 updated", "success")

                def exec(self) -> int:
                    return 1

            real = module.PullAllDialog
            module.PullAllDialog = Stub
            self.addCleanup(setattr, module, "PullAllDialog", real)

            window._pull_all()
            self.assertTrue(window._notice.isVisibleTo(window))
            self.assertEqual("Update all projects", window._notice._title.text())


if __name__ == "__main__":
    unittest.main()
