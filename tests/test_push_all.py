"""
Tests for sending every project to the server, and for the window starting on
its own.

What has to hold:

* **Unsent commits go out**, with their number in the report, and a project
  with nothing unsent says so instead of pretending to have sent something.
* **Nothing is forced.** A server with newer commits is a skip with the count
  waiting there, before git is even asked, and the project stays as it was.
* **A branch the server has never seen is not published on the side.**
* **Uncommitted edits are no obstacle**, since a push sends commits, not files.
* **The window starts the run itself** once it is shown. There is no second
  button asking the same question, and Close is there throughout.
"""

from __future__ import annotations

import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

import i18n
from config.app_settings import AppSettings
from services import pusher
from services.puller import (
    RESULT_CURRENT,
    RESULT_PUSHED,
    RESULT_SKIPPED,
    SKIP_DETACHED,
    SKIP_NO_REMOTE,
    PullJob,
    summarize,
)
from tests.support import requires_git, temp_repo, temp_repo_pair

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")


def job_for(key: str) -> PullJob:
    """
    Builds a job for a project that does not exist, for tests that feed results in.

    Args:
        key: Key and name for the job.

    Returns:
        PullJob: The job.
    """

    return PullJob(path=f"/nonexistent/{key}", key=key, name=key)


def job(repo, key: str = "one") -> PullJob:  # noqa: ANN001 - TempRepo
    """
    Builds a job for one fixture.

    Args:
        repo: The fixture.
        key: Key and name for the job.

    Returns:
        PullJob: The job.
    """

    return PullJob(path=repo.root, key=key, name=key)


