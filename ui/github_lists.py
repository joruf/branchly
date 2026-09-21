"""
The four lists the GitHub panel is made of: pull requests, issues, releases and
workflow runs.

They share a shape, which is the reason they share a base class: a filter row on
top, a list, a detail view below it, and a row of actions at the bottom. What
differs is what gets fetched, how a row is labelled, and which actions apply.

Three decisions run through all of them:

* **Nothing is fetched on the UI thread.** Every call goes through the panel's
  runner, which serialises them. A list is never left showing a state from before
  the user's own change, because the write empties the cache and the reload that
  follows it is queued behind the write rather than racing it.
* **Detail views are rendered as Markdown.** Issue bodies, release notes and
  comments are Markdown on GitHub, and Qt renders it. Building the same thing out
  of labels would be more code and read worse.
* **An action that cannot work is disabled with a reason, not hidden.** A missing
  push permission, an archived repository or a draft pull request each disable a
  different button, and the tooltip says which.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import i18n
from config.theme import get_theme_colors
from github_api import issues as issue_api
from github_api import pulls as pull_api
from github_api.client import GitHubClient
from github_api.http import ApiResult
from github_api.models import (
    CHECK_COLOR_TOKENS,
    CHECK_LABEL_KEYS,
    Comment,
    Issue,
    PullRequest,
    Release,
    Review,
    WorkflowRun,
)
from services import avatars
from ui.github_dialogs import (
    CommentDialog,
    CommentsDialog,
    IssueDraft,
    IssueEditorDialog,
    LabelMilestoneDialog,
    MergeDialog,
    PickerDialog,
    PullRequestDraft,
    PullRequestEditorDialog,
    ReleaseDraft,
    ReleaseEditorDialog,
    ReviewDialog,
)
from ui.github_worker import ApiRunner
from ui.widgets import FlowLayout, StatusDot

AVATAR_SIZE = 20
_ROLE_ID = int(Qt.ItemDataRole.UserRole)
_ROLE_PAYLOAD = int(Qt.ItemDataRole.UserRole) + 1

# How many items a list loads. Beyond this nobody scrolls, and a repository with
# thousands of closed issues would otherwise spend a minute paginating.
LIST_LIMIT = 200

# Review verdicts as GitHub names them, mapped onto translation keys. A state
# that is not in here is shown as the API spelled it rather than as a missing
# key, because a new verdict must not turn the review list into placeholders.
_REVIEW_STATE_KEYS = {
    "APPROVED": "github.review_state_approved",
    "CHANGES_REQUESTED": "github.review_state_changes_requested",
    "COMMENTED": "github.review_state_commented",
    "DISMISSED": "github.review_state_dismissed",
    "PENDING": "github.review_state_pending",
}


@dataclass(frozen=True, slots=True)
class GitHubContext:
    """
    Which repository the panel is currently showing.

    Attributes:
        owner: Repository owner on GitHub.
        repo: Repository name on GitHub.
        viewer_login: Login of the account the token belongs to, used to work out
            what the user may edit and whose pull request is their own.
        local_branch: Branch checked out locally, offered as the source for a new
            pull request.
        default_branch: Branch the server treats as the main one.
        can_push: Whether the token may write here.
        can_administer: Whether the token may change settings or delete.
        merge_methods: The merge methods this repository permits. A repository
            that switched squashing off must not offer it, and finding that out
            from a refused merge is the wrong moment.
        delete_branch_on_merge: Whether the server removes the branch itself,
            which decides how the merge dialog's checkbox starts out.
    """

    owner: str = ""
    repo: str = ""
    viewer_login: str = ""
    local_branch: str = ""
    default_branch: str = ""
    can_push: bool = True
    can_administer: bool = True
    merge_methods: tuple[str, ...] = ("merge", "squash", "rebase")
    delete_branch_on_merge: bool = False

    @property
    def ok(self) -> bool:
        """
        Reports whether a repository is set.

        Returns:
            bool: True when calls can be made.
        """

        return bool(self.owner and self.repo)

    @property
    def full_name(self) -> str:
        """
        Returns the repository as ``owner/name``.

        Returns:
            str: The full name, empty when no repository is set.
        """

        return f"{self.owner}/{self.repo}" if self.ok else ""


def _timestamp(value: str) -> str:
    """
    Shortens an ISO timestamp to the date and time.

    Args:
        value: Timestamp as the API returned it.

    Returns:
        str: ``YYYY-MM-DD HH:MM``, or the input when it has another shape.
    """

    if len(value) >= 16 and value[10] == "T":
        return f"{value[:10]} {value[11:16]}"
    return value


def _comment_section(comments: list[Comment]) -> str:
    """
    Renders comments as one Markdown block.

    Args:
        comments: The comments, oldest first.

    Returns:
        str: Markdown, empty when there are none.
    """

    if not comments:
        return ""
    parts = [f"### {i18n.t('github.comments_title')} ({len(comments)})"]
    for comment in comments:
        stamp = _timestamp(comment.created_at)
        parts.append(f"**{comment.author}** · {stamp}\n\n{comment.body.strip() or '*(leer)*'}")
    return "\n\n---\n\n".join(parts)


class ItemRow(QWidget):
    """
    One list row: an optional state dot, two lines of text, an optional avatar.
    """

    def __init__(
        self,
        title: str,
        subtitle: str,
        check_state: str | None,
        avatar_url: str,
        show_avatar: bool,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            title: Main line.
            subtitle: Secondary line.
            check_state: Check state for the dot, or None to omit it.
            avatar_url: Author avatar URL.
            show_avatar: Whether avatars are enabled.
            parent: Parent widget.
        """

        super().__init__(parent)
        colors = get_theme_colors()

        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 6, 4)
        row.setSpacing(8)

        if check_state is not None:
            dot = StatusDot(CHECK_COLOR_TOKENS.get(check_state, "check_neutral"), 9, self)
            dot.setToolTip(i18n.t(CHECK_LABEL_KEYS.get(check_state, "github.checks_none")))
            row.addWidget(dot)

        if show_avatar and avatar_url:
            picture = QLabel(self)
            picture.setFixedSize(AVATAR_SIZE, AVATAR_SIZE)
            payload = avatars.cached_bytes(avatar_url)
            if payload:
                pixmap = QPixmap()
                if pixmap.loadFromData(payload):
                    picture.setPixmap(
                        pixmap.scaled(
                            AVATAR_SIZE,
                            AVATAR_SIZE,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
            row.addWidget(picture)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        title_label = QLabel(title, self)
        title_label.setStyleSheet(f"color: {colors.text};")
        subtitle_label = QLabel(subtitle, self)
        subtitle_label.setStyleSheet(f"color: {colors.text_muted}; font-size: 11px;")
        text.addWidget(title_label)
        text.addWidget(subtitle_label)
        row.addLayout(text, 1)


class RemoteTab(QWidget):
    """
    Shared shell for the four lists.

    Attributes:
        open_url_requested: Emitted with a web address to open.
        failed: Emitted with a failed ``ApiResult`` for the panel to report.
        changed: Emitted after a successful write, so the panel can refresh
            anything else that depends on it.
        working: Emitted with True while a call is in flight.
    """

    open_url_requested = Signal(str)
    failed = Signal(object)
    changed = Signal()
    working = Signal(bool)
    confirm_requested = Signal(str, str, object)

    def __init__(
        self,
        client: GitHubClient,
        runner: ApiRunner,
        show_avatars: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            client: The API client.
            runner: Runner performing the calls off the UI thread.
            show_avatars: Whether author pictures are shown.
            parent: Parent widget.
        """

        super().__init__(parent)
        self._client = client
        self._runner = runner
        self._show_avatars = show_avatars
        self._context = GitHubContext()
        self._loaded = False
        self._menus: list[QMenu] = []

        column = QVBoxLayout(self)
        column.setContentsMargins(8, 8, 8, 8)
        column.setSpacing(6)

        self._toolbar = QHBoxLayout()
        self._toolbar.setSpacing(6)
        column.addLayout(self._toolbar)

        split = QSplitter(Qt.Orientation.Vertical, self)
        self._list = QListWidget(split)
        self._list.currentItemChanged.connect(lambda *_args: self._on_selected())
        self._list.itemDoubleClicked.connect(lambda *_args: self.open_selected())
        split.addWidget(self._list)

        self._detail = QTextBrowser(split)
        self._detail.setOpenExternalLinks(False)
        self._detail.setOpenLinks(False)
        self._detail.anchorClicked.connect(lambda url: self.open_url_requested.emit(url.toString()))
        split.addWidget(self._detail)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 5)
        column.addWidget(split, 1)

        # A wrapping layout, because eight buttons in a panel the user can make
        # narrow would otherwise be clipped off the right edge.
        self._actions = FlowLayout(spacing=6)
        column.addLayout(self._actions)

    # -------------------------------------------------------------- scaffolding

    def set_show_avatars(self, enabled: bool) -> None:
        """
        Turns author pictures on or off.

        Args:
            enabled: Whether to show them.

        Returns:
            None
        """

        self._show_avatars = enabled

    def set_context(self, context: GitHubContext) -> None:
        """
        Points the tab at a repository.

        The data is not fetched here. A tab the user never opens should cost no
        requests, so loading waits until the tab is shown.

        Args:
            context: The repository and what may be done with it.

        Returns:
            None
        """

        self._context = context
        self._loaded = False
        self._list.clear()
        self._detail.clear()
        self._update_actions()

    def ensure_loaded(self) -> None:
        """
        Fetches the list the first time the tab becomes visible.

        Returns:
            None
        """

        if self._loaded or not self._context.ok:
            return
        self.reload()

    def reload(self) -> None:
        """
        Fetches the list again.

        Returns:
            None
        """

        if not self._context.ok:
            return
        self._loaded = True
        self._load()

    def _load(self) -> None:
        """
        Performs the fetch. Implemented per tab.

        Returns:
            None
        """

        raise NotImplementedError

    def _on_selected(self) -> None:
        """
        Reacts to a new selection. Implemented per tab.

        Returns:
            None
        """

        self._update_actions()

    def _update_actions(self) -> None:
        """
        Enables and disables the action buttons for the current state.

        Returns:
            None
        """

    def open_selected(self) -> None:
        """
        Opens the selected item in the browser.

        Returns:
            None
        """

        payload = self.selected()
        url = getattr(payload, "html_url", "")
        if isinstance(url, str) and url:
            self.open_url_requested.emit(url)

    def selected(self) -> Any:
        """
        Returns the object behind the selected row.

        Returns:
            Any: The item, or None when nothing is selected.
        """

        item = self._list.currentItem()
        return item.data(_ROLE_PAYLOAD) if item is not None else None

    # ------------------------------------------------------------------ helpers

    def _button(self, label_key: str, handler: Callable[[], None]) -> QPushButton:
        """
        Adds a button to the action row.

        Args:
            label_key: Translation key for the label.
            handler: What to call when it is pressed.

        Returns:
            QPushButton: The button.
        """

        button = QPushButton(i18n.t(label_key), self)
        button.clicked.connect(lambda: handler())
        self._actions.addWidget(button)
        return button

    def _menu_button(self, entries: list[tuple[str, Callable[[], None]]]) -> QToolButton:
        """
        Adds a menu holding the actions that do not fit in the row.

        Args:
            entries: ``(translation key, handler)`` per entry.

        Returns:
            QToolButton: The button, with its menu already filled.
        """

        button = QToolButton(self)
        button.setText(i18n.t("github.more_actions"))
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(button)
        for label_key, handler in entries:
            action = menu.addAction(i18n.t(label_key))
            action.triggered.connect(lambda _checked=False, call=handler: call())
        # Qt does not own the menu through setMenu, so it has to stay referenced
        # here or it is collected and the button opens nothing.
        button.setMenu(menu)
        self._menus.append(menu)
        self._actions.addWidget(button)
        return button

    def _run(self, work: Callable[[], Any], done: Callable[[Any], None]) -> None:
        """
        Queues an API call.

        Args:
            work: The call, running on a worker thread.
            done: What to do with the result, on the UI thread.

        Returns:
            None
        """

        self.working.emit(True)

        def deliver(outcome: Any) -> None:
            self.working.emit(False)
            done(outcome)

        self._runner.submit(work, deliver)

    def _warm_avatars(self, result: ApiResult) -> None:
        """
        Downloads the author pictures a list will need.

        Runs on the worker thread, which is the point: the pictures are fetched
        over the network, and doing that from the UI thread would stall the
        window for one round trip per author.

        Args:
            result: The list result whose items carry an ``avatar_url``.

        Returns:
            None
        """

        if not self._show_avatars or not result.ok:
            return
        seen: set[str] = set()
        for entry in result.payload or []:
            url = getattr(entry, "avatar_url", "")
            if isinstance(url, str) and url and url not in seen:
                seen.add(url)
                avatars.fetch(url)

    def _after_write(self, outcome: Any, reload_list: bool = True) -> bool:
        """
        Handles the result of a write.

        Args:
            outcome: What the call returned or raised.
            reload_list: Whether to fetch the list again on success.

        Returns:
            bool: True when the write succeeded.
        """

        if isinstance(outcome, Exception) or not getattr(outcome, "ok", False):
            self.failed.emit(outcome)
            return False
        self.changed.emit()
        if reload_list:
            self.reload()
        return True

    def _fill(self, items: list[Any], label: Callable[[Any], ItemRow], key: Callable[[Any], int]) -> None:
        """
        Rebuilds the list.

        Args:
            items: Objects to show.
            label: Builds the row widget for one object.
            key: Returns the object's identifier, used to restore the selection.

        Returns:
            None
        """

        previous = self.selected()
        previous_key = key(previous) if previous is not None else None

        self._list.clear()
        if not items:
            placeholder = QListWidgetItem(i18n.t("github.list_empty"), self._list)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self._detail.clear()
            self._update_actions()
            return

        restore = -1
        for index, entry in enumerate(items):
            item = QListWidgetItem(self._list)
            item.setData(_ROLE_ID, key(entry))
            item.setData(_ROLE_PAYLOAD, entry)
            row = label(entry)
            item.setSizeHint(row.sizeHint())
            self._list.setItemWidget(item, row)
            if previous_key is not None and key(entry) == previous_key:
                restore = index
        self._list.setCurrentRow(restore if restore >= 0 else 0)


class PullRequestsTab(RemoteTab):
    """
    Pull requests: read them, review them, merge them.
    """

    checkout_branch_requested = Signal(str)

    def __init__(
        self,
        client: GitHubClient,
        runner: ApiRunner,
        show_avatars: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            client: The API client.
            runner: Runner performing the calls.
            show_avatars: Whether author pictures are shown.
            parent: Parent widget.
        """

        super().__init__(client, runner, show_avatars, parent)
        self._branches: list[str] = []
        self._detail_for: int | None = None

        self._state = QComboBox(self)
        self._state.addItem(i18n.t("github.state_open"), pull_api.STATE_OPEN)
        self._state.addItem(i18n.t("github.state_closed"), pull_api.STATE_CLOSED)
        self._state.addItem(i18n.t("github.state_all"), pull_api.STATE_ALL)
        self._state.currentIndexChanged.connect(lambda *_args: self.reload())
        self._toolbar.addWidget(QLabel(i18n.t("github.filter_state"), self))
        self._toolbar.addWidget(self._state)
        self._toolbar.addStretch(1)

        self._new = self._button("github.pr_new", self._on_new)
        self._edit = self._button("github.action_edit", self._on_edit)
        self._comment = self._button("github.action_comment", self._on_comment)
        self._review = self._button("github.review_title", self._on_review)
        self._merge = self._button("github.merge_title", self._on_merge)
        self._checkout = self._button("github.pr_checkout", self._on_checkout)
        self._open = self._button("github.open_in_browser", self.open_selected)
        self._more = self._menu_button(
            [
                ("github.action_close", self._on_close),
                ("github.action_reopen", self._on_reopen),
                ("github.pr_ready", self._on_ready),
                ("github.pr_to_draft", self._on_to_draft),
                ("github.pr_request_review", self._on_request_review),
                ("github.comments_manage", self._on_manage_comments),
            ]
        )

    def set_context(self, context: GitHubContext) -> None:
        """
        Points the tab at a repository and forgets the cached branch list.

        Args:
            context: The repository.

        Returns:
            None
        """

        self._branches = []
        self._detail_for = None
        super().set_context(context)

    def _load(self) -> None:
        """
        Fetches the pull request list and the branch names a new one could use.

        Returns:
            None
        """

        context = self._context
        state = self._state.currentData()

        def work() -> tuple[ApiResult, ApiResult]:
            pulls = self._client.list_pull_requests(
                context.owner, context.repo, state=state, limit=LIST_LIMIT
            )
            branches = self._client.branches(
                context.owner, context.repo, default_branch=context.default_branch
            )
            self._warm_avatars(pulls)
            return pulls, branches

        self._run(work, self._on_loaded)

    def _on_loaded(self, outcome: Any) -> None:
        """
        Fills the list from the fetch.

        Args:
            outcome: The two results, or an exception.

        Returns:
            None
        """

        if isinstance(outcome, Exception):
            self.failed.emit(outcome)
            return
        pulls, branches = outcome
        if not pulls.ok:
            self.failed.emit(pulls)
            return
        if branches.ok:
            self._branches = [branch.name for branch in branches.payload]

        self._fill(list(pulls.payload or []), self._row_for, lambda pull: pull.number)

    def _row_for(self, pull: PullRequest) -> ItemRow:
        """
        Builds the row widget for one pull request.

        Args:
            pull: The pull request.

        Returns:
            ItemRow: The row.
        """

        marks = []
        if pull.draft:
            marks.append(i18n.t("github.badge_draft"))
        if pull.state == "closed":
            marks.append(
                i18n.t("github.badge_merged") if pull.merged else i18n.t("github.badge_closed")
            )
        subtitle = " · ".join(
            part
            for part in (
                i18n.t("github.pr_number", number=pull.number),
                i18n.t("github.pr_by", author=pull.author) if pull.author else "",
                i18n.t("github.pr_into", branch=pull.base_branch) if pull.base_branch else "",
                *marks,
            )
            if part
        )
        return ItemRow(
            pull.title, subtitle, pull.check_state, pull.avatar_url, self._show_avatars, self._list
        )

    def _on_selected(self) -> None:
        """
        Loads the detail for the newly selected pull request.

        Returns:
            None
        """

        super()._on_selected()
        pull = self.selected()
        if not isinstance(pull, PullRequest):
            self._detail.clear()
            return

        self._detail.setMarkdown(self._summary(pull, None, [], []))
        self._detail_for = pull.number
        context = self._context
        number = pull.number

        def work() -> tuple[ApiResult, ApiResult, ApiResult, ApiResult, ApiResult]:
            full = self._client.pull_request(context.owner, context.repo, number)
            comments = self._client.comments(
                context.owner, context.repo, number, viewer_login=context.viewer_login
            )
            reviews = self._client.reviews(context.owner, context.repo, number)
            commits = self._client.commits(context.owner, context.repo, number)
            files = self._client.files(context.owner, context.repo, number)
            return full, comments, reviews, commits, files

        self._run(work, lambda outcome: self._on_detail(number, outcome))

    def _on_detail(self, number: int, outcome: Any) -> None:
        """
        Renders the detail once it arrives.

        Args:
            number: Pull request the detail belongs to.
            outcome: The three results, or an exception.

        Returns:
            None
        """

        # The user may have moved on while this was in flight.
        if self._detail_for != number or isinstance(outcome, Exception):
            return
        full, comments, reviews, commits, files = outcome
        if not full.ok:
            return
        pull = full.payload
        item = self._list.currentItem()
        if item is not None:
            item.setData(_ROLE_PAYLOAD, pull)
        self._detail.setMarkdown(
            self._summary(
                pull,
                pull,
                list(comments.payload or []) if comments.ok else [],
                list(reviews.payload or []) if reviews.ok else [],
                commits.items if commits.ok else [],
                list(files.payload or []) if files.ok else [],
            )
        )
        self._update_actions()

    def _summary(
        self,
        pull: PullRequest,
        full: PullRequest | None,
        comments: list[Comment],
        reviews: list[Review],
        commits: list[Any] | None = None,
        files: list[Any] | None = None,
    ) -> str:
        """
        Renders one pull request as Markdown.

        Args:
            pull: The pull request from the list.
            full: The detailed version, None while it is still loading.
            comments: Its comments.
            reviews: Its reviews.
            commits: Its commits, as the API returned them.
            files: The files it touches.

        Returns:
            str: The document.
        """

        source = full or pull
        lines = [f"# {source.title}", ""]
        meta = [
            i18n.t("github.pr_number", number=source.number),
            i18n.t("github.pr_by", author=source.author) if source.author else "",
            f"`{source.head_branch}` → `{source.base_branch}`",
            _timestamp(source.updated_at),
        ]
        lines.append(" · ".join(part for part in meta if part))
        if source.labels:
            lines.append("")
            lines.append(f"**{i18n.t('github.field_labels')}:** " + ", ".join(source.labels))
        if source.assignees:
            lines.append(f"**{i18n.t('github.field_assignees')}:** " + ", ".join(source.assignees))
        if full is not None:
            lines.append("")
            lines.append(f"**{i18n.t('github.mergeability')}:** {self._mergeability(full)}")
        lines.append("")
        lines.append(source.body.strip() or f"*{i18n.t('github.no_description')}*")

        if files:
            added = sum(entry.additions for entry in files)
            removed = sum(entry.deletions for entry in files)
            lines.append("")
            lines.append(
                f"### {i18n.t('github.files_title')} ({len(files)}, +{added} \u2212{removed})"
            )
            for entry in files[:40]:
                lines.append(f"- `{entry.filename}` +{entry.additions} \u2212{entry.deletions}")
            if len(files) > 40:
                lines.append(f"- \u2026 {len(files) - 40}")

        if commits:
            lines.append("")
            lines.append(f"### {i18n.t('github.commits_title')} ({len(commits)})")
            for entry in commits[:40]:
                if not isinstance(entry, dict):
                    continue
                sha = str(entry.get("sha", ""))[:8]
                message = ""
                inner = entry.get("commit")
                if isinstance(inner, dict):
                    message = str(inner.get("message", "")).splitlines()[0] if inner.get("message") else ""
                lines.append(f"- `{sha}` {message}")

        if reviews:
            lines.append("")
            lines.append(f"### {i18n.t('github.reviews_title')}")
            for review in reviews:
                label = _REVIEW_STATE_KEYS.get(review.state, "")
                lines.append(f"- **{review.author}**: {i18n.t(label) if label else review.state}")
                if review.body.strip():
                    lines.append(f"  {review.body.strip()}")

        block = _comment_section(comments)
        if block:
            lines.append("")
            lines.append(block)
        return "\n".join(lines)

    @staticmethod
    def _mergeability(pull: PullRequest) -> str:
        """
        Puts the merge verdict into one sentence.

        Args:
            pull: The detailed pull request.

        Returns:
            str: What GitHub says about merging it.
        """

        if pull.state == "closed":
            return i18n.t("github.badge_merged" if pull.merged else "github.badge_closed")
        if pull.mergeable is None:
            return i18n.t("github.mergeable_unknown")
        if pull.mergeable:
            return i18n.t("github.mergeable_yes")
        return i18n.t("github.mergeable_no", reason=pull.mergeable_state or "?")

    def _update_actions(self) -> None:
        """
        Enables the actions that apply to the selection.

        Returns:
            None
        """

        pull = self.selected()
        has = isinstance(pull, PullRequest)
        writable = self._context.can_push
        open_pull = has and pull.state == "open"

        self._new.setEnabled(writable and bool(self._context.ok))
        self._edit.setEnabled(has and writable)
        self._comment.setEnabled(has and writable)
        self._review.setEnabled(open_pull and writable)
        self._merge.setEnabled(open_pull and writable and not pull.draft)
        self._checkout.setEnabled(has and bool(pull.head_branch))
        self._open.setEnabled(has)
        self._more.setEnabled(has)

        if has and pull.draft:
            self._merge.setToolTip(i18n.t("github.merge_draft_hint"))
        elif not writable:
            self._merge.setToolTip(i18n.t("github.read_only_hint"))
        else:
            self._merge.setToolTip("")

    # ------------------------------------------------------------------ actions

    def _on_checkout(self) -> None:
        """
        Asks for the selected pull request's branch to be checked out.

        Returns:
            None
        """

        pull = self.selected()
        if isinstance(pull, PullRequest) and pull.head_branch:
            self.checkout_branch_requested.emit(pull.head_branch)

    def _on_new(self) -> None:
        """
        Opens a new pull request.

        Returns:
            None
        """

        context = self._context
        branches = self._branches or [context.local_branch, context.default_branch]
        start = PullRequestDraft(
            head=context.local_branch or "",
            base=context.default_branch or "",
        )
        dialog = PullRequestEditorDialog([name for name in branches if name], start, False, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        draft = dialog.draft()
        self._run(
            lambda: self._client.create_pull_request(
                context.owner,
                context.repo,
                draft.title,
                head=draft.head,
                base=draft.base,
                body=draft.body,
                draft=draft.draft,
            ),
            self._after_write,
        )

    def _on_edit(self) -> None:
        """
        Changes the selected pull request's title, text or target branch.

        Returns:
            None
        """

        pull = self.selected()
        if not isinstance(pull, PullRequest):
            return
        context = self._context
        start = PullRequestDraft(
            title=pull.title, body=pull.body, head=pull.head_branch, base=pull.base_branch
        )
        dialog = PullRequestEditorDialog(self._branches, start, True, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        draft = dialog.draft()
        self._run(
            lambda: self._client.update_pull_request(
                context.owner,
                context.repo,
                pull.number,
                title=draft.title,
                body=draft.body,
                base=draft.base,
            ),
            self._after_write,
        )

    def _on_comment(self) -> None:
        """
        Writes a comment on the selected pull request.

        Returns:
            None
        """

        pull = self.selected()
        if not isinstance(pull, PullRequest):
            return
        dialog = CommentDialog(parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        text = dialog.text()
        context = self._context
        self._run(
            lambda: self._client.create_comment(context.owner, context.repo, pull.number, text),
            lambda outcome: self._after_comment(outcome),
        )

    def _after_comment(self, outcome: Any) -> None:
        """
        Refreshes the detail after a comment was written or changed.

        Args:
            outcome: What the call returned.

        Returns:
            None
        """

        if self._after_write(outcome, reload_list=False):
            self._on_selected()

    def _on_manage_comments(self) -> None:
        """
        Opens the comment list for editing and deleting.

        Returns:
            None
        """

        pull = self.selected()
        if not isinstance(pull, PullRequest):
            return
        context = self._context
        self._run(
            lambda: self._client.comments(
                context.owner, context.repo, pull.number, viewer_login=context.viewer_login
            ),
            lambda outcome: self._show_comments(outcome, pull.number),
        )

    def _show_comments(self, outcome: Any, number: int) -> None:
        """
        Shows the comment dialog and performs what it asked for.

        Args:
            outcome: The comments result.
            number: Issue or pull request number.

        Returns:
            None
        """

        del number
        if isinstance(outcome, Exception) or not outcome.ok:
            self.failed.emit(outcome)
            return
        comments = list(outcome.payload or [])
        dialog = CommentsDialog(comments, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        context = self._context
        if dialog.edit_requested is not None:
            comment_id = dialog.edit_requested
            editor = CommentDialog(dialog.body_of(comment_id), editing=True, parent=self)
            if editor.exec() != editor.DialogCode.Accepted:
                return
            text = editor.text()
            self._run(
                lambda: self._client.update_comment(context.owner, context.repo, comment_id, text),
                self._after_comment,
            )
        elif dialog.delete_requested is not None:
            comment_id = dialog.delete_requested
            self.confirm_requested.emit(
                i18n.t("github.comment_delete_title"),
                i18n.t("github.comment_delete_question"),
                lambda: self._run(
                    lambda: self._client.delete_comment(context.owner, context.repo, comment_id),
                    self._after_comment,
                ),
            )

    def _on_review(self) -> None:
        """
        Submits a review on the selected pull request.

        Returns:
            None
        """

        pull = self.selected()
        if not isinstance(pull, PullRequest):
            return
        context = self._context
        own = bool(context.viewer_login) and pull.author == context.viewer_login
        dialog = ReviewDialog(own, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        review = dialog.review()
        self._run(
            lambda: self._client.create_review(
                context.owner, context.repo, pull.number, event=review.event, body=review.body
            ),
            self._after_comment,
        )

    def _on_merge(self) -> None:
        """
        Merges the selected pull request.

        Returns:
            None
        """

        pull = self.selected()
        if not isinstance(pull, PullRequest):
            return
        context = self._context
        allowed = {name: name in context.merge_methods for name in ("merge", "squash", "rebase")}
        dialog = MergeDialog(pull.head_branch, allowed, context.delete_branch_on_merge, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        choice = dialog.choice()

        def work() -> ApiResult:
            merged = self._client.merge_pull_request(
                context.owner,
                context.repo,
                pull.number,
                method=choice.method,
                title=choice.title,
                message=choice.message,
                sha=pull.head_sha,
            )
            if merged.ok and choice.delete_branch and pull.head_branch:
                # A failed branch deletion is not a failed merge. The merge is
                # what mattered and it happened; the leftover branch is reported
                # separately rather than presented as the merge having failed.
                self._client.delete_branch(context.owner, context.repo, pull.head_branch)
            return merged

        self._run(work, self._after_write)

    def _on_close(self) -> None:
        """
        Closes the selected pull request without merging.

        Returns:
            None
        """

        pull = self.selected()
        if not isinstance(pull, PullRequest):
            return
        context = self._context
        self._run(
            lambda: self._client.close_pull_request(context.owner, context.repo, pull.number),
            self._after_write,
        )

    def _on_reopen(self) -> None:
        """
        Reopens the selected pull request.

        Returns:
            None
        """

        pull = self.selected()
        if not isinstance(pull, PullRequest):
            return
        context = self._context
        self._run(
            lambda: self._client.reopen_pull_request(context.owner, context.repo, pull.number),
            self._after_write,
        )

    def _on_ready(self) -> None:
        """
        Turns the selected draft into a normal pull request.

        Returns:
            None
        """

        pull = self.selected()
        if not isinstance(pull, PullRequest):
            return
        context = self._context
        self._run(
            lambda: self._client.mark_ready_for_review(context.owner, context.repo, pull.node_id),
            self._after_write,
        )

    def _on_request_review(self) -> None:
        """
        Asks someone for a review on the selected pull request.

        Returns:
            None
        """

        pull = self.selected()
        if not isinstance(pull, PullRequest):
            return
        context = self._context
        self._run(
            lambda: self._client.collaborators(context.owner, context.repo),
            lambda outcome: self._pick_reviewer(pull.number, outcome),
        )

    def _pick_reviewer(self, number: int, outcome: Any) -> None:
        """
        Shows the reviewer picker and sends the request.

        Args:
            number: Pull request number.
            outcome: The list of people who can be asked.

        Returns:
            None
        """

        if isinstance(outcome, Exception) or not outcome.ok:
            self.failed.emit(outcome)
            return
        context = self._context
        # The author is filtered out: GitHub refuses a review request to oneself,
        # and offering it would be offering a certain failure.
        options = [
            (login, login)
            for login in (outcome.payload or [])
            if login != context.viewer_login
        ]
        dialog = PickerDialog(
            i18n.t("github.pr_request_review"), i18n.t("github.reviewer"), options, True, self
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        login = dialog.value()
        self._run(
            lambda: self._client.request_reviewers(context.owner, context.repo, number, [login]),
            self._after_write,
        )

    def _on_to_draft(self) -> None:
        """
        Turns the selected pull request back into a draft.

        Returns:
            None
        """

        pull = self.selected()
        if not isinstance(pull, PullRequest):
            return
        context = self._context
        self._run(
            lambda: self._client.convert_to_draft(context.owner, context.repo, pull.node_id),
            self._after_write,
        )


class IssuesTab(RemoteTab):
    """
    Issues: read them, write them, close them.
    """

    def __init__(
        self,
        client: GitHubClient,
        runner: ApiRunner,
        show_avatars: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            client: The API client.
            runner: Runner performing the calls.
            show_avatars: Whether author pictures are shown.
            parent: Parent widget.
        """

        super().__init__(client, runner, show_avatars, parent)
        self._labels: list[str] = []
        self._assignees: list[str] = []
        self._milestones: list[tuple[int, str]] = []
        self._detail_for: int | None = None

        self._state = QComboBox(self)
        self._state.addItem(i18n.t("github.state_open"), issue_api.STATE_OPEN)
        self._state.addItem(i18n.t("github.state_closed"), issue_api.STATE_CLOSED)
        self._state.addItem(i18n.t("github.state_all"), issue_api.STATE_ALL)
        self._state.currentIndexChanged.connect(lambda *_args: self.reload())
        self._toolbar.addWidget(QLabel(i18n.t("github.filter_state"), self))
        self._toolbar.addWidget(self._state)
        self._toolbar.addStretch(1)

        self._new = self._button("github.issue_new", self._on_new)
        self._edit = self._button("github.action_edit", self._on_edit)
        self._comment = self._button("github.action_comment", self._on_comment)
        self._close = self._button("github.action_close", self._on_close)
        self._open = self._button("github.open_in_browser", self.open_selected)
        self._more = self._menu_button(
            [
                ("github.issue_close_not_planned", self._on_close_not_planned),
                ("github.action_reopen", self._on_reopen),
                ("github.labels_manage", self._on_manage_labels),
                ("github.comments_manage", self._on_manage_comments),
            ]
        )

    def set_context(self, context: GitHubContext) -> None:
        """
        Points the tab at a repository and forgets the cached pickers.

        Args:
            context: The repository.

        Returns:
            None
        """

        self._labels = []
        self._assignees = []
        self._milestones = []
        self._detail_for = None
        super().set_context(context)

    def _load(self) -> None:
        """
        Fetches the issue list plus the labels, assignees and milestones the
        editor offers.

        Returns:
            None
        """

        context = self._context
        state = self._state.currentData()

        def work() -> tuple[ApiResult, ApiResult, ApiResult, ApiResult]:
            found = self._client.list_issues(
                context.owner, context.repo, state=state, limit=LIST_LIMIT
            )
            labels = self._client.labels(context.owner, context.repo)
            assignees = self._client.collaborators(context.owner, context.repo)
            milestones = self._client.milestones(context.owner, context.repo)
            self._warm_avatars(found)
            return found, labels, assignees, milestones

        self._run(work, self._on_loaded)

    def _on_loaded(self, outcome: Any) -> None:
        """
        Fills the list and the pickers from the fetch.

        Args:
            outcome: The four results, or an exception.

        Returns:
            None
        """

        if isinstance(outcome, Exception):
            self.failed.emit(outcome)
            return
        found, labels, assignees, milestones = outcome
        if not found.ok:
            self.failed.emit(found)
            return
        if labels.ok:
            self._labels = [label.name for label in labels.payload]
        if assignees.ok:
            self._assignees = list(assignees.payload or [])
        if milestones.ok:
            self._milestones = [(item.number, item.title) for item in milestones.payload]

        self._fill(list(found.payload or []), self._row_for, lambda issue: issue.number)

    def _row_for(self, issue: Issue) -> ItemRow:
        """
        Builds the row widget for one issue.

        Args:
            issue: The issue.

        Returns:
            ItemRow: The row.
        """

        marks = []
        if issue.state == "closed":
            marks.append(
                i18n.t("github.badge_not_planned")
                if issue.state_reason == issue_api.REASON_NOT_PLANNED
                else i18n.t("github.badge_closed")
            )
        if issue.labels:
            marks.append(", ".join(issue.labels))
        subtitle = " · ".join(
            part
            for part in (
                i18n.t("github.pr_number", number=issue.number),
                i18n.t("github.pr_by", author=issue.author) if issue.author else "",
                *marks,
            )
            if part
        )
        return ItemRow(issue.title, subtitle, None, issue.avatar_url, self._show_avatars, self._list)

    def _on_selected(self) -> None:
        """
        Loads the comments of the newly selected issue.

        Returns:
            None
        """

        super()._on_selected()
        issue = self.selected()
        if not isinstance(issue, Issue):
            self._detail.clear()
            return
        self._detail.setMarkdown(self._summary(issue, []))
        self._detail_for = issue.number

        context = self._context
        number = issue.number
        self._run(
            lambda: self._client.comments(
                context.owner, context.repo, number, viewer_login=context.viewer_login
            ),
            lambda outcome: self._on_comments(number, outcome),
        )

    def _on_comments(self, number: int, outcome: Any) -> None:
        """
        Adds the comments to the detail once they arrive.

        Args:
            number: Issue the comments belong to.
            outcome: The comments result.

        Returns:
            None
        """

        if self._detail_for != number or isinstance(outcome, Exception) or not outcome.ok:
            return
        issue = self.selected()
        if isinstance(issue, Issue) and issue.number == number:
            self._detail.setMarkdown(self._summary(issue, list(outcome.payload or [])))

    def _summary(self, issue: Issue, comments: list[Comment]) -> str:
        """
        Renders one issue as Markdown.

        Args:
            issue: The issue.
            comments: Its comments.

        Returns:
            str: The document.
        """

        lines = [f"# {issue.title}", ""]
        meta = [
            i18n.t("github.pr_number", number=issue.number),
            i18n.t("github.pr_by", author=issue.author) if issue.author else "",
            i18n.t(f"github.state_{issue.state}"),
            _timestamp(issue.updated_at),
        ]
        lines.append(" · ".join(part for part in meta if part))
        if issue.labels:
            lines.append("")
            lines.append(f"**{i18n.t('github.field_labels')}:** " + ", ".join(issue.labels))
        if issue.assignees:
            lines.append(f"**{i18n.t('github.field_assignees')}:** " + ", ".join(issue.assignees))
        if issue.milestone:
            lines.append(f"**{i18n.t('github.field_milestone')}:** {issue.milestone}")
        lines.append("")
        lines.append(issue.body.strip() or f"*{i18n.t('github.no_description')}*")

        block = _comment_section(comments)
        if block:
            lines.append("")
            lines.append(block)
        return "\n".join(lines)

    def _update_actions(self) -> None:
        """
        Enables the actions that apply to the selection.

        Returns:
            None
        """

        issue = self.selected()
        has = isinstance(issue, Issue)
        writable = self._context.can_push
        self._new.setEnabled(writable and self._context.ok)
        self._edit.setEnabled(has and writable)
        self._comment.setEnabled(has and writable)
        self._close.setEnabled(has and writable and issue.state == "open")
        self._open.setEnabled(has)
        self._more.setEnabled(has)

    # ------------------------------------------------------------------ actions

    def _on_new(self) -> None:
        """
        Creates an issue.

        Returns:
            None
        """

        dialog = IssueEditorDialog(self._labels, self._assignees, self._milestones, None, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        draft = dialog.draft()
        context = self._context
        self._run(
            lambda: self._client.create_issue(
                context.owner,
                context.repo,
                draft.title,
                body=draft.body,
                labels=draft.labels,
                assignees=draft.assignees,
                milestone=draft.milestone,
            ),
            self._after_write,
        )

    def _on_edit(self) -> None:
        """
        Changes the selected issue.

        Returns:
            None
        """

        issue = self.selected()
        if not isinstance(issue, Issue):
            return
        milestone_number = next(
            (number for number, title in self._milestones if title == issue.milestone), None
        )
        start = IssueDraft(
            title=issue.title,
            body=issue.body,
            labels=list(issue.labels),
            assignees=list(issue.assignees),
            milestone=milestone_number,
        )
        dialog = IssueEditorDialog(self._labels, self._assignees, self._milestones, start, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        draft = dialog.draft()
        context = self._context
        self._run(
            lambda: self._client.update_issue(
                context.owner,
                context.repo,
                issue.number,
                title=draft.title,
                body=draft.body,
                labels=draft.labels,
                assignees=draft.assignees,
                milestone=draft.milestone,
            ),
            self._after_write,
        )

    def _on_comment(self) -> None:
        """
        Writes a comment on the selected issue.

        Returns:
            None
        """

        issue = self.selected()
        if not isinstance(issue, Issue):
            return
        dialog = CommentDialog(parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        text = dialog.text()
        context = self._context
        self._run(
            lambda: self._client.create_comment(context.owner, context.repo, issue.number, text),
            self._after_comment,
        )

    def _after_comment(self, outcome: Any) -> None:
        """
        Refreshes the detail after a comment changed.

        Args:
            outcome: What the call returned.

        Returns:
            None
        """

        if self._after_write(outcome, reload_list=False):
            self._on_selected()

    def _on_manage_comments(self) -> None:
        """
        Opens the comment list for editing and deleting.

        Returns:
            None
        """

        issue = self.selected()
        if not isinstance(issue, Issue):
            return
        context = self._context
        self._run(
            lambda: self._client.comments(
                context.owner, context.repo, issue.number, viewer_login=context.viewer_login
            ),
            self._show_comments,
        )

    def _show_comments(self, outcome: Any) -> None:
        """
        Shows the comment dialog and performs what it asked for.

        Args:
            outcome: The comments result.

        Returns:
            None
        """

        if isinstance(outcome, Exception) or not outcome.ok:
            self.failed.emit(outcome)
            return
        dialog = CommentsDialog(list(outcome.payload or []), self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        context = self._context
        if dialog.edit_requested is not None:
            comment_id = dialog.edit_requested
            editor = CommentDialog(dialog.body_of(comment_id), editing=True, parent=self)
            if editor.exec() != editor.DialogCode.Accepted:
                return
            text = editor.text()
            self._run(
                lambda: self._client.update_comment(context.owner, context.repo, comment_id, text),
                self._after_comment,
            )
        elif dialog.delete_requested is not None:
            comment_id = dialog.delete_requested
            self.confirm_requested.emit(
                i18n.t("github.comment_delete_title"),
                i18n.t("github.comment_delete_question"),
                lambda: self._run(
                    lambda: self._client.delete_comment(context.owner, context.repo, comment_id),
                    self._after_comment,
                ),
            )

    def _on_close(self) -> None:
        """
        Closes the selected issue as completed.

        Returns:
            None
        """

        self._close_with(issue_api.REASON_COMPLETED)

    def _on_close_not_planned(self) -> None:
        """
        Closes the selected issue as not planned.

        Returns:
            None
        """

        self._close_with(issue_api.REASON_NOT_PLANNED)

    def _close_with(self, reason: str) -> None:
        """
        Closes the selected issue with a reason.

        Args:
            reason: ``completed`` or ``not_planned``.

        Returns:
            None
        """

        issue = self.selected()
        if not isinstance(issue, Issue):
            return
        context = self._context
        self._run(
            lambda: self._client.close_issue(context.owner, context.repo, issue.number, reason),
            self._after_write,
        )

    def _on_reopen(self) -> None:
        """
        Reopens the selected issue.

        Returns:
            None
        """

        issue = self.selected()
        if not isinstance(issue, Issue):
            return
        context = self._context
        self._run(
            lambda: self._client.reopen_issue(context.owner, context.repo, issue.number),
            self._after_write,
        )

    def _on_manage_labels(self) -> None:
        """
        Opens the label and milestone manager.

        Returns:
            None
        """

        context = self._context

        def work() -> tuple[ApiResult, ApiResult]:
            return (
                self._client.labels(context.owner, context.repo),
                self._client.milestones(context.owner, context.repo, state="all"),
            )

        self._run(work, self._show_label_manager)

    def _show_label_manager(self, outcome: Any) -> None:
        """
        Shows the manager and performs whatever it asked for.

        Args:
            outcome: The label and milestone results.

        Returns:
            None
        """

        if isinstance(outcome, Exception):
            self.failed.emit(outcome)
            return
        labels, milestones = outcome
        if not labels.ok:
            self.failed.emit(labels)
            return

        dialog = LabelMilestoneDialog(
            list(labels.payload or []),
            list(milestones.payload or []) if milestones.ok else [],
            self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted or dialog.requested is None:
            return

        action, value = dialog.requested
        context = self._context
        if action == "create_label":
            name, color = value
            self._run(
                lambda: self._client.create_label(context.owner, context.repo, name, color=color),
                self._after_write,
            )
        elif action == "rename_label":
            old_name, new_name, color = value
            self._run(
                lambda: self._client.update_label(
                    context.owner, context.repo, old_name, new_name=new_name, color=color
                ),
                self._after_write,
            )
        elif action == "delete_label":
            self.confirm_requested.emit(
                i18n.t("github.label_delete_title"),
                i18n.t("github.label_delete_question", name=value),
                lambda: self._run(
                    lambda: self._client.delete_label(context.owner, context.repo, value),
                    self._after_write,
                ),
            )
        elif action == "create_milestone":
            self._run(
                lambda: self._client.create_milestone(context.owner, context.repo, value),
                self._after_write,
            )
        elif action == "close_milestone":
            self._run(
                lambda: self._client.close_milestone(context.owner, context.repo, value),
                self._after_write,
            )
        elif action == "delete_milestone":
            self.confirm_requested.emit(
                i18n.t("github.milestone_delete_title"),
                i18n.t("github.milestone_delete_question"),
                lambda: self._run(
                    lambda: self._client.delete_milestone(context.owner, context.repo, value),
                    self._after_write,
                ),
            )


class ReleasesTab(RemoteTab):
    """
    Releases: publish them, change them, attach files to them.
    """

    def __init__(
        self,
        client: GitHubClient,
        runner: ApiRunner,
        show_avatars: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            client: The API client.
            runner: Runner performing the calls.
            show_avatars: Whether author pictures are shown.
            parent: Parent widget.
        """

        super().__init__(client, runner, show_avatars, parent)
        self._branches: list[str] = []
        self._tags: list[str] = []

        self._toolbar.addWidget(QLabel(i18n.t("github.releases"), self))
        self._toolbar.addStretch(1)

        self._new = self._button("github.release_new", self._on_new)
        self._edit = self._button("github.action_edit", self._on_edit)
        self._upload = self._button("github.asset_upload", self._on_upload)
        self._delete = self._button("github.action_delete", self._on_delete)
        self._open = self._button("github.open_in_browser", self.open_selected)
        self._more = self._menu_button(
            [
                ("github.asset_delete", self._on_delete_asset),
                ("github.notes_generate", self._on_generate_notes),
                ("github.tag_delete", self._on_delete_tag),
            ]
        )

    def set_context(self, context: GitHubContext) -> None:
        """
        Points the tab at a repository and forgets the cached refs.

        Args:
            context: The repository.

        Returns:
            None
        """

        self._branches = []
        self._tags = []
        super().set_context(context)

    def _load(self) -> None:
        """
        Fetches releases plus the branches and tags the editor offers.

        Returns:
            None
        """

        context = self._context

        def work() -> tuple[ApiResult, ApiResult, ApiResult]:
            found = self._client.releases(context.owner, context.repo, limit=LIST_LIMIT)
            branches = self._client.branches(
                context.owner, context.repo, default_branch=context.default_branch
            )
            tags = self._client.tags(context.owner, context.repo, limit=LIST_LIMIT)
            return found, branches, tags

        self._run(work, self._on_loaded)

    def _on_loaded(self, outcome: Any) -> None:
        """
        Fills the list from the fetch.

        Args:
            outcome: The three results, or an exception.

        Returns:
            None
        """

        if isinstance(outcome, Exception):
            self.failed.emit(outcome)
            return
        found, branches, tags = outcome
        if not found.ok:
            self.failed.emit(found)
            return
        if branches.ok:
            self._branches = [branch.name for branch in branches.payload]
        if tags.ok:
            self._tags = [tag.name for tag in tags.payload]

        self._fill(list(found.payload or []), self._row_for, lambda release: release.release_id)

    def _row_for(self, release: Release) -> ItemRow:
        """
        Builds the row widget for one release.

        Args:
            release: The release.

        Returns:
            ItemRow: The row.
        """

        marks = []
        if release.draft:
            marks.append(i18n.t("github.badge_draft"))
        if release.prerelease:
            marks.append(i18n.t("github.badge_prerelease"))
        subtitle = " · ".join(
            part
            for part in (
                release.tag_name,
                _timestamp(release.published_at),
                i18n.t("github.assets_count", count=len(release.assets)) if release.assets else "",
                *marks,
            )
            if part
        )
        return ItemRow(
            release.name or release.tag_name, subtitle, None, "", False, self._list
        )

    def _on_selected(self) -> None:
        """
        Renders the selected release.

        Returns:
            None
        """

        super()._on_selected()
        release = self.selected()
        if not isinstance(release, Release):
            self._detail.clear()
            return

        lines = [f"# {release.name or release.tag_name}", ""]
        meta = [release.tag_name, _timestamp(release.published_at)]
        if release.author:
            meta.append(i18n.t("github.pr_by", author=release.author))
        lines.append(" · ".join(part for part in meta if part))
        lines.append("")
        lines.append(release.body.strip() or f"*{i18n.t('github.no_description')}*")
        if release.assets:
            lines.append("")
            lines.append(f"### {i18n.t('github.assets_title')}")
            for asset in release.assets:
                size = max(1, asset.size // 1024)
                lines.append(
                    f"- **{asset.name}** · {size} KB · "
                    + i18n.t("github.asset_downloads", count=asset.download_count)
                )
        self._detail.setMarkdown("\n".join(lines))

    def _update_actions(self) -> None:
        """
        Enables the actions that apply to the selection.

        Returns:
            None
        """

        release = self.selected()
        has = isinstance(release, Release)
        writable = self._context.can_push
        self._new.setEnabled(writable and self._context.ok)
        self._edit.setEnabled(has and writable)
        self._upload.setEnabled(has and writable and bool(release.upload_url))
        self._delete.setEnabled(has and writable)
        self._open.setEnabled(has and bool(release.html_url))
        # The menu also holds tag deletion and note generation, which apply
        # without an asset, so it stays reachable for any writable selection.
        self._more.setEnabled(writable and self._context.ok)

    # ------------------------------------------------------------------ actions

    def _on_new(self) -> None:
        """
        Publishes a release.

        Returns:
            None
        """

        start = ReleaseDraft(target=self._context.default_branch)
        dialog = ReleaseEditorDialog(self._branches, self._tags, start, False, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        draft = dialog.draft()
        context = self._context
        self._run(
            lambda: self._client.create_release(
                context.owner,
                context.repo,
                draft.tag_name,
                name=draft.name,
                body=draft.body,
                target_commitish=draft.target,
                draft=draft.draft,
                prerelease=draft.prerelease,
                generate_notes=draft.generate_notes,
            ),
            self._after_write,
        )

    def _on_edit(self) -> None:
        """
        Changes the selected release.

        Returns:
            None
        """

        release = self.selected()
        if not isinstance(release, Release):
            return
        start = ReleaseDraft(
            tag_name=release.tag_name,
            target=release.target_commitish,
            name=release.name,
            body=release.body,
            draft=release.draft,
            prerelease=release.prerelease,
        )
        dialog = ReleaseEditorDialog(self._branches, self._tags, start, True, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        draft = dialog.draft()
        context = self._context
        self._run(
            lambda: self._client.update_release(
                context.owner,
                context.repo,
                release.release_id,
                tag_name=draft.tag_name,
                name=draft.name,
                body=draft.body,
                draft=draft.draft,
                prerelease=draft.prerelease,
            ),
            self._after_write,
        )

    def _on_upload(self) -> None:
        """
        Attaches a file to the selected release.

        Returns:
            None
        """

        release = self.selected()
        if not isinstance(release, Release):
            return
        chosen, _filter = QFileDialog.getOpenFileName(self, i18n.t("github.asset_upload"))
        if not chosen:
            return
        self._run(lambda: self._client.upload_asset(release, Path(chosen)), self._after_write)

    def _on_delete(self) -> None:
        """
        Asks the panel to confirm deleting the selected release.

        Returns:
            None
        """

        release = self.selected()
        if not isinstance(release, Release):
            return
        context = self._context
        self.confirm_requested.emit(
            i18n.t("github.release_delete_title"),
            i18n.t("github.release_delete_question", tag=release.tag_name),
            lambda: self._run(
                lambda: self._client.delete_release(
                    context.owner, context.repo, release.release_id
                ),
                self._after_write,
            ),
        )

    def _on_generate_notes(self) -> None:
        """
        Fetches GitHub's changelog for the selected release and opens the editor
        with it already filled in.

        Returns:
            None
        """

        release = self.selected()
        if not isinstance(release, Release):
            return
        context = self._context
        self._run(
            lambda: self._client.generate_notes(
                context.owner, context.repo, release.tag_name, target=context.default_branch
            ),
            lambda outcome: self._edit_with_notes(release, outcome),
        )

    def _edit_with_notes(self, release: Release, outcome: Any) -> None:
        """
        Opens the release editor with the generated notes in place.

        Args:
            release: The release being edited.
            outcome: What the note generation returned.

        Returns:
            None
        """

        if isinstance(outcome, Exception) or not outcome.ok:
            self.failed.emit(outcome)
            return
        generated = outcome.data.get("body")
        start = ReleaseDraft(
            tag_name=release.tag_name,
            target=release.target_commitish,
            name=release.name,
            body=generated if isinstance(generated, str) else release.body,
            draft=release.draft,
            prerelease=release.prerelease,
        )
        dialog = ReleaseEditorDialog(self._branches, self._tags, start, True, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        draft = dialog.draft()
        context = self._context
        self._run(
            lambda: self._client.update_release(
                context.owner,
                context.repo,
                release.release_id,
                tag_name=draft.tag_name,
                name=draft.name,
                body=draft.body,
                draft=draft.draft,
                prerelease=draft.prerelease,
            ),
            self._after_write,
        )

    def _on_delete_tag(self) -> None:
        """
        Removes a tag from the server.

        Returns:
            None
        """

        if not self._tags:
            return
        dialog = PickerDialog(
            i18n.t("github.tag_delete"),
            i18n.t("github.field_tag"),
            [(name, name) for name in self._tags],
            False,
            self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        tag = dialog.value()
        context = self._context
        self.confirm_requested.emit(
            i18n.t("github.tag_delete"),
            i18n.t("github.tag_delete_question", tag=tag),
            lambda: self._run(
                lambda: self._client.delete_tag(context.owner, context.repo, tag),
                self._after_write,
            ),
        )

    def _on_delete_asset(self) -> None:
        """
        Removes one file from the selected release.

        Returns:
            None
        """

        release = self.selected()
        if not isinstance(release, Release) or not release.assets:
            return
        names = [asset.name for asset in release.assets]
        chosen, ok = QInputDialog.getItem(
            self, i18n.t("github.asset_delete"), i18n.t("github.asset_pick"), names, 0, False
        )
        if not ok or not chosen:
            return
        asset = next(item for item in release.assets if item.name == chosen)
        context = self._context
        self.confirm_requested.emit(
            i18n.t("github.asset_delete"),
            i18n.t("github.asset_delete_question", name=asset.name),
            lambda: self._run(
                lambda: self._client.delete_asset(context.owner, context.repo, asset.asset_id),
                self._after_write,
            ),
        )


class ActionsTab(RemoteTab):
    """
    Workflow runs: see them, start them again, stop them.
    """

    def __init__(
        self,
        client: GitHubClient,
        runner: ApiRunner,
        show_avatars: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            client: The API client.
            runner: Runner performing the calls.
            show_avatars: Whether author pictures are shown.
            parent: Parent widget.
        """

        super().__init__(client, runner, show_avatars, parent)

        self._scope = QComboBox(self)
        self._scope.addItem(i18n.t("github.runs_all_branches"), "")
        self._scope.addItem(i18n.t("github.runs_this_branch"), "branch")
        self._scope.currentIndexChanged.connect(lambda *_args: self.reload())
        self._toolbar.addWidget(QLabel(i18n.t("github.filter_runs"), self))
        self._toolbar.addWidget(self._scope)
        self._toolbar.addStretch(1)

        self._rerun = self._button("github.run_rerun", lambda: self._on_rerun(False))
        self._rerun_failed = self._button("github.run_rerun_failed", lambda: self._on_rerun(True))
        self._cancel = self._button("github.run_cancel", self._on_cancel)
        self._open = self._button("github.open_in_browser", self.open_selected)
        self._more = self._menu_button(
            [
                ("github.workflow_dispatch", self._on_dispatch),
                ("github.run_delete", self._on_delete),
            ]
        )

    def _load(self) -> None:
        """
        Fetches the run list.

        Returns:
            None
        """

        context = self._context
        branch = context.local_branch if self._scope.currentData() == "branch" else ""
        self._run(
            lambda: self._client.workflow_runs(context.owner, context.repo, branch=branch),
            self._on_loaded,
        )

    def _on_loaded(self, outcome: Any) -> None:
        """
        Fills the list from the fetch.

        Args:
            outcome: The result, or an exception.

        Returns:
            None
        """

        if isinstance(outcome, Exception) or not outcome.ok:
            self.failed.emit(outcome)
            return
        self._fill(list(outcome.payload or []), self._row_for, lambda run: run.run_id)

    def _row_for(self, run: WorkflowRun) -> ItemRow:
        """
        Builds the row widget for one run.

        Args:
            run: The workflow run.

        Returns:
            ItemRow: The row.
        """

        subtitle = " · ".join(
            part
            for part in (
                run.name,
                f"#{run.run_number}" if run.run_number else "",
                run.branch,
                run.event,
                _timestamp(run.created_at),
            )
            if part
        )
        title = run.display_title or run.name or f"#{run.run_number}"
        return ItemRow(title, subtitle, run.check_state, "", False, self._list)

    def _on_selected(self) -> None:
        """
        Renders the selected run and loads its jobs.

        Returns:
            None
        """

        super()._on_selected()
        run = self.selected()
        if not isinstance(run, WorkflowRun):
            self._detail.clear()
            return
        self._detail.setMarkdown(self._summary(run, []))

        context = self._context
        run_id = run.run_id
        self._run(
            lambda: self._client.run_jobs(context.owner, context.repo, run_id),
            lambda outcome: self._on_jobs(run_id, outcome),
        )

    def _on_jobs(self, run_id: int, outcome: Any) -> None:
        """
        Adds the job list to the detail.

        Args:
            run_id: Run the jobs belong to.
            outcome: The jobs result.

        Returns:
            None
        """

        run = self.selected()
        if not isinstance(run, WorkflowRun) or run.run_id != run_id:
            return
        if isinstance(outcome, Exception) or not outcome.ok:
            return
        self._detail.setMarkdown(self._summary(run, outcome.items))

    @staticmethod
    def _summary(run: WorkflowRun, jobs: list[Any]) -> str:
        """
        Renders one run as Markdown.

        Args:
            run: The run.
            jobs: Its jobs, as the API returned them.

        Returns:
            str: The document.
        """

        lines = [f"# {run.display_title or run.name}", ""]
        meta = [
            run.name,
            f"#{run.run_number}" if run.run_number else "",
            run.branch,
            run.event,
            run.actor,
            _timestamp(run.created_at),
        ]
        lines.append(" · ".join(part for part in meta if part))
        lines.append("")
        state = run.conclusion or run.status
        lines.append(f"**{i18n.t('github.run_state')}:** {state}")
        if run.head_sha:
            lines.append(f"**{i18n.t('github.run_commit')}:** `{run.head_sha[:10]}`")
        if jobs:
            lines.append("")
            lines.append(f"### {i18n.t('github.jobs_title')}")
            for job in jobs:
                if not isinstance(job, dict):
                    continue
                name = job.get("name", "?")
                verdict = job.get("conclusion") or job.get("status") or "?"
                lines.append(f"- **{name}**: {verdict}")
        return "\n".join(lines)

    def _update_actions(self) -> None:
        """
        Enables the actions that apply to the selection.

        Returns:
            None
        """

        run = self.selected()
        has = isinstance(run, WorkflowRun)
        writable = self._context.can_push
        finished = has and not run.can_cancel
        self._rerun.setEnabled(has and writable and finished)
        self._rerun_failed.setEnabled(has and writable and finished)
        self._cancel.setEnabled(has and writable and run.can_cancel)
        self._open.setEnabled(has and bool(run.html_url))
        # Starting a workflow needs no selected run, so the menu stays reachable
        # even when the list is empty, which is exactly when it is most useful.
        self._more.setEnabled(writable and self._context.ok)

    # ------------------------------------------------------------------ actions

    def _on_rerun(self, failed_only: bool) -> None:
        """
        Starts the selected run again.

        Args:
            failed_only: Whether to repeat only the failed jobs.

        Returns:
            None
        """

        run = self.selected()
        if not isinstance(run, WorkflowRun):
            return
        context = self._context
        self._run(
            lambda: self._client.rerun(
                context.owner, context.repo, run.run_id, failed_only=failed_only
            ),
            self._after_write,
        )

    def _on_cancel(self) -> None:
        """
        Stops the selected run.

        Returns:
            None
        """

        run = self.selected()
        if not isinstance(run, WorkflowRun):
            return
        context = self._context
        self._run(
            lambda: self._client.cancel(context.owner, context.repo, run.run_id),
            self._after_write,
        )

    def _on_dispatch(self) -> None:
        """
        Starts a workflow by hand.

        Returns:
            None
        """

        context = self._context
        self._run(
            lambda: self._client.workflows(context.owner, context.repo),
            self._pick_workflow,
        )

    def _pick_workflow(self, outcome: Any) -> None:
        """
        Shows the workflow picker and dispatches the chosen one.

        Args:
            outcome: The workflow list result.

        Returns:
            None
        """

        if isinstance(outcome, Exception) or not outcome.ok:
            self.failed.emit(outcome)
            return
        options = [
            (str(entry.get("name") or entry.get("path") or entry.get("id")), entry.get("id"))
            for entry in outcome.items
            if isinstance(entry, dict) and entry.get("id") is not None
        ]
        if not options:
            self.failed.emit(
                ApiResult(ok=False, error_key="github.no_workflows", detail="")
            )
            return
        dialog = PickerDialog(
            i18n.t("github.workflow_dispatch"), i18n.t("github.workflow"), options, False, self
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        workflow_id = dialog.value()
        context = self._context
        branch = context.local_branch or context.default_branch
        self._run(
            lambda: self._client.dispatch_workflow(
                context.owner, context.repo, workflow_id, branch
            ),
            self._after_write,
        )

    def _on_delete(self) -> None:
        """
        Removes the selected run from the history.

        Returns:
            None
        """

        run = self.selected()
        if not isinstance(run, WorkflowRun):
            return
        context = self._context
        self._run(
            lambda: self._client.delete_run(context.owner, context.repo, run.run_id),
            self._after_write,
        )
