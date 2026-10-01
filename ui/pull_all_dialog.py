"""
The window for a run over every project: updating all of them, or sending all.

Two states in one window: how far the run has got, and what came of it. Keeping
them together means the report cannot be missed. With twenty projects, "3
updated, 2 skipped, 1 failed" is the whole point of the action, and a status bar
line would scroll past unread.

The run starts as soon as the window is on screen. Choosing the action in the
project list or the menu already was the decision, so a second button asking
the same question again only stood in the way.

It is modal on purpose. While a run is in progress, the user must not be able to
start a push or a branch switch in a project a worker is halfway through.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import i18n
from services import puller, pusher
from services.puller import PullJob, PullResult
from services.scheduler import PullCoordinator
from ui.widgets import InlineMessage, token_color

#: Theme token used for each outcome, so the list reads at a glance.
_TOKENS: dict[str, str] = {
    puller.RESULT_PULLED: "success",
    puller.RESULT_PUSHED: "success",
    puller.RESULT_CURRENT: "text_muted",
    puller.RESULT_SKIPPED: "warning",
    puller.RESULT_FAILED: "danger",
}


@dataclass(frozen=True, slots=True)
class BulkMode:
    """
    What a run does, and the words it uses for it.

    The texts are spelled out key by key rather than built from a prefix, so
    every one of them can be found where it is used.

    Attributes:
        work: What each worker does with its project.
        title: Window title.
        heading: Headline of the strip at the top.
        explain_one: What the run does, for one project.
        explain_many: What the run does, for several.
        running: Strip text while the run works.
        nothing: Text when there is no project at all.
        moved_one: Outcome of a project that moved by one commit.
        moved_many: Outcome of a project that moved by several.
        already_current: Outcome of a project with nothing to do.
        summary_moved: Count of projects that moved, for the report.
        summary_current: Count of projects with nothing to do.
        list_tip: Tooltip of the project list.
    """

    work: Callable[[PullJob], PullResult]
    title: str
    heading: str
    explain_one: str
    explain_many: str
    running: str
    nothing: str
    moved_one: str
    moved_many: str
    already_current: str
    summary_moved: str
    summary_current: str
    list_tip: str


#: Bringing every project up to the server's version.
MODE_PULL = BulkMode(
    work=puller.pull_one,
    title="pull_all.title",
    heading="pull_all.heading",
    explain_one="pull_all.explain_one",
    explain_many="pull_all.explain_many",
    running="pull_all.running",
    nothing="pull_all.nothing",
    moved_one="pull_all.moved_one",
    moved_many="pull_all.moved_many",
    already_current="pull_all.already_current",
    summary_moved="pull_all.summary_moved",
    summary_current="pull_all.summary_current",
    list_tip="tip.pull_all_list",
)

#: Sending every project's unsent commits to the server.
MODE_PUSH = BulkMode(
    work=pusher.push_one,
    title="push_all.title",
    heading="push_all.heading",
    explain_one="push_all.explain_one",
    explain_many="push_all.explain_many",
    running="push_all.running",
    nothing="push_all.nothing",
    moved_one="push_all.moved_one",
    moved_many="push_all.moved_many",
    already_current="push_all.already_current",
    summary_moved="push_all.summary_moved",
    summary_current="push_all.summary_current",
    list_tip="tip.push_all_list",
)


def describe_result(result: PullResult, mode: BulkMode = MODE_PULL) -> str:
    """
    Puts one project's outcome into a single line.

    Args:
        result: The outcome to describe.
        mode: The run it came from, which decides the words.

    Returns:
        str: Project name followed by what happened to it.
    """

    if result.changed:
        detail = i18n.plural(result.commits, mode.moved_one, mode.moved_many)
    elif result.state == puller.RESULT_CURRENT:
        detail = i18n.t(mode.already_current)
    else:
        detail = i18n.t(result.reason_key) if result.reason_key else i18n.t("error.title")
        if result.waiting:
            detail = f"{detail} · {i18n.t('pull_all.waiting', count=result.waiting)}"
    return f"{result.name or result.key}: {detail}"


class PullAllDialog(QDialog):
    """
    Runs and reports on a pull or a push over many projects.

    Attributes:
        results: Every outcome of the run, in the order they arrived.
    """

    def __init__(
        self,
        jobs: list[PullJob],
        parent: QWidget | None = None,
        mode: BulkMode = MODE_PULL,
        autostart: bool = True,
    ) -> None:
        """
        Args:
            jobs: Projects the run should cover.
            parent: Parent widget.
            mode: Pulling or pushing.
            autostart: Whether the run begins as soon as the window is shown.
                Off only for tests and screenshots that drive it themselves.
        """

        super().__init__(parent)
        self._mode = mode
        self.setWindowTitle(i18n.t(mode.title))
        self.setMinimumWidth(560)

        self._jobs = jobs
        self.results: list[PullResult] = []
        self._running = False
        self._autostart = autostart
        self._scheduled = False
        self._started = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self._notice = InlineMessage(
            i18n.t(mode.heading),
            i18n.plural(len(jobs), mode.explain_one, mode.explain_many),
            "info",
            self,
        )
        # Without this the strip swallows every spare pixel before the run starts,
        # leaving its two lines floating in an empty box.
        self._notice.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout.addWidget(self._notice)

        self._progress = QProgressBar(self)
        self._progress.setTextVisible(False)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._list = QListWidget(self)
        self._list.setAlternatingRowColors(False)
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._list.setToolTip(i18n.t(mode.list_tip))
        # Filled before the run rather than during it. A hidden list contributes
        # nothing to the window's size hint, so the window opened at the height
        # of the explanation and had no room left once the entries appeared.
        # It also shows at once which projects the run is about to touch.
        self._rows: dict[str, QListWidgetItem] = {}
        for job in jobs:
            item = QListWidgetItem(i18n.t("pull_all.pending", name=job.name), self._list)
            item.setForeground(QColor(token_color("text_muted")))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._rows[job.key] = item
        layout.addWidget(self._list, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        self._close = QPushButton(i18n.t("action.close"), self)
        self._close.setToolTip(i18n.t("tip.bulk_close"))
        self._close.clicked.connect(self.reject)
        row.addWidget(self._close)
        layout.addLayout(row)

        self._pulls = PullCoordinator(self, mode.work)
        self._pulls.result_ready.connect(self._on_result)
        self._pulls.progress.connect(self._on_progress)
        self._pulls.batch_finished.connect(self._on_finished)
        self._pulls.pull_failed.connect(self._on_worker_error)

        if not jobs:
            self._notice.set_message(
                i18n.t(mode.heading), i18n.t(mode.nothing), "info"
            )

        self.resize(620, self._preferred_height(len(jobs)))

    def _preferred_height(self, count: int) -> int:
        """
        Works out how tall the window should open.

        Tall enough for the list it holds, and never taller than the screen it
        is opening on. A window that does not fit has its bottom edge, and with
        it the Close button, somewhere below the desktop.

        Args:
            count: Number of projects in the run.

        Returns:
            int: Height in pixels.
        """

        # The explanation, the progress bar, the button and the margins.
        chrome = 190
        row = max(20, self._list.sizeHintForRow(0) if count else 24)
        wanted = chrome + row * min(max(count, 4), 14)

        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            wanted = min(wanted, int(screen.availableGeometry().height() * 0.8))
        return max(360, wanted)

    # -------------------------------------------------------------------- running

    def showEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """
        Begins the run the first time the window is on screen.

        Started from the event loop rather than right here, so the window has
        painted its list before the first result replaces a row of it.

        Args:
            event: Show event.

        Returns:
            None
        """

        super().showEvent(event)
        if self._autostart and not self._scheduled:
            self._scheduled = True
            QTimer.singleShot(0, self._begin)

    def _begin(self) -> None:
        """
        Starts the run.

        Returns:
            None
        """

        if self._running or not self._jobs:
            return
        if not self._pulls.start(self._jobs):
            return
        self._started = True
        self._running = True
        self.results = []
        self._progress.setVisible(True)
        # Nothing to cancel into: a worker halfway through would keep writing
        # into a project nobody is watching, so the way out is to let the batch
        # finish. The button stays where it is and comes back once it has.
        self._close.setEnabled(False)
        self._notice.set_message(
            i18n.t(self._mode.heading), i18n.t(self._mode.running), "info"
        )

    def _on_progress(self, done: int, total: int) -> None:
        """
        Moves the progress bar.

        Args:
            done: Finished projects.
            total: Projects in the batch.

        Returns:
            None
        """

        self._progress.setRange(0, max(1, total))
        self._progress.setValue(done)

    def _on_result(self, result: PullResult) -> None:
        """
        Adds one outcome to the list as it arrives.

        Args:
            result: The outcome.

        Returns:
            None
        """

        self.results.append(result)
        # The row is already there, waiting. Replacing its text keeps the order
        # of the list the order of the project list, rather than the order in
        # which the workers happened to finish.
        item = self._rows.get(result.key)
        if item is None:
            item = QListWidgetItem(self._list)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._rows[result.key] = item
        item.setText(describe_result(result, self._mode))
        item.setForeground(QColor(token_color(_TOKENS.get(result.state, "text"))))
        item.setToolTip(result.detail or "")
        self._list.scrollToItem(item)

    def _on_worker_error(self, key: str, message: str) -> None:
        """
        Records a worker that raised, so the report stays complete.

        Args:
            key: Registry key of the project.
            message: Exception text.

        Returns:
            None
        """

        self._on_result(
            PullResult(key=key, name=key, state=puller.RESULT_FAILED, detail=message)
        )

    def _on_finished(self) -> None:
        """
        Reports the totals and lets the window be closed again.

        Returns:
            None
        """

        self._running = False
        self._progress.setVisible(False)
        self._close.setEnabled(True)
        self._close.setDefault(True)

        summary = puller.summarize(self.results)
        parts = []
        mode = self._mode
        if summary.pulled:
            parts.append(i18n.t(mode.summary_moved, count=summary.pulled))
            parts.append(
                i18n.plural(summary.commits, mode.moved_one, mode.moved_many)
            )
        if summary.current:
            parts.append(i18n.t(mode.summary_current, count=summary.current))
        if summary.skipped:
            parts.append(i18n.t("pull_all.summary_skipped", count=summary.skipped))
        if summary.failed:
            parts.append(i18n.t("pull_all.summary_failed", count=summary.failed))

        token = "success"
        if summary.failed:
            token = "danger"
        elif summary.skipped:
            token = "warning"
        self._notice.set_message(
            i18n.t("pull_all.done"),
            " · ".join(parts) if parts else i18n.t(mode.nothing),
            token,
        )

    # --------------------------------------------------------------------- shared

    def reject(self) -> None:
        """
        Leaves the dialog, unless a batch is still writing.

        Returns:
            None
        """

        if self._running:
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """
        Refuses the window manager's close while a batch is running.

        Args:
            event: Close event.

        Returns:
            None
        """

        if self._running:
            event.ignore()
            return
        super().closeEvent(event)
