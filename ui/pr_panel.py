"""
The GitHub panel: pull requests, issues and build status.

Every state this panel can be in gets said out loud rather than shown as an empty
list: no token, not a GitHub project, rate limited, or genuinely nothing open.
An empty panel with no explanation is the thing that makes people think a program
is broken.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import i18n
from config.theme import get_theme_colors
from github_api.client import ERROR_NO_TOKEN, ERROR_RATE_LIMITED, GitHubClient
from github_api.models import (
    CHECK_COLOR_TOKENS,
    CHECK_LABEL_KEYS,
    Issue,
    PullRequest,
    RepositorySnapshot,
)
from services import avatars
from ui.widgets import InlineMessage, SectionHeader, StatusDot

_ROLE_URL = int(Qt.ItemDataRole.UserRole)
_ROLE_BRANCH = int(Qt.ItemDataRole.UserRole) + 1

AVATAR_SIZE = 20


class SnapshotWorker(QObject):
    """
    Fetches one repository's GitHub data off the UI thread.

    Attributes:
        finished: Emitted with the ``RepositorySnapshot``.
    """

    finished = Signal(object)

    def __init__(self, client: GitHubClient, owner: str, repo: str, ref: str) -> None:
        """
        Args:
            client: Client to use.
            owner: Repository owner.
            repo: Repository name.
            ref: Commit or branch whose check state is wanted.
        """

        super().__init__()
        self._client = client
        self._owner = owner
        self._repo = repo
        self._ref = ref

    def run(self) -> None:
        """
        Performs the fetch.

        Returns:
            None
        """

        try:
            snapshot = self._client.snapshot(self._owner, self._repo, self._ref)
        except Exception:  # noqa: BLE001 - a cosmetic panel must never crash the app
            snapshot = RepositorySnapshot(owner=self._owner, repo=self._repo, error_key="error.git_failed")
        self.finished.emit(snapshot)


class _ItemRow(QWidget):
    """
    One pull request or issue row: a check dot, a title and an avatar.
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


