"""
End-to-end tests of the window staying alive during a slow action, and of the
hourglass that says one is going on.

A click on Save or Upload used to run git on the window's own thread: the window
froze for the seconds that took, and the clicks made meanwhile piled up and were
all carried out at once afterwards. Now the action runs on another thread and the
window goes on. What has to hold, seen from the outside:

* **The window answers during the action**, measured as the longest stretch in
  which the event loop did nothing.
* **The hourglass is there while it runs and gone when it ends**: in the status
  bar, on the button that was pressed, and in the project's row in the list.
* **Nothing is blocked by it**: the user can go to another project, and an action
  there runs at the same time.
* **A click made twice is one action**, and a change to a project that is busy
  waits for the action in it instead of colliding with it.
* **What an action has to say reaches the person**, on the strip when it is for
  the project in front of them and in the status bar, with the project's name,
  when it is for another one.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QTimer
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

HOURGLASSES = ("⏳", "⌛")


def slow_uploads(repo, seconds: float = 1.2) -> None:  # noqa: ANN001 - TempRepo fixture
    """
    Makes every upload from a project take a while, as a real one does.

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
class BusyIndicatorTests(unittest.TestCase):
    """
    The hourglass and the window around a slow action.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _window(self):  # noqa: ANN202 - Qt at import time
        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="branchly-test-")
        window = MainWindow(
            AppSettings(auto_check_minutes=0, github_enabled=False, check_updates=False, language="en")
        )
        self.addCleanup(window.close)
        window._switch_scan_timer.stop()
        return window

    def _open(self, window, repo):  # noqa: ANN001, ANN202
        _outcome, entry = window._registry.add(repo.root)
        window._sidebar.refresh()
        window._activate(entry)
        window._switch_scan_timer.stop()
        window._tasks.wait_until_idle(5000)
        return entry

    def _pump(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.002)

    def _settle(self, window) -> None:  # noqa: ANN001
        deadline = time.monotonic() + 15
        quiet = None
        while time.monotonic() < deadline:
            self.app.processEvents()
            busy = window._scans.is_running or window._pending_scan_key is not None or window._tasks.is_busy()
            if busy:
                quiet = None
            elif quiet is None:
                quiet = time.monotonic()
            elif time.monotonic() - quiet > 0.2:
                return
            time.sleep(0.005)
        self.fail("the window never settled")

    def _heartbeat(self):  # noqa: ANN202
        """
        Starts a timer that records when the event loop gets to run.

        Returns:
            tuple: The list the ticks are recorded in and the timer.
        """

        ticks: list[float] = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        timer.start(10)
        self.addCleanup(timer.stop)
        return ticks

    @staticmethod
    def _longest_gap(ticks: list[float]) -> float:
        return max((later - earlier for earlier, later in zip(ticks, ticks[1:])), default=0.0)

    def _row_hourglass(self, window, key: str) -> str:  # noqa: ANN001
        row = window._sidebar._rows[key]
        return row._busy.text() if not row._busy.isHidden() else ""

    # ------------------------------------------------------------ the window lives

    @requires_posix_hooks
    def test_the_window_keeps_answering_during_a_slow_upload(self) -> None:
        with temp_repo_pair() as (first, _second, origin):
            slow_uploads(first, 1.5)
            window = self._window()
            self._open(window, first)
            first.write("new.txt", "x\n")
            window._reload_current()
            window._changes.set_draft(CommitDraft("Add new file"))
            ticks = self._heartbeat()

            window._changes._both_button.click()
            started = time.monotonic()
            self._settle(window)
            lasted = time.monotonic() - started

            self.assertGreater(lasted, 1.2)
            # Frozen for the whole upload would be one gap as long as the upload.
            self.assertLess(self._longest_gap(ticks), 0.4)
            self.assertEqual(first.head(), first.git("ls-remote", "origin", "main").stdout.split()[0])

    @requires_posix_hooks
    def test_the_hourglass_shows_in_the_status_bar_the_button_and_the_list(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            slow_uploads(first, 1.5)
            window = self._window()
            entry = self._open(window, first)
            first.write("new.txt", "x\n")
            window._reload_current()
            window._changes.set_draft(CommitDraft("Add new file"))
            self.assertTrue(window._busy_label.isHidden())

            window._changes._both_button.click()
            # Past the save, into the upload.
            self._pump(0.6)

            text = window._busy_label.text()
            self.assertFalse(window._busy_label.isHidden())
            self.assertTrue(any(glass in text for glass in HOURGLASSES))
            self.assertIn(i18n.t("tasks.saving_uploading"), text)
            button = window._changes._both_button
            self.assertIn(i18n.t("tasks.saving_uploading"), button.text())
            self.assertTrue(any(glass in button.text() for glass in HOURGLASSES))
            self.assertFalse(button.isEnabled())
            self.assertFalse(window._changes._commit_button.isEnabled())
            self.assertFalse(window._changes._push_button.isEnabled())
            self.assertIn(self._row_hourglass(window, entry.key), HOURGLASSES)

            self._settle(window)

            self.assertTrue(window._busy_label.isHidden())
            self.assertEqual("", self._row_hourglass(window, entry.key))
            self.assertNotIn(i18n.t("tasks.saving_uploading"), window._changes._both_button.text())

    @requires_posix_hooks
    def test_the_hourglass_turns_over(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            slow_uploads(first, 1.6)
            window = self._window()
            self._open(window, first)
            first.commit_file("a.txt", "a\n", "One")
            window._reload_current()

            window._do_push()
            seen: set[str] = set()
            deadline = time.monotonic() + 1.4
            while time.monotonic() < deadline:
                self.app.processEvents()
                seen.add(window._busy_label.text()[:1])
                time.sleep(0.01)
            self._settle(window)

            self.assertEqual(set(HOURGLASSES), seen & set(HOURGLASSES))

    def test_nothing_shows_when_nothing_is_going_on(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            window = self._window()
            entry = self._open(window, first)
            self.assertTrue(window._busy_label.isHidden())
            self.assertEqual("", self._row_hourglass(window, entry.key))
            self.assertFalse(window._hourglass_timer.isActive())

    def test_the_buttons_are_back_to_their_own_texts_afterwards(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            window = self._window()
            self._open(window, first)
            first.write("new.txt", "x\n")
            window._reload_current()
            window._changes.set_draft(CommitDraft("Add new file"))

            window._changes._commit_button.click()
            self._settle(window)

            # Saved but not uploaded: saving is off for lack of files, uploading on.
            self.assertTrue(window._changes._push_button.isEnabled())
            self.assertEqual(i18n.t("changes.push_step_count", count=1), window._changes._push_button.text())
            self.assertNotIn(i18n.t("tasks.saving"), window._changes._commit_button.text())

    # ----------------------------------------------------- one action at a time

    @requires_posix_hooks
    def test_a_second_action_for_a_busy_project_is_refused_and_says_so(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            slow_uploads(first, 1.5)
            window = self._window()
            self._open(window, first)
            first.commit_file("a.txt", "a\n", "One")
            window._reload_current()

            self.assertTrue(window._do_push())
            self._pump(0.1)
            again = window._do_push()

            self.assertFalse(again)
            self.assertIn(i18n.t("tasks.uploading"), window.statusBar().currentMessage())
            self._settle(window)
            self.assertEqual(1, len(first.git("ls-remote", "origin", "main").stdout.split()) // 2)

    @requires_posix_hooks
    def test_saving_twice_makes_one_version(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            window = self._window()
            self._open(window, first)
            first.write("new.txt", "x\n")
            window._reload_current()
            before = first.git("rev-list", "--count", "HEAD").stdout.strip()

            started = [
                window._do_commit(CommitDraft("Add new file"), ["new.txt"]),
                window._do_commit(CommitDraft("Add new file"), ["new.txt"]),
            ]
            self._settle(window)

            self.assertEqual([True, False], started)
            self.assertEqual(int(before) + 1, int(first.git("rev-list", "--count", "HEAD").stdout.strip()))

    @requires_posix_hooks
    def test_a_change_to_a_busy_project_waits_instead_of_colliding(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            slow_uploads(first, 1.5)
            window = self._window()
            self._open(window, first)
            first.commit_file("a.txt", "a\n", "One")
            window._reload_current()
            window._do_push()
            self._pump(0.1)

            asked: list[str] = []
            real = window._confirm
            window._confirm = lambda *a, **k: asked.append("asked") or real(*a, **k)
            window._do_merge("0" * 40)

            self.assertEqual([], asked)
            self.assertIn(window._entry.name, window.statusBar().currentMessage())
            self._settle(window)

    # ------------------------------------------------------- other projects

    @requires_posix_hooks
    def test_another_project_can_be_opened_and_used_while_one_uploads(self) -> None:
        with temp_repo_pair() as (first, second, _origin), temp_repo_pair() as (third, _fourth, _origin2):
            slow_uploads(first, 1.5)
            window = self._window()
            busy_entry = self._open(window, first)
            _o, other_entry = window._registry.add(third.root)
            window._sidebar.refresh()
            first.commit_file("a.txt", "a\n", "One")
            window._reload_current()
            window._do_push()
            self._pump(0.1)
            ticks = self._heartbeat()

            started = time.monotonic()
            window._sidebar.select_key(other_entry.key)
            window._activate(other_entry)
            switched_in = time.monotonic() - started

            self.assertLess(switched_in, 1.0)
            self.assertTrue(window._tasks.is_busy(busy_entry.key))
            # Not this project's action, so its buttons are not off ...
            third.write("fresh.txt", "x\n")
            window._reload_current()
            window._changes.set_draft(CommitDraft("Save in the other project"))
            self.assertTrue(window._changes._commit_button.isEnabled())
            # ... and its row is the one with the hourglass, with a count in the bar.
            self.assertIn(self._row_hourglass(window, busy_entry.key), HOURGLASSES)
            self.assertEqual("", self._row_hourglass(window, other_entry.key))
            self.assertIn(busy_entry.name, window._busy_label.text())

            window._changes._commit_button.click()
            self._settle(window)

            self.assertEqual("Save in the other project", third.git("log", "-1", "--format=%s").stdout.strip())
            self.assertLess(self._longest_gap(ticks), 0.5)
            self.assertEqual(first.head(), first.git("ls-remote", "origin", "main").stdout.split()[0])

    @requires_posix_hooks
    def test_switching_back_shows_the_hourglass_again(self) -> None:
        with temp_repo_pair() as (first, _second, _origin), temp_repo_pair() as (third, _fourth, _origin2):
            slow_uploads(first, 1.5)
            window = self._window()
            busy_entry = self._open(window, first)
            _o, other_entry = window._registry.add(third.root)
            window._sidebar.refresh()
            first.commit_file("a.txt", "a\n", "One")
            window._reload_current()
            window._do_push()
            self._pump(0.1)

            window._activate(other_entry)
            self.assertFalse(window._changes._push_button.text().startswith(HOURGLASSES))
            window._activate(busy_entry)

            self.assertTrue(any(glass in window._changes._push_button.text() for glass in HOURGLASSES))
            self.assertFalse(window._changes._push_button.isEnabled())
            self._settle(window)

    # ----------------------------------------------- what an action has to say

    @requires_posix_hooks
    def test_a_refusal_for_a_project_that_is_not_on_screen_goes_to_the_status_bar(self) -> None:
        with temp_repo_pair() as (first, second, _origin), temp_repo_pair() as (third, _fourth, _origin2):
            second.commit_file("theirs.txt", "y\n", "Theirs")
            second.git("push", "-q", "origin", "main")
            slow_uploads(first, 1.0)
            window = self._window()
            entry = self._open(window, first)
            _o, other_entry = window._registry.add(third.root)
            window._sidebar.refresh()
            first.commit_file("mine.txt", "x\n", "Mine")
            window._reload_current()

            window._do_push()
            self._pump(0.1)
            window._activate(other_entry)
            window._notice.setVisible(False)
            self._settle(window)

            self.assertIn(entry.name, window.statusBar().currentMessage())
            # The strip belongs to the project in front of the person.
            self.assertTrue(window._notice.isHidden())

    def test_a_refusal_for_the_project_on_screen_goes_to_the_strip(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            second.commit_file("theirs.txt", "y\n", "Theirs")
            second.git("push", "-q", "origin", "main")
            window = self._window()
            self._open(window, first)
            first.write("mine.txt", "x\n")
            window._reload_current()
            window._changes.set_draft(CommitDraft("Mine"))

            window._changes._both_button.click()
            self._settle(window)

            self.assertFalse(window._notice.isHidden())
            self.assertEqual("Mine", first.git("log", "-1", "--format=%s").stdout.strip())

    def test_the_next_message_typed_while_saving_is_kept(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            window = self._window()
            self._open(window, first)
            first.write("new.txt", "x\n")
            window._reload_current()
            window._changes.set_draft(CommitDraft("Add new file"))

            window._changes._commit_button.click()
            # Typed after the click, before the answer is taken.
            window._changes.set_draft(CommitDraft("Something else already"))
            self._settle(window)

            self.assertEqual("Something else already", window._changes.commit_draft().summary)

    # ----------------------------------------------------------------- closing

    @requires_posix_hooks
    def test_closing_during_an_action_does_not_call_back_into_a_dead_window(self) -> None:
        import contextlib
        import io

        with temp_repo_pair() as (first, _second, _origin):
            slow_uploads(first, 1.0)
            window = self._window()
            self._open(window, first)
            first.commit_file("a.txt", "a\n", "One")
            window._reload_current()
            window._do_push()
            self._pump(0.1)

            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                window.close()
                self._pump(0.3)

            self.assertNotIn("Traceback", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
