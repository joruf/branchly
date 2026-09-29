"""
The files of one commit, and getting old versions back.

Opened by a double click in the graph. The list on the left is every file the
commit touched, each ticked, with the same all-or-some box as the changes list
above it; the right side shows the change of the file under the cursor. Below
are the two things one does with an old version: save a copy somewhere, or put
it back into the project.

Both work on the ticked files and on the version *in* this commit, the file as
it was once the commit was made. A file the commit deleted comes back as it was
just before, the last version that existed.

Saving never touches the project. Putting back does, and only after a second
window has listed every file and singled out the ones whose current content
exists nowhere else. What it writes is an ordinary uncommitted change, so the
changes list shows it afterwards like any other edit.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import i18n
from gitops import diff as diff_mod
from gitops import snapshot
from gitops.diff import TARGET_COMMITS, FileDiff
from gitops.history import Commit
from gitops.status import (
    CHANGE_COLOR_TOKENS,
    CHANGE_DELETED,
    CHANGE_GLYPHS,
    CHANGE_LABEL_KEYS,
    CHANGE_MODIFIED,
)
from services import open_with
from ui.changes_panel import SelectAllBox
from ui.diff_view import DiffView
from ui.widgets import InlineMessage, token_color

_ROLE_INDEX = int(Qt.ItemDataRole.UserRole)

COLUMN_TICK = 0
COLUMN_PATH = 1
COLUMN_COUNTS = 2


@dataclass(frozen=True, slots=True)
class ViewOptions:
    """
    How the diff inside the window starts out.

    Attributes:
        mode: Diff layout.
        ignore_whitespace: Whether whitespace-only changes are hidden.
        word_level: Whether changed words are highlighted.
        full_context: Whether the whole file is shown.
        context_lines: Unchanged lines around each change otherwise.
    """

    mode: str
    ignore_whitespace: bool = False
    word_level: bool = True
    full_context: bool = False
    context_lines: int = 6


def default_download_folder() -> str:
    """
    Names the folder a first download is offered.

    Returns:
        str: The system's download folder, or the home folder without one.
    """

    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    return location or str(Path.home())


class RestoreConfirmDialog(QDialog):
    """
    Lists what putting old versions back would overwrite and asks once.

    The files whose current content has no copy anywhere, because it was never
    committed, come first, in red, with a sentence saying so. Everything else is
    a replacement that can be undone by throwing the change away again.
    """

    def __init__(
        self,
        files: list[snapshot.CommitFile],
        risky: list[str],
        short: str,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            files: Files about to be put back.
            risky: Paths among them whose current content would be lost.
            short: Short id of the commit the versions come from.
            parent: Parent widget.
        """

        super().__init__(parent)
        self._risky = set(risky)
        self.setWindowTitle(i18n.t("snapshot.confirm_title"))
        self.setMinimumWidth(580)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = i18n.plural(
            len(files), "snapshot.confirm_heading_one", "snapshot.confirm_heading_many", short=short
        )
        self._notice = InlineMessage(
            heading, i18n.t("snapshot.confirm_detail"), "warning" if risky else "info", self
        )
        layout.addWidget(self._notice)

        self._list = QListWidget(self)
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._list.setToolTip(i18n.t("tip.snapshot_confirm_list"))
        ordered = [item for item in files if item.path in self._risky]
        ordered += [item for item in files if item.path not in self._risky]
        for item in ordered:
            self._add_row(item)
        layout.addWidget(self._list, 1)

        if risky:
            warning = QLabel(
                i18n.plural(len(risky), "snapshot.risky_one", "snapshot.risky_many"), self
            )
            warning.setWordWrap(True)
            warning.setStyleSheet(f"color: {token_color('danger')};")
            layout.addWidget(warning)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setText(i18n.t("snapshot.confirm"))
            ok.setObjectName("Danger")
            ok.style().unpolish(ok)
            ok.style().polish(ok)
            ok.setToolTip(i18n.t("tip.snapshot_confirm"))
        cancel = self._buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel is not None:
            cancel.setText(i18n.t("action.cancel"))
            # A stray Return key takes the safe way out.
            cancel.setDefault(True)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        rows = min(max(len(files), 4), 14)
        self.resize(640, 220 + rows * 24 + (30 if risky else 0))

    def _add_row(self, item: snapshot.CommitFile) -> None:
        """
        Puts one file on screen with what happens to it.

        Args:
            item: The file.

        Returns:
            None
        """

        row = QListWidgetItem(self._list)
        row.setFlags(Qt.ItemFlag.ItemIsEnabled)
        if item.path in self._risky:
            note = i18n.t("snapshot.note_risky")
            token = "danger"
        else:
            # Red is kept for what would be lost. A deleted file coming back
            # loses nothing, so it reads like any other replacement.
            note = i18n.t(
                "snapshot.note_deleted" if item.kind == CHANGE_DELETED else "snapshot.note_replace"
            )
            token = "text"
        row.setText(f"{item.path}   ·   {note}")
        row.setForeground(QColor(token_color(token)))
        if item.path in self._risky:
            font = QFont(self._list.font())
            font.setBold(True)
            row.setFont(font)

    @property
    def row_texts(self) -> list[str]:
        """
        Returns what the list shows, top to bottom.

        Returns:
            list[str]: One text per row.
        """

        return [self._list.item(index).text() for index in range(self._list.count())]


