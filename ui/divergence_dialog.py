"""
Choosing how to bring a diverged branch back together.

The project and the server each hold commits the other does not. Sending and
fast-forwarding both refuse until one of two moves is made. This dialog names
those moves in plain language and lets the user pick one; Cancel leaves
everything untouched.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import i18n
from services import divergence as divergence_mod
from ui.widgets import InlineMessage


class DivergenceDialog(QDialog):
    """
    Explains a diverged branch and asks which fix to apply.
    """

    def __init__(
        self,
        project: str,
        ahead: int,
        behind: int,
        dirty: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            project: Display name of the project.
            ahead: Local commits not on the server.
            behind: Server commits not local yet.
            dirty: Whether unsaved changes block applying a fix.
            parent: Parent widget.
        """

        super().__init__(parent)
        self._chosen_mode = ""
        self.setWindowTitle(i18n.t("divergence.title"))
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = i18n.t("divergence.heading", name=project)
        detail = i18n.t("divergence.hint", ahead=ahead, behind=behind)
        self._notice = InlineMessage(heading, detail, "warning", self)
        layout.addWidget(self._notice)

        self._dirty_label = QLabel(i18n.t("divergence.dirty_hint"), self)
        self._dirty_label.setWordWrap(True)
        self._dirty_label.setVisible(dirty)
        layout.addWidget(self._dirty_label)

        self._buttons = QDialogButtonBox(self)
        self._rebase_button = self._buttons.addButton(
            i18n.t("divergence.rebase_push"),
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self._merge_button = self._buttons.addButton(
            i18n.t("divergence.merge_push"),
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self._cancel_button = self._buttons.addButton(
            i18n.t("action.cancel"),
            QDialogButtonBox.ButtonRole.RejectRole,
        )
        self._wire_buttons(dirty)
        layout.addWidget(self._buttons)

    def _wire_buttons(self, dirty: bool) -> None:
        """
        Labels, enables and connects the three choices.

        Args:
            dirty: Whether apply actions must stay off.

        Returns:
            None
        """

        rebase = self._rebase_button
        merge = self._merge_button
        cancel = self._cancel_button
        if isinstance(rebase, QPushButton):
            rebase.setObjectName("Primary")
            rebase.style().unpolish(rebase)
            rebase.style().polish(rebase)
            rebase.setEnabled(not dirty)
            rebase.setToolTip(i18n.t("tip.divergence_rebase"))
            rebase.clicked.connect(
                lambda: self._choose(divergence_mod.MODE_REBASE_PUSH)
            )
        if isinstance(merge, QPushButton):
            merge.setEnabled(not dirty)
            merge.setToolTip(i18n.t("tip.divergence_merge"))
            merge.clicked.connect(
                lambda: self._choose(divergence_mod.MODE_MERGE_PUSH)
            )
        if isinstance(cancel, QPushButton):
            cancel.setDefault(True)
            cancel.setToolTip(i18n.t("tip.divergence_cancel"))
        self._buttons.rejected.connect(self.reject)

    def _choose(self, mode: str) -> None:
        """
        Records the chosen fix and closes as accepted.

        Args:
            mode: ``MODE_REBASE_PUSH`` or ``MODE_MERGE_PUSH``.

        Returns:
            None
        """

        self._chosen_mode = mode
        self.accept()

    @property
    def chosen_mode(self) -> str:
        """
        Returns the fix the user picked.

        Returns:
            str: Mode constant, empty when the dialog was cancelled.
        """

        return self._chosen_mode
