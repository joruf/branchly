"""
The changes panel: which files changed, and the box that commits them.

Files carry a tick box rather than a separate "stage" step, because "pick the
files that belong together, then save them" is the mental model this program is
built around. Staging still happens underneath — the tick boxes are turned into
``git add`` calls right before the commit.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import i18n
from config.theme import get_theme_colors
from gitops.commit import CommitDraft
from gitops.status import CHANGE_UNTRACKED, RepositoryState
from ui.widgets import EmptyState, InlineMessage, SectionHeader

_ROLE_PATH = int(Qt.ItemDataRole.UserRole)
_ROLE_UNTRACKED = int(Qt.ItemDataRole.UserRole) + 1
_ROLE_CONFLICTED = int(Qt.ItemDataRole.UserRole) + 2


class ChangesPanel(QWidget):
    """
    Lists changed files and collects a commit.

    Attributes:
        file_selected: Emitted with ``(path, untracked)`` when a row is picked.
        commit_requested: Emitted with ``(draft, paths)``.
        discard_requested: Emitted with the paths to throw away.
        open_file_requested: Emitted with a path.
        reveal_file_requested: Emitted with a path.
        resolve_requested: Emitted when the user wants the conflict assistant.
        selection_changed: Emitted when the ticked set changed.
    """

    file_selected = Signal(str, bool)
    commit_requested = Signal(object, list)
    discard_requested = Signal(list)
    open_file_requested = Signal(str)
    reveal_file_requested = Signal(str)
    resolve_requested = Signal()
    selection_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """
        Args:
            parent: Parent widget.
        """

        super().__init__(parent)
        self._state: RepositoryState | None = None
        self._branch = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = SectionHeader(i18n.t("changes.title"), self)
        self._selected_label = QLabel("", self._header)
        self._selected_label.setObjectName("Muted")
        self._header.add_widget(self._selected_label)
        self._select_all = QPushButton(i18n.t("changes.select_all"), self._header)
        self._select_all.clicked.connect(lambda: self._set_all_checked(True))
        self._header.add_widget(self._select_all)
        self._select_none = QPushButton(i18n.t("changes.select_none"), self._header)
        self._select_none.clicked.connect(lambda: self._set_all_checked(False))
        self._header.add_widget(self._select_none)
        layout.addWidget(self._header)

        self._conflict_notice = InlineMessage(
            i18n.t("conflict.intro_title"), i18n.t("conflict.intro_hint"), "warning", self
        )
        self._conflict_notice.add_action(i18n.t("conflict.intro_start"), "resolve", primary=True)
        self._conflict_notice.action_clicked.connect(lambda _id: self.resolve_requested.emit())
        self._conflict_notice.setVisible(False)
        notice_holder = QWidget(self)
        notice_layout = QVBoxLayout(notice_holder)
        notice_layout.setContentsMargins(10, 10, 10, 0)
        notice_layout.addWidget(self._conflict_notice)
        layout.addWidget(notice_holder)
        self._notice_holder = notice_holder
        self._notice_holder.setVisible(False)

        self._stack = QStackedWidget(self)
        self._empty = EmptyState(i18n.t("changes.none_title"), i18n.t("changes.none_hint"), self)
        self._stack.addWidget(self._empty)

        self._list = QListWidget(self)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)
        self._list.currentItemChanged.connect(self._on_current_changed)
        self._list.itemChanged.connect(self._on_item_changed)
        self._stack.addWidget(self._list)

        layout.addWidget(self._stack, 1)
        layout.addWidget(self._build_commit_box())
        self._stack.setCurrentWidget(self._empty)

    def _build_commit_box(self) -> QWidget:
        """
        Builds the summary, description and commit button.

        Returns:
            QWidget: The commit box.
        """

        holder = QWidget(self)
        holder.setObjectName("PanelHeader")
        column = QVBoxLayout(holder)
        column.setContentsMargins(10, 10, 10, 10)
        column.setSpacing(6)

        self._hint = QLabel(i18n.t("changes.stage_hint"), holder)
        self._hint.setObjectName("Muted")
        self._hint.setWordWrap(True)
        column.addWidget(self._hint)

        self._summary = QLineEdit(holder)
        self._summary.setPlaceholderText(i18n.t("changes.summary_placeholder"))
        self._summary.textChanged.connect(lambda _text: self._update_commit_button())
        self._summary.returnPressed.connect(self._emit_commit)
        column.addWidget(self._summary)

        self._description = QPlainTextEdit(holder)
        self._description.setPlaceholderText(i18n.t("changes.description_placeholder"))
        self._description.setFixedHeight(64)
        column.addWidget(self._description)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self._amend = QCheckBox(i18n.t("changes.amend"), holder)
        row.addWidget(self._amend)
        row.addStretch(1)
        self._commit_button = QPushButton("", holder)
        self._commit_button.setObjectName("Primary")
        self._commit_button.clicked.connect(self._emit_commit)
        row.addWidget(self._commit_button)
        column.addLayout(row)

        self._commit_box = holder
        return holder

    # ------------------------------------------------------------------ filling

    def set_state(self, state: RepositoryState | None, branch: str = "") -> None:
        """
        Rebuilds the list from a repository state.

        Args:
            state: Freshly read state, or None to clear the panel.
            branch: Branch name for the commit button label.

        Returns:
            None
        """

        self._state = state
        self._branch = branch or (state.display_branch if state else "")

        previously_checked = self.checked_paths()
        previously_current = self.current_path()

        self._list.blockSignals(True)
        self._list.clear()

        if state is None or state.is_clean:
            self._list.blockSignals(False)
            self._stack.setCurrentWidget(self._empty)
            self._notice_holder.setVisible(False)
            self._conflict_notice.setVisible(False)
            self._commit_box.setVisible(state is not None)
            self._update_commit_button()
            self._update_selected_label()
            return

        colors = get_theme_colors()
        for change in state.files:
            item = QListWidgetItem(self._list)
            item.setData(_ROLE_PATH, change.path)
            item.setData(_ROLE_UNTRACKED, change.kind == CHANGE_UNTRACKED)
            item.setData(_ROLE_CONFLICTED, change.conflicted)
            item.setText(f"{change.display_path}")
            item.setToolTip(f"{i18n.t(change.label_key)} · {change.path}")
            item.setForeground(QBrush(QColor(getattr(colors, change.color_token, colors.text))))
            if change.conflicted:
                # A conflicted file cannot be committed until it is resolved, so
                # it must not be tickable.
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                font = QFont(self._list.font())
                font.setBold(True)
                item.setFont(font)
            else:
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                # Keep whatever the user had ticked across a refresh. On the first
                # look at a repository nothing was ticked yet, and starting with
                # everything selected matches what people expect from a commit box.
                keep = change.path in previously_checked if previously_checked else True
                item.setCheckState(Qt.CheckState.Checked if keep else Qt.CheckState.Unchecked)
        self._list.blockSignals(False)

        self._stack.setCurrentWidget(self._list)
        self._commit_box.setVisible(True)

        has_conflicts = state.has_conflicts
        self._notice_holder.setVisible(has_conflicts)
        self._conflict_notice.setVisible(has_conflicts)

        self._restore_current(previously_current)
        self._update_selected_label()
        self._update_commit_button()

    def _restore_current(self, path: str) -> None:
        """
        Re-selects a row after a rebuild.

        Args:
            path: Path that was selected before.

        Returns:
            None
        """

        target = path
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item is not None and item.data(_ROLE_PATH) == target:
                self._list.setCurrentItem(item)
                return
        if self._list.count():
            self._list.setCurrentRow(0)

    # ------------------------------------------------------------------ queries

    def current_path(self) -> str:
        """
        Returns the selected file path.

        Returns:
            str: Path, empty when nothing is selected.
        """

        item = self._list.currentItem()
        if item is None:
            return ""
        value = item.data(_ROLE_PATH)
        return value if isinstance(value, str) else ""

    def checked_paths(self) -> list[str]:
        """
        Returns the ticked file paths.

        Returns:
            list[str]: Paths the user wants to commit.
        """

        found: list[str] = []
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item is None:
                continue
            if not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                continue
            if item.checkState() == Qt.CheckState.Checked:
                value = item.data(_ROLE_PATH)
                if isinstance(value, str):
                    found.append(value)
        return found

    def commit_draft(self) -> CommitDraft:
        """
        Builds a draft from the commit box.

        Returns:
            CommitDraft: What the user typed.
        """

        return CommitDraft(
            summary=self._summary.text(),
            description=self._description.toPlainText(),
            amend=self._amend.isChecked(),
        )

    def set_draft(self, draft: CommitDraft) -> None:
        """
        Fills the commit box, for pre-loading a message to amend.

        Args:
            draft: Draft to show.

        Returns:
            None
        """

        self._summary.setText(draft.summary)
        self._description.setPlainText(draft.description)
        self._amend.setChecked(draft.amend)
        self._update_commit_button()

    def clear_draft(self) -> None:
        """
        Empties the commit box after a successful commit.

        Returns:
            None
        """

        self._summary.clear()
        self._description.clear()
        self._amend.setChecked(False)
        self._update_commit_button()

    # ------------------------------------------------------------------- events

    def _set_all_checked(self, checked: bool) -> None:
        """
        Ticks or unticks every tickable row.

        Args:
            checked: Target state.

        Returns:
            None
        """

        self._list.blockSignals(True)
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item is None or not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                continue
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._list.blockSignals(False)
        self._update_selected_label()
        self._update_commit_button()
        self.selection_changed.emit()

    def _on_item_changed(self, _item: QListWidgetItem) -> None:
        """
        Reacts to a tick box being toggled.

        Args:
            _item: The changed item.

        Returns:
            None
        """

        self._update_selected_label()
        self._update_commit_button()
        self.selection_changed.emit()

    def _on_current_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        """
        Announces the newly selected file.

        Args:
            current: Newly current item.
            _previous: Previously current item.

        Returns:
            None
        """

        if current is None:
            return
        path = current.data(_ROLE_PATH)
        untracked = bool(current.data(_ROLE_UNTRACKED))
        if isinstance(path, str) and path:
            self.file_selected.emit(path, untracked)

    def _update_selected_label(self) -> None:
        """
        Updates the "n of m selected" text in the header.

        Returns:
            None
        """

        total = sum(
            1
            for index in range(self._list.count())
            if (item := self._list.item(index)) is not None
            and bool(item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        )
        if not total:
            self._selected_label.setText("")
            return
        self._selected_label.setText(
            i18n.t("changes.selected_count", selected=len(self.checked_paths()), total=total)
        )

    def _update_commit_button(self) -> None:
        """
        Relabels and enables or disables the commit button.

        Returns:
            None
        """

        paths = self.checked_paths()
        draft = self.commit_draft()
        blocked_by_conflicts = bool(self._state and self._state.has_conflicts)

        if blocked_by_conflicts:
            self._commit_button.setText(i18n.t("conflict.intro_start"))
            self._commit_button.setEnabled(False)
            self._commit_button.setToolTip(i18n.t("conflict.intro_hint"))
            return

        self._commit_button.setText(
            i18n.t("changes.commit_button", count=len(paths), branch=self._branch or "?")
        )
        if not paths:
            self._commit_button.setEnabled(False)
            self._commit_button.setToolTip(i18n.t("changes.commit_empty"))
            return
        if not draft.is_valid:
            self._commit_button.setEnabled(False)
            self._commit_button.setToolTip(i18n.t("changes.commit_needs_summary"))
            return
        self._commit_button.setEnabled(True)
        self._commit_button.setToolTip("")

    def _emit_commit(self) -> None:
        """
        Asks the main window to commit the ticked files.

        Returns:
            None
        """

        if not self._commit_button.isEnabled():
            return
        self.commit_requested.emit(self.commit_draft(), self.checked_paths())

    def _show_context_menu(self, position) -> None:  # noqa: ANN001 - Qt passes a QPoint
        """
        Opens the right-click menu for a file row.

        Args:
            position: Click position in viewport coordinates.

        Returns:
            None
        """

        item = self._list.itemAt(position)
        if item is None:
            return
        path = item.data(_ROLE_PATH)
        if not isinstance(path, str) or not path:
            return
        untracked = bool(item.data(_ROLE_UNTRACKED))
        conflicted = bool(item.data(_ROLE_CONFLICTED))

        menu = QMenu(self)
        menu.addAction(i18n.t("diff.open_file"), lambda: self.open_file_requested.emit(path))
        menu.addAction(i18n.t("diff.open_folder"), lambda: self.reveal_file_requested.emit(path))
        menu.addAction(i18n.t("diff.copy_path"), lambda: self._copy_path(path))
        if conflicted:
            menu.addSeparator()
            menu.addAction(i18n.t("conflict.intro_start"), self.resolve_requested.emit)
        elif not untracked:
            menu.addSeparator()
            menu.addAction(
                i18n.t("changes.discard_action"), lambda: self.discard_requested.emit([path])
            )
        menu.exec(self._list.viewport().mapToGlobal(position))

    def _copy_path(self, path: str) -> None:
        """
        Puts a path on the clipboard.

        Args:
            path: Repository-relative path.

        Returns:
            None
        """

        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(path)