class CommitFilesDialog(QDialog):
    """
    Shows every file of one commit and saves or restores their versions.
    """

    def __init__(
        self,
        repo: Path,
        commit: Commit,
        project: str,
        options: ViewOptions,
        download_folder: str = "",
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            repo: Working tree root.
            commit: The commit whose files are shown.
            project: Project name, used for the download folder.
            options: How the diff starts out.
            download_folder: Folder the last download went to, empty for none.
            parent: Parent widget.
        """

        super().__init__(parent)
        self._repo = repo
        self._commit = commit
        self._project = project
        self._download_folder = download_folder
        self._restored: list[str] = []
        self._saved_folder: Path | None = None
        self._listing = snapshot.list_files(repo, commit.oid)
        self._diffs: dict[str, FileDiff] = {}

        short = commit.short or commit.oid[:7]
        self.setWindowTitle(i18n.t("snapshot.title", short=short))
        self.resize(1180, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        subject = QLabel(commit.subject or short, self)
        font = QFont(subject.font())
        font.setBold(True)
        subject.setFont(font)
        subject.setWordWrap(True)
        layout.addWidget(subject)
        facts = QLabel(
            "  ·  ".join(part for part in (short, commit.author, commit.date[:10]) if part), self
        )
        facts.setObjectName("Muted")
        layout.addWidget(facts)

        self._notice = InlineMessage(parent=self)
        self._notice.setVisible(False)
        self._notice.action_clicked.connect(self._on_notice_action)
        layout.addWidget(self._notice)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._build_list())
        self._diff = DiffView(
            options.mode,
            options.ignore_whitespace,
            options.word_level,
            options.full_context,
            options.context_lines,
            splitter,
        )
        self._diff.set_target_choices([TARGET_COMMITS])
        self._diff.show_history_only()
        self._diff.options_changed.connect(self._reload_diffs)
        self._diff.reload_requested.connect(self._reload_diffs)
        splitter.addWidget(self._diff)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        splitter.setSizes([420, 760])
        layout.addWidget(splitter, 1)

        hint = QLabel(i18n.t("snapshot.hint"), self)
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        layout.addWidget(hint)

        layout.addLayout(self._build_buttons())

        if not self._listing.ok:
            self._notice.set_message(i18n.t(self._listing.error_key), "", "danger")
            self._notice.setVisible(True)
        self._fill()
        self._reload_diffs()
        self._update_controls()

    # ------------------------------------------------------------------ building

    def _build_list(self) -> QWidget:
        """
        Creates the file list with its select-all box.

        Returns:
            QWidget: The left side of the window.
        """

        holder = QWidget(self)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        self._select_all = SelectAllBox("", holder)
        self._select_all.setTristate(True)
        self._select_all.setToolTip(i18n.t("tip.snapshot_select_all"))
        self._select_all.clicked.connect(self._on_select_all_clicked)
        column.addWidget(self._select_all)

        self._tree = QTreeWidget(holder)
        self._tree.setColumnCount(3)
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(False)
        self._tree.setUniformRowHeights(True)
        self._tree.setToolTip(i18n.t("tip.snapshot_list"))
        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COLUMN_TICK, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COLUMN_PATH, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COLUMN_COUNTS, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.currentItemChanged.connect(self._on_current_changed)
        column.addWidget(self._tree, 1)
        return holder

    def _build_buttons(self) -> QHBoxLayout:
        """
        Creates the row of actions at the bottom.

        Returns:
            QHBoxLayout: The row.
        """

        row = QHBoxLayout()
        self._download_button = QPushButton(i18n.t("snapshot.download"), self)
        self._download_button.setObjectName("Primary")
        self._download_button.setToolTip(i18n.t("tip.snapshot_download"))
        self._download_button.clicked.connect(self._on_download_clicked)
        row.addWidget(self._download_button)

        self._restore_button = QPushButton(i18n.t("snapshot.restore"), self)
        self._restore_button.setToolTip(i18n.t("tip.snapshot_restore"))
        self._restore_button.clicked.connect(self._on_restore_clicked)
        row.addWidget(self._restore_button)
        row.addStretch(1)

        close = QPushButton(i18n.t("action.close"), self)
        close.setToolTip(i18n.t("tip.snapshot_close"))
        close.clicked.connect(self.reject)
        row.addWidget(close)
        return row

    def _fill(self) -> None:
        """
        Puts one row on screen per file, all of them ticked.

        Returns:
            None
        """

        self._tree.blockSignals(True)
        for index, item in enumerate(self._listing.files):
            row = QTreeWidgetItem(self._tree)
            row.setData(COLUMN_TICK, _ROLE_INDEX, index)
            row.setText(COLUMN_TICK, CHANGE_GLYPHS.get(item.kind, CHANGE_GLYPHS[CHANGE_MODIFIED]))
            row.setForeground(
                COLUMN_TICK, QBrush(QColor(token_color(CHANGE_COLOR_TOKENS.get(item.kind, "text"))))
            )
            label = i18n.t(CHANGE_LABEL_KEYS.get(item.kind, "status.modified"))
            row.setToolTip(COLUMN_TICK, label)
            path = f"{item.old_path} → {item.path}" if item.old_path else item.path
            row.setText(COLUMN_PATH, path)
            row.setToolTip(COLUMN_PATH, f"{label}: {path}")
            row.setText(COLUMN_COUNTS, self._counts_text(item))
            row.setForeground(COLUMN_COUNTS, QBrush(QColor(token_color("text_muted"))))
            if item.restorable:
                row.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                row.setCheckState(COLUMN_TICK, Qt.CheckState.Checked)
            else:
                # A submodule is a pointer to another project. It still belongs
                # in the list, since the commit changed it, but there is nothing
                # to save or put back.
                row.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                row.setToolTip(COLUMN_PATH, i18n.t("snapshot.submodule"))
        self._tree.blockSignals(False)
        if self._tree.topLevelItemCount():
            self._tree.setCurrentItem(self._tree.topLevelItem(0))

    @staticmethod
    def _counts_text(item: snapshot.CommitFile) -> str:
        """
        Describes the size of a file's change.

        Args:
            item: The file.

        Returns:
            str: Added and removed lines, or a word for content without lines.
        """

        if item.is_submodule:
            return i18n.t("snapshot.kind_submodule")
        if item.binary:
            return i18n.t("snapshot.kind_binary")
        return f"+{item.added}  −{item.removed}"

    # ------------------------------------------------------------------- queries

    @property
    def files(self) -> list[snapshot.CommitFile]:
        """
        Returns every file of the commit.

        Returns:
            list[CommitFile]: The files, in list order.
        """

        return list(self._listing.files)

    def ticked(self) -> list[snapshot.CommitFile]:
        """
        Returns the files whose box is ticked.

        Returns:
            list[CommitFile]: The ticked files, in list order.
        """

        chosen: list[snapshot.CommitFile] = []
        for position in range(self._tree.topLevelItemCount()):
            row = self._tree.topLevelItem(position)
            if row is None or row.checkState(COLUMN_TICK) != Qt.CheckState.Checked:
                continue
            index = row.data(COLUMN_TICK, _ROLE_INDEX)
            if isinstance(index, int):
                chosen.append(self._listing.files[index])
        return chosen

    def set_ticked(self, paths: set[str]) -> None:
        """
        Ticks exactly the given files.

        Args:
            paths: Paths to tick; every other file is unticked.

        Returns:
            None
        """

        self._tree.blockSignals(True)
        for row in self._checkable_rows():
            index = row.data(COLUMN_TICK, _ROLE_INDEX)
            chosen = isinstance(index, int) and self._listing.files[index].path in paths
            row.setCheckState(
                COLUMN_TICK, Qt.CheckState.Checked if chosen else Qt.CheckState.Unchecked
            )
        self._tree.blockSignals(False)
        self._update_controls()

    @property
    def restored(self) -> list[str]:
        """
        Returns the files put back into the project.

        Returns:
            list[str]: Paths, empty when nothing was restored.
        """

        return list(self._restored)

    @property
    def download_folder(self) -> str:
        """
        Returns the folder the last download went into.

        Returns:
            str: The chosen folder, empty when there was no download.
        """

        return self._download_folder

    def _checkable_rows(self) -> list[QTreeWidgetItem]:
        """
        Collects the rows that have a box.

        Returns:
            list[QTreeWidgetItem]: Rows of files that can be saved or put back.
        """

        rows: list[QTreeWidgetItem] = []
        for position in range(self._tree.topLevelItemCount()):
            row = self._tree.topLevelItem(position)
            if row is not None and row.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                rows.append(row)
        return rows

    # -------------------------------------------------------------------- events

    def _update_controls(self) -> None:
        """
        Matches the select-all box and the buttons to what is ticked.

        Returns:
            None
        """

        rows = self._checkable_rows()
        chosen = self.ticked()
        total = len(rows)
        if not chosen:
            state = Qt.CheckState.Unchecked
        elif len(chosen) == total:
            state = Qt.CheckState.Checked
        else:
            state = Qt.CheckState.PartiallyChecked
        self._select_all.setCheckState(state)
        self._select_all.setEnabled(total > 0)
        self._select_all.setText(
            i18n.t("changes.selected_count", selected=len(chosen), total=total) if total else ""
        )
        self._download_button.setEnabled(any(item.downloadable for item in chosen))
        self._restore_button.setEnabled(any(item.restorable for item in chosen))

    def _on_select_all_clicked(self) -> None:
        """
        Ticks or unticks every file, as the box now says.

        Returns:
            None
        """

        everything = self._select_all.checkState() == Qt.CheckState.Checked
        self.set_ticked({item.path for item in self._listing.files} if everything else set())

    def _on_item_changed(self, _item: QTreeWidgetItem, column: int) -> None:
        """
        Follows a click on one file's box.

        Args:
            _item: The row.
            column: Column that changed.

        Returns:
            None
        """

        if column == COLUMN_TICK:
            self._update_controls()

    def _on_current_changed(
        self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        """
        Shows the change of the file under the cursor.

        Args:
            current: Newly current row.
            _previous: Previously current row.

        Returns:
            None
        """

        self._show_current(current)

    def _show_current(self, row: QTreeWidgetItem | None) -> None:
        """
        Puts one file's change into the diff view.

        Args:
            row: The row, or None for nothing.

        Returns:
            None
        """

        if row is None:
            self._diff.show_many([])
            return
        index = row.data(COLUMN_TICK, _ROLE_INDEX)
        if not isinstance(index, int):
            return
        item = self._listing.files[index]
        found = self._diffs.get(item.path) or self._diffs.get(item.old_path)
        self._diff.show_many([found] if found is not None else [], item.path)

    def _reload_diffs(self) -> None:
        """
        Reads the commit's changes again with the diff view's options.

        Returns:
            None
        """

        diffs = diff_mod.commit_diff(
            self._repo,
            self._commit.oid,
            self._diff.ignore_whitespace,
            self._diff.word_level,
            context_lines=self._diff.context_lines,
        )
        self._diffs = {}
        for item in diffs:
            self._diffs.setdefault(item.path or item.old_path, item)
        self._show_current(self._tree.currentItem())

    # ------------------------------------------------------------------ download

    def choose_folder(self) -> str:
        """
        Asks where the files should go.

        Returns:
            str: The chosen folder, empty when the user cancelled.
        """

        start = self._download_folder or default_download_folder()
        return QFileDialog.getExistingDirectory(self, i18n.t("snapshot.choose_folder"), start)

    def _on_download_clicked(self) -> None:
        """
        Asks for a folder and saves the ticked files there.

        Returns:
            None
        """

        folder = self.choose_folder()
        if folder:
            self.download_to(folder)

    def download_to(self, folder: str) -> snapshot.ExportOutcome:
        """
        Saves the ticked files into a new folder inside ``folder``.

        Args:
            folder: Folder the user chose.

        Returns:
            ExportOutcome: What was written.
        """

        self._download_folder = folder
        outcome = snapshot.export(
            self._repo, self._commit.oid, self.ticked(), folder, self._project
        )
        self._notice.clear_actions()
        if outcome.written:
            title = i18n.plural(
                len(outcome.written), "snapshot.saved_one", "snapshot.saved_many"
            )
            detail = str(outcome.folder)
            if outcome.skipped:
                detail += "\n" + self._skipped_text(outcome.skipped)
            self._notice.set_message(title, detail, "warning" if outcome.skipped else "success")
            self._notice.add_action(
                i18n.t("snapshot.open_folder"), "open_folder", tip=i18n.t("tip.snapshot_open_folder")
            )
            self._saved_folder = outcome.folder
        else:
            self._notice.set_message(
                i18n.t("snapshot.saved_none"), self._skipped_text(outcome.skipped), "danger"
            )
        self._notice.setVisible(True)
        return outcome

    @staticmethod
    def _skipped_text(skipped: list[tuple[str, str]]) -> str:
        """
        Lists files that were left out, with the reason for each.

        Args:
            skipped: ``(path, translation key)`` pairs.

        Returns:
            str: A few lines, the rest summed up as a count.
        """

        lines = [f"{path}: {i18n.t(key)}" for path, key in skipped[:5]]
        if len(skipped) > 5:
            lines.append(i18n.t("snapshot.more", count=len(skipped) - 5))
        return "\n".join(lines)

    def _on_notice_action(self, action_id: str) -> None:
        """
        Carries out a button on the message strip.

        Args:
            action_id: Which button.

        Returns:
            None
        """

        if action_id == "open_folder" and self._saved_folder is not None:
            open_with.open_path(self._saved_folder)

    # ------------------------------------------------------------------- restore

    def confirm_restore(self, files: list[snapshot.CommitFile], risky: list[str]) -> bool:
        """
        Shows the list of what would be overwritten and waits for an answer.

        Args:
            files: Files about to be put back.
            risky: Paths whose current content would be lost.

        Returns:
            bool: True when the user agreed.
        """

        dialog = RestoreConfirmDialog(files, risky, self._commit.short or self._commit.oid[:7], self)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def _on_restore_clicked(self) -> None:
        """
        Asks once and then puts the ticked files back.

        Returns:
            None
        """

        files = [item for item in self.ticked() if item.restorable]
        if not files:
            return
        # Read now, not when the window opened: the question is about what is
        # on disk at the moment the user agrees.
        risky = snapshot.at_risk(self._repo, [item.path for item in files])
        if self.confirm_restore(files, risky):
            self.restore_now(files)

    def restore_now(self, files: list[snapshot.CommitFile]) -> snapshot.RestoreOutcome:
        """
        Puts the given files back and closes when all of them went through.

        Args:
            files: Files to put back.

        Returns:
            RestoreOutcome: What happened.
        """

        outcome = snapshot.restore(self._repo, self._commit.oid, files)
        self._restored.extend(outcome.restored)
        if outcome.ok:
            self.accept()
            return outcome
        self._notice.clear_actions()
        detail = self._skipped_text(outcome.failed)
        if outcome.detail:
            detail += f"\n{outcome.detail}"
        self._notice.set_message(
            i18n.t("snapshot.restore_partial", count=len(outcome.failed)), detail, "danger"
        )
        self._notice.setVisible(True)
        return outcome
