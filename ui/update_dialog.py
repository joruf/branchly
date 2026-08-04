"""
The update dialog.

One window for the whole update story: what is installed, what is available, and
a single button that fetches it and restarts. Both the network calls run on a
worker thread, because a slow GitHub must not freeze the window.

The dialog never restarts anything itself. It sets ``restart_wanted`` and closes;
the main window then shuts down normally and ``run`` starts the fresh instance
once the old one is really gone. Replacing files under a running Python is safe —
the modules are already loaded — but a restart in the middle of an event loop is
not, so it waits.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import i18n
from constants import APP_NAME, APP_VERSION
from services import updater
from ui.widgets import InlineMessage


def describe_update(info: updater.UpdateInfo) -> str:
    """
    Builds the sentence shown under "a newer version is available".

    The commit subject alone would not tell a non-developer what happens next, so
    the reassurance about their data is always appended.

    Args:
        info: The check result to describe.

    Returns:
        str: Commit subject followed by what installing it does.
    """

    hint = i18n.t("update.available_hint")
    summary = info.summary.strip()
    if not summary:
        return hint
    # A commit subject carries no closing punctuation, so it needs a separator of
    # its own or the two sentences run into each other.
    return f"{summary} — {hint}"


class UpdateWorker(QObject):
    """
    Runs one update step off the UI thread.

    Attributes:
        checked: Emitted with an ``UpdateInfo`` after a check.
        applied: Emitted with an ``UpdateOutcome`` after an install.
    """

    checked = Signal(object)
    applied = Signal(object)

    def __init__(self, install: bool = False) -> None:
        """
        Args:
            install: Whether to install the update instead of only looking.
        """

        super().__init__()
        self._install = install

    def run(self) -> None:
        """
        Performs the step and emits its outcome.

        Returns:
            None
        """

        if self._install:
            self.applied.emit(updater.apply())
            return
        self.checked.emit(updater.check())


def build_update_thread(install: bool, parent: QObject | None = None) -> tuple[QThread, UpdateWorker]:
    """
    Wires an update worker onto its own thread.

    Args:
        install: Whether the worker should install rather than check.
        parent: Owner for the thread object.

    Returns:
        tuple[QThread, UpdateWorker]: The not-yet-started thread and its worker.
    """

    worker = UpdateWorker(install)
    thread = QThread(parent)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    return thread, worker


class UpdateDialog(QDialog):
    """
    Shows the update situation and installs on request.

    Attributes:
        restart_wanted: True when the update landed and Branchly should restart.
    """

    def __init__(self, info: updater.UpdateInfo | None = None, parent: QWidget | None = None) -> None:
        """
        Args:
            info: Result of a check that already happened, so opening the dialog
                after the startup check does not ask GitHub twice. None checks now.
            parent: Parent widget.
        """

        super().__init__(parent)
        self.setWindowTitle(i18n.t("update.title"))
        self.setMinimumWidth(520)

        self.restart_wanted = False
        self._info = info
        self._installing = False
        self._thread: QThread | None = None
        self._worker: UpdateWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QLabel(APP_NAME, self)
        heading.setObjectName("Heading")
        layout.addWidget(heading)

        self._version = QLabel(i18n.t("about.version", version=APP_VERSION), self)
        self._version.setObjectName("Muted")
        layout.addWidget(self._version)

        self._notice = InlineMessage(i18n.t("update.unknown"), "", "info", self)
        layout.addWidget(self._notice)

        self._detail = QLabel("", self)
        self._detail.setObjectName("Muted")
        self._detail.setWordWrap(True)
        self._detail.setVisible(False)
        layout.addWidget(self._detail)

        layout.addStretch(1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._again = QPushButton(i18n.t("update.check_again"), self)
        self._again.clicked.connect(self._start_check)
        row.addWidget(self._again)
        row.addStretch(1)
        self._install = QPushButton(i18n.t("update.install"), self)
        self._install.setObjectName("Primary")
        self._install.clicked.connect(self._confirm_install)
        self._install.setVisible(False)
        row.addWidget(self._install)
        self._close = QPushButton(i18n.t("action.close"), self)
        self._close.clicked.connect(self.reject)
        row.addWidget(self._close)
        layout.addLayout(row)

        if info is None:
            self._start_check()
        else:
            self._show_info(info)

    @property
    def info(self) -> updater.UpdateInfo | None:
        """
        Returns the most recent check result.

        Returns:
            updater.UpdateInfo | None: What the last check found, None when no
                check has finished yet.
        """

        return self._info

    # ------------------------------------------------------------------ checking

    def _start_check(self) -> None:
        """
        Asks GitHub what the newest commit is.

        Returns:
            None
        """

        if self._thread is not None:
            return
        self._set_busy(True)
        self._notice.set_message(i18n.t("update.checking"), "", "info")
        self._detail.setVisible(False)
        self._install.setVisible(False)

        thread, worker = build_update_thread(False, self)
        worker.checked.connect(self._on_checked)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_checked(self, info: updater.UpdateInfo) -> None:
        """
        Shows what the check found.

        Args:
            info: The check result.

        Returns:
            None
        """

        self._release_thread()
        self._set_busy(False)
        self._info = info
        self._show_info(info)

    def _show_info(self, info: updater.UpdateInfo) -> None:
        """
        Renders one check result.

        Args:
            info: The result to show.

        Returns:
            None
        """

        if not info.known:
            self._notice.set_message(
                i18n.t(info.error_key or "update.check_failed"), "", "warning"
            )
            self._set_detail(info.detail)
            self._install.setVisible(False)
            return

        if info.available:
            self._notice.set_message(i18n.t("update.available"), describe_update(info), "success")
            self._set_detail(i18n.t("update.commits", local=info.local, remote=info.remote))
            self._install.setVisible(True)
            self._install.setDefault(True)
            return

        self._install.setVisible(False)
        if not info.local:
            # Not a checkout, so there is nothing to compare against. Saying "this
            # is the newest version" here would be a claim nobody can back up.
            self._notice.set_message(
                i18n.t("update.unknown_local"),
                i18n.t("update.unknown_local_hint", remote=info.remote),
                "warning",
            )
            self._set_detail("")
            return
        self._notice.set_message(i18n.t("update.current", commit=info.remote), "", "info")
        self._set_detail("")

    # ----------------------------------------------------------------- installing

    def _confirm_install(self) -> None:
        """
        Asks before replacing files and restarting.

        Returns:
            None
        """

        answer = QMessageBox.question(
            self,
            i18n.t("update.confirm_title"),
            i18n.t("update.confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_install()

    def _start_install(self) -> None:
        """
        Fetches the new version on a worker thread.

        Returns:
            None
        """

        if self._thread is not None:
            return
        self._installing = True
        self._set_busy(True)
        self._notice.set_message(i18n.t("update.installing"), "", "info")
        self._detail.setVisible(False)

        thread, worker = build_update_thread(True, self)
        worker.applied.connect(self._on_applied)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_applied(self, outcome: updater.UpdateOutcome) -> None:
        """
        Closes for a restart, or explains why the update did not happen.

        Args:
            outcome: What the install attempt did.

        Returns:
            None
        """

        self._release_thread()
        self._installing = False
        self._set_busy(False)

        if not outcome.ok:
            self._notice.set_message(
                i18n.t(outcome.error_key or "error.title"),
                i18n.t("update.blocked_dirty_hint") if outcome.error_key == updater.ERROR_DIRTY else "",
                "danger",
            )
            self._set_detail(outcome.detail)
            return

        self.restart_wanted = True
        self.accept()

    # --------------------------------------------------------------------- shared

    def _set_detail(self, text: str) -> None:
        """
        Shows or hides the technical detail line.

        Args:
            text: Detail text, empty to hide it.

        Returns:
            None
        """

        self._detail.setText(text.strip())
        self._detail.setVisible(bool(text.strip()))

    def _set_busy(self, busy: bool) -> None:
        """
        Disables the buttons while something is running.

        Args:
            busy: Whether work is in flight.

        Returns:
            None
        """

        self._again.setEnabled(not busy)
        self._install.setEnabled(not busy)
        # Closing mid-install would leave the worker writing files into a folder
        # nobody is watching any more, so that one button waits.
        self._close.setEnabled(not self._installing)

    def _release_thread(self) -> None:
        """
        Stops and forgets the worker thread.

        Returns:
            None
        """

        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
            self._thread = None
        self._worker = None

    def reject(self) -> None:
        """
        Leaves the dialog, unless an install is running.

        Covers the Close button and the Escape key, neither of which goes through
        ``closeEvent``.

        Returns:
            None
        """

        if self._installing:
            return
        self._release_thread()
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """
        Refuses the window manager's close while an install is running.

        Args:
            event: Close event.

        Returns:
            None
        """

        if self._installing:
            event.ignore()
            return
        self._release_thread()
        super().closeEvent(event)
