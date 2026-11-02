"""
End-to-end tests of handing work over: save, upload, update, and what the window
says about it afterwards.

They drive the real main window against real repositories, because the failures
worth catching here are not in one function. They are in how the pieces meet: a
scan that is still running when the upload ends, a refresh that was asked for and
never made, a badge that goes on claiming a commit is waiting after the server has
it. Each test therefore ends by looking at three things separately: what the server
has, what the project says, and what the buttons say.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
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
from tests.support import requires_git, temp_repo_pair

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")
requires_posix_hooks = unittest.skipIf(os.name == "nt", "the slow upload is made with a shell hook")

SETTLE_SECONDS = 15.0


def _write_slow_upload_hook(repo, seconds: int = 1) -> None:  # noqa: ANN001 - TempRepo fixture
    """
    Makes every upload from a project take a moment.

    A real upload takes seconds. A local one takes milliseconds, which hides every
    problem that only exists because other work goes on while the upload runs.

    Args:
        repo: Fixture whose uploads should be slow.
        seconds: How long each upload takes.

    Returns:
        None
    """

    hook = repo.root / ".git" / "hooks" / "pre-push"
    hook.write_text(f"#!/bin/sh\nsleep {seconds}\n", encoding="utf-8")
    hook.chmod(0o755)


@requires_git
@requires_qt
class SaveAndUploadEndToEndTests(unittest.TestCase):
    """
    The button that saves and uploads, from the click to the settled window.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _window(self, root: Path):  # noqa: ANN202 - Qt at import time
        """
        Builds a window on one project and lets its first scans finish.

        Args:
            root: The project.

        Returns:
            tuple: The window and the project's entry.
        """

        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="branchly-test-")
        window = MainWindow(
            AppSettings(auto_check_minutes=0, github_enabled=False, check_updates=False, language="en")
        )
        self.addCleanup(window.close)
        _outcome, entry = window._registry.add(root)
        window._sidebar.refresh()
        window._activate(entry)
        window._switch_scan_timer.stop()
        self._settle(window)
        return window, entry

    def _settle(self, window) -> None:  # noqa: ANN001 - MainWindow
        """
        Runs the event loop until no scan is running or waiting to run.

        Args:
            window: The window.

        Returns:
            None
        """

        deadline = time.time() + SETTLE_SECONDS
        quiet_since = None
        while time.time() < deadline:
            self.app.processEvents()
            busy = window._scans.is_running or window._pending_scan_key is not None
            if busy:
                quiet_since = None
            elif quiet_since is None:
                quiet_since = time.time()
            elif time.time() - quiet_since > 0.2:
                return
            time.sleep(0.01)
        self.fail("the window never settled")

    def _on_server(self, origin: Path, branch: str = "main") -> str:
        """
        Reads what the server has for a branch, bypassing every local record.

        Args:
            origin: The server's repository.
            branch: Branch to look at.

        Returns:
            str: The commit the server has, empty when it has none.
        """

        out = subprocess.run(
            ["git", "--git-dir", str(origin), "rev-parse", "--verify", "-q", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        return out

    def _save_and_upload(self, window, summary: str) -> None:  # noqa: ANN001 - MainWindow
        """
        Types the summary and presses "Save and upload".

        Args:
            window: The window.
            summary: The commit summary.

        Returns:
            None
        """

        window._reload_current()
        window._changes.set_draft(CommitDraft(summary))
        self.assertTrue(window._changes._both_button.isEnabled())
        window._changes._both_button.click()

    def test_the_server_has_the_commit_and_nothing_is_shown_as_waiting(self) -> None:
        with temp_repo_pair() as (first, _second, origin):
            window, entry = self._window(first.root)
            first.write("new.txt", "x\n")

            self._save_and_upload(window, "Add new file")
            self._settle(window)

            self.assertEqual(first.head(), self._on_server(origin))
            self.assertEqual(0, entry.status.ahead)
            self.assertEqual(i18n.t("push_all.button"), window._push_button.text())
            self.assertFalse(window._changes._push_button.isEnabled())

    @requires_posix_hooks
    def test_a_slow_upload_does_not_leave_the_commit_marked_as_waiting(self) -> None:
        # The scan that the save starts is still running when the upload ends, so
        # the refresh the upload asks for is refused. That refresh must be kept,
        # or the project goes on saying "1 waiting" and the top-right button
        # offers to upload what the server already has.
        with temp_repo_pair() as (first, _second, origin):
            _write_slow_upload_hook(first)
            window, entry = self._window(first.root)
            first.write("new.txt", "x\n")

            self._save_and_upload(window, "Add new file")
            self._settle(window)

            self.assertEqual(first.head(), self._on_server(origin))
            self.assertEqual(0, entry.status.ahead)
            self.assertEqual(i18n.t("push_all.button"), window._push_button.text())

    def test_the_content_reaches_somebody_who_clones_afterwards(self) -> None:
        with temp_repo_pair() as (first, _second, origin):
            window, _entry = self._window(first.root)
            first.write("docs/note.txt", "hello\n")

            self._save_and_upload(window, "Add note")
            self._settle(window)

            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "copy"
                subprocess.run(
                    ["git", "clone", "-q", origin.as_uri(), str(target)],
                    check=True,
                    capture_output=True,
                )
                self.assertEqual("hello\n", (target / "docs" / "note.txt").read_text(encoding="utf-8"))

    def test_a_new_branch_is_published_and_remembers_its_server_branch(self) -> None:
        with temp_repo_pair() as (first, _second, origin):
            first.git("checkout", "-q", "-b", "feature")
            window, _entry = self._window(first.root)
            first.write("feature.txt", "x\n")

            self._save_and_upload(window, "Start the feature")
            self._settle(window)

            self.assertEqual(first.head(), self._on_server(origin, "feature"))
            upstream = first.git("rev-parse", "--abbrev-ref", "feature@{upstream}").stdout.strip()
            self.assertEqual("origin/feature", upstream)

    def test_a_refused_upload_keeps_the_commit_and_keeps_saying_it_is_waiting(self) -> None:
        with temp_repo_pair() as (first, second, origin):
            second.commit_file("theirs.txt", "y\n", "theirs")
            second.git("push", "-q", "origin", "main")
            before = self._on_server(origin)
            window, entry = self._window(first.root)
            first.write("mine.txt", "x\n")

            self._save_and_upload(window, "Mine")
            self._settle(window)

            self.assertEqual(before, self._on_server(origin))
            self.assertEqual("Mine", first.git("log", "-1", "--format=%s").stdout.strip())
            self.assertEqual(1, entry.status.ahead)
            self.assertTrue(window._notice.isVisibleTo(window))

    def test_saving_alone_uploads_nothing_and_says_one_is_waiting(self) -> None:
        with temp_repo_pair() as (first, _second, origin):
            before = self._on_server(origin)
            window, entry = self._window(first.root)
            first.write("new.txt", "x\n")

            window._reload_current()
            window._changes.set_draft(CommitDraft("Only local"))
            window._changes._commit_button.click()
            self._settle(window)

            self.assertEqual(before, self._on_server(origin))
            self.assertEqual(1, entry.status.ahead)
            self.assertEqual(i18n.t("push_all.button_count", count=1), window._push_button.text())

    def test_the_upload_button_afterwards_sends_exactly_what_saving_left_behind(self) -> None:
        with temp_repo_pair() as (first, _second, origin):
            window, entry = self._window(first.root)
            first.write("new.txt", "x\n")
            window._reload_current()
            window._changes.set_draft(CommitDraft("Only local"))
            window._changes._commit_button.click()
            self._settle(window)

            window._changes._push_button.click()
            self._settle(window)

            self.assertEqual(first.head(), self._on_server(origin))
            self.assertEqual(0, entry.status.ahead)


@requires_git
@requires_qt
class RefusedScanRequestTests(unittest.TestCase):
    """
    A refresh asked for while another one runs is kept, not dropped.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _window_with_two_projects(self):  # noqa: ANN202 - Qt at import time
        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="branchly-test-")
        window = MainWindow(
            AppSettings(auto_check_minutes=0, github_enabled=False, check_updates=False, language="en")
        )
        self.addCleanup(window.close)
        return window

    def test_a_refused_request_is_remembered_and_made_good_afterwards(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            window = self._window_with_two_projects()
            _a, one = window._registry.add(first.root)
            window._registry.add(second.root)
            window._scans._running = True

            self.assertFalse(window._start_scan(one.key))
            self.assertEqual(one.key, window._pending_scan_key)

            started: list[int] = []
            window._scans._running = False
            window._scans.start = lambda requests: started.append(len(requests)) or True
            window._run_pending_scan()

            self.assertEqual([1], started)
            self.assertIsNone(window._pending_scan_key)

    def test_a_request_for_every_project_is_not_narrowed_by_a_later_single_one(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            window = self._window_with_two_projects()
            _a, one = window._registry.add(first.root)
            window._registry.add(second.root)
            window._scans._running = True

            window._start_scan("")
            window._start_scan(one.key)

            self.assertEqual("", window._pending_scan_key)
            started: list[int] = []
            window._scans._running = False
            window._scans.start = lambda requests: started.append(len(requests)) or True
            window._run_pending_scan()
            self.assertEqual([2], started)

    def test_a_started_scan_leaves_nothing_pending(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            window = self._window_with_two_projects()
            _a, one = window._registry.add(first.root)
            window._scans.start = lambda requests: True

            self.assertTrue(window._start_scan(one.key))
            self.assertIsNone(window._pending_scan_key)

    def test_nothing_is_remembered_for_a_project_that_does_not_exist(self) -> None:
        window = self._window_with_two_projects()
        window._scans._running = True
        self.assertFalse(window._start_scan("/nowhere"))
        self.assertIsNone(window._pending_scan_key)

    def test_a_closing_window_does_not_start_a_remembered_scan(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            window = self._window_with_two_projects()
            _a, one = window._registry.add(first.root)
            window._pending_scan_key = one.key
            window._closing = True
            started: list[int] = []
            window._scans.start = lambda requests: started.append(len(requests)) or True

            window._run_pending_scan()

            self.assertEqual([], started)


@requires_git
@requires_qt
class UpdateOneProjectEndToEndTests(unittest.TestCase):
    """
    "Update this project" fetches the server's commit into that project.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _run_with_real_jobs(self, window) -> list[list[str]]:  # noqa: ANN001 - MainWindow
        """
        Replaces the progress window by one that runs the real jobs at once.

        The window itself is a modal dialog with a thread pool behind it, which a
        test has no use for. What it runs is the real thing.

        Args:
            window: The main window.

        Returns:
            list[list[str]]: The keys of the projects each run covered.
        """

        import ui.main_window as module
        from services import puller

        covered: list[list[str]] = []

        class Immediate:
            def __init__(self, jobs: list, *_args: object, **_kwargs: object) -> None:
                covered.append([job.key for job in jobs])
                self._jobs = jobs
                self.results: list = []
                self.summary = None

            def exec(self) -> int:
                self.results = [puller.pull_one(job) for job in self._jobs]
                return 1

        real = module.PullAllDialog
        module.PullAllDialog = Immediate
        self.addCleanup(setattr, module, "PullAllDialog", real)
        return covered

    def test_the_servers_commit_arrives_in_that_project_only(self) -> None:
        from ui.main_window import MainWindow

        with temp_repo_pair() as (first, second, _origin), tempfile.TemporaryDirectory() as other:
            first.commit_file("shared.txt", "theirs\n", "their commit")
            first.git("push", "-q", "origin", "main")

            os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="branchly-test-")
            window = MainWindow(
                AppSettings(auto_check_minutes=0, github_enabled=False, check_updates=False, language="en")
            )
            self.addCleanup(window.close)
            _a, entry = window._registry.add(second.root)
            (Path(other) / "untouched.txt").write_text("x", encoding="utf-8")
            window._sidebar.refresh()
            window._switch_scan_timer.stop()
            covered = self._run_with_real_jobs(window)

            window._update_one(entry.key)

            self.assertEqual([[entry.key]], covered)
            self.assertEqual(first.head(), second.head())
            self.assertEqual("theirs\n", (second.root / "shared.txt").read_text(encoding="utf-8"))

    def test_a_project_with_its_own_unsaved_work_is_left_alone(self) -> None:
        from ui.main_window import MainWindow

        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("shared.txt", "theirs\n", "their commit")
            first.git("push", "-q", "origin", "main")
            second.write("README.md", "my own unsaved edit\n")
            before = second.head()

            os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="branchly-test-")
            window = MainWindow(
                AppSettings(auto_check_minutes=0, github_enabled=False, check_updates=False, language="en")
            )
            self.addCleanup(window.close)
            _a, entry = window._registry.add(second.root)
            window._sidebar.refresh()
            window._switch_scan_timer.stop()
            self._run_with_real_jobs(window)

            window._update_one(entry.key)

            self.assertEqual(before, second.head())
            self.assertEqual("my own unsaved edit\n", (second.root / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
