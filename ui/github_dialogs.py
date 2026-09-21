"""
The dialogs that change something on GitHub.

They collect input and hand it back; none of them calls the API. The panel that
opened a dialog is what sends the request and what reports the outcome, so a
dialog never has to know about threads, rate limits or a token that expired
between opening and confirming.

One rule runs through all of them: anything irreversible names what will be lost
and asks for the name to be typed where the loss is total. Deleting a repository
is the only such case, and it is the reason the confirmation there is different
in kind from every other confirmation in the application.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

import i18n
from ui.widgets import InlineMessage

# Body fields are multi-line and get a sensible amount of room without taking
# over the screen on a small display.
BODY_MIN_HEIGHT = 140
NOTES_MIN_HEIGHT = 180


def _checkable_list(parent: QWidget, options: list[str], selected: list[str]) -> QListWidget:
    """
    Builds a list where each entry can be ticked.

    Args:
        parent: Parent widget.
        options: Every value that can be picked.
        selected: Values that start out ticked.

    Returns:
        QListWidget: The list.
    """

    widget = QListWidget(parent)
    widget.setMaximumHeight(120)
    chosen = set(selected)
    for option in options:
        item = QListWidgetItem(option, widget)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if option in chosen else Qt.CheckState.Unchecked
        )
    return widget


def _checked_values(widget: QListWidget) -> list[str]:
    """
    Reads the ticked entries of a list.

    Args:
        widget: The list.

    Returns:
        list[str]: Ticked values, in the order they appear.
    """

    values: list[str] = []
    for index in range(widget.count()):
        item = widget.item(index)
        if item.checkState() == Qt.CheckState.Checked:
            values.append(item.text())
    return values


class _FormDialog(QDialog):
    """
    Shared shell: a form, an inline message and an OK/Cancel row.
    """

    def __init__(self, title: str, accept_label: str, parent: QWidget | None = None) -> None:
        """
        Args:
            title: Window title.
            accept_label: Label for the confirming button.
            parent: Parent widget.
        """

        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)

        self._column = QVBoxLayout(self)
        self._form = QFormLayout()
        self._column.addLayout(self._form)

        self._notice = InlineMessage("", "", "danger", self)
        self._notice.setVisible(False)
        self._column.addWidget(self._notice)

        self._buttons = QDialogButtonBox(self)
        self._accept_button = self._buttons.addButton(
            accept_label, QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._buttons.addButton(
            i18n.t("action.cancel"), QDialogButtonBox.ButtonRole.RejectRole
        )
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        self._column.addWidget(self._buttons)

    def _complain(self, message: str) -> None:
        """
        Shows a validation message.

        Args:
            message: What is wrong.

        Returns:
            None
        """

        self._notice.set_message(message, "", "danger")
        self._notice.setVisible(True)

    def _on_accept(self) -> None:
        """
        Validates and closes.

        Returns:
            None
        """

        problem = self.validate()
        if problem:
            self._complain(problem)
            return
        self.accept()

    def validate(self) -> str:
        """
        Checks the input before the dialog closes.

        Returns:
            str: What is wrong, empty when the input is usable.
        """

        return ""


# --------------------------------------------------------------------- issues


@dataclass(slots=True)
class IssueDraft:
    """
    What the issue editor collected.

    Attributes:
        title: Issue title.
        body: Description text.
        labels: Ticked label names.
        assignees: Ticked logins.
        milestone: Milestone number, or None for no milestone.
    """

    title: str = ""
    body: str = ""
    labels: list[str] = field(default_factory=list)
    assignees: list[str] = field(default_factory=list)
    milestone: int | None = None


class IssueEditorDialog(_FormDialog):
    """
    Creates or edits an issue.
    """

    def __init__(
        self,
        labels: list[str],
        assignees: list[str],
        milestones: list[tuple[int, str]],
        draft: IssueDraft | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            labels: Label names the repository defines.
            assignees: Logins that can be assigned.
            milestones: ``(number, title)`` per milestone.
            draft: Values to start from, None for a new issue.
            parent: Parent widget.
        """

        editing = draft is not None
        super().__init__(
            i18n.t("github.issue_edit" if editing else "github.issue_new"),
            i18n.t("action.save" if editing else "github.create"),
            parent,
        )
        start = draft or IssueDraft()

        self._title = QLineEdit(start.title, self)
        self._form.addRow(i18n.t("github.field_title"), self._title)

        self._body = QPlainTextEdit(start.body, self)
        self._body.setMinimumHeight(BODY_MIN_HEIGHT)
        self._form.addRow(i18n.t("github.field_body"), self._body)

        self._labels = _checkable_list(self, labels, start.labels)
        self._form.addRow(i18n.t("github.field_labels"), self._labels)

        self._assignees = _checkable_list(self, assignees, start.assignees)
        self._form.addRow(i18n.t("github.field_assignees"), self._assignees)

        self._milestone = QComboBox(self)
        self._milestone.addItem(i18n.t("github.milestone_none"), None)
        for number, title in milestones:
            self._milestone.addItem(title, number)
            if start.milestone == number:
                self._milestone.setCurrentIndex(self._milestone.count() - 1)
        self._form.addRow(i18n.t("github.field_milestone"), self._milestone)

    def validate(self) -> str:
        """
        Refuses an empty title, which is the one thing GitHub insists on.

        Returns:
            str: What is wrong, empty when usable.
        """

        return "" if self._title.text().strip() else i18n.t("github.title_required")

    def draft(self) -> IssueDraft:
        """
        Returns what was entered.

        Returns:
            IssueDraft: The collected values.
        """

        return IssueDraft(
            title=self._title.text().strip(),
            body=self._body.toPlainText().strip(),
            labels=_checked_values(self._labels),
            assignees=_checked_values(self._assignees),
            milestone=self._milestone.currentData(),
        )


# -------------------------------------------------------------- pull requests


@dataclass(slots=True)
class PullRequestDraft:
    """
    What the pull request editor collected.

    Attributes:
        title: Pull request title.
        body: Description text.
        head: Branch the changes are on.
        base: Branch they should go into.
        draft: Whether to open it as a draft.
    """

    title: str = ""
    body: str = ""
    head: str = ""
    base: str = ""
    draft: bool = False


