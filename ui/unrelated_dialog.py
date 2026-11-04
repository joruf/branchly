"""
Joining a project to a server whose history it does not share.

The project and the server each have versions, and none of them is the other's
ancestor. Uploading is refused, and so is every way of combining the two. This
window says that in plain words, shows what the one fix that works would do to
the files, and asks what to call the new version. Cancel leaves everything as it
is.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import i18n
from services.unrelated import GROUPS_SHOWN, Unrelated
from ui.widgets import InlineMessage


class UnrelatedDialog(QDialog):
    """
    Explains histories that share nothing and asks to put this side on top.
    """

    def __init__(
        self,
        project: str,
        found: Unrelated,
        dirty: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            project: Display name of the project.
            found: What was found out about the two histories.
            dirty: Whether unsaved changes block the fix.
            parent: Parent widget.
        """

        super().__init__(parent)
        self.setWindowTitle(i18n.t("unrelated.title"))
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(
            InlineMessage(
                i18n.t("unrelated.heading", name=project),
                i18n.t(
                    "unrelated.hint",
                    local=found.local_commits,
                    server=found.server_commits,
                ),
                "warning",
                self,
            )
        )

        plan = QLabel(i18n.t("unrelated.plan"), self)
        plan.setWordWrap(True)
        layout.addWidget(plan)

        compare = QLabel(
            i18n.t(
                "unrelated.compare",
                added=found.added,
                changed=found.changed,
                removed=found.removed,
            ),
            self,
        )
        compare.setWordWrap(True)
        layout.addWidget(compare)

        # The explanation wraps, the list does not: a wrapped label cannot tell how
        # tall it is until it knows how wide it is, and the list was cut off at
        # the bottom. Each place is a short line of its own anyway.
        self._removed = QLabel("", self)
        self._removed.setWordWrap(True)
        self._removed.setObjectName("Muted")
        self._removed_list = QLabel("", self)
        self._removed_list.setObjectName("Muted")
        if found.removed:
            shown = found.removed_groups[:GROUPS_SHOWN]
            listed = "\n".join(
                f"  {place}  \u00b7  {i18n.t('unrelated.files', count=count)}" for place, count in shown
            )
            more = found.removed - sum(count for _place, count in shown)
            if more > 0:
                listed += "\n  " + i18n.t("unrelated.and_more", count=more)
            self._removed.setText(i18n.t("unrelated.removed_hint"))
            self._removed_list.setText(listed)
        self._removed.setVisible(bool(found.removed))
        self._removed_list.setVisible(bool(found.removed))
        layout.addWidget(self._removed)
        layout.addWidget(self._removed_list)

        layout.addWidget(QLabel(i18n.t("unrelated.summary_label"), self))
        self._summary = QLineEdit(found.local_subject, self)
        self._summary.setToolTip(i18n.t("tip.unrelated_summary"))
        self._summary.textChanged.connect(self._refresh_buttons)
        layout.addWidget(self._summary)

        self._dirty = dirty
        self._dirty_label = QLabel(i18n.t("divergence.dirty_hint"), self)
        self._dirty_label.setWordWrap(True)
        self._dirty_label.setVisible(dirty)
        layout.addWidget(self._dirty_label)

        self._buttons = QDialogButtonBox(self)
        self._apply_button = self._buttons.addButton(
            i18n.t("unrelated.apply"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._cancel_button = self._buttons.addButton(
            i18n.t("action.cancel"), QDialogButtonBox.ButtonRole.RejectRole
        )
        if isinstance(self._apply_button, QPushButton):
            self._apply_button.setObjectName("Primary")
            self._apply_button.setToolTip(i18n.t("tip.unrelated_apply"))
        if isinstance(self._cancel_button, QPushButton):
            # The default, so that a stray Return key changes nothing.
            self._cancel_button.setDefault(True)
            self._cancel_button.setToolTip(i18n.t("tip.divergence_cancel"))
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._refresh_buttons()

    def showEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """
        Makes the window tall enough for its wrapped texts at the width it has.

        A wrapped label cannot tell how tall it is before it knows how wide, so
        the window opened a line or two short and the bottom of the list was cut
        off.

        Args:
            event: Show event.

        Returns:
            None
        """

        super().showEvent(event)
        needed = self.layout().totalHeightForWidth(self.width())
        if needed > self.height():
            self.resize(self.width(), needed)

    @property
    def summary(self) -> str:
        """
        Returns what the new version is to be called.

        Returns:
            str: The trimmed summary.
        """

        return self._summary.text().strip()

    def _refresh_buttons(self) -> None:
        """
        Allows the fix only with a summary and a tree with nothing unsaved.

        Returns:
            None
        """

        self._apply_button.setEnabled(bool(self.summary) and not self._dirty)
