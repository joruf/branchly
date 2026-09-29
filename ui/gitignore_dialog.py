"""
Editing the project's ``.gitignore``.

A plain text editor, deliberately. A ``.gitignore`` is a list of patterns with
comments between them, grouped the way its author thought about it, and any
form that turned it into rows and switches would lose exactly that: the order,
the blank lines, the "# build output" above a group. So it is shown and saved as
the text it is, with the file's own line endings kept.

The hints below the editor answer the two questions every newcomer to the file
has: how a pattern is written, and why a file that is already tracked does not
vanish when it is added.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

import i18n
from gitops import ignore as ignore_mod
from ui.widgets import InlineMessage, apply_monospace


class GitignoreDialog(QDialog):
    """
    Shows the ``.gitignore`` of one project and saves what is typed.
    """

    def __init__(self, repo: Path, parent: QWidget | None = None) -> None:
        """
        Args:
            repo: Working tree root.
            parent: Parent widget.
        """

        super().__init__(parent)
        self._repo = repo
        self._loaded = ignore_mod.read_file(repo)
        self._saved = False

        self.setWindowTitle(i18n.t("gitignore.title"))
        self.resize(640, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        intro = QLabel(
            i18n.t(
                "gitignore.intro_existing" if self._loaded.exists else "gitignore.intro_new",
                path=str(repo / ignore_mod.GITIGNORE_NAME),
            ),
            self,
        )
        intro.setWordWrap(True)
        intro.setObjectName("Muted")
        layout.addWidget(intro)

        self._notice = InlineMessage(self)
        self._notice.setVisible(False)
        layout.addWidget(self._notice)
        if self._loaded.error_key:
            self._notice.set_message(i18n.t(self._loaded.error_key), "", "danger")
            self._notice.setVisible(True)

        self._editor = QPlainTextEdit(self)
        self._editor.setPlainText(self._loaded.text)
        self._editor.setPlaceholderText(i18n.t("gitignore.placeholder"))
        self._editor.setToolTip(i18n.t("tip.gitignore_editor"))
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        apply_monospace(self._editor, 0)
        self._editor.document().setModified(False)
        self._editor.textChanged.connect(self._update_buttons)
        layout.addWidget(self._editor, 1)

        help_text = QLabel(i18n.t("gitignore.help"), self)
        help_text.setWordWrap(True)
        help_text.setObjectName("Muted")
        layout.addWidget(help_text)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        save = self._buttons.button(QDialogButtonBox.StandardButton.Save)
        if save is not None:
            save.setText(i18n.t("gitignore.save"))
            save.setObjectName("Primary")
            save.style().unpolish(save)
            save.style().polish(save)
            save.setToolTip(i18n.t("tip.gitignore_save"))
        cancel = self._buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel is not None:
            cancel.setText(i18n.t("action.cancel"))
        self._buttons.accepted.connect(self._save)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        # A file that could not be read must not be overwritten with nothing.
        self._editor.setReadOnly(bool(self._loaded.error_key))
        self._update_buttons()

    # ------------------------------------------------------------------ results

    @property
    def saved(self) -> bool:
        """
        Reports whether anything was written.

        Returns:
            bool: True after a successful save.
        """

        return self._saved

    @property
    def text(self) -> str:
        """
        Returns what the editor holds.

        Returns:
            str: The content with ``\\n`` line endings.
        """

        return self._editor.toPlainText()

    # ------------------------------------------------------------------- actions

    def _update_buttons(self) -> None:
        """
        Offers saving only when there is something to save.

        Returns:
            None
        """

        save = self._buttons.button(QDialogButtonBox.StandardButton.Save)
        if save is not None:
            save.setEnabled(
                not self._loaded.error_key and self._editor.document().isModified()
            )

    def _save(self) -> None:
        """
        Writes the file and closes.

        Returns:
            None
        """

        # A file that could not be read shows an empty editor, and saving an
        # empty editor removes the file. The button is off in that case, but
        # this is the one place that must not depend on it.
        if self._loaded.error_key:
            return
        result = ignore_mod.write_file(self._repo, self.text, self._loaded.newline)
        if not result.ok:
            self._notice.set_message(
                i18n.t(result.error_key or "ignore.write_failed"), "", "danger"
            )
            self._notice.setVisible(True)
            return
        self._saved = True
        self._editor.document().setModified(False)
        self.accept()

    def reject(self) -> None:
        """
        Asks before throwing typed changes away.

        Returns:
            None
        """

        if self._editor.document().isModified() and not self._loaded.error_key:
            answer = QMessageBox.question(
                self,
                i18n.t("gitignore.title"),
                i18n.t("gitignore.discard_prompt"),
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Discard:
                return
        super().reject()
