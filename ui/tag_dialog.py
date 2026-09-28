"""
The tag list.

A tag is a name pinned to one commit, and the two things anybody wants from a
list of them are to add one and to remove one. That is all this does.

Deleting only ever touches the local repository. A tag that was already pushed
stays on the server until somebody removes it there, and saying so in the dialog
is more useful than quietly doing half the job or, worse, reaching for the server
without being asked.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import i18n
from gitops import branch as branch_mod
from ui.widgets import EmptyState, InlineMessage


class TagDialog(QDialog):
    """
    Lists the repository's tags and lets one be added or removed.
    """

    def __init__(self, repo: Path, branch: str = "", parent: QWidget | None = None) -> None:
        """
        Args:
            repo: Working tree path.
            branch: Branch the new tag would land on, shown for orientation.
            parent: Parent widget.
        """

        super().__init__(parent)
        self._repo = repo
        self.setWindowTitle(i18n.t("tag.title"))
        self.setMinimumSize(460, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        intro = QLabel(
            i18n.t("tag.intro_branch", branch=branch) if branch else i18n.t("tag.intro"), self
        )
        intro.setWordWrap(True)
        intro.setObjectName("Muted")
        layout.addWidget(intro)

        self._notice = InlineMessage(self)
        self._notice.setVisible(False)
        layout.addWidget(self._notice)

        self._list = QListWidget(self)
        self._list.setToolTip(i18n.t("tip.tag_list"))
        self._list.itemSelectionChanged.connect(self._update_enabled)
        layout.addWidget(self._list, 1)

        self._empty = EmptyState(i18n.t("tag.none"), i18n.t("tag.none_hint"), self)
        layout.addWidget(self._empty, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._name = QLineEdit(self)
        self._name.setPlaceholderText(i18n.t("tag.name_placeholder"))
        self._name.setToolTip(i18n.t("tip.tag_name"))
        self._name.textChanged.connect(lambda _text: self._update_enabled())
        self._name.returnPressed.connect(self._create)
        row.addWidget(self._name, 1)

        self._create_button = QPushButton(i18n.t("tag.create"), self)
        self._create_button.setToolTip(i18n.t("tip.tag_create"))
        self._create_button.clicked.connect(self._create)
        row.addWidget(self._create_button)

        self._delete_button = QPushButton(i18n.t("tag.delete"), self)
        self._delete_button.setObjectName("Danger")
        self._delete_button.setToolTip(i18n.t("tip.tag_delete"))
        self._delete_button.clicked.connect(self._delete)
        row.addWidget(self._delete_button)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_button is not None:
            close_button.setText(i18n.t("action.close"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._reload()

    # ------------------------------------------------------------------ contents

    def _reload(self) -> None:
        """
        Reads the tags again.

        Returns:
            None
        """

        self._list.clear()
        tags = branch_mod.list_tags(self._repo)
        for name in tags:
            self._list.addItem(name)
        self._list.setVisible(bool(tags))
        self._empty.setVisible(not tags)
        self._update_enabled()

    def _update_enabled(self) -> None:
        """
        Matches the buttons to what is currently possible.

        Returns:
            None
        """

        self._create_button.setEnabled(bool(self._name.text().strip()))
        self._delete_button.setEnabled(self._list.currentItem() is not None)

    @property
    def names(self) -> list[str]:
        """
        Returns the tags currently listed.

        Returns:
            list[str]: Tag names, newest first.
        """

        return [self._list.item(index).text() for index in range(self._list.count())]

    # ------------------------------------------------------------------- actions

    def _create(self) -> None:
        """
        Gives the current commit a name.

        Returns:
            None
        """

        name = self._name.text().strip()
        if not name:
            return
        result = branch_mod.create_tag(self._repo, name)
        if result.failed:
            self._fail(result.error_key() or "error.git_failed", result.message)
            return
        self._name.clear()
        self._notice.setVisible(False)
        self._reload()

    def _delete(self) -> None:
        """
        Removes the selected tag from this computer.

        Returns:
            None
        """

        item = self._list.currentItem()
        if item is None:
            return
        name = item.text()
        answer = QMessageBox.question(
            self,
            i18n.t("tag.delete_title"),
            i18n.t("tag.delete_prompt", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        result = branch_mod.delete_tag(self._repo, name)
        if result.failed:
            self._fail(result.error_key() or "error.git_failed", result.message)
            return
        self._notice.setVisible(False)
        self._reload()

    def _fail(self, key: str, detail: str) -> None:
        """
        Shows why something did not work.

        Args:
            key: Translation key for the headline.
            detail: Git's own words, shown underneath.

        Returns:
            None
        """

        self._notice.set_message(i18n.t(key), detail.strip(), "danger")
        self._notice.setVisible(True)
