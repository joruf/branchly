"""
The "update every project" dialog.

Three states in one window: what is about to happen, how far it has got, and what
came of it. Keeping them together means the report cannot be missed — with twenty
projects, "3 pulled, 2 skipped, 1 failed" is the whole point of the action, and a
status-bar line would scroll past unread.

It is modal on purpose. This is the one bulk action in Branchly that writes to
working trees, and while it runs, the user must not be able to start a push or a
branch switch in a project a worker is halfway through.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
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
from services import puller
from services.puller import PullJob, PullResult
from services.scheduler import PullCoordinator
from ui.widgets import InlineMessage, token_color

#: Theme token used for each outcome, so the list reads at a glance.
_TOKENS: dict[str, str] = {
    puller.RESULT_PULLED: "success",
    puller.RESULT_CURRENT: "text_muted",
    puller.RESULT_SKIPPED: "warning",
    puller.RESULT_FAILED: "danger",
}


def describe_result(result: PullResult) -> str:
    """
    Puts one project's outcome into a single line.

    Args:
        result: The outcome to describe.

    Returns:
        str: Project name followed by what happened to it.
    """

    if result.state == puller.RESULT_PULLED:
        detail = i18n.plural(
            result.commits, "pull_all.arrived_one", "pull_all.arrived_many"
        )
    elif result.state == puller.RESULT_CURRENT:
        detail = i18n.t("pull_all.already_current")
    else:
        detail = i18n.t(result.reason_key) if result.reason_key else i18n.t("error.title")
        if result.waiting:
            detail = f"{detail} · {i18n.t('pull_all.waiting', count=result.waiting)}"
    return f"{result.name or result.key} — {detail}"


class PullAllDialog(QDialog):
    """
    Asks, runs, and reports on a bulk pull.

    Attributes:
        results: Every outcome of the run, in the order they arrived.
    """

    def __init__(self, jobs: list[PullJob], parent: QWidget | None = None) -> None:
        """
        Args:
            jobs: Projects the run should cover.
            parent: Parent widget.
        """

        super().__init__(parent)
        self.setWindowTitle(i18n.t("pull_all.title"))
        self.setMinimumWidth(560)

        self._jobs = jobs
        self.results: list[PullResult] = []
        self._running = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self._notice = InlineMessage(
            i18n.t("pull_all.heading"),
            i18n.plural(len(jobs), "pull_all.explain_one", "pull_all.explain_many"),
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
        self._list.setVisible(False)
        self._list.setMinimumHeight(220)
        layout.addWidget(self._list, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        self._start = QPushButton(i18n.t("pull_all.start"), self)
        self._start.setToolTip(i18n.t("tip.pull_all_start"))
        self._start.setObjectName("Primary")
        self._start.setDefault(True)
        self._start.clicked.connect(self._begin)
        row.addWidget(self._start)
        self._close = QPushButton(i18n.t("action.cancel"), self)
        self._close.clicked.connect(self.reject)
        row.addWidget(self._close)
        layout.addLayout(row)

        self._pulls = PullCoordinator(self)
        self._pulls.result_ready.connect(self._on_result)
        self._pulls.progress.connect(self._on_progress)
        self._pulls.batch_finished.connect(self._on_finished)
        self._pulls.pull_failed.connect(self._on_worker_error)

        if not jobs:
            self._notice.set_message(
                i18n.t("pull_all.heading"), i18n.t("pull_all.nothing"), "info"
            )
            self._start.setEnabled(False)

    # -------------------------------------------------------------------- running

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
        self._running = True
        self.results = []
        self._list.clear()
        self._list.setVisible(True)
        self._progress.setVisible(True)
        self._start.setEnabled(False)
        # Nothing to cancel into: a worker mid-pull would keep writing into a
        # project nobody is watching, so the way out is to let the batch finish.
        self._close.setEnabled(False)
        self._notice.set_message(
            i18n.t("pull_all.heading"), i18n.t("pull_all.running"), "info"
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
        item = QListWidgetItem(describe_result(result), self._list)
        item.setForeground(QColor(token_color(_TOKENS.get(result.state, "text"))))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        if result.detail:
            item.setToolTip(result.detail)
        self._list.scrollToBottom()

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
        self._close.setText(i18n.t("action.close"))
        self._close.setDefault(True)
        self._start.setVisible(False)

        summary = puller.summarize(self.results)
        parts = []
        if summary.pulled:
            parts.append(i18n.t("pull_all.summary_pulled", count=summary.pulled))
            parts.append(
                i18n.plural(summary.commits, "pull_all.arrived_one", "pull_all.arrived_many")
            )
        if summary.current:
            parts.append(i18n.t("pull_all.summary_current", count=summary.current))
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
            " · ".join(parts) if parts else i18n.t("pull_all.nothing"),
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