class PullRequestEditorDialog(_FormDialog):
    """
    Opens a pull request, or edits an existing one.
    """

    def __init__(
        self,
        branches: list[str],
        draft: PullRequestDraft | None = None,
        editing: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            branches: Branch names on the server.
            draft: Values to start from.
            editing: Whether an existing pull request is being changed, in which
                case the source branch is fixed and only the target can move.
            parent: Parent widget.
        """

        super().__init__(
            i18n.t("github.pr_edit" if editing else "github.pr_new"),
            i18n.t("action.save" if editing else "github.create"),
            parent,
        )
        start = draft or PullRequestDraft()
        self._editing = editing

        self._title = QLineEdit(start.title, self)
        self._form.addRow(i18n.t("github.field_title"), self._title)

        self._head = QComboBox(self)
        self._head.addItems(branches)
        if start.head:
            self._select(self._head, start.head)
        self._head.setEnabled(not editing)
        self._form.addRow(i18n.t("github.field_head"), self._head)

        self._base = QComboBox(self)
        self._base.addItems(branches)
        if start.base:
            self._select(self._base, start.base)
        self._form.addRow(i18n.t("github.field_base"), self._base)

        self._body = QPlainTextEdit(start.body, self)
        self._body.setMinimumHeight(BODY_MIN_HEIGHT)
        self._form.addRow(i18n.t("github.field_body"), self._body)

        self._draft = QCheckBox(i18n.t("github.field_draft"), self)
        self._draft.setChecked(start.draft)
        self._draft.setVisible(not editing)
        self._form.addRow("", self._draft)

    @staticmethod
    def _select(box: QComboBox, value: str) -> None:
        """
        Selects a value, adding it when the list does not hold it.

        Args:
            box: The combo box.
            value: Value to select.

        Returns:
            None
        """

        index = box.findText(value)
        if index < 0:
            box.addItem(value)
            index = box.count() - 1
        box.setCurrentIndex(index)

    def validate(self) -> str:
        """
        Refuses an empty title and a pull request pointed at its own branch.

        Returns:
            str: What is wrong, empty when usable.
        """

        if not self._title.text().strip():
            return i18n.t("github.title_required")
        if not self._editing and self._head.currentText() == self._base.currentText():
            return i18n.t("github.pr_same_branch")
        return ""

    def draft(self) -> PullRequestDraft:
        """
        Returns what was entered.

        Returns:
            PullRequestDraft: The collected values.
        """

        return PullRequestDraft(
            title=self._title.text().strip(),
            body=self._body.toPlainText().strip(),
            head=self._head.currentText(),
            base=self._base.currentText(),
            draft=self._draft.isChecked(),
        )


@dataclass(slots=True)
class MergeChoice:
    """
    How a pull request should be merged.

    Attributes:
        method: ``merge``, ``squash`` or ``rebase``.
        title: Commit title, empty for GitHub's default.
        message: Commit body.
        delete_branch: Whether to remove the source branch afterwards.
    """

    method: str = "merge"
    title: str = ""
    message: str = ""
    delete_branch: bool = False


class MergeDialog(_FormDialog):
    """
    Asks how to merge, and whether the branch should go afterwards.
    """

    def __init__(
        self,
        head_branch: str,
        allowed: dict[str, bool] | None = None,
        delete_branch: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            head_branch: Branch that would be merged and possibly deleted.
            allowed: Which merge methods the repository permits, as
                ``{"merge": True, "squash": False, "rebase": True}``. A method the
                repository switched off is shown disabled rather than hidden, so
                the reason a familiar button is missing stays visible.
            delete_branch: Whether the branch checkbox starts out ticked, which
                follows the repository's own setting.
            parent: Parent widget.
        """

        super().__init__(i18n.t("github.merge_title"), i18n.t("github.merge_confirm"), parent)
        permitted = allowed or {"merge": True, "squash": True, "rebase": True}

        self._methods: dict[str, QRadioButton] = {}
        choices = QWidget(self)
        column = QVBoxLayout(choices)
        column.setContentsMargins(0, 0, 0, 0)
        for method, label_key in (
            ("merge", "github.merge_method_merge"),
            ("squash", "github.merge_method_squash"),
            ("rebase", "github.merge_method_rebase"),
        ):
            button = QRadioButton(i18n.t(label_key), choices)
            button.setEnabled(bool(permitted.get(method, True)))
            if not button.isEnabled():
                button.setToolTip(i18n.t("github.merge_method_off"))
            column.addWidget(button)
            self._methods[method] = button
        first_enabled = next(
            (button for button in self._methods.values() if button.isEnabled()), None
        )
        if first_enabled is not None:
            first_enabled.setChecked(True)
        self._form.addRow(i18n.t("github.merge_method"), choices)

        self._title = QLineEdit(self)
        self._title.setPlaceholderText(i18n.t("github.merge_title_placeholder"))
        self._form.addRow(i18n.t("github.field_title"), self._title)

        self._message = QPlainTextEdit(self)
        self._message.setMaximumHeight(100)
        self._form.addRow(i18n.t("github.field_body"), self._message)

        self._delete_branch = QCheckBox(
            i18n.t("github.merge_delete_branch", branch=head_branch), self
        )
        self._delete_branch.setChecked(delete_branch)
        self._form.addRow("", self._delete_branch)

    def validate(self) -> str:
        """
        Refuses a merge when the repository permits no method at all.

        Returns:
            str: What is wrong, empty when usable.
        """

        if not any(button.isChecked() for button in self._methods.values()):
            return i18n.t("github.merge_no_method")
        return ""

    def choice(self) -> MergeChoice:
        """
        Returns what was chosen.

        Returns:
            MergeChoice: The collected values.
        """

        method = next(
            (name for name, button in self._methods.items() if button.isChecked()), "merge"
        )
        return MergeChoice(
            method=method,
            title=self._title.text().strip(),
            message=self._message.toPlainText().strip(),
            delete_branch=self._delete_branch.isChecked(),
        )


@dataclass(slots=True)
class ReviewDraft:
    """
    A review about to be submitted.

    Attributes:
        event: ``APPROVE``, ``REQUEST_CHANGES`` or ``COMMENT``.
        body: Review text.
    """

    event: str = "COMMENT"
    body: str = ""


class ReviewDialog(_FormDialog):
    """
    Submits a review.
    """

    def __init__(self, own_pull_request: bool = False, parent: QWidget | None = None) -> None:
        """
        Args:
            own_pull_request: Whether the token's account opened this pull
                request. GitHub refuses an approval of one's own work, so the
                option is disabled with the reason spelled out rather than
                offered and then refused by the server.
            parent: Parent widget.
        """

        super().__init__(i18n.t("github.review_title"), i18n.t("github.review_submit"), parent)

        self._events: dict[str, QRadioButton] = {}
        choices = QWidget(self)
        column = QVBoxLayout(choices)
        column.setContentsMargins(0, 0, 0, 0)
        for event, label_key in (
            ("COMMENT", "github.review_comment"),
            ("APPROVE", "github.review_approve"),
            ("REQUEST_CHANGES", "github.review_request_changes"),
        ):
            button = QRadioButton(i18n.t(label_key), choices)
            column.addWidget(button)
            self._events[event] = button
        self._events["COMMENT"].setChecked(True)
        if own_pull_request:
            self._events["APPROVE"].setEnabled(False)
            self._events["APPROVE"].setToolTip(i18n.t("github.review_own_hint"))
            self._events["REQUEST_CHANGES"].setEnabled(False)
            self._events["REQUEST_CHANGES"].setToolTip(i18n.t("github.review_own_hint"))
        self._form.addRow(i18n.t("github.review_kind"), choices)

        self._body = QPlainTextEdit(self)
        self._body.setMinimumHeight(BODY_MIN_HEIGHT)
        self._form.addRow(i18n.t("github.field_body"), self._body)

    def validate(self) -> str:
        """
        Requires a text for everything but an approval.

        Returns:
            str: What is wrong, empty when usable.
        """

        if self._current_event() != "APPROVE" and not self._body.toPlainText().strip():
            return i18n.t("github.review_body_required")
        return ""

    def _current_event(self) -> str:
        """
        Returns the selected review kind.

        Returns:
            str: One of the event names.
        """

        return next(
            (event for event, button in self._events.items() if button.isChecked()), "COMMENT"
        )

    def review(self) -> ReviewDraft:
        """
        Returns what was entered.

        Returns:
            ReviewDraft: The collected values.
        """

        return ReviewDraft(event=self._current_event(), body=self._body.toPlainText().strip())


class CommentDialog(_FormDialog):
    """
    Writes or edits a single comment.
    """

    def __init__(self, text: str = "", editing: bool = False, parent: QWidget | None = None) -> None:
        """
        Args:
            text: Existing text when editing.
            editing: Whether an existing comment is being changed.
            parent: Parent widget.
        """

        super().__init__(
            i18n.t("github.comment_edit" if editing else "github.comment_new"),
            i18n.t("action.save" if editing else "github.comment_send"),
            parent,
        )
        self._body = QPlainTextEdit(text, self)
        self._body.setMinimumHeight(BODY_MIN_HEIGHT)
        self._form.addRow(i18n.t("github.field_body"), self._body)

    def validate(self) -> str:
        """
        Refuses an empty comment.

        Returns:
            str: What is wrong, empty when usable.
        """

        return "" if self._body.toPlainText().strip() else i18n.t("github.comment_required")

    def text(self) -> str:
        """
        Returns what was entered.

        Returns:
            str: The comment text.
        """

        return self._body.toPlainText().strip()


# ------------------------------------------------------------------- releases


@dataclass(slots=True)
class ReleaseDraft:
    """
    What the release editor collected.

    Attributes:
        tag_name: Tag to publish.
        target: Branch or commit a new tag is created from.
        name: Release title.
        body: Release notes.
        draft: Whether to keep it unpublished.
        prerelease: Whether to mark it as a pre-release.
        generate_notes: Whether GitHub should append its generated changelog.
    """

    tag_name: str = ""
    target: str = ""
    name: str = ""
    body: str = ""
    draft: bool = False
    prerelease: bool = False
    generate_notes: bool = False


class ReleaseEditorDialog(_FormDialog):
    """
    Creates or edits a release.
    """

    def __init__(
        self,
        branches: list[str],
        tags: list[str],
        draft: ReleaseDraft | None = None,
        editing: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            branches: Branch names a new tag could be created from.
            tags: Existing tag names, offered for reuse.
            draft: Values to start from.
            editing: Whether an existing release is being changed.
            parent: Parent widget.
        """

        super().__init__(
            i18n.t("github.release_edit" if editing else "github.release_new"),
            i18n.t("action.save" if editing else "github.create"),
            parent,
        )
        start = draft or ReleaseDraft()

        self._tag = QComboBox(self)
        self._tag.setEditable(True)
        self._tag.addItems(tags)
        self._tag.setCurrentText(start.tag_name)
        self._form.addRow(i18n.t("github.field_tag"), self._tag)

        self._target = QComboBox(self)
        self._target.addItems(branches)
        if start.target:
            index = self._target.findText(start.target)
            if index >= 0:
                self._target.setCurrentIndex(index)
        self._form.addRow(i18n.t("github.field_target"), self._target)

        self._name = QLineEdit(start.name, self)
        self._name.setPlaceholderText(i18n.t("github.release_name_placeholder"))
        self._form.addRow(i18n.t("github.field_title"), self._name)

        self._body = QPlainTextEdit(start.body, self)
        self._body.setMinimumHeight(NOTES_MIN_HEIGHT)
        self._form.addRow(i18n.t("github.field_notes"), self._body)

        self._generate = QCheckBox(i18n.t("github.release_generate_notes"), self)
        self._generate.setChecked(start.generate_notes)
        self._generate.setVisible(not editing)
        self._form.addRow("", self._generate)

        self._draft = QCheckBox(i18n.t("github.release_draft"), self)
        self._draft.setChecked(start.draft)
        self._form.addRow("", self._draft)

        self._prerelease = QCheckBox(i18n.t("github.release_prerelease"), self)
        self._prerelease.setChecked(start.prerelease)
        self._form.addRow("", self._prerelease)

    def validate(self) -> str:
        """
        Refuses a release without a tag.

        Returns:
            str: What is wrong, empty when usable.
        """

        return "" if self._tag.currentText().strip() else i18n.t("github.tag_required")

    def draft(self) -> ReleaseDraft:
        """
        Returns what was entered.

        Returns:
            ReleaseDraft: The collected values.
        """

        return ReleaseDraft(
            tag_name=self._tag.currentText().strip(),
            target=self._target.currentText(),
            name=self._name.text().strip(),
            body=self._body.toPlainText().strip(),
            draft=self._draft.isChecked(),
            prerelease=self._prerelease.isChecked(),
            generate_notes=self._generate.isChecked(),
        )


# --------------------------------------------------------------- repositories


@dataclass(slots=True)
class RepositorySettings:
    """
    The repository settings the dialog can change.

    Attributes:
        description: Short description.
        homepage: Project website.
        topics: Topic names.
        default_branch: Branch the server treats as the main one.
        visibility: ``public`` or ``private``.
        has_issues: Whether the issue tracker is on.
        has_wiki: Whether the wiki is on.
        has_projects: Whether project boards are on.
        has_discussions: Whether discussions are on.
        archived: Whether the repository is archived.
        allow_merge_commit: Whether a merge commit is permitted.
        allow_squash_merge: Whether squashing is permitted.
        allow_rebase_merge: Whether rebasing is permitted.
        delete_branch_on_merge: Whether the server removes a merged branch.
    """

    description: str = ""
    homepage: str = ""
    topics: list[str] = field(default_factory=list)
    default_branch: str = ""
    visibility: str = "private"
    has_issues: bool = True
    has_wiki: bool = True
    has_projects: bool = True
    has_discussions: bool = False
    archived: bool = False
    allow_merge_commit: bool = True
    allow_squash_merge: bool = True
    allow_rebase_merge: bool = True
    delete_branch_on_merge: bool = False

    def changes_from(self, other: RepositorySettings) -> dict[str, Any]:
        """
        Returns only the fields that differ from another set.

        Sending the whole form back would rewrite fields nobody touched, which
        matters for ``visibility``: patching it to its current value is harmless,
        but patching it to a stale value read minutes ago is not.

        Args:
            other: The values as they were.

        Returns:
            dict[str, Any]: API field names to new values. Topics are left out,
                because they go through their own endpoint.
        """

        changes: dict[str, Any] = {}
        for name in (
            "description",
            "homepage",
            "default_branch",
            "visibility",
            "has_issues",
            "has_wiki",
            "has_projects",
            "has_discussions",
            "archived",
            "allow_merge_commit",
            "allow_squash_merge",
            "allow_rebase_merge",
            "delete_branch_on_merge",
        ):
            mine = getattr(self, name)
            theirs = getattr(other, name)
            if mine != theirs:
                changes[name] = mine
        return changes


class RepositorySettingsDialog(_FormDialog):
    """
    Changes a repository's settings on the server.

    Attributes:
        delete_requested: True when the user asked for deletion instead of a
            save. The dialog does not delete anything itself; the panel does,
            after its own confirmation.
    """

    def __init__(
        self,
        full_name: str,
        settings: RepositorySettings,
        branches: list[str],
        can_administer: bool = True,
        collaborators: list[tuple[str, str]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            full_name: ``owner/name``, shown as the title.
            settings: Current values.
            branches: Branch names that could be the default.
            can_administer: Whether the token may change settings at all. When it
                may not, the form is shown read-only with the reason, which is
                more useful than an empty dialog or a 403 later.
            collaborators: ``(login, role)`` per person with access.
            parent: Parent widget.
        """

        super().__init__(
            i18n.t("github.settings_title", repo=full_name), i18n.t("action.save"), parent
        )
        self._original = settings
        self.delete_requested = False
        self.collaborator_request: tuple[str, str, str] | None = None

        self._description = QLineEdit(settings.description, self)
        self._form.addRow(i18n.t("github.field_description"), self._description)

        self._homepage = QLineEdit(settings.homepage, self)
        self._form.addRow(i18n.t("github.field_homepage"), self._homepage)

        self._topics = QLineEdit(", ".join(settings.topics), self)
        self._topics.setPlaceholderText(i18n.t("github.topics_placeholder"))
        self._form.addRow(i18n.t("github.field_topics"), self._topics)

        self._default_branch = QComboBox(self)
        self._default_branch.addItems(branches or [settings.default_branch])
        index = self._default_branch.findText(settings.default_branch)
        if index >= 0:
            self._default_branch.setCurrentIndex(index)
        self._form.addRow(i18n.t("github.field_default_branch"), self._default_branch)

        self._visibility = QComboBox(self)
        self._visibility.addItem(i18n.t("github.visibility_private"), "private")
        self._visibility.addItem(i18n.t("github.visibility_public"), "public")
        self._visibility.setCurrentIndex(0 if settings.visibility != "public" else 1)
        self._form.addRow(i18n.t("github.field_visibility"), self._visibility)

        self._has_issues = QCheckBox(i18n.t("github.feature_issues"), self)
        self._has_issues.setChecked(settings.has_issues)
        self._form.addRow("", self._has_issues)

        self._has_wiki = QCheckBox(i18n.t("github.feature_wiki"), self)
        self._has_wiki.setChecked(settings.has_wiki)
        self._form.addRow("", self._has_wiki)

        self._has_projects = QCheckBox(i18n.t("github.feature_projects"), self)
        self._has_projects.setChecked(settings.has_projects)
        self._form.addRow("", self._has_projects)

        self._has_discussions = QCheckBox(i18n.t("github.feature_discussions"), self)
        self._has_discussions.setChecked(settings.has_discussions)
        self._form.addRow("", self._has_discussions)

        self._archived = QCheckBox(i18n.t("github.feature_archived"), self)
        self._archived.setChecked(settings.archived)
        self._form.addRow("", self._archived)

        self._allow_merge = QCheckBox(i18n.t("github.allow_merge_commit"), self)
        self._allow_merge.setChecked(settings.allow_merge_commit)
        self._allow_squash = QCheckBox(i18n.t("github.allow_squash"), self)
        self._allow_squash.setChecked(settings.allow_squash_merge)
        self._allow_rebase = QCheckBox(i18n.t("github.allow_rebase"), self)
        self._allow_rebase.setChecked(settings.allow_rebase_merge)
        self._delete_on_merge = QCheckBox(i18n.t("github.delete_branch_on_merge"), self)
        self._delete_on_merge.setChecked(settings.delete_branch_on_merge)
        merge_block = QWidget(self)
        merge_column = QVBoxLayout(merge_block)
        merge_column.setContentsMargins(0, 0, 0, 0)
        for box in (self._allow_merge, self._allow_squash, self._allow_rebase, self._delete_on_merge):
            merge_column.addWidget(box)
        self._form.addRow(i18n.t("github.merge_method"), merge_block)

        self._people = QListWidget(self)
        self._people.setMaximumHeight(110)
        for login, role in collaborators or []:
            item = QListWidgetItem(f"{login}  ({role})" if role else login, self._people)
            item.setData(Qt.ItemDataRole.UserRole, login)
        self._form.addRow(i18n.t("github.collaborators"), self._people)

        people_row = QWidget(self)
        people_layout = QHBoxLayout(people_row)
        people_layout.setContentsMargins(0, 0, 0, 0)
        self._new_person = QLineEdit(people_row)
        self._new_person.setPlaceholderText(i18n.t("github.collaborator_placeholder"))
        people_layout.addWidget(self._new_person, 1)
        self._permission = QComboBox(people_row)
        for value, label_key in (
            ("pull", "github.permission_read"),
            ("triage", "github.permission_triage"),
            ("push", "github.permission_write"),
            ("maintain", "github.permission_maintain"),
            ("admin", "github.permission_admin"),
        ):
            self._permission.addItem(i18n.t(label_key), value)
        self._permission.setCurrentIndex(2)
        people_layout.addWidget(self._permission)
        self._add_person = QPushButton(i18n.t("github.collaborator_add"), people_row)
        self._add_person.clicked.connect(self._on_add_person)
        people_layout.addWidget(self._add_person)
        self._remove_person = QPushButton(i18n.t("github.collaborator_remove"), people_row)
        self._remove_person.clicked.connect(self._on_remove_person)
        people_layout.addWidget(self._remove_person)
        self._form.addRow("", people_row)

        delete_row = QWidget(self)
        row = QHBoxLayout(delete_row)
        row.setContentsMargins(0, 0, 0, 0)
        self._delete_button = QPushButton(i18n.t("github.repo_delete"), delete_row)
        self._delete_button.clicked.connect(self._on_delete)
        row.addWidget(self._delete_button)
        row.addStretch(1)
        self._column.insertWidget(self._column.count() - 1, delete_row)

        if not can_administer:
            for widget in (
                self._description,
                self._homepage,
                self._topics,
                self._default_branch,
                self._visibility,
                self._has_issues,
                self._has_wiki,
                self._has_projects,
                self._has_discussions,
                self._archived,
                self._allow_merge,
                self._allow_squash,
                self._allow_rebase,
                self._delete_on_merge,
                self._new_person,
                self._permission,
                self._add_person,
                self._remove_person,
                self._delete_button,
                self._accept_button,
            ):
                widget.setEnabled(False)
            self._notice.set_message(i18n.t("github.settings_read_only"), "", "info")
            self._notice.setVisible(True)

    def _on_delete(self) -> None:
        """
        Closes the dialog with the deletion request set.

        Returns:
            None
        """

        self.delete_requested = True
        self.accept()

    def _on_add_person(self) -> None:
        """
        Asks for someone to be invited.

        Returns:
            None
        """

        login = self._new_person.text().strip()
        if not login:
            self._complain(i18n.t("github.collaborator_required"))
            return
        self.collaborator_request = ("add", login, self._permission.currentData())
        self.accept()

    def _on_remove_person(self) -> None:
        """
        Asks for the selected person's access to be taken away.

        Returns:
            None
        """

        item = self._people.currentItem()
        if item is None:
            self._complain(i18n.t("github.nothing_selected"))
            return
        self.collaborator_request = ("remove", item.data(Qt.ItemDataRole.UserRole), "")
        self.accept()

    def settings(self) -> RepositorySettings:
        """
        Returns what was entered.

        Returns:
            RepositorySettings: The collected values.
        """

        topics = [part.strip() for part in self._topics.text().split(",")]
        return RepositorySettings(
            description=self._description.text().strip(),
            homepage=self._homepage.text().strip(),
            topics=[part for part in topics if part],
            default_branch=self._default_branch.currentText(),
            visibility=self._visibility.currentData(),
            has_issues=self._has_issues.isChecked(),
            has_wiki=self._has_wiki.isChecked(),
            has_projects=self._has_projects.isChecked(),
            has_discussions=self._has_discussions.isChecked(),
            archived=self._archived.isChecked(),
            allow_merge_commit=self._allow_merge.isChecked(),
            allow_squash_merge=self._allow_squash.isChecked(),
            allow_rebase_merge=self._allow_rebase.isChecked(),
            delete_branch_on_merge=self._delete_on_merge.isChecked(),
        )

    def changes(self) -> dict[str, Any]:
        """
        Returns only the settings that were actually changed.

        Returns:
            dict[str, Any]: API field names to new values.
        """

        return self.settings().changes_from(self._original)

    def topics_changed(self) -> bool:
        """
        Reports whether the topic list differs from the stored one.

        Returns:
            bool: True when the topics need their own request.
        """

        return self.settings().topics != self._original.topics


class DeleteRepositoryDialog(QDialog):
    """
    The confirmation for deleting a repository.

    Deliberately not a yes/no box. Everything else in this application can be
    recovered, from a discarded change to a deleted branch; this cannot. So the
    full name has to be typed, exactly as GitHub's own web interface asks for it,
    and the button stays disabled until it matches.
    """

    def __init__(self, full_name: str, parent: QWidget | None = None) -> None:
        """
        Args:
            full_name: ``owner/name`` of the repository.
            parent: Parent widget.
        """

        super().__init__(parent)
        self.setWindowTitle(i18n.t("github.repo_delete"))
        self.setMinimumWidth(480)
        self._expected = full_name

        column = QVBoxLayout(self)

        warning = InlineMessage(
            i18n.t("github.repo_delete_warning", repo=full_name),
            i18n.t("github.repo_delete_detail"),
            "danger",
            self,
        )
        column.addWidget(warning)

        prompt = QLabel(i18n.t("github.repo_delete_prompt", repo=full_name), self)
        prompt.setWordWrap(True)
        column.addWidget(prompt)

        self._confirmation = QLineEdit(self)
        self._confirmation.textChanged.connect(self._on_typed)
        column.addWidget(self._confirmation)

        buttons = QDialogButtonBox(self)
        self._accept_button = buttons.addButton(
            i18n.t("github.repo_delete_confirm"), QDialogButtonBox.ButtonRole.DestructiveRole
        )
        self._accept_button.setEnabled(False)
        buttons.addButton(i18n.t("action.cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        column.addWidget(buttons)

    def _on_typed(self, text: str) -> None:
        """
        Enables the button once the name matches exactly.

        Args:
            text: What was typed.

        Returns:
            None
        """

        self._accept_button.setEnabled(text.strip() == self._expected)


@dataclass(slots=True)
class NewRepositoryDraft:
    """
    What the repository creator collected.

    Attributes:
        name: Repository name.
        description: Short description.
        organization: Organisation to create it in, empty for the personal
            account.
        private: Whether it starts out private.
        auto_init: Whether to create an initial commit with a README.
        gitignore_template: Name of a ``.gitignore`` template.
        license_template: License key.
        clone_after: Whether to open the clone dialog once it exists.
    """

    name: str = ""
    description: str = ""
    organization: str = ""
    private: bool = True
    auto_init: bool = True
    gitignore_template: str = ""
    license_template: str = ""
    clone_after: bool = True


# The templates GitHub offers are a long list; these are the ones worth putting
# in a desktop dialog. Anything else is a click away in the web interface.
GITIGNORE_TEMPLATES = ["", "Python", "Node", "Java", "Go", "Rust", "C++", "VisualStudio", "Unity"]
LICENSE_TEMPLATES = [
    ("", "github.license_none"),
    ("mit", "github.license_mit"),
    ("apache-2.0", "github.license_apache"),
    ("gpl-3.0", "github.license_gpl3"),
    ("bsd-3-clause", "github.license_bsd3"),
    ("unlicense", "github.license_unlicense"),
]


class CreateRepositoryDialog(_FormDialog):
    """
    Creates a repository on GitHub and offers to clone it straight away.
    """

    def __init__(self, organizations: list[str], parent: QWidget | None = None) -> None:
        """
        Args:
            organizations: Organisations the account may create in.
            parent: Parent widget.
        """

        super().__init__(i18n.t("github.repo_new"), i18n.t("github.create"), parent)

        self._name = QLineEdit(self)
        self._form.addRow(i18n.t("github.field_name"), self._name)

        self._owner = QComboBox(self)
        self._owner.addItem(i18n.t("github.owner_personal"), "")
        for organization in organizations:
            self._owner.addItem(organization, organization)
        self._form.addRow(i18n.t("github.field_owner"), self._owner)

        self._description = QLineEdit(self)
        self._form.addRow(i18n.t("github.field_description"), self._description)

        self._visibility = QComboBox(self)
        self._visibility.addItem(i18n.t("github.visibility_private"), True)
        self._visibility.addItem(i18n.t("github.visibility_public"), False)
        self._form.addRow(i18n.t("github.field_visibility"), self._visibility)

        self._gitignore = QComboBox(self)
        for template in GITIGNORE_TEMPLATES:
            self._gitignore.addItem(template or i18n.t("github.gitignore_none"), template)
        self._form.addRow(i18n.t("github.field_gitignore"), self._gitignore)

        self._license = QComboBox(self)
        for key, label_key in LICENSE_TEMPLATES:
            self._license.addItem(i18n.t(label_key), key)
        self._form.addRow(i18n.t("github.field_license"), self._license)

        self._auto_init = QCheckBox(i18n.t("github.repo_auto_init"), self)
        self._auto_init.setChecked(True)
        self._form.addRow("", self._auto_init)

        self._clone = QCheckBox(i18n.t("github.repo_clone_after"), self)
        self._clone.setChecked(True)
        self._form.addRow("", self._clone)

    def validate(self) -> str:
        """
        Refuses a missing name and one GitHub would reject anyway.

        Returns:
            str: What is wrong, empty when usable.
        """

        name = self._name.text().strip()
        if not name:
            return i18n.t("github.name_required")
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
        if set(name) - allowed:
            return i18n.t("github.name_invalid")
        return ""

    def draft(self) -> NewRepositoryDraft:
        """
        Returns what was entered.

        Returns:
            NewRepositoryDraft: The collected values.
        """

        return NewRepositoryDraft(
            name=self._name.text().strip(),
            description=self._description.text().strip(),
            organization=self._owner.currentData() or "",
            private=bool(self._visibility.currentData()),
            auto_init=self._auto_init.isChecked(),
            gitignore_template=self._gitignore.currentData() or "",
            license_template=self._license.currentData() or "",
            clone_after=self._clone.isChecked(),
        )


class CommentsDialog(QDialog):
    """
    Lists the comments on an issue or pull request and edits them.

    Reading comments happens in the detail view, which renders them as one
    document. This dialog exists for the other half: changing or removing one,
    which needs the comments as separate, selectable things rather than as text.

    Attributes:
        edit_requested: Set to the comment id the user wants to change.
        delete_requested: Set to the comment id the user wants removed.
    """

    def __init__(self, comments: list[Any], parent: QWidget | None = None) -> None:
        """
        Args:
            comments: The ``Comment`` objects to list.
            parent: Parent widget.
        """

        super().__init__(parent)
        self.setWindowTitle(i18n.t("github.comments_title"))
        self.setMinimumSize(560, 380)
        self.edit_requested: int | None = None
        self.delete_requested: int | None = None
        self._comments = comments

        column = QVBoxLayout(self)

        self._list = QListWidget(self)
        for comment in comments:
            first_line = (comment.body or "").strip().splitlines()
            preview = first_line[0] if first_line else ""
            label = f"{comment.author}: {preview[:80]}"
            if not comment.editable:
                label = f"{label}  ({i18n.t('github.comment_foreign')})"
            item = QListWidgetItem(label, self._list)
            item.setData(Qt.ItemDataRole.UserRole, comment.comment_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, comment.editable)
        self._list.currentItemChanged.connect(self._on_selection)
        column.addWidget(self._list, 1)

        row = QHBoxLayout()
        self._edit = QPushButton(i18n.t("action.edit"), self)
        self._edit.clicked.connect(lambda: self._finish("edit"))
        self._edit.setEnabled(False)
        row.addWidget(self._edit)
        self._delete = QPushButton(i18n.t("action.delete"), self)
        self._delete.clicked.connect(lambda: self._finish("delete"))
        self._delete.setEnabled(False)
        row.addWidget(self._delete)
        row.addStretch(1)
        close = QPushButton(i18n.t("action.close"), self)
        close.clicked.connect(self.reject)
        row.addWidget(close)
        column.addLayout(row)

        if not comments:
            self._list.addItem(i18n.t("github.comments_none"))

    def _on_selection(self, current: QListWidgetItem | None, _previous: Any = None) -> None:
        """
        Enables the buttons only for a comment this account may change.

        Args:
            current: Newly selected row.
            _previous: Previously selected row, unused.

        Returns:
            None
        """

        editable = bool(current and current.data(Qt.ItemDataRole.UserRole + 1))
        self._edit.setEnabled(editable)
        self._delete.setEnabled(editable)

    def _finish(self, action: str) -> None:
        """
        Records which action was chosen and closes.

        Args:
            action: ``edit`` or ``delete``.

        Returns:
            None
        """

        item = self._list.currentItem()
        if item is None:
            return
        comment_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(comment_id, int):
            return
        if action == "edit":
            self.edit_requested = comment_id
        else:
            self.delete_requested = comment_id
        self.accept()

    def body_of(self, comment_id: int) -> str:
        """
        Returns the text of one comment.

        Args:
            comment_id: Comment identifier.

        Returns:
            str: Its body, empty when the id is unknown.
        """

        for comment in self._comments:
            if comment.comment_id == comment_id:
                return comment.body
        return ""


class LabelMilestoneDialog(QDialog):
    """
    Manages a repository's labels and milestones.

    Both are things an issue picks from, and until now Branchly could only pick.
    Creating them belongs here rather than in the issue editor: a label made by
    accident while writing an issue is a label the repository keeps.

    Actions are handed back rather than performed, in keeping with every other
    dialog here.

    Attributes:
        requested: What the user asked for, as ``(action, value)``. Actions are
            ``create_label``, ``delete_label``, ``create_milestone``,
            ``delete_milestone``. Empty when the dialog was just closed.
    """

    def __init__(
        self,
        labels: list[Any],
        milestones: list[Any],
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            labels: The repository's ``Label`` objects.
            milestones: The repository's ``Milestone`` objects.
            parent: Parent widget.
        """

        super().__init__(parent)
        self.setWindowTitle(i18n.t("github.labels_title"))
        self.setMinimumSize(560, 420)
        self.requested: tuple[str, Any] | None = None

        column = QVBoxLayout(self)

        column.addWidget(QLabel(i18n.t("github.field_labels"), self))
        self._labels = QListWidget(self)
        for label in labels:
            item = QListWidgetItem(
                f"{label.name}  ({label.color})" if label.color else label.name, self._labels
            )
            item.setData(Qt.ItemDataRole.UserRole, label.name)
        column.addWidget(self._labels, 1)

        label_row = QHBoxLayout()
        self._new_label = QLineEdit(self)
        self._new_label.setPlaceholderText(i18n.t("github.label_name_placeholder"))
        label_row.addWidget(self._new_label, 1)
        self._new_color = QLineEdit(self)
        self._new_color.setPlaceholderText("ededed")
        self._new_color.setMaximumWidth(90)
        label_row.addWidget(self._new_color)
        add_label = QPushButton(i18n.t("github.create"), self)
        add_label.clicked.connect(self._on_create_label)
        label_row.addWidget(add_label)
        rename_label = QPushButton(i18n.t("github.label_rename"), self)
        rename_label.setToolTip(i18n.t("github.label_rename_hint"))
        rename_label.clicked.connect(self._on_rename_label)
        label_row.addWidget(rename_label)
        remove_label = QPushButton(i18n.t("action.delete"), self)
        remove_label.clicked.connect(self._on_delete_label)
        label_row.addWidget(remove_label)
        column.addLayout(label_row)

        column.addWidget(QLabel(i18n.t("github.field_milestone"), self))
        self._milestones = QListWidget(self)
        for milestone in milestones:
            text = f"{milestone.title}  ({milestone.open_issues} {i18n.t('github.state_open').lower()})"
            item = QListWidgetItem(text, self._milestones)
            item.setData(Qt.ItemDataRole.UserRole, milestone.number)
        column.addWidget(self._milestones, 1)

        milestone_row = QHBoxLayout()
        self._new_milestone = QLineEdit(self)
        self._new_milestone.setPlaceholderText(i18n.t("github.milestone_title_placeholder"))
        milestone_row.addWidget(self._new_milestone, 1)
        add_milestone = QPushButton(i18n.t("github.create"), self)
        add_milestone.clicked.connect(self._on_create_milestone)
        milestone_row.addWidget(add_milestone)
        close_milestone = QPushButton(i18n.t("github.milestone_close"), self)
        close_milestone.setToolTip(i18n.t("github.milestone_close_hint"))
        close_milestone.clicked.connect(self._on_close_milestone)
        milestone_row.addWidget(close_milestone)
        remove_milestone = QPushButton(i18n.t("action.delete"), self)
        remove_milestone.clicked.connect(self._on_delete_milestone)
        milestone_row.addWidget(remove_milestone)
        column.addLayout(milestone_row)

        self._notice = InlineMessage("", "", "danger", self)
        self._notice.setVisible(False)
        column.addWidget(self._notice)

        buttons = QDialogButtonBox(self)
        buttons.addButton(i18n.t("action.close"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self.reject)
        column.addWidget(buttons)

    def _complain(self, message: str) -> None:
        """
        Shows what is wrong with the input.

        Args:
            message: The problem.

        Returns:
            None
        """

        self._notice.set_message(message, "", "danger")
        self._notice.setVisible(True)

    def _finish(self, action: str, value: Any) -> None:
        """
        Records the request and closes.

        Args:
            action: What was asked for.
            value: What it applies to.

        Returns:
            None
        """

        self.requested = (action, value)
        self.accept()

    def _on_create_label(self) -> None:
        """
        Asks for a new label.

        Returns:
            None
        """

        name = self._new_label.text().strip()
        if not name:
            self._complain(i18n.t("github.name_required"))
            return
        self._finish("create_label", (name, self._new_color.text().strip() or "ededed"))

    def _on_rename_label(self) -> None:
        """
        Asks for the selected label to be renamed to what is in the name field.

        Returns:
            None
        """

        item = self._labels.currentItem()
        if item is None:
            self._complain(i18n.t("github.nothing_selected"))
            return
        new_name = self._new_label.text().strip()
        if not new_name:
            self._complain(i18n.t("github.label_rename_needs_name"))
            return
        self._finish(
            "rename_label",
            (item.data(Qt.ItemDataRole.UserRole), new_name, self._new_color.text().strip()),
        )

    def _on_delete_label(self) -> None:
        """
        Asks for the selected label to be removed.

        Returns:
            None
        """

        item = self._labels.currentItem()
        if item is None:
            self._complain(i18n.t("github.nothing_selected"))
            return
        self._finish("delete_label", item.data(Qt.ItemDataRole.UserRole))

    def _on_create_milestone(self) -> None:
        """
        Asks for a new milestone.

        Returns:
            None
        """

        title = self._new_milestone.text().strip()
        if not title:
            self._complain(i18n.t("github.title_required"))
            return
        self._finish("create_milestone", title)

    def _on_close_milestone(self) -> None:
        """
        Asks for the selected milestone to be closed rather than deleted.

        Returns:
            None
        """

        item = self._milestones.currentItem()
        if item is None:
            self._complain(i18n.t("github.nothing_selected"))
            return
        self._finish("close_milestone", item.data(Qt.ItemDataRole.UserRole))

    def _on_delete_milestone(self) -> None:
        """
        Asks for the selected milestone to be removed.

        Returns:
            None
        """

        item = self._milestones.currentItem()
        if item is None:
            self._complain(i18n.t("github.nothing_selected"))
            return
        self._finish("delete_milestone", item.data(Qt.ItemDataRole.UserRole))


class PickerDialog(_FormDialog):
    """
    Picks one value out of a list, with a free-text field as the fallback.

    Used where the choice is a plain one and a dedicated dialog would be more
    ceremony than the decision deserves: which workflow to start, which tag to
    delete, whom to ask for a review.
    """

    def __init__(
        self,
        title: str,
        label: str,
        options: list[tuple[str, Any]],
        editable: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            title: Window title.
            label: Label in front of the field.
            options: ``(shown text, value)`` per entry.
            editable: Whether a value can also be typed, for a login that is not
                in the list.
            parent: Parent widget.
        """

        super().__init__(title, i18n.t("action.ok"), parent)
        self._box = QComboBox(self)
        self._box.setEditable(editable)
        for text, value in options:
            self._box.addItem(text, value)
        self._form.addRow(label, self._box)

    def validate(self) -> str:
        """
        Refuses an empty choice.

        Returns:
            str: What is wrong, empty when usable.
        """

        return "" if self.value() not in (None, "") else i18n.t("github.nothing_selected")

    def value(self) -> Any:
        """
        Returns what was chosen.

        Returns:
            Any: The stored value of the selected entry, or the typed text when
                the box is editable and holds something the list does not.
        """

        data = self._box.currentData()
        if data is not None and self._box.currentText() == self._box.itemText(self._box.currentIndex()):
            return data
        return self._box.currentText().strip()


class RepositoryPickerDialog(QDialog):
    """
    Picks one of the account's repositories.

    This is the one place the whole account is listed rather than the selected
    project. It exists because cloning is the moment where "which of my
    repositories" is the actual question, and typing a URL from memory is the
    worse answer.

    Attributes:
        chosen: Clone address of the picked repository, empty when cancelled.
    """

    def __init__(self, repositories: list[Any], parent: QWidget | None = None) -> None:
        """
        Args:
            repositories: The ``Repository`` objects to offer.
            parent: Parent widget.
        """

        super().__init__(parent)
        self.setWindowTitle(i18n.t("clone.from_github"))
        self.setMinimumSize(620, 460)
        self.chosen = ""
        self._repositories = repositories

        column = QVBoxLayout(self)

        self._search = QLineEdit(self)
        self._search.setPlaceholderText(i18n.t("clone.search_placeholder"))
        self._search.textChanged.connect(self._refilter)
        column.addWidget(self._search)

        self._list = QListWidget(self)
        self._list.itemDoubleClicked.connect(lambda _item: self._accept_selection())
        column.addWidget(self._list, 1)
        self._refilter("")

        self._count = QLabel("", self)
        self._count.setObjectName("Muted")
        column.addWidget(self._count)
        self._update_count()

        buttons = QDialogButtonBox(self)
        self._accept_button = buttons.addButton(
            i18n.t("action.ok"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        buttons.addButton(i18n.t("action.cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        column.addWidget(buttons)

    def _refilter(self, text: str) -> None:
        """
        Rebuilds the list for the current search text.

        Args:
            text: What was typed.

        Returns:
            None
        """

        needle = text.strip().lower()
        self._list.clear()
        for repository in self._repositories:
            haystack = f"{repository.full_name} {repository.description}".lower()
            if needle and needle not in haystack:
                continue
            marks = []
            if repository.private:
                marks.append(i18n.t("github.visibility_private"))
            if repository.archived:
                marks.append(i18n.t("github.feature_archived"))
            if repository.fork:
                marks.append("fork")
            suffix = f"  ({', '.join(marks)})" if marks else ""
            item = QListWidgetItem(f"{repository.full_name}{suffix}", self._list)
            item.setToolTip(repository.description or repository.full_name)
            item.setData(Qt.ItemDataRole.UserRole, repository.clone_url)
        if self._list.count():
            self._list.setCurrentRow(0)
        self._update_count()

    def _update_count(self) -> None:
        """
        Shows how many entries the filter left.

        Returns:
            None
        """

        if hasattr(self, "_count"):
            self._count.setText(
                i18n.t("clone.repo_count", shown=self._list.count(), total=len(self._repositories))
            )

    def _accept_selection(self) -> None:
        """
        Takes the selected repository's clone address and closes.

        Returns:
            None
        """

        item = self._list.currentItem()
        if item is None:
            return
        url = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(url, str) and url:
            self.chosen = url
            self.accept()
