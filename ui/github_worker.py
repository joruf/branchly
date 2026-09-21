"""
Running GitHub calls off the UI thread.

Every call in this application talks to a server over a network that may be slow
or gone, and Qt repaints nothing while a slot is running. So no widget calls the
client directly: it hands the call to a runner, keeps working, and gets the
result back in a signal on the UI thread.

The runner serialises: one call at a time per runner, queued in order. That is
not a performance compromise but the point. The client keeps an ETag cache behind
a lock, a write invalidates that cache, and letting a read overtake the write it
was meant to follow would show the user the state from before their own change.

A call that is still running when its panel goes away is not waited for. Its
result is dropped instead, because a panel that no longer exists has nothing to
show it, and blocking a window close on a fifteen second timeout is worse than a
wasted request.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

# What a queued call looks like: something to run, and what to do with the
# result once it is back on the UI thread.
Work = Callable[[], Any]
Done = Callable[[Any], None]


class _Worker(QObject):
    """
    Runs one callable on a worker thread.

    Attributes:
        finished: Emitted with whatever the callable returned, or with the
            exception it raised.
    """

    finished = Signal(object)

    def __init__(self, work: Work) -> None:
        """
        Args:
            work: The callable to run.
        """

        super().__init__()
        self._work = work

    def run(self) -> None:
        """
        Performs the call.

        Returns:
            None
        """

        try:
            outcome = self._work()
        except Exception as error:  # noqa: BLE001 - a failed call must not take the app down
            outcome = error
        self.finished.emit(outcome)


class ApiRunner(QObject):
    """
    Queues GitHub calls and delivers their results on the UI thread.

    Attributes:
        busy_changed: Emitted with True when work starts and False when the
            queue runs empty, so a panel can disable its buttons while something
            is in flight.
    """

    busy_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        """
        Args:
            parent: Parent object, which owns the runner's lifetime.
        """

        super().__init__(parent)
        self._queue: list[tuple[Work, Done]] = []
        self._thread: QThread | None = None
        self._worker: _Worker | None = None
        self._current: Done | None = None
        self._stopped = False

    @property
    def busy(self) -> bool:
        """
        Reports whether a call is in flight.

        Returns:
            bool: True while something is running or queued.
        """

        return self._thread is not None or bool(self._queue)

    def submit(self, work: Work, on_done: Done) -> None:
        """
        Queues a call.

        Args:
            work: Callable performing the API call. It runs on a worker thread,
                so it must not touch widgets.
            on_done: Called on the UI thread with the result. An exception raised
                by ``work`` is passed in as the result rather than raised, so the
                handler decides how to report it.

        Returns:
            None
        """

        if self._stopped:
            return
        was_idle = not self.busy
        self._queue.append((work, on_done))
        if was_idle:
            self.busy_changed.emit(True)
        self._start_next()

    def stop(self) -> None:
        """
        Drops the queue and stops delivering results.

        A call already in flight is left to finish on its own thread; its result
        is discarded. Waiting for it would block the window close for as long as
        the request timeout.

        Returns:
            None
        """

        self._stopped = True
        self._queue.clear()
        self._current = None
        thread = self._thread
        if thread is not None:
            thread.quit()
            # A short wait catches the common case of an already finished call
            # without turning a close into a freeze when the network is hanging.
            thread.wait(200)

    def _start_next(self) -> None:
        """
        Starts the next queued call when nothing is running.

        Returns:
            None
        """

        if self._thread is not None or not self._queue or self._stopped:
            return

        work, on_done = self._queue.pop(0)
        self._current = on_done

        thread = QThread(self)
        worker = _Worker(work)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_finished(self, outcome: object) -> None:
        """
        Delivers one result and starts whatever is next.

        Args:
            outcome: What the callable returned or raised.

        Returns:
            None
        """

        handler = self._current
        self._current = None
        thread = self._thread
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.quit()
            thread.wait(2000)
            thread.deleteLater()

        if handler is not None and not self._stopped:
            handler(outcome)

        if self._queue:
            self._start_next()
        elif not self.busy:
            self.busy_changed.emit(False)
