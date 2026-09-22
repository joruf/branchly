"""
The dialog that finds repositories on disk and offers to register them.

The list is pre-ticked on purpose. Someone who just installed Branchly wants
their projects in it, and unticking the two they do not want is less work than
ticking the twenty they do. Repositories Branchly already knows are listed as
well, so the result never looks like the scan overlooked something, but they are
shown ticked-off and untickable: there is nothing to do with them.

The walk happens on a worker thread. A home directory holds thousands of folders,
and a window that stops repainting for two seconds looks broken even when it is
not. The thread also makes cancelling possible, which matters for the one case
that is genuinely slow: a folder on a network share.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import i18n
from models.category import Category
from services import discovery
from services.discovery import Candidate
from ui.widgets import InlineMessage

_ROLE_PATH = int(Qt.ItemDataRole.UserRole)
_ROLE_KNOWN = int(Qt.ItemDataRole.UserRole) + 1


class _ScanWorker(QObject):
    """
    Walks the filesystem on a worker thread.

    Attributes:
        progress: Emitted with the number of folders looked at and the folder
            currently being looked at.
        finished: Emitted with the list of candidates.
    """

    progress = Signal(int, str)
    finished = Signal(object)

    def __init__(self, roots: list[Path], known_keys: set[str]) -> None:
        """
        Args:
            roots: Folders to search.
            known_keys: Registry keys Branchly already has.
        """

        super().__init__()
        self._roots = roots
        self._known = known_keys
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """
        Asks the walk to stop at the next folder.

        Returns:
            None
        """

        self._cancel.set()

    def run(self) -> None:
        """
        Performs the walk.

        Returns:
            None
        """

        try:
            found = discovery.scan(
                self._roots,
                known_keys=self._known,
                on_progress=lambda count, where: self.progress.emit(count, where),
                should_cancel=self._cancel.is_set,
            )
        except Exception:  # noqa: BLE001 - a failed scan must not take the dialog down
            found = []
        self.finished.emit(found)


class DiscoverDialog(QDialog):
    """
    Finds repositories below a folder and hands back the ones to register.

    Attributes:
        chosen: Working tree roots the user ticked, filled once the dialog was
            accepted.
        category: Category the new entries should go into.
    """

    def __init__(
        self,
        known_keys: set[str],
        categories: list[Category],
        roots: list[Path] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            known_keys: Registry keys Branchly already has.
            categories: Categories offered for filing the new projects.
            roots: Folders to search, defaulting to the home directory.
            parent: Parent widget.
        """

        super().__init__(parent)
        self.setWindowTitle(i18n.t("discover.title"))
        self.setMinimumSize(720, 560)

        self.chosen: list[Path] = []
        self.category = ""
        self._known = known_keys
        self._candidates: list[Candidate] = []
        self._thread: QThread | None = None
        self._worker: _ScanWorker | None = None

        column = QVBoxLayout(self)
        column.setContentsMargins(16, 16, 16, 16)
        column.setSpacing(10)

        intro = QLabel(i18n.t("discover.intro"), self)
        intro.setWordWrap(True)
        intro.setObjectName("Muted")
        column.addWidget(intro)

        column.addWidget(self._build_root_row(roots))

        self._progress = QProgressBar(self)
        # An indeterminate bar: the walk cannot know how many folders are left
        # without walking them first, and a fake percentage is worse than none.
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setVisible(False)
        column.addWidget(self._progress)

        self._status = QLabel("", self)
        self._status.setObjectName("Muted")
        column.addWidget(self._status)

        self._list = QListWidget(self)
        self._list.setToolTip(i18n.t("tip.discover_list"))
        self._list.itemChanged.connect(lambda _item: self._update_buttons())
        column.addWidget(self._list, 1)

        column.addWidget(self._build_selection_row())

        self._notice = InlineMessage("", "", "info", self)
        self._notice.setVisible(False)
        column.addWidget(self._notice)

        self._buttons = QDialogButtonBox(self)
        self._apply_button = self._buttons.addButton(
            i18n.t("discover.apply"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._apply_button.setToolTip(i18n.t("tip.discover_apply"))
        self._cancel_button = self._buttons.addButton(
            i18n.t("action.cancel"), QDialogButtonBox.ButtonRole.RejectRole
        )
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        column.addWidget(self._buttons)

        for category in categories:
            if not category.is_uncategorized:
                self._category.addItem(category.name, category.name)

        self._update_buttons()
        self.start_scan()

    # ------------------------------------------------------------------- build

    def _build_root_row(self, roots: list[Path] | None) -> QWidget:
        """
        Builds the folder field with its browse and rescan buttons.

        Args:
            roots: Folders to start from.

        Returns:
            QWidget: The row.
        """

        holder = QWidget(self)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        row.addWidget(QLabel(i18n.t("discover.root_label"), holder))
        start = (roots or discovery.default_roots())[0]
        self._root = QLineEdit(str(start), holder)
        self._root.setToolTip(i18n.t("tip.discover_root"))
        row.addWidget(self._root, 1)

        browse = QPushButton(i18n.t("action.browse"), holder)
        browse.setToolTip(i18n.t("tip.discover_browse"))
        browse.clicked.connect(self._on_browse)
        row.addWidget(browse)

        self._rescan = QPushButton(i18n.t("discover.rescan"), holder)
        self._rescan.setToolTip(i18n.t("tip.discover_rescan"))
        self._rescan.clicked.connect(self.start_scan)
        row.addWidget(self._rescan)
        return holder

    def _build_selection_row(self) -> QWidget:
        """
        Builds the tick helpers and the category picker.

        Returns:
            QWidget: The row.
        """

        holder = QWidget(self)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self._select_all = QPushButton(i18n.t("changes.select_all"), holder)
        self._select_all.setToolTip(i18n.t("tip.discover_select_all"))
        self._select_all.clicked.connect(lambda: self._set_all_checked(True))
        row.addWidget(self._select_all)

        self._select_none = QPushButton(i18n.t("changes.select_none"), holder)
        self._select_none.setToolTip(i18n.t("tip.discover_select_none"))
        self._select_none.clicked.connect(lambda: self._set_all_checked(False))
        row.addWidget(self._select_none)

        row.addStretch(1)
        row.addWidget(QLabel(i18n.t("discover.category_label"), holder))
        self._category = QComboBox(holder)
        self._category.setToolTip(i18n.t("tip.discover_category"))
        self._category.addItem(i18n.t("sidebar.uncategorized"), "")
        row.addWidget(self._category)
        return holder

    # -------------------------------------------------------------------- scan

    def start_scan(self) -> None:
        """
        Walks the chosen folder again.

        Returns:
            None
        """

        if self._thread is not None:
            return

        root = Path(self._root.text().strip()).expanduser()
        if not root.is_dir():
            self._notice.set_message(i18n.t("discover.root_missing"), "", "warning")
            self._notice.setVisible(True)
            return

        self._notice.setVisible(False)
        self._list.clear()
        self._candidates = []
        self._progress.setVisible(True)
        self._rescan.setEnabled(False)
        self._status.setText(i18n.t("discover.searching"))
        self._update_buttons()

        worker = _ScanWorker([root], self._known)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_found)
        self._worker = worker
        self._thread = thread
        thread.start()

    def _on_progress(self, count: int, where: str) -> None:
        """
        Shows how far the walk has got.

        Args:
            count: Folders looked at so far.
            where: Folder currently being looked at.

        Returns:
            None
        """

        del where
        self._status.setText(i18n.t("discover.searching_count", count=count))

    def _on_found(self, candidates: object) -> None:
        """
        Fills the list once the walk is done.

        Args:
            candidates: The candidates the walk reported.

        Returns:
            None
        """

        self._stop_thread()
        self._progress.setVisible(False)
        self._rescan.setEnabled(True)

        self._candidates = list(candidates) if isinstance(candidates, list) else []
        self._fill()

    def _stop_thread(self) -> None:
        """
        Winds the worker thread down.

        Returns:
            None
        """

        thread = self._thread
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.quit()
            thread.wait(5000)
            thread.deleteLater()

    def _fill(self) -> None:
        """
        Rebuilds the list from the last scan.

        Returns:
            None
        """

        self._list.blockSignals(True)
        self._list.clear()

        fresh = 0
        for candidate in self._candidates:
            parts = [candidate.display_path]
            if candidate.branch:
                parts.append(candidate.branch)
            if candidate.known:
                parts.append(i18n.t("discover.already_known"))
            item = QListWidgetItem(f"{candidate.name}\n{'  ·  '.join(parts)}", self._list)
            item.setData(_ROLE_PATH, str(candidate.path))
            item.setData(_ROLE_KNOWN, candidate.known)
            item.setToolTip(candidate.remote_url or str(candidate.path))
            if candidate.known:
                # Nothing to do with one Branchly already has, so it is shown but
                # not offered.
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                item.setCheckState(Qt.CheckState.Unchecked)
            else:
                fresh += 1
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(Qt.CheckState.Checked)

        self._list.blockSignals(False)

        known = len(self._candidates) - fresh
        if not self._candidates:
            self._status.setText(i18n.t("discover.none_found"))
        else:
            self._status.setText(i18n.t("discover.summary", new=fresh, known=known))
        self._update_buttons()

    # ------------------------------------------------------------------ actions

    def _on_browse(self) -> None:
        """
        Asks for the folder to search.

        Returns:
            None
        """

        chosen = QFileDialog.getExistingDirectory(
            self, i18n.t("discover.root_label"), self._root.text().strip() or str(Path.home())
        )
        if chosen:
            self._root.setText(chosen)
            self.start_scan()

    def _set_all_checked(self, checked: bool) -> None:
        """
        Ticks or unticks every offered row.

        Args:
            checked: Target state.

        Returns:
            None
        """

        self._list.blockSignals(True)
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
        self._list.blockSignals(False)
        self._update_buttons()

    def checked_paths(self) -> list[Path]:
        """
        Returns the working trees the user ticked.

        Returns:
            list[Path]: Paths to register.
        """

        picked: list[Path] = []
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            path = item.data(_ROLE_PATH)
            if isinstance(path, str) and path:
                picked.append(Path(path))
        return picked

    def _update_buttons(self) -> None:
        """
        Enables the confirming button only when something is ticked.

        Returns:
            None
        """

        count = len(self.checked_paths())
        busy = self._thread is not None
        self._apply_button.setEnabled(count > 0 and not busy)
        self._apply_button.setText(
            i18n.t("discover.apply_count", count=count) if count else i18n.t("discover.apply")
        )
        offerable = any(
            self._list.item(index).flags() & Qt.ItemFlag.ItemIsUserCheckable
            for index in range(self._list.count())
        )
        self._select_all.setEnabled(offerable)
        self._select_none.setEnabled(offerable)

    def _on_accept(self) -> None:
        """
        Takes the ticked paths and closes.

        Returns:
            None
        """

        self.chosen = self.checked_paths()
        self.category = self._category.currentData() or ""
        if not self.chosen:
            return
        self.accept()

    def reject(self) -> None:
        """
        Stops a running scan before closing.

        Returns:
            None
        """

        if self._worker is not None:
            self._worker.cancel()
        self._stop_thread()
        super().reject()