class PullRequestPanel(QWidget):
    """
    Shows pull requests and issues for the selected repository.

    Attributes:
        checkout_branch_requested: Emitted with a branch name.
        open_url_requested: Emitted with a web address.
        refresh_requested: Emitted when the user asks for fresh data.
    """

    checkout_branch_requested = Signal(str)
    open_url_requested = Signal(str)
    refresh_requested = Signal()

    def __init__(self, show_avatars: bool = True, parent: QWidget | None = None) -> None:
        """
        Args:
            show_avatars: Whether author pictures are shown.
            parent: Parent widget.
        """

        super().__init__(parent)
        self._show_avatars = show_avatars

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = SectionHeader(i18n.t("github.pull_requests"), self)
        self._refresh_button = QPushButton(i18n.t("action.refresh"), header)
        self._refresh_button.clicked.connect(self.refresh_requested.emit)
        header.add_widget(self._refresh_button)
        layout.addWidget(header)

        self._stack = QStackedWidget(self)

        self._notice_holder = QWidget(self)
        notice_layout = QVBoxLayout(self._notice_holder)
        notice_layout.setContentsMargins(16, 16, 16, 16)
        self._notice = InlineMessage("", "", "info", self._notice_holder)
        notice_layout.addWidget(self._notice)
        notice_layout.addStretch(1)
        self._stack.addWidget(self._notice_holder)

        self._tabs = QTabWidget(self)
        self._pulls = QListWidget(self._tabs)
        self._pulls.itemDoubleClicked.connect(self._open_item)
        self._issues = QListWidget(self._tabs)
        self._issues.itemDoubleClicked.connect(self._open_item)
        self._tabs.addTab(self._pulls, i18n.t("github.pull_requests"))
        self._tabs.addTab(self._issues, i18n.t("github.issues"))
        self._stack.addWidget(self._tabs)

        layout.addWidget(self._stack, 1)

        actions = QWidget(self)
        action_row = QHBoxLayout(actions)
        action_row.setContentsMargins(10, 6, 10, 6)
        action_row.setSpacing(8)
        self._checkout_button = QPushButton(i18n.t("github.pr_checkout"), actions)
        self._checkout_button.clicked.connect(self._checkout_selected)
        self._open_button = QPushButton(i18n.t("github.open_in_browser"), actions)
        self._open_button.clicked.connect(lambda: self._open_item(None))
        action_row.addWidget(self._checkout_button)
        action_row.addWidget(self._open_button)
        action_row.addStretch(1)
        layout.addWidget(actions)
        self._actions = actions

        self.show_message("github.no_token", "github.no_token_hint", "info")

    # ------------------------------------------------------------------- states

    def set_show_avatars(self, enabled: bool) -> None:
        """
        Turns author pictures on or off.

        Args:
            enabled: Whether to show them.

        Returns:
            None
        """

        self._show_avatars = enabled

    def show_message(self, title_key: str, detail_key: str = "", token: str = "info", **params) -> None:
        """
        Replaces the panel with an explanation.

        Args:
            title_key: Translation key for the headline.
            detail_key: Translation key for the explanation.
            token: Theme token naming the accent colour.
            **params: Placeholder values for the texts.

        Returns:
            None
        """

        self._notice.set_message(
            i18n.t(title_key, **params),
            i18n.t(detail_key, **params) if detail_key else "",
            token,
        )
        self._stack.setCurrentWidget(self._notice_holder)
        self._actions.setVisible(False)
        self._refresh_button.setEnabled(title_key != "github.not_github")

    def show_snapshot(self, snapshot: RepositorySnapshot) -> None:
        """
        Fills the lists from a fetch result.

        Args:
            snapshot: What the client returned.

        Returns:
            None
        """

        if not snapshot.ok:
            if snapshot.error_key == ERROR_NO_TOKEN:
                self.show_message("github.no_token", "github.no_token_hint", "info")
                return
            if snapshot.error_key == ERROR_RATE_LIMITED:
                self.show_message(
                    "github.rate_limited",
                    "github.rate_limited_hint",
                    "warning",
                    count=max(1, snapshot.retry_after_minutes),
                )
                return
            self.show_message("error.title", snapshot.error_key, "danger")
            return

        self._fill_pulls(snapshot.pull_requests)
        self._fill_issues(snapshot.issues)
        self._tabs.setTabText(
            0, f"{i18n.t('github.pull_requests')} ({len(snapshot.pull_requests)})"
        )
        self._tabs.setTabText(1, f"{i18n.t('github.issues')} ({len(snapshot.issues)})")
        self._stack.setCurrentWidget(self._tabs)
        self._actions.setVisible(True)
        self._refresh_button.setEnabled(True)

    def _fill_pulls(self, pulls: tuple[PullRequest, ...]) -> None:
        """
        Rebuilds the pull request list.

        Args:
            pulls: Pull requests to show.

        Returns:
            None
        """

        self._pulls.clear()
        if not pulls:
            placeholder = QListWidgetItem(i18n.t("github.pr_none"), self._pulls)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            return
        for pull in pulls:
            item = QListWidgetItem(self._pulls)
            item.setData(_ROLE_URL, pull.html_url)
            item.setData(_ROLE_BRANCH, pull.head_branch)
            draft_marker = " · draft" if pull.draft else ""
            subtitle = " · ".join(
                part
                for part in (
                    i18n.t("github.pr_number", number=pull.number),
                    i18n.t("github.pr_by", author=pull.author) if pull.author else "",
                    i18n.t("github.pr_into", branch=pull.base_branch) if pull.base_branch else "",
                )
                if part
            )
            row = _ItemRow(
                pull.title,
                subtitle + draft_marker,
                pull.check_state,
                pull.avatar_url,
                self._show_avatars,
                self._pulls,
            )
            item.setSizeHint(row.sizeHint())
            self._pulls.setItemWidget(item, row)

    def _fill_issues(self, issues: tuple[Issue, ...]) -> None:
        """
        Rebuilds the issue list.

        Args:
            issues: Issues to show.

        Returns:
            None
        """

        self._issues.clear()
        if not issues:
            placeholder = QListWidgetItem(i18n.t("github.issues_none"), self._issues)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            return
        for issue in issues:
            item = QListWidgetItem(self._issues)
            item.setData(_ROLE_URL, issue.html_url)
            subtitle = " · ".join(
                part
                for part in (
                    i18n.t("github.pr_number", number=issue.number),
                    i18n.t("github.pr_by", author=issue.author) if issue.author else "",
                )
                if part
            )
            row = _ItemRow(issue.title, subtitle, None, issue.avatar_url, self._show_avatars, self._issues)
            item.setSizeHint(row.sizeHint())
            self._issues.setItemWidget(item, row)

    # ------------------------------------------------------------------- actions

    def _current_item(self) -> QListWidgetItem | None:
        """
        Returns the selected row of the visible tab.

        Returns:
            QListWidgetItem | None: The item, or None.
        """

        widget = self._tabs.currentWidget()
        if isinstance(widget, QListWidget):
            return widget.currentItem()
        return None

    def _open_item(self, item: QListWidgetItem | None) -> None:
        """
        Opens a pull request or issue in the browser.

        Args:
            item: Row that was double-clicked, or None to use the selection.

        Returns:
            None
        """

        target = item or self._current_item()
        if target is None:
            return
        url = target.data(_ROLE_URL)
        if isinstance(url, str) and url:
            self.open_url_requested.emit(url)

    def _checkout_selected(self) -> None:
        """
        Asks for the selected pull request's branch to be checked out.

        Returns:
            None
        """

        item = self._current_item()
        if item is None:
            return
        branch = item.data(_ROLE_BRANCH)
        if isinstance(branch, str) and branch:
            self.checkout_branch_requested.emit(branch)


def build_snapshot_thread(
    client: GitHubClient, owner: str, repo: str, ref: str, parent: QObject | None = None
) -> tuple[QThread, SnapshotWorker]:
    """
    Wires a snapshot worker onto its own thread.

    Args:
        client: Client to use.
        owner: Repository owner.
        repo: Repository name.
        ref: Commit or branch whose check state is wanted.
        parent: Owner for the thread object.

    Returns:
        tuple[QThread, SnapshotWorker]: The started-on-demand thread and worker.
    """

    worker = SnapshotWorker(client, owner, repo, ref)
    thread = QThread(parent)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    return thread, worker
