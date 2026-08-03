"""
The conflict assistant.

The design goal is that someone who has never heard the word "merge" can finish
this dialog. That shapes three decisions:

* One question at a time. Every conflicting place in every file becomes a single
  decision, numbered, with a count of what is left.
* No git vocabulary and no markers. The user sees "your change", "from the
  server" and "result" — never ``<<<<<<<``, never "ours/theirs", never a SHA.
* Nothing is written until every decision is made. Up to that point, backing out
  restores exactly the state from before.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import i18n
from config.theme import get_theme_colors
from gitops import conflict as conflict_mod
from gitops.conflict import (
    CHOICE_BOTH,
    CHOICE_CUSTOM,
    CHOICE_OURS,
    CHOICE_PENDING,
    CHOICE_RESULT_KEYS,
    CHOICE_THEIRS,
    KIND_BINARY,
    KIND_DELETED_BY_THEM,
    KIND_DELETED_BY_US,
    ConflictedFile,
    ConflictRegion,
)
from ui.widgets import InlineMessage, apply_monospace


class _CustomTextDialog(QDialog):
    """
    Lets the user type the result of one conflict by hand.
    """

    def __init__(self, lines: list[str], parent: QWidget | None = None) -> None:
        """
        Args:
            lines: Text to start from.
            parent: Parent widget.
        """

        super().__init__(parent)
        self.setWindowTitle(i18n.t("conflict.custom_title"))
        self.setMinimumSize(560, 320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        hint = QLabel(i18n.t("conflict.custom_hint"), self)
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._editor = QPlainTextEdit(self)
        self._editor.setPlainText("\n".join(lines))
        apply_monospace(self._editor)
        layout.addWidget(self._editor, 1)

        buttons = QDialogButtonBox(self)
        accept = buttons.addButton(i18n.t("action.ok"), QDialogButtonBox.ButtonRole.AcceptRole)
        accept.setObjectName("Primary")
        buttons.addButton(i18n.t("action.cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def lines(self) -> list[str]:
        """
        Returns what the user typed.

        Returns:
            list[str]: Lines without terminators.
        """

        return self._editor.toPlainText().split("\n")


class _Decision:
    """
    One thing the user has to decide.

    A decision is either one conflicting place inside a text file, or a whole
    file when there is nothing to merge line by line.
    """

    def __init__(self, item: ConflictedFile, region: ConflictRegion | None) -> None:
        """
        Args:
            item: The conflicted file.
            region: The conflicting place, or None for a whole-file decision.
        """

        self.item = item
        self.region = region

    @property
    def is_whole_file(self) -> bool:
        """
        Reports whether this decision covers the entire file.

        Returns:
            bool: True for binary and delete/modify conflicts.
        """

        return self.region is None

    @property
    def choice(self) -> str:
        """
        Returns what has been chosen so far.

        Returns:
            str: One of the ``CHOICE_*`` constants.
        """

        if self.region is not None:
            return self.region.choice
        return self.item.whole_file_choice

    def set_choice(self, choice: str, custom: list[str] | None = None) -> None:
        """
        Records a choice.

        Args:
            choice: One of the ``CHOICE_*`` constants.
            custom: Lines to use when the choice is ``custom``.

        Returns:
            None
        """

        if self.region is not None:
            self.region.choice = choice
            if custom is not None:
                self.region.custom = custom
            return
        self.item.whole_file_choice = choice


class ConflictDialog(QDialog):
    """
    Walks the user through every conflict and writes the result.

    Attributes:
        finished_merge: Emitted when the merge was completed successfully.
        aborted_merge: Emitted when the user backed out and the repository was
            restored.
    """

    finished_merge = Signal()
    aborted_merge = Signal()

    def __init__(self, repo: Path, files: list[ConflictedFile], parent: QWidget | None = None) -> None:
        """
        Args:
            repo: Working tree path.
            files: Conflicted files, already loaded.
            parent: Parent widget.
        """

        super().__init__(parent)
        self.setWindowTitle(i18n.t("conflict.title"))
        self.setMinimumSize(900, 560)

        self._repo = repo
        self._files = files
        self._decisions: list[_Decision] = []
        for item in files:
            if item.error_key:
                continue
            if item.needs_whole_file_choice:
                self._decisions.append(_Decision(item, None))
                continue
            for region in item.regions:
                self._decisions.append(_Decision(item, region))
        self._index = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._build_intro_page())
        self._stack.addWidget(self._build_decision_page())
        self._stack.addWidget(self._build_finish_page())
        layout.addWidget(self._stack, 1)

        self._stack.setCurrentIndex(0 if self._decisions else 2)
        if not self._decisions:
            self._refresh_finish_page()

    # -------------------------------------------------------------------- pages

    def _build_intro_page(self) -> QWidget:
        """
        Builds the explanation shown before the first decision.

        Returns:
            QWidget: The intro page.
        """

        page = QWidget(self)
        column = QVBoxLayout(page)
        column.setSpacing(12)
        column.addStretch(1)

        notice = InlineMessage(
            i18n.t("conflict.intro_title"), i18n.t("conflict.intro_hint"), "info", page
        )
        column.addWidget(notice)

        summary = QLabel(
            i18n.plural(
                len(self._decisions), "conflict.remaining_one", "conflict.remaining_many"
            ),
            page,
        )
        summary.setObjectName("Muted")
        summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(summary)
        column.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        abort = QPushButton(i18n.t("conflict.abort_action"), page)
        abort.clicked.connect(self._confirm_abort)
        start = QPushButton(i18n.t("conflict.intro_start"), page)
        start.setObjectName("Primary")
        start.clicked.connect(self._show_current_decision)
        buttons.addWidget(abort)
        buttons.addWidget(start)
        column.addLayout(buttons)
        return page

    def _build_decision_page(self) -> QWidget:
        """
        Builds the page that asks one question.

        Returns:
            QWidget: The decision page.
        """

        page = QWidget(self)
        column = QVBoxLayout(page)
        column.setSpacing(10)

        self._progress_label = QLabel("", page)
        self._progress_label.setObjectName("Heading")
        column.addWidget(self._progress_label)

        self._file_label = QLabel("", page)
        self._file_label.setObjectName("Muted")
        column.addWidget(self._file_label)

        self._progress_bar = QProgressBar(page)
        self._progress_bar.setTextVisible(False)
        column.addWidget(self._progress_bar)

        self._reason = QLabel("", page)
        self._reason.setWordWrap(True)
        column.addWidget(self._reason)

        self._columns = QHBoxLayout()
        self._columns.setSpacing(10)
        self._ours_view, self._ours_caption = self._build_column(page, "conflict.column_ours")
        self._theirs_view, self._theirs_caption = self._build_column(page, "conflict.column_theirs")
        self._result_view, self._result_caption = self._build_column(page, "conflict.column_result")
        column.addLayout(self._columns, 1)

        choices = QHBoxLayout()
        choices.setSpacing(8)
        self._take_ours = QPushButton(i18n.t("conflict.take_ours"), page)
        self._take_ours.clicked.connect(lambda: self._choose(CHOICE_OURS))
        self._take_theirs = QPushButton(i18n.t("conflict.take_theirs"), page)
        self._take_theirs.clicked.connect(lambda: self._choose(CHOICE_THEIRS))
        self._take_both = QPushButton(i18n.t("conflict.take_both"), page)
        self._take_both.clicked.connect(lambda: self._choose(CHOICE_BOTH))
        self._edit_custom = QPushButton(i18n.t("conflict.edit_custom"), page)
        self._edit_custom.clicked.connect(self._choose_custom)
        for button in (self._take_ours, self._take_theirs, self._take_both, self._edit_custom):
            choices.addWidget(button)
        choices.addStretch(1)
        column.addLayout(choices)

        self._apply_all = QPushButton(i18n.t("conflict.apply_to_all"), page)
        self._apply_all.clicked.connect(self._apply_to_all)
        column.addWidget(self._apply_all, alignment=Qt.AlignmentFlag.AlignLeft)

        navigation = QHBoxLayout()
        self._back = QPushButton(i18n.t("action.back"), page)
        self._back.clicked.connect(self._go_back)
        self._abort = QPushButton(i18n.t("conflict.abort_action"), page)
        self._abort.clicked.connect(self._confirm_abort)
        self._remaining = QLabel("", page)
        self._remaining.setObjectName("Muted")
        self._next = QPushButton(i18n.t("action.next"), page)
        self._next.setObjectName("Primary")
        self._next.clicked.connect(self._go_next)
        navigation.addWidget(self._back)
        navigation.addWidget(self._abort)
        navigation.addStretch(1)
        navigation.addWidget(self._remaining)
        navigation.addWidget(self._next)
        column.addLayout(navigation)
        return page

    def _build_column(self, parent: QWidget, caption_key: str) -> tuple[QPlainTextEdit, QLabel]:
        """
        Builds one of the three side-by-side columns.

        Args:
            parent: Parent widget.
            caption_key: Translation key for the column heading.

        Returns:
            tuple[QPlainTextEdit, QLabel]: The text view and its caption.
        """

        holder = QWidget(parent)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)

        caption = QLabel(i18n.t(caption_key), holder)
        caption_font = caption.font()
        caption_font.setBold(True)
        caption.setFont(caption_font)
        column.addWidget(caption)

        subtitle = QLabel("", holder)
        subtitle.setObjectName("Muted")
        column.addWidget(subtitle)

        view = QPlainTextEdit(holder)
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        apply_monospace(view, -1)
        column.addWidget(view, 1)

        self._columns.addWidget(holder, 1)
        return view, subtitle

    def _build_finish_page(self) -> QWidget:
        """
        Builds the page shown once every decision is made.

        Returns:
            QWidget: The finish page.
        """

        page = QWidget(self)
        column = QVBoxLayout(page)
        column.setSpacing(12)
        column.addStretch(1)

        self._finish_notice = InlineMessage("", "", "success", page)
        column.addWidget(self._finish_notice)
        column.addStretch(1)

        buttons = QHBoxLayout()
        self._finish_back = QPushButton(i18n.t("action.back"), page)
        self._finish_back.clicked.connect(self._show_current_decision)
        buttons.addWidget(self._finish_back)
        buttons.addStretch(1)
        abort = QPushButton(i18n.t("conflict.abort_action"), page)
        abort.clicked.connect(self._confirm_abort)
        buttons.addWidget(abort)
        self._finish_button = QPushButton(i18n.t("conflict.finish_action"), page)
        self._finish_button.setObjectName("Primary")
        self._finish_button.clicked.connect(self._write_and_finish)
        buttons.addWidget(self._finish_button)
        column.addLayout(buttons)
        return page

    # ------------------------------------------------------------------ display

    def _show_current_decision(self) -> None:
        """
        Draws the decision at the current index.

        Returns:
            None
        """

        if not self._decisions:
            self._refresh_finish_page()
            self._stack.setCurrentIndex(2)
            return
        self._index = max(0, min(self._index, len(self._decisions) - 1))
        decision = self._decisions[self._index]
        colors = get_theme_colors()

        self._progress_label.setText(
            i18n.t("conflict.progress", current=self._index + 1, total=len(self._decisions))
        )
        self._file_label.setText(f"{i18n.t('conflict.file_label')}: {decision.item.path}")
        self._progress_bar.setRange(0, len(self._decisions))
        self._progress_bar.setValue(self._index + 1)

        pending = sum(1 for item in self._decisions if item.choice == CHOICE_PENDING)
        self._remaining.setText(
            i18n.t("conflict.remaining_none")
            if not pending
            else i18n.plural(pending, "conflict.remaining_one", "conflict.remaining_many")
        )
        self._back.setEnabled(self._index > 0)

        if decision.is_whole_file:
            self._show_whole_file(decision, colors)
        else:
            self._show_region(decision, colors)

        self._next.setText(
            i18n.t("action.next") if self._index < len(self._decisions) - 1 else i18n.t("action.finish")
        )
        self._stack.setCurrentIndex(1)

    def _show_region(self, decision: _Decision, colors) -> None:  # noqa: ANN001 - ThemeColors
        """
        Fills the three columns for a line-level conflict.

        Args:
            decision: The decision being shown.
            colors: Active theme tokens.

        Returns:
            None
        """

        region = decision.region
        assert region is not None

        self._reason.setText(i18n.t(region.reason_key))
        self._ours_view.setPlainText("\n".join(region.ours))
        self._theirs_view.setPlainText("\n".join(region.theirs))
        self._ours_view.setStyleSheet(f"background-color: {colors.conflict_ours_bg};")
        self._theirs_view.setStyleSheet(f"background-color: {colors.conflict_theirs_bg};")

        chosen = region.resolution_lines()
        self._result_view.setPlainText("\n".join(chosen))
        border = (
            f"border: 2px solid {colors.conflict_chosen_border};"
            if region.choice != CHOICE_PENDING
            else f"border: 1px solid {colors.border};"
        )
        self._result_view.setStyleSheet(f"background-color: {colors.conflict_result_bg};{border}")
        self._result_caption.setText(i18n.t(CHOICE_RESULT_KEYS.get(region.choice, "")))

        self._ours_caption.setText("")
        self._theirs_caption.setText("")
        for button in (self._take_ours, self._take_theirs, self._take_both, self._edit_custom):
            button.setVisible(True)
        self._apply_all.setVisible(True)

    def _show_whole_file(self, decision: _Decision, colors) -> None:  # noqa: ANN001 - ThemeColors
        """
        Fills the columns for a conflict decided as a whole file.

        Args:
            decision: The decision being shown.
            colors: Active theme tokens.

        Returns:
            None
        """

        item = decision.item
        if item.kind == KIND_BINARY:
            self._reason.setText(f"{i18n.t('conflict.binary_title')} — {i18n.t('conflict.binary_hint')}")
        elif item.kind == KIND_DELETED_BY_THEM:
            self._reason.setText(i18n.t("conflict.reason_deleted_by_them"))
        elif item.kind == KIND_DELETED_BY_US:
            self._reason.setText(i18n.t("conflict.reason_deleted_by_us"))

        self._ours_view.setPlainText(i18n.t("conflict.binary_keep_ours"))
        self._theirs_view.setPlainText(i18n.t("conflict.binary_keep_theirs"))
        self._ours_view.setStyleSheet(f"background-color: {colors.conflict_ours_bg};")
        self._theirs_view.setStyleSheet(f"background-color: {colors.conflict_theirs_bg};")

        self._result_view.setPlainText(i18n.t(CHOICE_RESULT_KEYS.get(item.whole_file_choice, "")))
        self._result_view.setStyleSheet(f"background-color: {colors.conflict_result_bg};")
        self._result_caption.setText("")

        self._take_ours.setVisible(True)
        self._take_theirs.setVisible(True)
        # Neither "both" nor free text means anything for a whole-file decision.
        self._take_both.setVisible(False)
        self._edit_custom.setVisible(False)
        self._apply_all.setVisible(False)

    def _refresh_finish_page(self) -> None:
        """
        Updates the closing message and enables or blocks finishing.

        Returns:
            None
        """

        pending = sum(1 for item in self._decisions if item.choice == CHOICE_PENDING)
        if pending:
            self._finish_notice.set_message(
                i18n.t("conflict.unresolved_title"),
                i18n.t("conflict.unresolved_hint", count=pending),
                "warning",
            )
            self._finish_button.setEnabled(False)
            return
        ready = sum(1 for item in self._files if item.resolved)
        self._finish_notice.set_message(
            i18n.t("conflict.finish_title"),
            i18n.t("conflict.finish_hint", count=ready),
            "success",
        )
        self._finish_button.setEnabled(True)

    # ------------------------------------------------------------------ choices

    def _choose(self, choice: str) -> None:
        """
        Records a choice and moves on.

        Args:
            choice: One of the ``CHOICE_*`` constants.

        Returns:
            None
        """

        if not self._decisions:
            return
        self._decisions[self._index].set_choice(choice)
        self._advance()

    def _choose_custom(self) -> None:
        """
        Opens the free-text editor for the current conflict.

        Returns:
            None
        """

        if not self._decisions:
            return
        decision = self._decisions[self._index]
        region = decision.region
        if region is None:
            return
        starting_point = region.custom or [*region.ours, *region.theirs]
        dialog = _CustomTextDialog(starting_point, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        decision.set_choice(CHOICE_CUSTOM, dialog.lines())
        self._advance()

    def _apply_to_all(self) -> None:
        """
        Repeats the current choice for every remaining undecided conflict.

        Only line-level decisions are affected: a binary or deleted file is a
        different question and must not be answered by accident.

        Returns:
            None
        """

        if not self._decisions:
            return
        current = self._decisions[self._index]
        choice = current.choice
        if choice == CHOICE_PENDING or choice == CHOICE_CUSTOM:
            return
        for decision in self._decisions:
            if decision.is_whole_file:
                continue
            if decision.choice == CHOICE_PENDING:
                decision.set_choice(choice)
        self._refresh_finish_page()
        self._stack.setCurrentIndex(2)

    def _advance(self) -> None:
        """
        Moves to the next decision, or to the closing page.

        Returns:
            None
        """

        if self._index < len(self._decisions) - 1:
            self._index += 1
            self._show_current_decision()
            return
        self._refresh_finish_page()
        self._stack.setCurrentIndex(2)

    def _go_next(self) -> None:
        """
        Moves forward, refusing to skip an undecided conflict.

        Returns:
            None
        """

        if self._decisions and self._decisions[self._index].choice == CHOICE_PENDING:
            QMessageBox.information(
                self,
                i18n.t("conflict.unresolved_title"),
                i18n.t("conflict.unresolved_hint", count=1),
            )
            return
        self._advance()

    def _go_back(self) -> None:
        """
        Moves to the previous decision.

        Returns:
            None
        """

        if self._index > 0:
            self._index -= 1
            self._show_current_decision()

    # ------------------------------------------------------------------ writing

    def _write_and_finish(self) -> None:
        """
        Writes every decided file, stages it and completes the merge.

        Returns:
            None
        """

        failures: list[str] = []
        for item in self._files:
            if item.error_key or not item.resolved:
                continue
            result = conflict_mod.write_resolution(self._repo, item)
            if result.failed:
                failures.append(f"{item.path}: {result.message}")

        if failures:
            QMessageBox.warning(self, i18n.t("error.title"), "\n".join(failures[:5]))
            return

        remaining = conflict_mod.conflicted_paths(self._repo)
        if remaining:
            QMessageBox.warning(
                self,
                i18n.t("conflict.unresolved_title"),
                i18n.t("conflict.unresolved_hint", count=len(remaining)),
            )
            return

        result = conflict_mod.finish_merge(self._repo)
        if result.failed:
            QMessageBox.warning(
                self, i18n.t("error.title"), result.message or i18n.t("error.git_failed")
            )
            return
        self.finished_merge.emit()
        self.accept()

    def _confirm_abort(self) -> None:
        """
        Confirms backing out, then restores the repository.

        Returns:
            None
        """

        answer = QMessageBox.question(
            self,
            i18n.t("conflict.abort_title"),
            i18n.t("conflict.abort_hint"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = conflict_mod.abort(self._repo)
        if result.failed:
            QMessageBox.warning(
                self, i18n.t("error.title"), result.message or i18n.t("error.git_failed")
            )
            return
        self.aborted_merge.emit()
        self.reject()
