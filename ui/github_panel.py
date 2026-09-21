"""
The GitHub panel: everything the selected repository has on the server.

Four lists behind four tabs, plus the repository's own settings. The panel itself
does three things the tabs do not: it decides what the panel shows at all (a
token that is missing, a remote that is not GitHub, a rate limit that has not
expired), it owns the one runner every tab queues its calls on, and it is where a
failure turns into a sentence.

Every state this panel can be in gets said out loud rather than shown as an empty
list. An empty panel with no explanation is the thing that makes people think a
program is broken.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import i18n
from github_api.client import GitHubClient
from github_api.http import (
    ERROR_FORBIDDEN,
    ERROR_NO_TOKEN,
    ERROR_RATE_LIMITED,
    ERROR_UNAUTHORIZED,
    ApiResult,
)
from ui.github_dialogs import (
    DeleteRepositoryDialog,
    RepositorySettings,
    RepositorySettingsDialog,
)
from ui.github_lists import (
    ActionsTab,
    GitHubContext,
    IssuesTab,
    PullRequestsTab,
    ReleasesTab,
    RemoteTab,
)
from ui.github_worker import ApiRunner
from ui.widgets import InlineMessage, SectionHeader

TAB_PULLS = 0
TAB_ISSUES = 1
TAB_RELEASES = 2
TAB_ACTIONS = 3


class GitHubPanel(QWidget):
    """
    Shows and edits what the selected repository has on GitHub.

    Attributes:
        checkout_branch_requested: Emitted with a branch name to check out.
        open_url_requested: Emitted with a web address to open.
        refresh_requested: Emitted when the user asks for fresh data.
        repository_removed: Emitted after the repository was deleted on the
            server, so the window can offer to drop the local copy as well.
    """

    checkout_branch_requested = Signal(str)
    open_url_requested = Signal(str)
    refresh_requested = Signal()
    repository_removed = Signal(str)

    def __init__(
        self,
        client: GitHubClient,
        show_avatars: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            client: The API client, shared with the rest of the window.
            show_avatars: Whether author pictures are shown.
            parent: Parent widget.
        """

        super().__init__(parent)
        self._client = client
        self._context = GitHubContext()
        self._runner = ApiRunner(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = SectionHeader(i18n.t("github.title"), self)
        self._settings_button = QPushButton(i18n.t("github.settings_button"), header)
        self._settings_button.setToolTip(i18n.t("tip.repo_settings"))
        self._settings_button.clicked.connect(self._open_settings)
        header.add_widget(self._settings_button)
        self._refresh_button = QPushButton(i18n.t("action.refresh"), header)
        self._refresh_button.setToolTip(i18n.t("tip.github_refresh"))
        self._refresh_button.clicked.connect(self.refresh_requested.emit)
        header.add_widget(self._refresh_button)
        layout.addWidget(header)
        self._header = header

        self._stack = QStackedWidget(self)

        self._notice_holder = QWidget(self)
        notice_layout = QVBoxLayout(self._notice_holder)
        notice_layout.setContentsMargins(16, 16, 16, 16)
        self._notice = InlineMessage("", "", "info", self._notice_holder)
        notice_layout.addWidget(self._notice)
        notice_layout.addStretch(1)
        self._stack.addWidget(self._notice_holder)

        self._tabs = QTabWidget(self)
        self._pulls = PullRequestsTab(client, self._runner, show_avatars, self._tabs)
        self._issues = IssuesTab(client, self._runner, show_avatars, self._tabs)
        self._releases = ReleasesTab(client, self._runner, show_avatars, self._tabs)
        self._runs = ActionsTab(client, self._runner, show_avatars, self._tabs)
        self._tabs.addTab(self._pulls, i18n.t("github.pull_requests"))
        self._tabs.addTab(self._issues, i18n.t("github.issues"))
        self._tabs.addTab(self._releases, i18n.t("github.releases"))
        self._tabs.addTab(self._runs, i18n.t("github.runs"))
        for index, key in enumerate(
            ("tip.tab_pulls", "tip.tab_issues", "tip.tab_releases", "tip.tab_runs")
        ):
            self._tabs.setTabToolTip(index, i18n.t(key))
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._stack.addWidget(self._tabs)

        layout.addWidget(self._stack, 1)

        self._pulls.checkout_branch_requested.connect(self.checkout_branch_requested.emit)
        for tab in self._all_tabs():
            tab.open_url_requested.connect(self.open_url_requested.emit)
            tab.failed.connect(self._report)
            tab.working.connect(self._on_working)
            tab.confirm_requested.connect(self._confirm_then)

        self.show_message("github.no_token", "github.no_token_hint", "info")

    def _all_tabs(self) -> list[RemoteTab]:
        """
        Returns the four lists.

        Returns:
            list[RemoteTab]: The tabs, in the order they are shown.
        """

        return [self._pulls, self._issues, self._releases, self._runs]

    # ------------------------------------------------------------------- states

    def set_show_avatars(self, enabled: bool) -> None:
        """
        Turns author pictures on or off.

        Args:
            enabled: Whether to show them.

        Returns:
            None
        """

        for tab in self._all_tabs():
            tab.set_show_avatars(enabled)

    def show_message(
        self, title_key: str, detail_key: str = "", token: str = "info", **params: object
    ) -> None:
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
        self._refresh_button.setEnabled(title_key != "github.not_github")
        self._settings_button.setEnabled(False)

    def set_context(self, context: GitHubContext) -> None:
        """
        Points the panel at a repository and loads the visible tab.

        Args:
            context: The repository and what may be done with it.

        Returns:
            None
        """

        self._context = context
        for tab in self._all_tabs():
            tab.set_context(context)
        self._header.set_title(
            i18n.t("github.title_for", repo=context.full_name) if context.ok else i18n.t("github.title")
        )
        self._stack.setCurrentWidget(self._tabs)
        self._refresh_button.setEnabled(True)
        self._settings_button.setEnabled(context.ok)
        self._current_tab().ensure_loaded()

    def reload(self) -> None:
        """
        Fetches the visible tab again.

        Returns:
            None
        """

        if self._context.ok:
            self._current_tab().reload()

    def stop(self) -> None:
        """
        Drops any call still in flight.

        Returns:
            None
        """

        self._runner.stop()

    def _current_tab(self) -> RemoteTab:
        """
        Returns the tab the user is looking at.

        Returns:
            RemoteTab: The visible tab.
        """

        widget = self._tabs.currentWidget()
        return widget if isinstance(widget, RemoteTab) else self._pulls

    def _on_tab_changed(self, _index: int) -> None:
        """
        Loads a tab the first time it is opened.

        Args:
            _index: Index of the new tab, unused.

        Returns:
            None
        """

        self._current_tab().ensure_loaded()

    def _on_working(self, busy: bool) -> None:
        """
        Shows that something is in flight.

        Args:
            busy: Whether a call is running.

        Returns:
            None
        """

        self._refresh_button.setEnabled(not busy)

    # ------------------------------------------------------------------ failures

    def _report(self, outcome: Any) -> None:
        """
        Turns a failed call into a sentence the user can act on.

        Args:
            outcome: A failed ``ApiResult`` or the exception that was raised.

        Returns:
            None
        """

        if isinstance(outcome, Exception):
            title = i18n.t("error.title")
            detail = str(outcome)
        elif isinstance(outcome, ApiResult):
            if outcome.error_key == ERROR_NO_TOKEN:
                self.show_message("github.no_token", "github.no_token_hint", "info")
                return
            if outcome.error_key == ERROR_RATE_LIMITED:
                self.show_message(
                    "github.rate_limited",
                    "github.rate_limited_hint",
                    "warning",
                    count=max(1, outcome.retry_after_minutes),
                )
                return
            title = i18n.t(outcome.error_key or "error.title")
            detail = outcome.detail
            if outcome.error_key in {ERROR_FORBIDDEN, ERROR_UNAUTHORIZED}:
                # The most common cause by far is a token without the scope this
                # action needs, and that is worth naming rather than leaving the
                # user to guess from GitHub's wording.
                detail = f"{detail}\n\n{i18n.t('github.scope_hint')}".strip()
        else:
            title = i18n.t("error.title")
            detail = ""

        box = QMessageBox(self)
        box.setWindowTitle(i18n.t("error.title"))
        box.setText(title)
        if detail:
            box.setInformativeText(detail)
        box.setIcon(QMessageBox.Icon.Warning)
        box.exec()

    def _confirm_then(self, title: str, question: str, action: Callable[[], None]) -> None:
        """
        Asks before performing something a tab flagged as needing confirmation.

        Args:
            title: Dialog title.
            question: What would happen.
            action: What to do when the user agrees.

        Returns:
            None
        """

        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(title)
        box.setInformativeText(question)
        box.setIcon(QMessageBox.Icon.Warning)
        accept = box.addButton(i18n.t("action.delete"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(i18n.t("action.cancel"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is accept:
            action()

    # ------------------------------------------------------ repository settings

    def _open_settings(self) -> None:
        """
        Loads the repository's settings and opens the editor.

        Returns:
            None
        """

        if not self._context.ok:
            return
        context = self._context

        def work() -> tuple[ApiResult, ApiResult, ApiResult]:
            repository = self._client.repository(context.owner, context.repo)
            branches = self._client.branches(
                context.owner, context.repo, default_branch=context.default_branch
            )
            # Listing collaborators needs administrative permission. A failure
            # here is expected for a repository the user only contributes to, so
            # it empties the list rather than taking the dialog down.
            people = self._client.collaborator_list(context.owner, context.repo)
            return repository, branches, people

        self._runner.submit(work, self._show_settings)

    def _show_settings(self, outcome: Any) -> None:
        """
        Shows the settings dialog and sends whatever it changed.

        Args:
            outcome: The repository and branch results.

        Returns:
            None
        """

        if isinstance(outcome, Exception):
            self._report(outcome)
            return
        repository_result, branches_result, people_result = outcome
        if not repository_result.ok:
            self._report(repository_result)
            return

        repository = repository_result.payload
        branches = (
            [branch.name for branch in branches_result.payload] if branches_result.ok else []
        )
        current = RepositorySettings(
            description=repository.description,
            homepage=repository.homepage,
            topics=list(repository.topics),
            default_branch=repository.default_branch,
            visibility=repository.visibility,
            has_issues=repository.has_issues,
            has_wiki=repository.has_wiki,
            has_projects=repository.has_projects,
            has_discussions=repository.has_discussions,
            archived=repository.archived,
            allow_merge_commit=repository.allow_merge_commit,
            allow_squash_merge=repository.allow_squash_merge,
            allow_rebase_merge=repository.allow_rebase_merge,
            delete_branch_on_merge=repository.delete_branch_on_merge,
        )
        dialog = RepositorySettingsDialog(
            repository.full_name or self._context.full_name,
            current,
            branches,
            repository.can_administer,
            list(people_result.payload or []) if people_result.ok else [],
            self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        if dialog.delete_requested:
            self._confirm_delete(repository.full_name or self._context.full_name)
            return

        if dialog.collaborator_request is not None:
            self._change_collaborator(*dialog.collaborator_request)
            return

        changes = dialog.changes()
        topics = dialog.settings().topics if dialog.topics_changed() else None
        if not changes and topics is None:
            return

        owner, repo = self._context.owner, self._context.repo

        def work() -> ApiResult:
            if changes:
                result = self._client.update_repository(owner, repo, **changes)
                if not result.ok:
                    return result
            if topics is not None:
                return self._client.set_topics(owner, repo, topics)
            return self._client.repository(owner, repo)

        self._runner.submit(work, self._after_settings)

    def _change_collaborator(self, action: str, login: str, permission: str) -> None:
        """
        Invites someone or takes their access away.

        Args:
            action: ``add`` or ``remove``.
            login: The account.
            permission: Access level for an invitation.

        Returns:
            None
        """

        owner, repo = self._context.owner, self._context.repo
        if action == "add":
            self._runner.submit(
                lambda: self._client.add_collaborator(owner, repo, login, permission),
                self._after_collaborator,
            )
            return
        self._confirm_then(
            i18n.t("github.collaborator_remove"),
            i18n.t("github.collaborator_remove_question", login=login),
            lambda: self._runner.submit(
                lambda: self._client.remove_collaborator(owner, repo, login),
                self._after_collaborator,
            ),
        )

    def _after_collaborator(self, outcome: Any) -> None:
        """
        Reports the outcome of a collaborator change.

        Args:
            outcome: What the call returned.

        Returns:
            None
        """

        if isinstance(outcome, Exception) or not outcome.ok:
            self._report(outcome)
            return
        # An invitation only takes effect once accepted, so saying "done" without
        # saying that would be misleading.
        self._notice.set_message(i18n.t("github.collaborator_changed"), "", "success")

    def _after_settings(self, outcome: Any) -> None:
        """
        Reports a failed settings change and refreshes on success.

        Args:
            outcome: What the call returned.

        Returns:
            None
        """

        if isinstance(outcome, Exception) or not outcome.ok:
            self._report(outcome)
            return
        self.reload()

    def _confirm_delete(self, full_name: str) -> None:
        """
        Asks for the repository name and deletes it when it matches.

        Args:
            full_name: ``owner/name`` of the repository.

        Returns:
            None
        """

        dialog = DeleteRepositoryDialog(full_name, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        owner, repo = self._context.owner, self._context.repo
        self._runner.submit(
            lambda: self._client.delete_repository(owner, repo),
            lambda outcome: self._after_delete(full_name, outcome),
        )

    def _after_delete(self, full_name: str, outcome: Any) -> None:
        """
        Reports the outcome of a deletion.

        Args:
            full_name: ``owner/name`` of the repository.
            outcome: What the call returned.

        Returns:
            None
        """

        if isinstance(outcome, Exception) or not outcome.ok:
            self._report(outcome)
            return
        self.show_message("github.repo_deleted", "github.repo_deleted_hint", "warning", repo=full_name)
        self.repository_removed.emit(full_name)
