"""
Running a slow action off the window's thread.

Saving, uploading, fetching and the fixes for a split history are all a git call
that takes seconds, some of them over a network. Run on the window's own thread,
they freeze everything while they wait, and the clicks made in the meantime pile
up and are all carried out at once when the window wakes up again. This module is
the other way of doing it: the work goes to a thread, the window stays alive, and
what the work produced comes back on the window's thread, where it is safe to
touch the interface.

The rules that keep that safe:

* **One action at a time per project.** Two git commands writing to the same
  repository at once end in "index.lock" errors or, worse, in a half-made commit.
  A second request for a busy project is refused, and the caller says so, rather
  than queued: a click made twice should not mean the action happens twice.
* **Different projects do not wait for each other.** An upload that takes a minute
  must not hold up saving in another project.
* **The work is pure git.** It gets everything it needs as arguments and reads
  nothing from the interface. What the user sees changes only in the callback,
  which runs on the window's thread.

Nothing here knows what an action means: that is the caller's. This module only
decides where it runs and who is told when it is over.
"""

from __future__ import annotations

import itertools
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QCoreApplication, QObject, QRunnable, QThreadPool, Signal, Slot

# A few at a time: enough that a slow upload does not hold up another project,
# few enough that the disk and the network are not what the user notices.
MAX_CONCURRENT_TASKS = 4


@dataclass(frozen=True, slots=True)
class RunningTask:
    """
    One action that is under way.

    Attributes:
        key: The project it is for, empty when it belongs to no project.
        label_key: Translation key for what it is doing, in the form shown next
            to the hourglass.
    """

    key: str
    label_key: str


@dataclass(slots=True)
class _Pending:
    """
    What is kept about a task until it is over.

    Attributes:
        task: The public description.
        done: Called with the result, on the window's thread.
        failed: Called with the exception when the work raised, instead of ``done``.
    """

    task: RunningTask
    done: Callable[[Any], None]
    failed: Callable[[BaseException], None] | None


class _Signals(QObject):
    """
    Carries the end of a task from its thread to the window's.
    """

    finished = Signal(int, object, object)


class _Job(QRunnable):
    """
    Runs one piece of work and reports how it ended.
    """

    def __init__(self, token: int, work: Callable[[], Any], signals: _Signals) -> None:
        """
        Args:
            token: Identifies the task when it reports back.
            work: The call to make, on a thread of the pool.
            signals: Where to report to.
        """

        super().__init__()
        self._token = token
        self._work = work
        self._signals = signals

    @Slot()
    def run(self) -> None:
        """
        Makes the call and reports the result or the exception.

        Returns:
            None
        """

        result: Any = None
        error: BaseException | None = None
        try:
            result = self._work()
        except BaseException as caught:  # noqa: BLE001 - a thread must report, never die silently
            error = caught
        self._signals.finished.emit(self._token, result, error)


class BackgroundTasks(QObject):
    """
    Runs actions on a pool and hands their results back on the window's thread.

    Attributes:
        changed: Emitted whenever an action starts or ends, so whatever shows that
            something is going on can look at ``running`` again.
    """

    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        """
        Args:
            parent: Parent object.
        """

        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(MAX_CONCURRENT_TASKS)
        self._signals = _Signals(self)
        self._signals.finished.connect(self._on_finished)
        self._pending: dict[int, _Pending] = {}
        self._ids = itertools.count(1)
        self._closing = False

    # ------------------------------------------------------------------ asking

    @property
    def running(self) -> list[RunningTask]:
        """
        Lists the actions that are under way, oldest first.

        Returns:
            list[RunningTask]: One entry per action.
        """

        return [pending.task for _token, pending in sorted(self._pending.items())]

    def is_busy(self, key: str = "") -> bool:
        """
        Reports whether an action is under way.

        Args:
            key: A project, or empty to ask about any.

        Returns:
            bool: True when one is.
        """

        if not key:
            return bool(self._pending)
        return any(pending.task.key == key for pending in self._pending.values())

    def label_for(self, key: str) -> str:
        """
        Names what a project is busy with.

        Args:
            key: The project.

        Returns:
            str: Translation key of the newest action for it, empty when idle.
        """

        found = [task.label_key for task in self.running if task.key == key]
        return found[-1] if found else ""

    def busy_keys(self) -> set[str]:
        """
        Lists the projects that have an action under way.

        Returns:
            set[str]: Their keys.
        """

        return {task.key for task in self.running if task.key}

    # ------------------------------------------------------------------ running

    def submit(
        self,
        key: str,
        label_key: str,
        work: Callable[[], Any],
        done: Callable[[Any], None],
        failed: Callable[[BaseException], None] | None = None,
    ) -> bool:
        """
        Starts an action, unless the project is busy.

        Args:
            key: The project the action is for, empty for none. A project with an
                action already under way refuses another.
            label_key: Translation key for what the action is doing.
            work: The call to make on a pool thread. It must not touch the
                interface.
            done: Called with ``work``'s result, on the window's thread.
            failed: Called with the exception when ``work`` raised. Without it the
                exception is printed and otherwise ignored, which is better than
                losing it and worse than saying something.

        Returns:
            bool: True when it started, False when the project is busy or the
                window is closing.
        """

        if self._closing or (key and self.is_busy(key)):
            return False
        token = next(self._ids)
        self._pending[token] = _Pending(RunningTask(key, label_key), done, failed)
        self._pool.start(_Job(token, work, self._signals))
        self.changed.emit()
        return True

    @Slot(int, object, object)
    def _on_finished(self, token: int, result: Any, error: BaseException | None) -> None:
        """
        Takes the end of an action, on the window's thread.

        Args:
            token: Which action.
            result: What the work returned.
            error: What it raised, None when it did not.

        Returns:
            None
        """

        pending = self._pending.pop(token, None)
        if pending is None or self._closing:
            return
        try:
            if error is not None:
                if pending.failed is not None:
                    pending.failed(error)
                else:
                    traceback.print_exception(error)
            else:
                pending.done(result)
        except Exception:  # noqa: BLE001 - one bad callback must not leave the indicator spinning
            traceback.print_exc()
        finally:
            self.changed.emit()

    # --------------------------------------------------------------- shutting down

    def wait_until_idle(self, timeout_ms: int = 30_000) -> bool:
        """
        Lets the event loop run until every action is over.

        For tests and for the few places that must not go on before an action is
        done. The window keeps answering while this waits.

        Args:
            timeout_ms: How long to wait at most.

        Returns:
            bool: True when everything finished in time.
        """

        deadline = time.monotonic() + timeout_ms / 1000
        while self._pending and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.002)
        QCoreApplication.processEvents()
        return not self._pending

    def shutdown(self, timeout_ms: int = 5000) -> None:
        """
        Stops taking actions and gives the ones under way a moment to end.

        What ends after the window is gone is not reported to anybody: the callback
        would be touching widgets that no longer exist.

        Args:
            timeout_ms: How long to wait at most.

        Returns:
            None
        """

        self._closing = True
        self._pool.waitForDone(timeout_ms)
        self._pending.clear()
