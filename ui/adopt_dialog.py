"""
Taking over the server's version.

For a project whose server holds a version this computer should simply follow,
most often because the history there was rewritten. The window says what the
server's version would do to the files, where the old versions are kept, and asks
before anything moves. Cancel leaves everything as it is.
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
from services.adopt import Adoptable
from ui.widgets import InlineMessage


class AdoptDialog(QDialog):
    """
    Explains what taking over the server's version does and asks to do it.
    """

    def __init__(
        self,
        project: str,
        found: Adoptable,
        dirty: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            project: Display name of the project.
            found: What was found out about the two sides.
            dirty: Whether unsaved changes block the fix.
            parent: Parent widget.
        """

        super().__init__(parent)
        self.setWindowTitle(i18n.t("adopt.title"))
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        hint = (
            i18n.t(
                "adopt.hint_rewritten",
                matching=found.matching,
                local=found.local_only,
            )
            if found.rewritten
            else i18n.t("adopt.hint", local=found.local_only, server=found.server_only)
        )
        layout.addWidget(
            InlineMessage(i18n.t("adopt.heading", name=project), hint, "warning", self)
        )

        plan = QLabel(i18n.t("adopt.plan"), self)
        plan.setWordWrap(True)
        layout.addWidget(plan)

        backup = QLabel(i18n.t("adopt.backup_name", branch=found.branch), self)
        backup.setWordWrap(True)
        backup.setObjectName("Muted")
        layout.addWidget(backup)

        compare = QLabel(
            i18n.t(
                "adopt.compare",
                added=found.will_add,
                changed=found.will_change,
                removed=found.will_remove,
            ),
            self,
        )
        compare.setWordWrap(True)
        layout.addWidget(compare)

        self._dirty = dirty
        self._dirty_label = QLabel(i18n.t("divergence.dirty_hint"), self)
        self._dirty_label.setWordWrap(True)
        self._dirty_label.setVisible(dirty)
        layout.addWidget(self._dirty_label)

        self._buttons = QDialogButtonBox(self)
        self._apply_button = self._buttons.addButton(
            i18n.t("adopt.apply"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._cancel_button = self._buttons.addButton(
            i18n.t("action.cancel"), QDialogButtonBox.ButtonRole.RejectRole
        )
        if isinstance(self._apply_button, QPushButton):
            self._apply_button.setObjectName("Primary")
            self._apply_button.setToolTip(i18n.t("tip.adopt_apply"))
            self._apply_button.setEnabled(not dirty)
        if isinstance(self._cancel_button, QPushButton):
            # The default, so that a stray Return key changes nothing.
            self._cancel_button.setDefault(True)
            self._cancel_button.setToolTip(i18n.t("tip.divergence_cancel"))
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    def showEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """
        Makes the window tall enough for its wrapped texts at the width it has.

        Args:
            event: Show event.

        Returns:
            None
        """

        super().showEvent(event)
        needed = self.layout().totalHeightForWidth(self.width())
        if needed > self.height():
            self.resize(self.width(), needed)
