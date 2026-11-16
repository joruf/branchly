"""
Tests for running slow actions off the window's thread.

What has to hold is mostly about threads and about not getting in the way:

* **The work runs on another thread and the answer comes back on this one**, so
  that the window is never the one waiting and never touched from outside.
* **One action at a time per project**, because two git commands writing to one
  repository at once are a lock error or worse. A second request is refused, not
  queued: a click made twice must not be an action done twice.
* **Different projects do not wait for each other.**
* **Whatever goes wrong is reported and the indicator ends.** An exception in the
  work, or in the callback, must not leave an hourglass turning for ever.
* **After shutdown nothing is called**, because the callbacks would be touching
  widgets that are gone.
"""

from __future__ import annotations

import os
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, QTimer
    from PySide6.QtWidgets import QApplication

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")


@requires_qt
class BackgroundTasksTests(unittest.TestCase):
    """
    The runner on its own, with nothing of Branchly around it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from services.tasks import BackgroundTasks

        self.tasks = BackgroundTasks()
        self.addCleanup(self.tasks.shutdown, 2000)
        self.addCleanup(self.tasks.deleteLater)

    def test_the_work_runs_elsewhere_and_the_answer_comes_back_here(self) -> None:
        here = threading.get_ident()
        seen: dict[str, int] = {}

        def work() -> str:
            seen["work"] = threading.get_ident()
            return "result"

        def done(result: str) -> None:
            seen["done"] = threading.get_ident()
            seen["value"] = result  # type: ignore[assignment]

        self.assertTrue(self.tasks.submit("a", "tasks.saving", work, done))
        self.assertTrue(self.tasks.wait_until_idle(5000))

        self.assertNotEqual(here, seen["work"])
        self.assertEqual(here, seen["done"])
        self.assertEqual("result", seen["value"])

    def test_it_says_what_is_running_and_that_it_stopped(self) -> None:
        release = threading.Event()
        started = threading.Event()

        def work() -> None:
            started.set()
            release.wait(5)

        changes: list[int] = []
        self.tasks.changed.connect(lambda: changes.append(len(self.tasks.running)))
        self.tasks.submit("a", "tasks.uploading", work, lambda _r: None)
        started.wait(5)

        self.assertTrue(self.tasks.is_busy())
        self.assertTrue(self.tasks.is_busy("a"))
        self.assertFalse(self.tasks.is_busy("b"))
        self.assertEqual("tasks.uploading", self.tasks.label_for("a"))
        self.assertEqual({"a"}, self.tasks.busy_keys())

        release.set()
        self.tasks.wait_until_idle(5000)

        self.assertFalse(self.tasks.is_busy())
        self.assertEqual("", self.tasks.label_for("a"))
        self.assertEqual(set(), self.tasks.busy_keys())
        # Once when it started, once when it ended.
        self.assertEqual([1, 0], changes)

    def test_a_second_request_for_a_busy_project_is_refused(self) -> None:
        release = threading.Event()
        started = threading.Event()
        calls: list[str] = []

        def slow() -> None:
            started.set()
            release.wait(5)

        self.assertTrue(self.tasks.submit("a", "tasks.saving", slow, lambda _r: calls.append("first")))
        started.wait(5)

        refused = self.tasks.submit("a", "tasks.saving", lambda: None, lambda _r: calls.append("second"))

        self.assertFalse(refused)
        release.set()
        self.tasks.wait_until_idle(5000)
        self.assertEqual(["first"], calls)

    def test_different_projects_run_at_the_same_time(self) -> None:
        both_inside = threading.Barrier(2, timeout=5)
        finished: list[str] = []

        def work() -> None:
            # Only returns when the other one is inside as well, which is only
            # possible when the two really run side by side.
            both_inside.wait()

        for key in ("a", "b"):
            self.assertTrue(self.tasks.submit(key, "tasks.saving", work, lambda _r, k=key: finished.append(k)))

        self.assertTrue(self.tasks.wait_until_idle(8000))
        self.assertEqual(["a", "b"], sorted(finished))

    def test_an_action_without_a_project_is_never_refused(self) -> None:
        release = threading.Event()
        self.assertTrue(self.tasks.submit("", "tasks.saving", lambda: release.wait(5), lambda _r: None))
        self.assertTrue(self.tasks.submit("", "tasks.saving", lambda: release.wait(5), lambda _r: None))
        release.set()
        self.assertTrue(self.tasks.wait_until_idle(5000))

    def test_the_project_can_start_again_from_inside_the_callback(self) -> None:
        order: list[str] = []

        def first_done(_result: object) -> None:
            order.append("first")
            self.assertTrue(
                self.tasks.submit("a", "tasks.uploading", lambda: None, lambda _r: order.append("second"))
            )

        self.tasks.submit("a", "tasks.saving", lambda: None, first_done)
        self.assertTrue(self.tasks.wait_until_idle(5000))
        self.assertEqual(["first", "second"], order)

    def test_an_exception_in_the_work_is_handed_to_the_failure_callback(self) -> None:
        errors: list[BaseException] = []
        done: list[object] = []

        def work() -> None:
            raise ValueError("boom")

        self.tasks.submit("a", "tasks.saving", work, done.append, failed=errors.append)
        self.assertTrue(self.tasks.wait_until_idle(5000))

        self.assertEqual(1, len(errors))
        self.assertIsInstance(errors[0], ValueError)
        self.assertEqual([], done)
        self.assertFalse(self.tasks.is_busy())

    def test_an_exception_without_a_failure_callback_still_ends_the_action(self) -> None:
        import contextlib
        import io

        def work() -> None:
            raise RuntimeError("nobody listens")

        with contextlib.redirect_stderr(io.StringIO()):
            self.tasks.submit("a", "tasks.saving", work, lambda _r: None)
            self.assertTrue(self.tasks.wait_until_idle(5000))
        self.assertFalse(self.tasks.is_busy())

    def test_a_callback_that_raises_does_not_leave_the_action_running(self) -> None:
        import contextlib
        import io

        def done(_result: object) -> None:
            raise RuntimeError("in the callback")

        changes: list[int] = []
        self.tasks.changed.connect(lambda: changes.append(len(self.tasks.running)))
        with contextlib.redirect_stderr(io.StringIO()):
            self.tasks.submit("a", "tasks.saving", lambda: None, done)
            self.assertTrue(self.tasks.wait_until_idle(5000))
        self.assertFalse(self.tasks.is_busy())
        self.assertEqual(0, changes[-1])
        # And the project is free for the next action.
        self.assertTrue(self.tasks.submit("a", "tasks.saving", lambda: None, lambda _r: None))
        self.tasks.wait_until_idle(5000)

    def test_the_window_keeps_answering_while_the_work_waits(self) -> None:
        ticks: list[float] = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        timer.start(10)
        self.addCleanup(timer.stop)

        self.tasks.submit("a", "tasks.uploading", lambda: time.sleep(0.6), lambda _r: None)
        self.assertTrue(self.tasks.wait_until_idle(5000))

        # A blocked event loop would have produced a handful of ticks, not dozens.
        self.assertGreater(len(ticks), 20)
        gaps = [later - earlier for earlier, later in zip(ticks, ticks[1:])]
        self.assertLess(max(gaps), 0.25)

    def test_wait_gives_up_when_the_work_does_not_end(self) -> None:
        release = threading.Event()
        self.tasks.submit("a", "tasks.saving", lambda: release.wait(5), lambda _r: None)
        self.assertFalse(self.tasks.wait_until_idle(150))
        release.set()
        self.assertTrue(self.tasks.wait_until_idle(5000))

    def test_after_shutdown_nothing_is_called_and_nothing_starts(self) -> None:
        release = threading.Event()
        started = threading.Event()
        called: list[object] = []

        def work() -> None:
            started.set()
            release.wait(5)

        self.tasks.submit("a", "tasks.saving", work, called.append)
        started.wait(5)
        release.set()
        self.tasks.shutdown(3000)
        for _ in range(20):
            QCoreApplication.processEvents()
            time.sleep(0.005)

        self.assertEqual([], called)
        self.assertFalse(self.tasks.submit("b", "tasks.saving", lambda: None, called.append))

    def test_the_oldest_is_listed_first(self) -> None:
        release = threading.Event()
        for key in ("a", "b", "c"):
            self.tasks.submit(key, "tasks.saving", lambda: release.wait(5), lambda _r: None)
        self.assertEqual(["a", "b", "c"], [task.key for task in self.tasks.running])
        release.set()
        self.tasks.wait_until_idle(5000)


if __name__ == "__main__":
    unittest.main()
