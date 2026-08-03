"""
The clone dialog.

Two things it does that a plain "URL and folder" form would not:

* It refuses an unsafe address before git ever sees it, and says whether the
  problem looks like a typo or like something deliberately crafted.
* It inspects the destination as you type and, when the folder already holds
  files, explains exactly what will happen instead of either refusing outright or
  cheerfully overwriting.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import i18n
import paths
from gitops import clone as clone_mod
from gitops import remote_url
from models.category import Category
from ui.widgets import InlineMessage


class _CloneWorker(QObject):
    """
    Runs a clone on a worker thread and reports progress.

    Attributes:
        progress: Emitted with each progress line git prints.
        finished: Emitted with ``(ok, message)`` when the clone ended.
    """

    progress = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, request: clone_mod.CloneRequest) -> None:
        """
        Args:
            request: Validated clone job.
        """

        super().__init__()
        self._request = request
        self._cancelled = False

    def cancel(self) -> None:
        """
        Asks the clone to stop at the next progress line.

        Returns:
            None
        """

        self._cancelled = True

    def run(self) -> None:
        """
        Performs the clone.

        Returns:
            None
        """

        result = clone_mod.clone(
            self._request,
            on_progress=self.progress.emit,
            should_cancel=lambda: self._cancelled,
        )
        self.finished.emit(result.ok, result.message)


class CloneDialog(QDialog):
    """
    Asks for an address and a destination, then clones.
    """

    def __init__(self, categories: list[Category], parent: QWidget | None = None) -> None:
        """
        Args:
            categories: Categories offered for filing the new project.
            parent: Parent widget.
        """

        super().__init__(parent)
        self.setWindowTitle(i18n.t("clone.title"))
        self.setMinimumWidth(560)

        self._thread: QThread | None = None
        self._worker: _CloneWorker | None = None
        self._result_path: Path | None = None
        self._result_category = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)

        self._url = QLineEdit(self)
        self._url.setPlaceholderText(i18n.t("clone.url_placeholder"))
        self._url.textChanged.connect(self._on_url_changed)
        form.addRow(i18n.t("clone.url_label"), self._url)

        parent_row = QWidget(self)
        parent_layout = QHBoxLayout(parent_row)
        parent_layout.setContentsMargins(0, 0, 0, 0)
        parent_layout.setSpacing(6)
        self._parent_dir = QLineEdit(parent_row)
        self._parent_dir.setText(str(paths.default_clone_parent()))
        self._parent_dir.textChanged.connect(lambda _text: self._revalidate())
        browse = QPushButton(i18n.t("action.browse"), parent_row)
        browse.clicked.connect(self._pick_directory)
        parent_layout.addWidget(self._parent_dir, 1)
        parent_layout.addWidget(browse)
        form.addRow(i18n.t("clone.target_label"), parent_row)

        self._folder = QLineEdit(self)
        self._folder.textChanged.connect(lambda _text: self._revalidate())
        form.addRow(i18n.t("clone.name_label"), self._folder)

        self._category = QComboBox(self)
        self._category.addItem(i18n.t("sidebar.uncategorized"), "")
        for category in categories:
            self._category.addItem(category.name, category.name)
        form.addRow(i18n.t("clone.category_label"), self._category)

        layout.addLayout(form)

        self._notice = InlineMessage("", "", "info", self)
        self._notice.setVisible(False)
        layout.addWidget(self._notice)

        self._progress = QProgressBar(self)
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._progress_text = QLabel("", self)
        self._progress_text.setObjectName("Muted")
        self._progress_text.setVisible(False)
        layout.addWidget(self._progress_text)

        self._buttons = QDialogButtonBox(self)
        self._start_button = self._buttons.addButton(
            i18n.t("clone.start"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._start_button.setObjectName("Primary")
        self._cancel_button = self._buttons.addButton(
            i18n.t("action.cancel"), QDialogButtonBox.ButtonRole.RejectRole
        )
        self._buttons.accepted.connect(self._start)
        self._buttons.rejected.connect(self._on_cancel)
        layout.addWidget(self._buttons)

        self._revalidate()

    # ------------------------------------------------------------------ results

    @property
    def cloned_path(self) -> Path | None:
        """
        Returns where the project ended up.

        Returns:
            Path | None: Destination directory, or None when nothing was cloned.
        """

        return self._result_path

    @property
    def chosen_category(self) -> str:
        """
        Returns the category picked for the new project.

        Returns:
            str: Category name, empty for the catch-all group.
        """

        return self._result_category

    # --------------------------------------------------------------- validation

    def _pick_directory(self) -> None:
        """
        Opens a folder picker for the destination's parent.

        Returns:
            None
        """

        chosen = QFileDialog.getExistingDirectory(
            self, i18n.t("clone.target_label"), self._parent_dir.text()
        )
        if chosen:
            self._parent_dir.setText(chosen)

    def _on_url_changed(self, text: str) -> None:
        """
        Fills in a folder name suggestion and revalidates.

        Args:
            text: The URL as typed.

        Returns:
            None
        """

        suggestion = remote_url.suggested_directory_name(text)
        if suggestion and not self._folder.isModified():
            self._folder.blockSignals(True)
            self._folder.setText(suggestion)
            self._folder.blockSignals(False)
        self._revalidate()

    def _revalidate(self) -> None:
        """
        Re-checks the URL and destination and updates the message and button.

        Returns:
            None
        """

        url_text = self._url.text().strip()
        if not url_text:
            self._notice.setVisible(False)
            self._start_button.setEnabled(False)
            return

        reason = remote_url.classify(url_text)
        if reason != remote_url.REASON_OK:
            if remote_url.is_suspicious(url_text):
                self._show(i18n.t("clone.url_suspicious"), i18n.t("clone.url_suspicious_hint"), "danger")
            else:
                self._show(i18n.t("clone.invalid_url"), i18n.t("clone.invalid_url_hint"), "warning")
            self._start_button.setEnabled(False)
            return

        parent_text = self._parent_dir.text().strip()
        if not parent_text:
            self._show(i18n.t("clone.target_missing"), "", "warning")
            self._start_button.setEnabled(False)
            return

        request = clone_mod.prepare(url_text, parent_text, self._folder.text())
        if request is None:
            self._show(i18n.t("clone.invalid_url"), i18n.t("clone.invalid_url_hint"), "warning")
            self._start_button.setEnabled(False)
            return

        inspection = clone_mod.inspect_target(request.target)
        if inspection.state == clone_mod.TARGET_IS_REPOSITORY:
            self._show(
                i18n.t("clone.nonempty_is_repo"),
                i18n.t("clone.nonempty_is_repo_hint", path=str(request.target)),
                "danger",
            )
            self._start_button.setEnabled(False)
            return
        if not inspection.can_proceed:
            self._show(i18n.t("clone.failed"), str(request.target), "danger")
            self._start_button.setEnabled(False)
            return
        if inspection.needs_confirmation:
            self._show(
                i18n.t("clone.nonempty_title"),
                i18n.t("clone.nonempty_hint", path=str(request.target), count=inspection.entry_count),
                "warning",
            )
            self._start_button.setText(i18n.t("clone.nonempty_confirm"))
            self._start_button.setEnabled(True)
            return

        self._notice.setVisible(False)
        self._start_button.setText(i18n.t("clone.start"))
        self._start_button.setEnabled(True)

    def _show(self, title: str, detail: str, token: str) -> None:
        """
        Shows a message strip.

        Args:
            title: Headline.
            detail: Explanation.
            token: Theme token naming the accent colour.

        Returns:
            None
        """

        self._notice.set_message(title, detail, token)
        self._notice.setVisible(True)

    # -------------------------------------------------------------------- clone

    def _start(self) -> None:
        """
        Kicks off the clone on a worker thread.

        Returns:
            None
        """

        if self._thread is not None:
            return
        request = clone_mod.prepare(self._url.text(), self._parent_dir.text(), self._folder.text())
        if request is None:
            self._revalidate()
            return

        self._result_category = str(self._category.currentData() or "")
        self._set_busy(True)

        worker = _CloneWorker(request)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(lambda ok, message: self._on_finished(ok, message, request.target))
        self._worker = worker
        self._thread = thread
        thread.start()

    def _set_busy(self, busy: bool) -> None:
        """
        Switches the dialog between editing and cloning.

        Args:
            busy: Whether a clone is running.

        Returns:
            None
        """

        self._progress.setVisible(busy)
        self._progress_text.setVisible(busy)
        self._start_button.setEnabled(not busy)
        self._url.setEnabled(not busy)
        self._parent_dir.setEnabled(not busy)
        self._folder.setEnabled(not busy)
        self._category.setEnabled(not busy)
        if busy:
            self._show(i18n.t("clone.working"), "", "info")

    def _on_progress(self, line: str) -> None:
        """
        Shows the latest progress line.

        Args:
            line: Text git printed.

        Returns:
            None
        """

        parsed = clone_mod.parse_progress(line)
        if parsed is not None:
            phase, percent = parsed
            self._progress.setRange(0, 100)
            self._progress.setValue(percent)
            self._progress_text.setText(f"{phase} — {percent}%")
            return
        self._progress_text.setText(line)

    def _on_finished(self, ok: bool, message: str, target: Path) -> None:
        """
        Closes the dialog on success, or explains the failure.

        Args:
            ok: Whether the clone succeeded.
            message: Text git produced.
            target: Destination directory.

        Returns:
            None
        """

        self._teardown_thread()
        self._set_busy(False)
        if ok:
            self._result_path = target
            self.accept()
            return
        self._show(i18n.t("clone.failed"), message.strip() or i18n.t("error.git_failed"), "danger")
        self._revalidate()

    def _on_cancel(self) -> None:
        """
        Cancels a running clone, or closes the dialog.

        Returns:
            None
        """

        if self._worker is not None:
            self._worker.cancel()
            self._progress_text.setText(i18n.t("action.abort"))
            return
        self.reject()

    def _teardown_thread(self) -> None:
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

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """
        Refuses to close while a clone is in flight.

        Args:
            event: Close event.

        Returns:
            None
        """

        if self._worker is not None:
            self._worker.cancel()
            self._teardown_thread()
        super().closeEvent(event)