@requires_git
class PushOneTests(unittest.TestCase):
    """
    One project, real git, a real bare server.
    """

    def test_unsent_commits_go_out(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            first.commit_file("a.txt", "a\n", "one")
            first.commit_file("b.txt", "b\n", "two")
            result = pusher.push_one(job(first))
            self.assertEqual(RESULT_PUSHED, result.state)
            self.assertEqual(2, result.commits)
            self.assertTrue(result.changed)
            self.assertEqual(
                first.head(), first.git("rev-parse", "origin/main").stdout.strip()
            )

    def test_nothing_unsent_is_nothing_to_do(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            result = pusher.push_one(job(first))
            self.assertEqual(RESULT_CURRENT, result.state)

    def test_uncommitted_edits_do_not_stop_it(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            first.commit_file("a.txt", "a\n", "one")
            first.write("draft.txt", "not committed\n")
            self.assertEqual(RESULT_PUSHED, pusher.push_one(job(first)).state)

    def test_a_server_with_newer_commits_is_skipped_not_forced(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("theirs.txt", "x\n", "theirs")
            first.git("push", "-q", "origin", "main")
            second.git("fetch", "-q")
            second.commit_file("mine.txt", "y\n", "mine")
            before = second.head()
            result = pusher.push_one(job(second))
            self.assertEqual(RESULT_SKIPPED, result.state)
            self.assertEqual(pusher.SKIP_BEHIND, result.reason_key)
            self.assertEqual(1, result.waiting)
            self.assertEqual(before, second.head())
            self.assertEqual(
                first.head(), first.git("ls-remote", "origin", "main").stdout.split()[0]
            )

    def test_a_refusal_git_reports_reads_the_same(self) -> None:
        # The server moved on after the last fetch: only git can tell.
        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("theirs.txt", "x\n", "theirs")
            first.git("push", "-q", "origin", "main")
            second.commit_file("mine.txt", "y\n", "mine")
            result = pusher.push_one(job(second))
            self.assertEqual(RESULT_SKIPPED, result.state)
            self.assertEqual(pusher.SKIP_BEHIND, result.reason_key)

    def test_a_branch_the_server_has_never_seen_stays_local(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            first.git("switch", "-q", "-c", "scratch")
            first.commit_file("s.txt", "s\n", "scratch work")
            result = pusher.push_one(job(first))
            self.assertEqual(RESULT_SKIPPED, result.state)
            self.assertEqual(pusher.SKIP_UNPUBLISHED, result.reason_key)
            self.assertEqual("", first.git("ls-remote", "origin", "scratch").stdout.strip())

    def test_no_server_and_no_branch_are_named(self) -> None:
        with temp_repo() as lonely:
            self.assertEqual(SKIP_NO_REMOTE, pusher.push_one(job(lonely)).reason_key)
        with temp_repo_pair() as (first, _second, _origin):
            first.git("checkout", "-q", "--detach")
            self.assertEqual(SKIP_DETACHED, pusher.push_one(job(first)).reason_key)

    def test_a_missing_folder_is_skipped(self) -> None:
        result = pusher.push_one(PullJob(path="/nonexistent/branchly", key="gone", name="gone"))
        self.assertEqual(RESULT_SKIPPED, result.state)

    def test_sent_projects_count_as_moved(self) -> None:
        from services.puller import PullResult

        summary = summarize(
            [
                PullResult(key="a", state=RESULT_PUSHED, commits=2),
                PullResult(key="b", state=RESULT_CURRENT),
            ]
        )
        self.assertEqual((1, 2, 1), (summary.pulled, summary.commits, summary.current))


@requires_git
@requires_qt
class WindowTests(unittest.TestCase):
    """
    The shared window, running by itself.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _drain(self, dialog, timeout: float = 90.0) -> None:  # noqa: ANN001
        """
        Spins the event loop until the run has started and finished.

        Args:
            dialog: The window.
            timeout: Seconds before giving up.

        Returns:
            None
        """

        deadline = time.monotonic() + timeout
        while (not dialog._started or dialog._running) and time.monotonic() < deadline:
            self.app.processEvents()
        self.assertFalse(dialog._running, "the run never finished")

    def test_showing_the_window_starts_the_run(self) -> None:
        from ui.pull_all_dialog import MODE_PUSH, PullAllDialog

        with temp_repo_pair() as (first, _second, _origin):
            first.commit_file("a.txt", "a\n", "one")
            dialog = PullAllDialog([job(first, "first")], mode=MODE_PUSH)
            self.addCleanup(dialog.close)
            dialog.show()
            self._drain(dialog)
            self.assertEqual([RESULT_PUSHED], [item.state for item in dialog.results])
            self.assertIn(i18n.t("push_all.moved_one", count=1), dialog._moved_list.item(0).text())
            self.assertTrue(dialog._close.isEnabled())

    def test_a_project_that_moved_jumps_right_and_keeps_its_colour(self) -> None:
        from PySide6.QtGui import QColor

        from services.puller import RESULT_CURRENT, RESULT_PULLED, PullResult
        from ui.pull_all_dialog import PullAllDialog
        from ui.widgets import token_color

        jobs = [job_for("a"), job_for("b")]
        dialog = PullAllDialog(jobs, autostart=False)
        self.addCleanup(dialog.close)
        self.assertEqual((2, 0), (dialog._list.count(), dialog._moved_list.count()))

        dialog._on_result(PullResult(key="a", name="a", state=RESULT_PULLED, commits=2))
        dialog._on_result(PullResult(key="b", name="b", state=RESULT_CURRENT))

        self.assertEqual((1, 1), (dialog._list.count(), dialog._moved_list.count()))
        moved = dialog._moved_list.item(0)
        self.assertTrue(moved.text().startswith("a:"))
        self.assertEqual(QColor(token_color("success")), moved.foreground().color())
        self.assertIn("(1)", dialog._moved_heading.text())

    def test_the_window_closes_itself_when_asked_to(self) -> None:
        from ui.pull_all_dialog import CLOSE_DELAY_MS, PullAllDialog

        with temp_repo() as repo:
            dialog = PullAllDialog([job(repo)], autostart=False, close_when_done=True)
            self.addCleanup(dialog.close)
            closed: list[int] = []
            dialog.finished.connect(closed.append)
            dialog.show()
            dialog._begin()
            self._drain(dialog)
            self.assertIsNotNone(dialog.summary)
            deadline = time.monotonic() + (CLOSE_DELAY_MS / 1000) + 5
            while not closed and time.monotonic() < deadline:
                self.app.processEvents()
            self.assertEqual([dialog.DialogCode.Accepted], closed)

    def test_without_the_setting_it_waits_for_close(self) -> None:
        from ui.pull_all_dialog import CLOSE_DELAY_MS, PullAllDialog

        with temp_repo() as repo:
            dialog = PullAllDialog([job(repo)], autostart=False)
            self.addCleanup(dialog.close)
            dialog.show()
            dialog._begin()
            self._drain(dialog)
            deadline = time.monotonic() + (CLOSE_DELAY_MS / 1000) + 0.5
            while time.monotonic() < deadline:
                self.app.processEvents()
            self.assertTrue(dialog.isVisible())

    def test_the_setting_starts_on_and_survives_a_round_trip(self) -> None:
        self.assertTrue(AppSettings().bulk_close_when_done)
        again = AppSettings.from_dict(AppSettings(bulk_close_when_done=False).to_dict())
        self.assertFalse(again.bulk_close_when_done)

    def test_there_is_no_start_button_and_close_stays(self) -> None:
        from PySide6.QtWidgets import QPushButton

        from ui.pull_all_dialog import PullAllDialog

        with temp_repo() as repo:
            dialog = PullAllDialog([job(repo)], autostart=False)
            self.addCleanup(dialog.close)
            buttons = [button.text() for button in dialog.findChildren(QPushButton)]
            self.assertEqual([i18n.t("action.close")], buttons)

    def test_close_waits_while_the_run_works(self) -> None:
        from ui.pull_all_dialog import PullAllDialog

        with temp_repo() as repo:
            dialog = PullAllDialog([job(repo)], autostart=False)
            self.addCleanup(dialog.close)
            dialog._begin()
            self.assertTrue(dialog._close.isVisibleTo(dialog))
            self.assertFalse(dialog._close.isEnabled())
            self._drain(dialog)
            self.assertTrue(dialog._close.isEnabled())

    def test_the_window_speaks_of_sending_when_it_sends(self) -> None:
        from ui.pull_all_dialog import MODE_PUSH, PullAllDialog

        dialog = PullAllDialog([], mode=MODE_PUSH, autostart=False)
        self.addCleanup(dialog.close)
        self.assertEqual(i18n.t("push_all.title"), dialog.windowTitle())

    def test_the_branch_menu_offers_it_under_the_single_push(self) -> None:
        import tempfile
        from pathlib import Path

        from config.app_settings import AppSettings
        from ui.main_window import MainWindow

        with tempfile.TemporaryDirectory() as base:
            os.environ["XDG_CONFIG_HOME"] = str(Path(base) / "config")
            window = MainWindow(AppSettings(auto_check_minutes=0, github_enabled=False, language="en"))
            self.addCleanup(window.close)
            menus = {
                menu.text(): [action.text() for action in menu.menu().actions()]
                for menu in window.menuBar().actions()
                if menu.menu() is not None
            }
            branch = menus[i18n.t("menu.branch")]
            # Right under sending the one project, and nowhere else.
            single = branch.index(i18n.t("sync.push_generic"))
            self.assertEqual(i18n.t("push_all.menu"), branch[single + 1])
            elsewhere = [
                name for name, texts in menus.items()
                if name != i18n.t("menu.branch") and i18n.t("push_all.menu") in texts
            ]
            self.assertEqual([], elsewhere)


if __name__ == "__main__":
    unittest.main()
