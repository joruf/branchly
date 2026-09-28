"""
The list of what a revert would throw away.

Everything here is about one property of the operation: it cannot be undone.
Git keeps no copy of an uncommitted change, so a confirmation that says only
"are you sure" leaves the user guessing at the size of what they are about to
agree to. This window answers that instead: every file by name, and next to it
what happens to it.

The two cases are kept apart on purpose. A tracked file goes back to its last
saved version, which is a change nobody can recover but a file that stays. An
untracked file is deleted from disk, and that one is worse, so it gets its own
group, its own colour and its own sentence. Merging the two under one count
would hide exactly the half worth hesitating over.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

import i18n
from gitops.status import CHANGE_COLOR_TOKENS, CHANGE_LABEL_KEYS
from services import revert as revert_mod
from ui.widgets import InlineMessage, token_color


class RevertDialog(QDialog):
    """
    Shows everything a revert would touch and asks once.
    """

    def __init__(
        self, plan: revert_mod.RevertPlan, project: str = "", parent: QWidget | None = None
    ) -> None:
        """
        Args:
            plan: What the revert would do.
            project: Name of the project, shown when the whole of it is meant.
            parent: Parent widget.
        """

        super().__init__(parent)
        self._plan = plan
        self.setWindowTitle(i18n.t("revert.title"))
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = (
            i18n.t("revert.heading_project", name=project)
            if project
            else i18n.plural(len(plan.items), "revert.heading_one", "revert.heading_many")
        )
        self._notice = InlineMessage(heading, self._explain(), "warning", self)
        layout.addWidget(self._notice)

        self._list = QListWidget(self)
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._list.setToolTip(i18n.t("tip.revert_list"))
        self._fill()
        layout.addWidget(self._list, 1)

        if plan.deleted:
            # The irreversible half, said once more in words, because a list of
            # file names does not read as a warning on its own.
            warning = QLabel(
                i18n.plural(len(plan.deleted), "revert.deleted_one", "revert.deleted_many"),
                self,
            )
            warning.setWordWrap(True)
            warning.setStyleSheet(f"color: {token_color('danger')};")
            layout.addWidget(warning)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons = self._buttons
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setText(i18n.t("revert.confirm"))
            ok.setObjectName("Danger")
            # Qt has already styled the button by the time a dialog box hands it
            # over, and a name set afterwards does not reach the sheet on its
            # own. A confirm button for something irreversible that looks like
            # every other button is exactly the wrong thing to ship.
            ok.style().unpolish(ok)
            ok.style().polish(ok)
            ok.setEnabled(not plan.is_empty)
            ok.setToolTip(i18n.t("tip.revert_confirm"))
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel is not None:
            cancel.setText(i18n.t("action.cancel"))
            # The safe way out is the one a stray Return key should take.
            cancel.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.resize(620, self._preferred_height())

    # ------------------------------------------------------------------ contents

    def _explain(self) -> str:
        """
        Sums up what will happen, in one sentence.

        Returns:
            str: The explanation for the strip at the top.
        """

        parts: list[str] = []
        if self._plan.restored:
            parts.append(
                i18n.plural(
                    len(self._plan.restored), "revert.restored_one", "revert.restored_many"
                )
            )
        if self._plan.deleted:
            parts.append(
                i18n.plural(
                    len(self._plan.deleted), "revert.removed_one", "revert.removed_many"
                )
            )
        return " · ".join(parts) if parts else i18n.t("revert.nothing")

    def _fill(self) -> None:
        """
        Puts one row on screen per file.

        Returns:
            None
        """

        for item in self._plan.items:
            row = QListWidgetItem(self._list)
            row.setFlags(Qt.ItemFlag.ItemIsEnabled)
            action = i18n.t(
                "revert.action_delete"
                if item.action == revert_mod.ACTION_DELETE
                else "revert.action_restore"
            )
            row.setText(f"{item.path}   ·   {action}")
            row.setToolTip(i18n.t(CHANGE_LABEL_KEYS.get(item.kind, "status.modified")))
            token = (
                "danger"
                if item.action == revert_mod.ACTION_DELETE
                else CHANGE_COLOR_TOKENS.get(item.kind, "text")
            )
            row.setForeground(QColor(token_color(token)))
            if item.action == revert_mod.ACTION_DELETE:
                font = QFont(self._list.font())
                font.setBold(True)
                row.setFont(font)

    def _preferred_height(self) -> int:
        """
        Works out how tall the window should open.

        Tall enough for the list, never taller than the screen. A window whose
        bottom edge is off the desktop hides the button that stops the whole
        thing.

        Returns:
            int: Height in pixels.
        """

        from PySide6.QtGui import QGuiApplication

        chrome = 210 if self._plan.deleted else 180
        row = max(20, self._list.sizeHintForRow(0) if self._plan.items else 24)
        wanted = chrome + row * min(max(len(self._plan.items), 4), 14)

        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            wanted = min(wanted, int(screen.availableGeometry().height() * 0.8))
        return max(340, wanted)

    @property
    def plan(self) -> revert_mod.RevertPlan:
        """
        Returns the plan the user agreed to.

        Returns:
            RevertPlan: The plan, unchanged.
        """

        return self._plan
