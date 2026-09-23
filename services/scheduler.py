"""
Running scans and bulk pulls in the background.

Scanning a dozen repositories means a dozen git processes, so they go through a
thread pool with a small ceiling rather than all at once — otherwise a check
would briefly saturate the machine and make the window stutter, which is the
opposite of helpful. Bulk pulls get their own, smaller pool for the same reason.

Nothing here decides *what* a scan or a pull means; that is ``services.scanner``
and ``services.puller``. This module only handles when work runs, how much of it
at a time, and how results get back to the UI thread.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot

from services.puller import PullJob, PullResult, pull_one
from services.scanner import ScanRequest, ScanResult, scan

# Concurrent git processes. Four keeps a scan of twenty repositories quick while
# leaving the machine responsive.
MAX_CONCURRENT_SCANS = 4

# Fewer than for scans: every pull is a full fetch plus a write to the working
# tree, so three at a time keeps the network and the disk from being the thing the
# user notices.
MAX_CONCURRENT_PULLS = 3


class _WorkerSignals(QObject):
    """
    Signal carrier for a worker, because QRunnable is not a QObject.
    """

    finished = Signal(object)
    failed = Signal(str, str)


class _ScanWorker(QRunnable):
    """
    Scans one repository off the UI thread.
    """

    def __init__(self, request: ScanRequest) -> None:
        """
        Args:
            request: Repository to scan.
        """

        super().__init__()
        self.request = request
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        """
        Performs the scan and emits the outcome.

        Returns:
            None
        """

        try:
            result = scan(self.request)
        except Exception as error:  # noqa: BLE001 - a worker must never take the app down
            self.signals.failed.emit(self.request.key, str(error))
            return
        self.signals.finished.emit(result)


class ScanCoordinator(QObject):
    """
    Runs a batch of scans and reports progress.

    Attributes:
        result_ready: Emitted with each ``ScanResult`` as it arrives.
        progress: Emitted with ``(done, total)`` after each result.
        batch_finished: Emitted once when every scan in the batch has ended.
        scan_failed: Emitted with ``(key, message)`` when a worker raised.
    """

    result_ready = Signal(object)
    progress = Signal(int, int)
    batch_finished = Signal()
    scan_failed = Signal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        """
        Args:
            parent: Parent object.
        """

        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(MAX_CONCURRENT_SCANS)
        self._total = 0
        self._done = 0
        self._running = False

    @property
    def is_running(self) -> bool:
        """
        Reports whether a batch is in flight.

        Returns:
            bool: True while scans are outstanding.
        """

        return self._running

    def start(self, requests: list[ScanRequest]) -> bool:
        """
        Starts a batch of scans.

        A batch is refused while another one runs, rather than queued: the point
        of a check is a current picture, and two overlapping batches would just
        fight over the same repositories.

        Args:
            requests: Repositories to scan.

        Returns:
            bool: True when the batch started.
        """

        if self._running or not requests:
            return False
        self._running = True
        self._total = len(requests)
        self._done = 0
        self.progress.emit(0, self._total)
        for request in requests:
            worker = _ScanWorker(request)
            worker.signals.finished.connect(self._on_finished)
            worker.signals.failed.connect(self._on_failed)
            self._pool.start(worker)
        return True

    def wait(self, timeout_ms: int = 30_000) -> bool:
        """
        Blocks until the batch is done.

        Only for shutdown and for tests; the UI never calls this.

        Args:
            timeout_ms: Milliseconds to wait.

        Returns:
            bool: True when the pool drained in time.
        """

        return self._pool.waitForDone(timeout_ms)

    @Slot(object)
    def _on_finished(self, result: ScanResult) -> None:
        """
        Handles one completed scan.

        Args:
            result: The scan outcome.

        Returns:
            None
        """

        self.result_ready.emit(result)
        self._advance()

    @Slot(str, str)
    def _on_failed(self, key: str, message: str) -> None:
        """
        Handles a worker that raised.

        Args:
            key: Registry key of the repository.
            message: Exception text.

        Returns:
            None
        """

        self.scan_failed.emit(key, message)
        self._advance()

    def _advance(self) -> None:
        """
        Counts a finished scan and closes the batch when it was the last one.

        Returns:
            None
        """

        self._done += 1
        self.progress.emit(self._done, self._total)
        if self._done >= self._total:
            self._running = False
            self.batch_finished.emit()


class _PullWorker(QRunnable):
    """
    Brings one project up to date, off the UI thread.
    """

    def __init__(self, job: PullJob) -> None:
        """
        Args:
            job: Project to update.
        """

        super().__init__()
        self.job = job
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        """
        Performs the pull and emits the outcome.

        Returns:
            None
        """

        try:
            result = pull_one(self.job)
        except Exception as error:  # noqa: BLE001 - a worker must never take the app down
            self.signals.failed.emit(self.job.key, str(error))
            return
        self.signals.finished.emit(result)


class PullCoordinator(QObject):
    """
    Runs a batch of pulls and reports progress.

    Attributes:
        result_ready: Emitted with each ``PullResult`` as it arrives.
        progress: Emitted with ``(done, total)`` after each result.
        batch_finished: Emitted once when every pull in the batch has ended.
        pull_failed: Emitted with ``(key, message)`` when a worker raised.
    """

    result_ready = Signal(object)
    progress = Signal(int, int)
    batch_finished = Signal()
    pull_failed = Signal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        """
        Args:
            parent: Parent object.
        """

        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(MAX_CONCURRENT_PULLS)
        self._total = 0
        self._done = 0
        self._running = False

    @property
    def is_running(self) -> bool:
        """
        Reports whether a batch is in flight.

        Returns:
            bool: True while pulls are outstanding.
        """

        return self._running

    def start(self, jobs: list[PullJob]) -> bool:
        """
        Starts a batch of pulls.

        A second batch is refused while one runs. Two batches over the same
        working trees would fight over the index lock, and the point of the action
        is one clear answer per project.

        Args:
            jobs: Projects to update.

        Returns:
            bool: True when the batch started.
        """

        if self._running or not jobs:
            return False
        self._running = True
        self._total = len(jobs)
        self._done = 0
        self.progress.emit(0, self._total)
        for job in jobs:
            worker = _PullWorker(job)
            worker.signals.finished.connect(self._on_finished)
            worker.signals.failed.connect(self._on_failed)
            self._pool.start(worker)
        return True

    def wait(self, timeout_ms: int = 120_000) -> bool:
        """
        Blocks until the batch is done.

        Only for shutdown and for tests; the UI never calls this. The default is
        generous because every job may be a network fetch.

        Args:
            timeout_ms: Milliseconds to wait.

        Returns:
            bool: True when the pool drained in time.
        """

        return self._pool.waitForDone(timeout_ms)

    @Slot(object)
    def _on_finished(self, result: PullResult) -> None:
        """
        Handles one completed pull.

        Args:
            result: The outcome.

        Returns:
            None
        """

        self.result_ready.emit(result)
        self._advance()

    @Slot(str, str)
    def _on_failed(self, key: str, message: str) -> None:
        """
        Handles a worker that raised.

        Args:
            key: Registry key of the project.
            message: Exception text.

        Returns:
            None
        """

        self.pull_failed.emit(key, message)
        self._advance()

    def _advance(self) -> None:
        """
        Counts a finished pull and closes the batch when it was the last one.

        Returns:
            None
        """

        self._done += 1
        self.progress.emit(self._done, self._total)
        if self._done >= self._total:
            self._running = False
            self.batch_finished.emit()


class AutoCheckScheduler(QObject):
    """
    Fires a signal on an interval the user configured.

    Attributes:
        due: Emitted when it is time to check again.
    """

    due = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        """
        Args:
            parent: Parent object.
        """

        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(False)
        self._timer.timeout.connect(self.due.emit)
        self._minutes = 0

    @property
    def minutes(self) -> int:
        """
        Returns the configured interval.

        Returns:
            int: Minutes between checks, 0 when disabled.
        """

        return self._minutes

    @property
    def is_active(self) -> bool:
        """
        Reports whether the timer is running.

        Returns:
            bool: True when checks are scheduled.
        """

        return self._timer.isActive()

    def configure(self, minutes: int) -> None:
        """
        Sets the interval, starting or stopping the timer as needed.

        Args:
            minutes: Minutes between checks. Zero or less disables it.

        Returns:
            None
        """

        self._minutes = max(0, int(minutes))
        self._timer.stop()
        if self._minutes > 0:
            self._timer.setInterval(self._minutes * 60_000)
            self._timer.start()

    def stop(self) -> None:
        """
        Stops scheduled checks without forgetting the interval.

        Returns:
            None
        """

        self._timer.stop()

    def trigger_soon(self, delay_ms: int = 1500) -> None:
        """
        Asks for one check shortly from now.

        Used right after startup so the sidebar fills in without the user waiting
        for the first full interval.

        Args:
            delay_ms: Milliseconds to wait.

        Returns:
            None
        """

        QTimer.singleShot(max(0, delay_ms), self.due.emit)


class RefreshThrottle:
    """
    Keeps a repeated trigger from turning into a storm of git calls.

    Some refreshes are asked for by the user pressing a button, and those always
    run. Others are asked for by something the user does incidentally, above all
    giving the window focus: alt-tabbing between an editor and Branchly a dozen
    times must not mean a dozen rounds of ``git status``. This says how long a
    trigger stays quiet after it fired.

    The clock is ``time.monotonic``. A wall clock that jumps backwards, which
    happens on a laptop waking up or on a time sync, would otherwise block every
    refresh until it caught up again.
    """

    def __init__(self, cooldown_seconds: float) -> None:
        """
        Args:
            cooldown_seconds: How long a trigger stays quiet after firing. Zero
                or less means every trigger fires.
        """

        self._cooldown = max(0.0, float(cooldown_seconds))
        self._last: dict[str, float] = {}

    @property
    def cooldown(self) -> float:
        """
        Returns the configured quiet period.

        Returns:
            float: Seconds.
        """

        return self._cooldown

    def allow(self, key: str = "", now: float | None = None) -> bool:
        """
        Reports whether a trigger may fire, and records it when it may.

        Args:
            key: What is being throttled, so two unrelated triggers do not block
                each other. One key is enough for most callers.
            now: Current monotonic time, for tests.

        Returns:
            bool: True when the trigger fires. The time is recorded in that case
                and only then, so a refused trigger does not extend the wait.
        """

        moment = time.monotonic() if now is None else now
        if self._cooldown <= 0:
            self._last[key] = moment
            return True
        previous = self._last.get(key)
        if previous is not None and moment - previous < self._cooldown:
            return False
        self._last[key] = moment
        return True

    def forget(self, key: str = "") -> None:
        """
        Drops what is remembered about one trigger, so it may fire again at once.

        Args:
            key: The trigger.

        Returns:
            None
        """

        self._last.pop(key, None)

    def reset(self) -> None:
        """
        Forgets every trigger.

        Returns:
            None
        """

        self._last.clear()
