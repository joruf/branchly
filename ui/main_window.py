"""
The main window.

This is the only module that knows about every other one, and its job is wiring:
turn a signal from a panel into a call on the git layer, then push the result back
into the panels. Nothing here parses git output or builds a query — that belongs
to ``gitops`` and ``services``.

Two rules it enforces on behalf of the whole program:

* Anything that can lose work asks first, in a sentence that says what would be
  lost. That check lives here because this is where the user's intent arrives.
* Long-running git calls go to a worker, so the window never freezes. Scans run
  in ``services.scheduler``; clone and token checks own their dialogs' threads.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import i18n
import paths
from config.app_settings import AppSettings, save_settings
from config.theme import (
    THEME_DARK,
    THEME_LIGHT,
    available_themes,
    build_application_stylesheet,
    set_current_theme,
)
from constants import APP_NAME, APP_VERSION
from github_api import token as token_store
from github_api.client import GitHubClient
from gitops import blobs
from gitops import branch as branch_mod
from gitops import conflict as conflict_mod
from gitops import diff as diff_mod
from gitops import ignore as ignore_mod
from gitops import remote as remote_mod
from gitops import stage as stage_mod
from gitops.commit import CommitDraft
from gitops.commit import commit as do_commit
from gitops.history import read_history
from gitops.refname import suggest_branch_name
from gitops.remote_url import github_slug
from gitops.runner import GitResult
from gitops.status import RepositoryState, read_state
from models.repository import RepoEntry
from services import open_with, puller, scanner, updater
from services.registry import ADD_DUPLICATE, ADD_NOT_A_REPOSITORY, ADD_OK, load_registry
from services.scheduler import AutoCheckScheduler, RefreshThrottle, ScanCoordinator
from ui.changes_panel import ChangesPanel
from ui.clone_dialog import CloneDialog
from ui.conflict_dialog import ConflictDialog
from ui.diff_view import DiffView
from ui.discover_dialog import DiscoverDialog
from ui.github_dialogs import CreateRepositoryDialog
from ui.github_lists import GitHubContext
from ui.github_panel import GitHubPanel
from ui.github_worker import ApiRunner
from ui.graph_view import GraphView
from ui.pull_all_dialog import PullAllDialog
from ui.settings_dialog import SettingsDialog
from ui.sidebar import Sidebar
from ui.update_dialog import UpdateDialog, build_update_thread, describe_update
from ui.widgets import InlineMessage, refresh_theme_aware

TAB_CHANGES = 0
TAB_GRAPH = 1
TAB_GITHUB = 2

# Delay before the startup update check runs. Long enough that the first scan and
# the first repository are already on screen: news about Branchly itself is never
# more urgent than the window being usable.
UPDATE_CHECK_DELAY_MS = 4000

# Long enough for the window to be painted first, so the search dialog opens
# on top of something rather than into a grey rectangle.
DISCOVERY_OFFER_DELAY_MS = 700

# What each theme is called in the Appearance menu, and what the entry explains.
THEME_LABEL_KEYS: dict[str, str] = {
    THEME_DARK: "theme.dark",
    THEME_LIGHT: "theme.light",
}
THEME_TIP_KEYS: dict[str, str] = {
    THEME_DARK: "tip.theme_dark",
    THEME_LIGHT: "tip.theme_light",
}

# How long the window stays quiet after a focus-driven refresh. Alt-tabbing
# between an editor and Branchly a dozen times must not mean a dozen rounds of
# reading the working tree.
FOCUS_REFRESH_COOLDOWN_SECONDS = 2.0

# Settle time after the selected repository changed. Arrowing down the project
# list would otherwise start a scan for every repository passed on the way.
SWITCH_SCAN_DEBOUNCE_MS = 300

ACTION_UPDATE_INSTALL = "update.install"
ACTION_UPDATE_LATER = "update.later"


class MainWindow(QMainWindow):
    """
    The application window.
    """

    def __init__(self, settings: AppSettings) -> None:
        """
        Args:
            settings: Settings already applied to theme and language.
        """

        super().__init__()
        self._settings = settings
        self._registry = load_registry()
        self._entry: RepoEntry | None = None
        self._state: RepositoryState | None = None
        self._github = GitHubClient(token_store.load() if settings.github_enabled else "")
        # The sidebar badge is fetched here rather than in the panel: it has to
        # keep working while the user is looking at a different tab.
        self._github_runner = ApiRunner(self)
        self._pending_github_key = ""
        self._viewer_login = ""
        self._update_thread = None
        self._update_worker = None
        self._discovery_offered_this_run = False
        self._focus_throttle = RefreshThrottle(FOCUS_REFRESH_COOLDOWN_SECONDS)
        # Set the moment the window starts closing. Refreshing now happens on
        # focus and on every project switch, so without this a scan could still
        # be started after closeEvent already waited for the running ones, and
        # Qt aborts the process when a thread outlives its pool.
        self._closing = False
        # Set when a scan was asked for while another batch was running, so it
        # can be made good once that batch is done rather than silently lost.
        self._pending_scan_key: str | None = None
        self._update_info: updater.UpdateInfo | None = None
        self._restart_after_update = False

        self.setWindowTitle(APP_NAME)
        self.resize(1360, 860)

        self._build_ui()
        self._build_menu()
        self._wire()

        self._restore_geometry()
        self._sidebar.refresh()
        # Establish the diff panel's target list before anything is selected, so
        # the first file shown is compared against the last commit rather than
        # against whatever happened to be first in the dropdown.
        self._on_tab_changed(TAB_CHANGES)
        self._select_initial_repo()

        self._scheduler.configure(self._settings.auto_check_minutes)
        self._scheduler.trigger_soon()
        if self._settings.check_updates:
            QTimer.singleShot(UPDATE_CHECK_DELAY_MS, self._maybe_check_updates)

    # --------------------------------------------------------------------- build

    def _build_ui(self) -> None:
        """
        Assembles the panels.

        Returns:
            None
        """

        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)

        self._sidebar = Sidebar(self._registry, self._settings.sort_mode, self._splitter)
        self._sidebar.setMinimumWidth(260)
        self._splitter.addWidget(self._sidebar)

        right = QWidget(self._splitter)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        right_layout.addWidget(self._build_repo_bar(right))

        # Its own strip rather than sharing the repository notice below: news about
        # Branchly itself must not be wiped out by the next repository selection.
        self._update_banner = InlineMessage("", "", "success", right)
        self._update_banner.setVisible(False)
        right_layout.addWidget(self._update_banner)

        self._notice = InlineMessage("", "", "info", right)
        self._notice.setVisible(False)
        right_layout.addWidget(self._notice)

        content = QSplitter(Qt.Orientation.Horizontal, right)

        self._tabs = QTabWidget(content)
        self._changes = ChangesPanel(self._tabs)
        self._graph = GraphView(self._tabs)
        self._pull_requests = GitHubPanel(self._github, self._settings.show_avatars, self._tabs)
        self._tabs.addTab(self._changes, i18n.t("changes.title"))
        self._tabs.addTab(self._graph, i18n.t("changes.graph"))
        self._tabs.addTab(self._pull_requests, i18n.t("github.title"))
        content.addWidget(self._tabs)

        self._diff = DiffView(
            self._settings.diff_mode,
            self._settings.diff_ignore_whitespace,
            self._settings.diff_word_level,
            content,
        )
        content.addWidget(self._diff)
        content.setStretchFactor(0, 4)
        content.setStretchFactor(1, 6)
        self._content = content

        right_layout.addWidget(content, 1)
        self._splitter.addWidget(right)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([300, 1060])

        self.setCentralWidget(self._splitter)
        self.setStatusBar(QStatusBar(self))

        self._scans = ScanCoordinator(self)
        self._scheduler = AutoCheckScheduler(self)

        self._switch_scan_timer = QTimer(self)
        self._switch_scan_timer.setSingleShot(True)
        self._switch_scan_timer.setInterval(SWITCH_SCAN_DEBOUNCE_MS)
        self._switch_scan_timer.timeout.connect(self._scan_current)

    def _build_repo_bar(self, parent: QWidget) -> QWidget:
        """
        Builds the bar above the tabs holding the sync buttons.

        Args:
            parent: Parent widget.

        Returns:
            QWidget: The bar.
        """

        holder = QWidget(parent)
        holder.setObjectName("PanelHeader")
        row = QHBoxLayout(holder)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)

        self._repo_label = QLabel("", holder)
        self._repo_label.setToolTip(i18n.t("tip.current_repo"))
        self._repo_label.setObjectName("Heading")
        row.addWidget(self._repo_label)

        self._branch_label = QLabel("", holder)
        self._branch_label.setToolTip(i18n.t("tip.branch_current"))
        self._branch_label.setObjectName("Muted")
        row.addWidget(self._branch_label)
        row.addStretch(1)

        self._new_branch_button = QPushButton(i18n.t("branch.new"), holder)
        self._new_branch_button.setToolTip(i18n.t("tip.branch_new"))
        self._new_branch_button.clicked.connect(self._prompt_new_branch)
        row.addWidget(self._new_branch_button)

        self._switch_branch_button = QPushButton(i18n.t("branch.switch"), holder)
        self._switch_branch_button.setToolTip(i18n.t("tip.branch_switch"))
        self._switch_branch_button.clicked.connect(self._prompt_switch_branch)
        row.addWidget(self._switch_branch_button)

        self._fetch_button = QPushButton(i18n.t("sync.fetch"), holder)
        self._fetch_button.setToolTip(i18n.t("tip.fetch"))
        self._fetch_button.clicked.connect(self._do_fetch)
        row.addWidget(self._fetch_button)

        self._pull_button = QPushButton(i18n.t("sync.pull_generic"), holder)
        self._pull_button.setToolTip(i18n.t("tip.pull"))
        self._pull_button.clicked.connect(self._do_pull)
        row.addWidget(self._pull_button)

        self._push_button = QPushButton(i18n.t("sync.push_generic"), holder)
        self._push_button.setToolTip(i18n.t("tip.push"))
        self._push_button.setObjectName("Primary")
        self._push_button.clicked.connect(self._do_push)
        row.addWidget(self._push_button)
        return holder

    def _build_menu(self) -> None:
        """
        Builds the menu bar.

        Returns:
            None
        """

        bar = self.menuBar()

        file_menu = bar.addMenu(i18n.t("menu.file"))
        settings_action = QAction(i18n.t("menu.settings"), self)
        settings_action.setShortcut(QKeySequence.StandardKey.Preferences)
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)
        file_menu.addSeparator()
        quit_action = QAction(i18n.t("menu.quit"), self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = bar.addMenu(i18n.t("menu.view"))
        # Qt hides action tooltips in a menu unless asked, and the entry itself
        # is drawn from the submenu's own action rather than from the submenu.
        view_menu.setToolTipsVisible(True)
        appearance = view_menu.addMenu(i18n.t("menu.appearance"))
        appearance.setToolTipsVisible(True)
        appearance.menuAction().setToolTip(i18n.t("tip.menu_appearance"))
        # Checkable and grouped, so the menu says which one is on rather than
        # only offering the switch.
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        self._theme_actions: dict[str, QAction] = {}
        for name in available_themes():
            action = QAction(i18n.t(THEME_LABEL_KEYS.get(name, name)), self)
            action.setCheckable(True)
            action.setChecked(name == self._settings.theme)
            action.setToolTip(i18n.t(THEME_TIP_KEYS.get(name, "tip.menu_appearance")))
            action.triggered.connect(lambda _checked=False, theme=name: self._choose_theme(theme))
            self._theme_group.addAction(action)
            appearance.addAction(action)
            self._theme_actions[name] = action

        repo_menu = bar.addMenu(i18n.t("menu.repository"))
        repo_menu.addAction(i18n.t("sidebar.add_repo"), self._prompt_add_repo)
        repo_menu.addAction(i18n.t("sidebar.clone_repo"), lambda: self._open_clone_dialog())
        repo_menu.addAction(i18n.t("github.repo_new"), self._create_github_repository)
        repo_menu.addAction(i18n.t("discover.menu"), self._discover_repositories)
        repo_menu.addSeparator()
        refresh_action = QAction(i18n.t("action.refresh"), self)
        refresh_action.setShortcut(QKeySequence.StandardKey.Refresh)
        refresh_action.triggered.connect(self._reload_current)
        repo_menu.addAction(refresh_action)
        repo_menu.addAction(i18n.t("sidebar.check_all"), lambda: self._start_scan(""))
        repo_menu.addAction(i18n.t("sidebar.pull_all"), self._pull_all)
        repo_menu.addSeparator()
        repo_menu.addAction(i18n.t("repo.open_folder"), self._open_current_folder)
        repo_menu.addAction(i18n.t("repo.open_remote"), self._open_current_remote)

        branch_menu = bar.addMenu(i18n.t("menu.branch"))
        branch_menu.addAction(i18n.t("branch.new"), self._prompt_new_branch)
        branch_menu.addAction(i18n.t("branch.switch"), self._prompt_switch_branch)
        branch_menu.addSeparator()
        branch_menu.addAction(i18n.t("sync.fetch"), self._do_fetch)
        branch_menu.addAction(i18n.t("sync.pull_generic"), self._do_pull)
        branch_menu.addAction(i18n.t("sync.push_generic"), self._do_push)

        help_menu = bar.addMenu(i18n.t("menu.help"))
        help_menu.addAction(i18n.t("menu.check_updates"), self._open_update_dialog)
        help_menu.addSeparator()
        help_menu.addAction(i18n.t("menu.about"), self._show_about)

    def _wire(self) -> None:
        """
        Connects every panel signal to its handler.

        Returns:
            None
        """

        self._sidebar.repo_selected.connect(self._on_repo_selected)
        self._sidebar.check_requested.connect(self._start_scan)
        self._sidebar.pull_all_requested.connect(self._pull_all)
        self._sidebar.add_requested.connect(self._prompt_add_repo)
        self._sidebar.clone_requested.connect(self._open_clone_dialog)
        self._sidebar.registry_changed.connect(self._save_registry)
        self._sidebar.open_folder_requested.connect(self._open_folder_of)
        self._sidebar.open_remote_requested.connect(self._open_remote_of)
        self._sidebar.rename_requested.connect(self._prompt_rename_repo)
        self._sidebar.remove_requested.connect(self._prompt_remove_repo)

        self._changes.file_selected.connect(self._show_file_diff)
        self._changes.commit_requested.connect(self._do_commit)
        self._changes.discard_requested.connect(self._confirm_discard)
        self._changes.open_file_requested.connect(self._open_repo_file)
        self._changes.reveal_file_requested.connect(self._reveal_repo_file)
        self._changes.resolve_requested.connect(self._open_conflict_assistant)
        self._changes.ignore_requested.connect(self._add_to_gitignore)
        self._changes.selection_changed.connect(self._remember_selection)

        self._diff.options_changed.connect(self._persist_diff_options)
        self._diff.reload_requested.connect(self._reload_diff)
        self._diff.target_changed.connect(lambda _target: self._reload_diff())
        self._diff.open_file_requested.connect(self._open_repo_file)
        self._diff.reveal_file_requested.connect(self._reveal_repo_file)

        self._graph.commit_selected.connect(self._show_commit_diff)
        self._graph.checkout_requested.connect(self._confirm_checkout_commit)
        self._graph.branch_from_requested.connect(self._prompt_branch_from)
        self._graph.merge_requested.connect(self._do_merge)
        self._graph.cherry_pick_requested.connect(self._do_cherry_pick)
        self._graph.revert_requested.connect(self._confirm_revert)
        self._graph.reset_requested.connect(self._confirm_reset)
        self._graph.tag_requested.connect(self._prompt_tag)
        self._graph.compare_requested.connect(self._show_range_diff)
        self._graph.reload_requested.connect(self._reload_graph)

        self._pull_requests.open_url_requested.connect(self._open_url)
        self._pull_requests.checkout_branch_requested.connect(self._checkout_branch)
        self._pull_requests.refresh_requested.connect(self._reload_github)
        self._pull_requests.repository_removed.connect(self._on_remote_repo_deleted)

        self._tabs.currentChanged.connect(self._on_tab_changed)

        self._scans.result_ready.connect(self._on_scan_result)
        self._scans.progress.connect(self._sidebar.set_scan_progress)
        self._scans.batch_finished.connect(self._on_scan_batch_finished)
        self._scheduler.due.connect(lambda: self._start_scan(""))

        self._update_banner.action_clicked.connect(self._on_update_banner_action)

    # ----------------------------------------------------------------- selection

    def _select_initial_repo(self) -> None:
        """
        Selects whatever was open when the window last closed.

        Returns:
            None
        """

        target = self._settings.last_repo
        if target:
            entry = self._registry.find(target)
            if entry is not None:
                self._sidebar.select_key(entry.key)
                self._activate(entry)
                return
        entries = self._registry.entries
        if entries:
            self._sidebar.select_key(entries[0].key)
            self._activate(entries[0])
        else:
            self._show_empty_state()

    def select_startup_repo(self, path: str) -> None:
        """
        Selects a repository given on the command line, adding it if needed.

        Args:
            path: Directory inside a repository.

        Returns:
            None
        """

        entry = self._registry.find(path)
        if entry is None:
            outcome, entry = self._registry.add(path)
            if outcome not in {ADD_OK, ADD_DUPLICATE} or entry is None:
                return
            self._save_registry()
            self._sidebar.refresh()
        self._sidebar.select_key(entry.key)
        self._activate(entry)

    def _on_repo_selected(self, key: str) -> None:
        """
        Handles a project being picked in the sidebar.

        Args:
            key: Registry key.

        Returns:
            None
        """

        for entry in self._registry.entries:
            if entry.key == key:
                self._activate(entry)
                return

    def _activate(self, entry: RepoEntry) -> None:
        """
        Makes a repository the current one and loads its state.

        Args:
            entry: Repository to open.

        Returns:
            None
        """

        self._entry = entry
        self._registry.touch(entry)
        self._settings.last_repo = str(entry.path)
        self._graph.clear_comparison_mark()

        if not entry.exists:
            self._show_notice("repo.missing", "repo.missing_hint", "danger", path=str(entry.path))
            self._changes.set_state(None)
            self._diff.clear()
            self._graph.set_history(read_history(entry.path))
            return

        self._reload_current(restore_selection=True)
        self._reload_github()
        # The panels above read the working tree; this brings the badges and the
        # server's answer up to date as well.
        self._schedule_current_scan()

    def _show_empty_state(self) -> None:
        """
        Puts the window into its "no projects yet" state.

        Returns:
            None
        """

        self._entry = None
        self._state = None
        self._repo_label.setText(i18n.t("sidebar.empty_title"))
        self._branch_label.setText("")
        self._show_notice("sidebar.empty_title", "sidebar.empty_hint", "info")
        self._changes.set_state(None)
        self._diff.clear()
        self._set_sync_enabled(False)

    # ------------------------------------------------------------------ reloading

    def _reload_current(self, restore_selection: bool = False) -> None:
        """
        Reads the current repository's state and refreshes the panels.

        Args:
            restore_selection: Whether to apply the deselection stored for this
                repository. True when switching to it, False for a plain
                refresh, where whatever is ticked right now must stay ticked.

        Returns:
            None
        """

        entry = self._entry
        if entry is None:
            return
        state = read_state(entry.path)
        self._state = state

        self._repo_label.setText(entry.name)
        if not state.ok:
            self._branch_label.setText("")
            self._show_notice("error.title", state.error_key, "danger")
            self._changes.set_state(None)
            self._set_sync_enabled(False)
            return

        self._branch_label.setText(state.display_branch)
        self._changes.set_state(
            state,
            state.display_branch,
            deselected=set(entry.deselected_paths) if restore_selection else None,
        )
        self._update_notice(state)
        self._update_sync_buttons(state)
        self._reload_graph()

        if state.has_conflicts:
            self._tabs.setCurrentIndex(TAB_CHANGES)

        if not self._changes.current_path():
            self._diff.clear()

    def _update_notice(self, state: RepositoryState) -> None:
        """
        Shows or hides the strip above the tabs.

        Args:
            state: Current repository state.

        Returns:
            None
        """

        if state.has_conflicts:
            self._notice.setVisible(False)
            return
        if state.detached:
            self._show_notice("status.detached", "status.detached_hint", "warning")
            return
        entry = self._entry
        if entry is not None and not entry.remote_url and not remote_mod.has_remote(entry.path):
            self._show_notice("sync.no_remote", "sync.no_remote_hint", "info")
            return
        self._notice.setVisible(False)

    def _show_notice(
        self, title_key: str, detail_key: str = "", token: str = "info", **params: object
    ) -> None:
        """
        Shows the message strip above the tabs.

        Args:
            title_key: Translation key for the headline.
            detail_key: Translation key for the explanation.
            token: Theme token naming the accent colour.
            **params: Placeholder values.

        Returns:
            None
        """

        self._notice.set_message(
            i18n.t(title_key, **params),
            i18n.t(detail_key, **params) if detail_key else "",
            token,
        )
        self._notice.setVisible(True)

    def _set_sync_enabled(self, enabled: bool) -> None:
        """
        Enables or disables the branch and sync buttons together.

        Args:
            enabled: Whether they are usable.

        Returns:
            None
        """

        for button in (
            self._fetch_button,
            self._pull_button,
            self._push_button,
            self._new_branch_button,
            self._switch_branch_button,
        ):
            button.setEnabled(enabled)

    def _update_sync_buttons(self, state: RepositoryState) -> None:
        """
        Relabels the sync buttons with the counts they would move.

        Args:
            state: Current repository state.

        Returns:
            None
        """

        entry = self._entry
        has_remote = bool(entry and remote_mod.has_remote(entry.path))
        self._set_sync_enabled(True)
        for button in (self._fetch_button, self._pull_button, self._push_button):
            button.setEnabled(has_remote)

        self._pull_button.setText(
            i18n.t("sync.pull", count=state.behind) if state.behind else i18n.t("sync.pull_generic")
        )
        self._push_button.setText(
            i18n.t("sync.push", count=state.ahead) if state.ahead else i18n.t("sync.push_generic")
        )

    def _reload_graph(self) -> None:
        """
        Reads the history again and hands it to the graph.

        Returns:
            None
        """

        entry = self._entry
        if entry is None or not entry.exists:
            return
        history = read_history(entry.path)
        branch = self._state.display_branch if self._state else ""
        self._graph.set_history(history, branch)

    def _on_tab_changed(self, index: int) -> None:
        """
        Adjusts the diff panel to the newly visible tab.

        Args:
            index: Index of the new tab.

        Returns:
            None
        """

        if index == TAB_CHANGES:
            self._diff.set_target_choices(
                [
                    diff_mod.TARGET_WORKTREE_HEAD,
                    diff_mod.TARGET_WORKTREE_INDEX,
                    diff_mod.TARGET_INDEX_HEAD,
                ]
            )
            path = self._changes.current_path()
            if path:
                self._show_file_diff(path, False)
            else:
                self._diff.clear()
            return
        if index == TAB_GRAPH:
            self._diff.set_target_choices([diff_mod.TARGET_COMMITS])
            oid = self._graph.selected_oid()
            if oid:
                self._show_commit_diff(oid)
            return
        if index == TAB_GITHUB:
            self._reload_github()

    # ---------------------------------------------------------------------- diff

    def _show_file_diff(self, path: str, untracked: bool) -> None:
        """
        Shows the diff of one working-tree file.

        Args:
            path: Repository-relative path.
            untracked: Whether git does not track the file yet.

        Returns:
            None
        """

        entry = self._entry
        if entry is None or not path:
            self._diff.clear()
            return

        if untracked:
            parsed = diff_mod.untracked_file_diff(entry.path, path, self._diff.word_level)
            if parsed.binary:
                self._show_binary_comparison(entry, path, untracked=True)
                return
            self._diff.show_diff(parsed)
            return

        parsed = diff_mod.file_diff(
            entry.path,
            path,
            self._diff.target,
            self._diff.ignore_whitespace,
            self._diff.word_level,
        )
        if parsed.binary:
            # Every file git calls binary gets the two versions compared, not
            # only the ones that happen to be pictures: a new version of a PDF
            # is a change, and saying "not a text file" says nothing about it.
            self._show_binary_comparison(entry, path, untracked=False)
            return
        self._diff.show_diff(parsed)

    def _show_binary_comparison(self, entry: RepoEntry, path: str, untracked: bool) -> None:
        """
        Puts both versions of a non-text file side by side.

        Args:
            entry: Repository the file belongs to.
            path: Repository-relative path.
            untracked: Whether git does not track the file yet, in which case
                there is no older version to look for.

        Returns:
            None
        """

        # The single-file view only ever compares against the working tree, the
        # index or HEAD; two selected commits go through the multi-file path.
        self._diff.show_comparison(
            blobs.compare(entry.path, path, target=self._diff.target, untracked=untracked)
        )

    def _reload_diff(self) -> None:
        """
        Produces the current diff again with the current options.

        Returns:
            None
        """

        if self._tabs.currentIndex() == TAB_GRAPH:
            oid = self._graph.selected_oid()
            if oid:
                self._show_commit_diff(oid)
            return
        path = self._changes.current_path()
        if path:
            untracked = any(
                item.path == path and item.untracked for item in (self._state.files if self._state else [])
            )
            self._show_file_diff(path, untracked)

    def _show_commit_diff(self, oid: str) -> None:
        """
        Shows everything one commit changed.

        Args:
            oid: Object id.

        Returns:
            None
        """

        entry = self._entry
        if entry is None or not oid:
            return
        # Filling the graph selects its first row, which fires this. While the
        # changes tab is in front, that must not replace the file diff the user
        # is looking at.
        if self._tabs.currentIndex() != TAB_GRAPH:
            return
        diffs = diff_mod.commit_diff(
            entry.path, oid, self._diff.ignore_whitespace, self._diff.word_level
        )
        self._diff.show_many(diffs, oid[:7])

    def _show_range_diff(self, older: str, newer: str) -> None:
        """
        Shows everything that changed between two commits.

        Args:
            older: Older revision.
            newer: Newer revision.

        Returns:
            None
        """

        entry = self._entry
        if entry is None:
            return
        diffs = diff_mod.range_diff(
            entry.path, older, newer, self._diff.ignore_whitespace, self._diff.word_level
        )
        self._diff.set_target_choices([diff_mod.TARGET_COMMITS])
        self._diff.show_many(diffs, f"{older[:7]} → {newer[:7]}")

    def _persist_diff_options(self) -> None:
        """
        Copies the diff panel's options into the settings.

        Returns:
            None
        """

        self._settings.diff_mode = self._diff.mode
        self._settings.diff_ignore_whitespace = self._diff.ignore_whitespace
        self._settings.diff_word_level = self._diff.word_level

    # -------------------------------------------------------------------- commit

    def _do_commit(self, draft: CommitDraft, selected: list[str]) -> None:
        """
        Stages the ticked files and commits them.

        Args:
            draft: Message the user typed.
            selected: Paths to include.

        Returns:
            None
        """

        entry = self._entry
        if entry is None or not selected:
            return

        staged = stage_mod.unstage_all(entry.path)
        if staged.failed and "did not match" not in staged.message:
            self._report(staged)
            return
        added = stage_mod.stage_files(entry.path, selected)
        if added.failed:
            self._report(added)
            return
        result = do_commit(entry.path, draft)
        if result.failed:
            self._report(result)
            return
        self._changes.clear_draft()
        # Whatever went in is settled. Keeping it in the deselection would untick
        # a future, unrelated change to the same file.
        self._changes.forget_selection(selected)
        entry.forget_selection(selected)
        self._save_registry()
        self.statusBar().showMessage(draft.summary.strip(), 4000)
        self._reload_current()
        self._start_scan(entry.key)

    def _remember_selection(self) -> None:
        """
        Stores which files the user unticked, so a restart brings them back.

        Returns:
            None
        """

        entry = self._entry
        if entry is None:
            return
        chosen = self._changes.deselected_paths()
        if chosen == entry.deselected_paths:
            return
        entry.deselected_paths = chosen
        self._save_registry()

    def _add_to_gitignore(self, pattern: str) -> None:
        """
        Writes one pattern into the repository's ``.gitignore``.

        Args:
            pattern: The line to add, as the context menu built it.

        Returns:
            None
        """

        entry = self._entry
        if entry is None or not pattern:
            return

        outcome = ignore_mod.add_pattern(entry.path, pattern)
        if not outcome.ok:
            box = QMessageBox(self)
            box.setWindowTitle(i18n.t("error.title"))
            box.setText(i18n.t(outcome.error_key or "error.title"))
            box.setIcon(QMessageBox.Icon.Warning)
            box.exec()
            return

        if outcome.already_present:
            self.statusBar().showMessage(
                i18n.t("ignore.already_there", pattern=outcome.pattern), 5000
            )
            return

        key = "ignore.added_new_file" if outcome.created else "ignore.added"
        self.statusBar().showMessage(i18n.t(key, pattern=outcome.pattern), 5000)
        self._reload_current()
        self._start_scan(entry.key)

    def _confirm_discard(self, paths_to_discard: list[str]) -> None:
        """
        Confirms and then throws away changes to the given files.

        Args:
            paths_to_discard: Repository-relative paths.

        Returns:
            None
        """

        entry = self._entry
        if entry is None or not paths_to_discard:
            return
        name = paths_to_discard[0] if len(paths_to_discard) == 1 else f"{len(paths_to_discard)}"
        if not self._confirm(
            i18n.t("changes.discard_title"),
            i18n.t("changes.discard_prompt", name=name),
            i18n.t("changes.discard_action"),
        ):
            return
        result = stage_mod.discard_changes(entry.path, paths_to_discard)
        if result.failed:
            self._report(result)
            return
        self._reload_current()

    # -------------------------------------------------------------------- branch

    def _prompt_new_branch(self) -> None:
        """
        Asks for a name and creates a branch from the current commit.

        Returns:
            None
        """

        self._prompt_branch_from("")

    def _prompt_branch_from(self, start_point: str) -> None:
        """
        Asks for a name and creates a branch.

        Args:
            start_point: Revision to branch from, empty for the current commit.

        Returns:
            None
        """

        entry = self._entry
        if entry is None:
            return
        typed, accepted = QInputDialog.getText(
            self, i18n.t("branch.new_title"), i18n.t("branch.new_prompt")
        )
        if not accepted or not typed.strip():
            return
        name = suggest_branch_name(typed) or typed.strip()
        if branch_mod.branch_exists(entry.path, name):
            QMessageBox.information(
                self, i18n.t("branch.new_title"), i18n.t("branch.exists", name=name)
            )
            return
        result = branch_mod.create_branch(entry.path, name, start_point)
        if result.failed:
            self._report(result, fallback_title="branch.invalid_name")
            return
        self._reload_current()

    def _prompt_switch_branch(self) -> None:
        """
        Asks which branch to switch to.

        Returns:
            None
        """

        entry = self._entry
        if entry is None:
            return
        if self._state is not None and not self._state.is_clean:
            QMessageBox.information(
                self, i18n.t("branch.uncommitted_title"), i18n.t("branch.uncommitted_hint")
            )
            return
        branches = branch_mod.list_branches(entry.path)
        names = [item.name for item in branches]
        if not names:
            return
        current = next((item.name for item in branches if item.is_current), names[0])
        chosen, accepted = QInputDialog.getItem(
            self, i18n.t("branch.switch"), i18n.t("branch.current"), names, names.index(current), False
        )
        if not accepted or not chosen:
            return
        self._checkout_branch(chosen)

    def _checkout_branch(self, name: str) -> None:
        """
        Switches to a branch.

        Args:
            name: Branch name, local or remote-tracking.

        Returns:
            None
        """

        entry = self._entry
        if entry is None:
            return
        result = branch_mod.checkout_branch(entry.path, name)
        if result.failed:
            self._report(result)
            return
        self._tabs.setCurrentIndex(TAB_CHANGES)
        self._reload_current()

    def _confirm_checkout_commit(self, oid: str) -> None:
        """
        Explains the detached state, offering a branch instead.

        Args:
            oid: Object id to check out.

        Returns:
            None
        """

        entry = self._entry
        if entry is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle(i18n.t("confirm.checkout_detached_title"))
        box.setText(i18n.t("confirm.checkout_detached_title"))
        box.setInformativeText(i18n.t("confirm.checkout_detached_hint"))
        branch_button = box.addButton(
            i18n.t("confirm.checkout_detached_branch"), QMessageBox.ButtonRole.AcceptRole
        )
        look_button = box.addButton(
            i18n.t("confirm.checkout_detached_anyway"), QMessageBox.ButtonRole.DestructiveRole
        )
        box.addButton(i18n.t("action.cancel"), QMessageBox.ButtonRole.RejectRole)
        box.exec()

        clicked = box.clickedButton()
        if clicked is branch_button:
            self._prompt_branch_from(oid)
            return
        if clicked is look_button:
            result = branch_mod.checkout_revision(entry.path, oid)
            if result.failed:
                self._report(result)
                return
            self._reload_current()

    def _do_merge(self, oid: str) -> None:
        """
        Merges a commit into the current branch.

        Args:
            oid: Object id to merge.

        Returns:
            None
        """

        entry = self._entry
        if entry is None:
            return
        result = branch_mod.merge(entry.path, oid)
        self._reload_current()
        if result.failed:
            state = read_state(entry.path)
            if state.has_conflicts:
                self._open_conflict_assistant()
                return
            self._report(result)

    def _do_cherry_pick(self, oid: str) -> None:
        """
        Copies one commit onto the current branch.

        Args:
            oid: Object id to copy.

        Returns:
            None
        """

        entry = self._entry
        if entry is None:
            return
        result = branch_mod.cherry_pick(entry.path, oid)
        self._reload_current()
        if result.failed:
            state = read_state(entry.path)
            if state.has_conflicts:
                self._open_conflict_assistant()
                return
            self._report(result)

    def _confirm_revert(self, oid: str) -> None:
        """
        Confirms and then adds a commit undoing an earlier one.

        Args:
            oid: Object id to undo.

        Returns:
            None
        """

        entry = self._entry
        if entry is None:
            return
        if not self._confirm(
            i18n.t("confirm.revert_title"),
            i18n.t("confirm.revert_hint", version=oid[:7]),
            i18n.t("action.continue"),
        ):
            return
        result = branch_mod.revert(entry.path, oid)
        self._reload_current()
        if result.failed:
            state = read_state(entry.path)
            if state.has_conflicts:
                self._open_conflict_assistant()
                return
            self._report(result)

    def _confirm_reset(self, oid: str, mode: str) -> None:
        """
        Confirms and then moves the current branch to another commit.

        The hard variant is the only action in Branchly that can destroy work
        with no way back, so its confirmation spells that out.

        Args:
            oid: Object id to move to.
            mode: One of the reset modes.

        Returns:
            None
        """

        entry = self._entry
        if entry is None:
            return
        if mode == branch_mod.RESET_HARD and not self._confirm(
            i18n.t("confirm.reset_hard_title"),
            i18n.t("confirm.reset_hard_hint", version=oid[:7]),
            i18n.t("confirm.reset_hard_action"),
            destructive=True,
        ):
            return
        result = branch_mod.reset(entry.path, oid, mode)
        if result.failed:
            self._report(result)
            return
        self._reload_current()

    def _prompt_tag(self, oid: str) -> None:
        """
        Asks for a name and tags a commit.

        Args:
            oid: Object id to tag.

        Returns:
            None
        """

        entry = self._entry
        if entry is None:
            return
        name, accepted = QInputDialog.getText(self, i18n.t("graph.tag"), i18n.t("branch.new_prompt"))
        if not accepted or not name.strip():
            return
        result = branch_mod.create_tag(entry.path, name.strip(), oid)
        if result.failed:
            self._report(result, fallback_title="branch.invalid_name")
            return
        self._reload_graph()

    # ---------------------------------------------------------------------- sync

    def _do_fetch(self) -> None:
        """
        Downloads new commits without changing the working tree.

        Returns:
            None
        """

        entry = self._entry
        if entry is None:
            return
        self.statusBar().showMessage(i18n.t("sync.working"))
        result = remote_mod.fetch(entry.path)
        self.statusBar().clearMessage()
        if result.failed:
            self._report(result)
            return
        self._reload_current()
        self._start_scan(entry.key)

    def _do_pull(self) -> None:
        """
        Fetches and integrates the server's commits.

        Returns:
            None
        """

        entry = self._entry
        if entry is None:
            return
        self.statusBar().showMessage(i18n.t("sync.working"))
        result = remote_mod.pull(entry.path)
        self.statusBar().clearMessage()
        self._reload_current()
        if result.failed:
            state = read_state(entry.path)
            if state.has_conflicts:
                self._open_conflict_assistant()
                return
            self._report(result)
            return
        self._start_scan(entry.key)

    def _pull_all(self) -> None:
        """
        Brings every project up to the server's version in one run.

        Deliberately fast-forward only, and deliberately modal: this is the one
        bulk action that writes to working trees, so nothing else may touch a
        project while a worker is inside it. Whatever cannot be fast-forwarded is
        reported by name rather than merged behind the user's back.

        Returns:
            None
        """

        if self._scans.is_running:
            self.statusBar().showMessage(i18n.t("pull_all.busy"), 4000)
            return
        entries = self._registry.entries
        if not entries:
            self._show_notice("pull_all.heading", "pull_all.nothing", "info")
            return

        dialog = PullAllDialog(puller.build_jobs(entries), self)
        dialog.exec()
        if not dialog.results:
            return

        if any(result.changed for result in dialog.results):
            self._reload_current()
        # Every badge is stale now, whether the project moved or was only fetched.
        self._start_scan("")

    def _do_push(self) -> None:
        """
        Sends local commits to the server.

        Returns:
            None
        """

        entry = self._entry
        if entry is None:
            return
        state = self._state
        set_upstream = bool(state and not state.upstream)
        self.statusBar().showMessage(i18n.t("sync.working"))
        result = remote_mod.push(entry.path, set_upstream=set_upstream)
        self.statusBar().clearMessage()
        if result.failed:
            if result.error_key() == "sync.push_rejected":
                self._show_notice("sync.push_rejected", "sync.push_rejected_hint", "warning")
            else:
                self._report(result)
            self._reload_current()
            return
        self._reload_current()
        self._start_scan(entry.key)

    # ------------------------------------------------------------------ conflicts

    def _open_conflict_assistant(self) -> None:
        """
        Loads the conflicts and walks the user through them.

        Returns:
            None
        """

        entry = self._entry
        if entry is None:
            return
        files = conflict_mod.load_all(entry.path)
        if not files:
            self._reload_current()
            return
        dialog = ConflictDialog(Path(entry.path), files, self)
        dialog.finished_merge.connect(lambda: self.statusBar().showMessage(i18n.t("action.finish"), 4000))
        dialog.exec()
        self._reload_current()
        self._start_scan(entry.key)

    # --------------------------------------------------------------- repositories

    def _prompt_add_repo(self) -> None:
        """
        Asks for a folder and adds the repository it belongs to.

        Returns:
            None
        """

        chosen = QFileDialog.getExistingDirectory(
            self, i18n.t("repo.add_title"), str(paths.default_clone_parent())
        )
        if not chosen:
            return
        outcome, entry = self._registry.add(chosen)
        if outcome == ADD_NOT_A_REPOSITORY:
            QMessageBox.information(
                self, i18n.t("repo.add_not_git"), i18n.t("repo.add_not_git_hint", path=chosen)
            )
            return
        if entry is None:
            QMessageBox.information(self, i18n.t("error.title"), i18n.t("repo.add_not_git_hint", path=chosen))
            return
        if outcome == ADD_DUPLICATE:
            self.statusBar().showMessage(i18n.t("repo.add_duplicate"), 4000)
        self._save_registry()
        self._sidebar.refresh()
        self._sidebar.select_key(entry.key)
        self._activate(entry)
        self._start_scan(entry.key)

    def _discover_repositories(self, roots: list[Path] | None = None) -> None:
        """
        Searches the disk for repositories and registers the ones picked.

        Args:
            roots: Folders to search, defaulting to the home directory.

        Returns:
            None
        """

        known = {entry.key for entry in self._registry.entries}
        dialog = DiscoverDialog(known, self._registry.categories, roots, self)
        if dialog.exec() != DiscoverDialog.DialogCode.Accepted:
            return

        added: list[RepoEntry] = []
        skipped = 0
        for path in dialog.chosen:
            outcome, entry = self._registry.add(path, dialog.category)
            if entry is None or outcome != ADD_OK:
                # A path that stopped being a repository between the scan and the
                # click, or one that was already there. Neither is worth a dialog.
                skipped += 1
                continue
            added.append(entry)

        if not added:
            self.statusBar().showMessage(i18n.t("discover.nothing_added"), 5000)
            return

        self._save_registry()
        self._sidebar.refresh()
        self.statusBar().showMessage(
            i18n.t("discover.added", count=len(added)), 6000
        )
        del skipped

        first = added[0]
        self._sidebar.select_key(first.key)
        self._activate(first)
        # One scan for all of them rather than one per project, so the sidebar
        # fills in as the results come back.
        for entry in added:
            self._start_scan(entry.key)

    def _maybe_offer_discovery(self) -> None:
        """
        Offers the search once, on the first start with an empty project list.

        Returns:
            None
        """

        if self._settings.discovery_offered or self._registry.entries:
            return
        self._settings.discovery_offered = True
        save_settings(self._settings)
        self._discover_repositories()

    def _open_clone_dialog(self, initial_url: str = "") -> None:
        """
        Opens the clone dialog and registers whatever it produced.

        Args:
            initial_url: Address to start from, used right after a repository was
                created on GitHub.

        Returns:
            None
        """

        dialog = CloneDialog(self._registry.categories, initial_url, self._github, self)
        if dialog.exec() != CloneDialog.DialogCode.Accepted:
            return
        target = dialog.cloned_path
        if target is None:
            return
        _outcome, entry = self._registry.add(target, dialog.chosen_category)
        if entry is None:
            return
        self._save_registry()
        self._sidebar.refresh()
        self._sidebar.select_key(entry.key)
        self._activate(entry)
        self.statusBar().showMessage(i18n.t("clone.done", name=entry.name), 5000)
        self._start_scan(entry.key)

    def _prompt_rename_repo(self, key: str) -> None:
        """
        Asks for a new display name.

        Args:
            key: Registry key.

        Returns:
            None
        """

        entry = self._find(key)
        if entry is None:
            return
        name, accepted = QInputDialog.getText(
            self, i18n.t("repo.rename_title"), i18n.t("repo.rename_prompt"), text=entry.name
        )
        if not accepted:
            return
        if self._registry.rename(entry, name):
            self._save_registry()
            self._sidebar.refresh()
            if self._entry is entry:
                self._repo_label.setText(entry.name)

    def _prompt_remove_repo(self, key: str) -> None:
        """
        Confirms and then forgets a repository.

        Args:
            key: Registry key.

        Returns:
            None
        """

        entry = self._find(key)
        if entry is None:
            return
        if not self._confirm(
            i18n.t("repo.remove_title"),
            i18n.t("repo.remove_prompt", name=entry.name),
            i18n.t("action.remove"),
        ):
            return
        self._registry.remove(entry)
        self._save_registry()
        self._sidebar.refresh()
        if self._entry is entry:
            self._entry = None
            entries = self._registry.entries
            if entries:
                self._sidebar.select_key(entries[0].key)
                self._activate(entries[0])
            else:
                self._show_empty_state()

    def _find(self, key: str) -> RepoEntry | None:
        """
        Looks up a registry entry by key.

        Args:
            key: Registry key.

        Returns:
            RepoEntry | None: The entry, or None.
        """

        for entry in self._registry.entries:
            if entry.key == key:
                return entry
        return None

    # -------------------------------------------------------------------- scans

    def _start_scan(self, key: str) -> bool:
        """
        Scans one repository, or all of them.

        Args:
            key: Registry key, or an empty string for every repository.

        Returns:
            bool: True when the batch started. A batch is refused while another
                one runs, and a caller that cares can try again later.
        """

        entries = self._registry.entries
        if key:
            entries = [item for item in entries if item.key == key]
        if not entries:
            return False
        return self._scans.start(
            scanner.build_requests(entries, self._settings.check_online_automatically)
        )

    def _schedule_current_scan(self) -> None:
        """
        Asks for the current repository to be scanned shortly.

        Debounced rather than immediate: moving through the project list with
        the arrow keys would otherwise start a scan for every repository passed
        on the way to the one actually wanted.

        Returns:
            None
        """

        if self._closing or self._entry is None:
            return
        self._switch_scan_timer.start()

    def _scan_current(self) -> None:
        """
        Scans the repository that is selected right now.

        Returns:
            None
        """

        entry = self._entry
        if self._closing or entry is None:
            return
        if not self._start_scan(entry.key):
            # Another batch is running and a second one is refused. Remember the
            # request so it is made good when that batch finishes.
            self._pending_scan_key = entry.key

    def _refresh_on_focus(self) -> None:
        """
        Brings the window up to date after it was given focus.

        This is the case the whole feature exists for: the user edits files in
        another program and comes back, and what Branchly shows has to be what is
        on disk rather than what was there when they left.

        GitHub is deliberately left out of it. Those are network requests against
        a rate limit, the data behind them changes in minutes rather than
        seconds, and the panel has its own refresh button.

        Returns:
            None
        """

        if self._closing or self._entry is None or not self._focus_throttle.allow():
            return
        self._reload_current()
        self._reload_diff()
        self._schedule_current_scan()

    def _on_scan_result(self, result: scanner.ScanResult) -> None:
        """
        Applies one scan result to its entry and row.

        Args:
            result: Fresh scan outcome.

        Returns:
            None
        """

        entry = self._find(result.key)
        if entry is None:
            return
        scanner.apply_result(entry, result)
        self._sidebar.update_entry(entry)

    def _run_pending_scan(self) -> None:
        """
        Starts a scan that was refused while another batch was running.

        Returns:
            None
        """

        key = self._pending_scan_key
        self._pending_scan_key = None
        if self._closing:
            return
        if key and self._find(key) is not None:
            self._start_scan(key)

    def _on_scan_batch_finished(self) -> None:
        """
        Saves the registry and refreshes the summary after a batch.

        Returns:
            None
        """

        self._save_registry()
        self._sidebar.refresh_summary()
        self._run_pending_scan()

    # ------------------------------------------------------------------- github

    def _reload_github(self) -> None:
        """
        Points the GitHub panel at the current repository.

        What the panel needs before it can show anything is not in the registry:
        which account the token belongs to, what the default branch is called on
        the server, and whether this account may write here. Those are fetched
        first, and the panel is switched over in one step once they are known,
        rather than being set up twice and reloading itself in between.

        Returns:
            None
        """

        entry = self._entry
        if entry is None:
            return
        if not self._settings.github_enabled or not self._github.has_token:
            self._pull_requests.show_message("github.no_token", "github.no_token_hint", "info")
            return

        remote = entry.remote_url or remote_mod.remote_fetch_url(entry.path)
        slug = github_slug(remote)
        if slug is None:
            self._pull_requests.show_message("github.not_github", "github.not_github_hint", "info")
            return

        owner, repo = slug
        branch = self._state.display_branch if self._state else ""
        key = entry.key
        self._pending_github_key = key
        self._pull_requests.show_message("github.loading", "", "info")
        self._github_runner.submit(
            lambda: self._fetch_github_context(owner, repo, branch),
            lambda outcome: self._on_github_context(key, owner, repo, branch, outcome),
        )

    def _fetch_github_context(self, owner: str, repo: str, branch: str) -> tuple:
        """
        Collects what the panel and the sidebar badge need, on a worker thread.

        Args:
            owner: Repository owner.
            repo: Repository name.
            branch: Branch checked out locally.

        Returns:
            tuple: ``(viewer login, repository result, open pull requests, check
                state)``.
        """

        login = self._viewer_login
        if not login:
            viewer, _error = self._github.viewer()
            login = viewer.login if viewer is not None else ""
        repository = self._github.repository(owner, repo)
        count, check, _error = self._github.repository_badge(owner, repo, branch)
        return login, repository, count, check

    def _on_github_context(
        self, key: str, owner: str, repo: str, branch: str, outcome: object
    ) -> None:
        """
        Switches the panel over once the repository is known.

        Args:
            key: Registry key the fetch was started for.
            owner: Repository owner.
            repo: Repository name.
            branch: Branch checked out locally.
            outcome: What the fetch returned or raised.

        Returns:
            None
        """

        # The user may have selected a different project while this was running.
        if key != self._pending_github_key:
            return
        if isinstance(outcome, Exception):
            self._pull_requests.show_message("error.title", "error.git_failed", "danger")
            return

        login, repository, count, check = outcome
        self._viewer_login = login
        if not repository.ok:
            self._pull_requests.show_message("error.title", repository.error_key, "danger")
            return

        details = repository.payload
        self._pull_requests.set_context(
            GitHubContext(
                owner=owner,
                repo=repo,
                viewer_login=login,
                local_branch=branch,
                default_branch=details.default_branch,
                can_push=details.can_push,
                can_administer=details.can_administer,
                merge_methods=tuple(
                    name for name, allowed in details.merge_methods.items() if allowed
                ),
                delete_branch_on_merge=details.delete_branch_on_merge,
            )
        )

        entry = self._find(key)
        if entry is not None:
            entry.status.open_pull_requests = count
            entry.status.check_state = check
            self._sidebar.update_entry(entry)

    def _create_github_repository(self) -> None:
        """
        Creates a repository on GitHub and offers to clone it.

        Returns:
            None
        """

        if not self._settings.github_enabled or not self._github.has_token:
            QMessageBox.information(
                self, i18n.t("github.no_token"), i18n.t("github.no_token_hint")
            )
            return
        self._github_runner.submit(self._github.organizations, self._prompt_new_repository)

    def _prompt_new_repository(self, outcome: object) -> None:
        """
        Shows the creation dialog once the organisation list is known.

        Args:
            outcome: The organisations result.

        Returns:
            None
        """

        organizations: list[str] = []
        if not isinstance(outcome, Exception) and getattr(outcome, "ok", False):
            organizations = list(outcome.payload or [])

        dialog = CreateRepositoryDialog(organizations, self)
        if dialog.exec() != CreateRepositoryDialog.DialogCode.Accepted:
            return
        draft = dialog.draft()
        self._github_runner.submit(
            lambda: self._github.create_repository(
                draft.name,
                description=draft.description,
                private=draft.private,
                organization=draft.organization,
                auto_init=draft.auto_init,
                gitignore_template=draft.gitignore_template,
                license_template=draft.license_template,
            ),
            lambda result: self._on_repository_created(draft.clone_after, result),
        )

    def _on_repository_created(self, clone_after: bool, outcome: object) -> None:
        """
        Reports the new repository and opens the clone dialog for it.

        Args:
            clone_after: Whether the user asked to clone it straight away.
            outcome: What the creation returned.

        Returns:
            None
        """

        if isinstance(outcome, Exception) or not getattr(outcome, "ok", False):
            detail = getattr(outcome, "detail", "") or str(outcome)
            box = QMessageBox(self)
            box.setWindowTitle(i18n.t("error.title"))
            box.setText(i18n.t("github.repo_create_failed"))
            if detail:
                box.setInformativeText(detail)
            box.setIcon(QMessageBox.Icon.Warning)
            box.exec()
            return

        repository = outcome.payload
        self.statusBar().showMessage(
            i18n.t("github.repo_created", repo=repository.full_name), 6000
        )
        if not clone_after:
            return
        # The clone dialog owns destination validation and the clone itself, so
        # the address is handed to it rather than the clone being repeated here.
        self._open_clone_dialog(repository.clone_url)

    def _on_remote_repo_deleted(self, full_name: str) -> None:
        """
        Offers to forget the local copy after the server copy was deleted.

        Args:
            full_name: ``owner/name`` of the deleted repository.

        Returns:
            None
        """

        entry = self._entry
        if entry is None:
            return
        if not self._confirm(
            i18n.t("github.repo_deleted", repo=full_name),
            i18n.t("github.repo_forget_question", name=entry.name),
            i18n.t("action.remove"),
            destructive=False,
        ):
            return
        self._prompt_remove_repo(entry.key)

    # ------------------------------------------------------------------- desktop

    def _open_repo_file(self, path: str) -> None:
        """
        Opens a file with the system's default program.

        Args:
            path: Repository-relative path.

        Returns:
            None
        """

        entry = self._entry
        if entry is None or not path:
            return
        outcome = open_with.open_repo_file(entry.path, path)
        self._report_open(outcome)

    def _reveal_repo_file(self, path: str) -> None:
        """
        Opens the file manager with a file selected.

        Args:
            path: Repository-relative path.

        Returns:
            None
        """

        entry = self._entry
        if entry is None or not path:
            return
        self._report_open(open_with.reveal_repo_file(entry.path, path))

    def _open_current_folder(self) -> None:
        """
        Opens the current repository's folder.

        Returns:
            None
        """

        if self._entry is not None:
            self._report_open(open_with.open_path(self._entry.path))

    def _open_folder_of(self, key: str) -> None:
        """
        Opens a repository's folder.

        Args:
            key: Registry key.

        Returns:
            None
        """

        entry = self._find(key)
        if entry is not None:
            self._report_open(open_with.open_path(entry.path))

    def _open_current_remote(self) -> None:
        """
        Opens the current repository's web page.

        Returns:
            None
        """

        if self._entry is not None:
            self._open_remote_of(self._entry.key)

    def _open_remote_of(self, key: str) -> None:
        """
        Opens a repository's web page.

        Args:
            key: Registry key.

        Returns:
            None
        """

        entry = self._find(key)
        if entry is None:
            return
        remote = entry.remote_url or remote_mod.remote_fetch_url(entry.path)
        url = open_with.web_url_for_remote(remote)
        if not url:
            self._show_notice("sync.no_remote", "sync.no_remote_hint", "info")
            return
        self._open_url(url)

    def _open_url(self, url: str) -> None:
        """
        Opens a web address in the browser.

        Args:
            url: Address to open.

        Returns:
            None
        """

        self._report_open(open_with.open_url(url))

    def _report_open(self, outcome: str) -> None:
        """
        Puts a message in the status bar when opening something failed.

        Args:
            outcome: One of the ``open_with.OPEN_*`` constants.

        Returns:
            None
        """

        if outcome == open_with.OPEN_OK:
            return
        self.statusBar().showMessage(i18n.t("error.title"), 4000)

    # ------------------------------------------------------------------ settings

    def _choose_theme(self, name: str) -> None:
        """
        Switches the appearance from the menu.

        Args:
            name: Theme to switch to.

        Returns:
            None
        """

        if name == self._settings.theme:
            return
        self._settings.theme = name
        save_settings(self._settings)
        self._apply_theme(name)

    def _apply_theme(self, name: str) -> None:
        """
        Repaints the whole window in a theme.

        Args:
            name: Theme to paint in.

        Returns:
            None
        """

        set_current_theme(name)
        instance = QApplication.instance()
        if instance is not None:
            instance.setStyleSheet(build_application_stylesheet(name))
        refresh_theme_aware(self)
        self._sidebar.refresh()
        self._reload_graph()
        self._reload_diff()
        action = self._theme_actions.get(name)
        if action is not None and not action.isChecked():
            action.setChecked(True)

    def _open_settings(self) -> None:
        """
        Opens the settings dialog and applies the result.

        Returns:
            None
        """

        dialog = SettingsDialog(self._settings, self)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return
        previous_language = self._settings.language
        updated = dialog.result_settings()
        updated.last_repo = self._settings.last_repo
        self._settings = updated
        save_settings(self._settings)

        self._sidebar.set_sort_mode(self._settings.sort_mode)
        self._pull_requests.set_show_avatars(self._settings.show_avatars)
        self._github.set_token(token_store.load() if self._settings.github_enabled else "")
        self._viewer_login = ""
        self._scheduler.configure(self._settings.auto_check_minutes)
        self._reload_github()

        if self._settings.language != previous_language:
            QMessageBox.information(
                self,
                i18n.t("settings.language_restart_title"),
                i18n.t("settings.language_restart_hint"),
            )

    def _show_about(self) -> None:
        """
        Shows the about box.

        Returns:
            None
        """

        QMessageBox.information(
            self,
            i18n.t("menu.about"),
            f"{APP_NAME}\n{i18n.t('about.version', version=APP_VERSION)}\n\n"
            f"{i18n.t('about.description')}\n\n{i18n.t('about.credits')}",
        )

    # ------------------------------------------------------------------- updates

    def _maybe_check_updates(self) -> None:
        """
        Shows an update that is still waiting, then checks again if that is due.

        Returns:
            None
        """

        if not self._settings.check_updates:
            return
        self._show_pending_update()
        if not updater.due(self._settings.update_check_hours, self._settings.update_checked_at):
            return
        self._start_update_check()

    def _show_pending_update(self) -> None:
        """
        Restores the notice for an update the last check already found.

        Without this, "not now" would silence Branchly until the next check is
        due — a restart would not bring the notice back, because the throttle says
        there is nothing to ask about.

        Returns:
            None
        """

        pending = self._settings.update_remote_commit
        if not pending:
            return
        local = updater.local_commit()
        if not local or local.startswith(pending):
            # Already installed, or no longer comparable: nothing to announce.
            self._forget_pending_update()
            return
        # Deliberately not stored as the live result: clicking Install re-checks,
        # which both fetches the list of changes and confirms it is still pending.
        self._show_update_banner(
            updater.UpdateInfo(
                available=True,
                local=local[:10],
                remote=pending,
                summary=self._settings.update_remote_summary,
            )
        )

    def _start_update_check(self) -> None:
        """
        Asks GitHub for the newest commit on a worker thread.

        Returns:
            None
        """

        if self._update_thread is not None:
            return
        thread, worker = build_update_thread(False, self)
        worker.checked.connect(self._on_update_checked)
        self._update_thread = thread
        self._update_worker = worker
        thread.start()

    def _on_update_checked(self, info: updater.UpdateInfo) -> None:
        """
        Announces a new version, and says nothing at all otherwise.

        A failed check stays silent on purpose: the startup check runs whether or
        not there is a network, and a dialog about that would be noise.

        Args:
            info: What the check found.

        Returns:
            None
        """

        if self._update_thread is not None:
            self._update_thread.quit()
            self._update_thread.wait(5000)
            self._update_thread = None
        self._update_worker = None

        if not info.known:
            return
        self._update_info = info
        self._remember_update_check(info)
        if info.available:
            self._show_update_banner(info)

    def _remember_update_check(self, info: updater.UpdateInfo) -> None:
        """
        Stores when the last successful check happened and what it found.

        Args:
            info: The result of a check that produced a usable answer.

        Returns:
            None
        """

        self._settings.update_checked_at = time.time()
        self._settings.update_remote_commit = info.remote if info.available else ""
        self._settings.update_remote_summary = info.summary if info.available else ""
        save_settings(self._settings)

    def _forget_pending_update(self) -> None:
        """
        Drops a remembered update, without touching the check throttle.

        Returns:
            None
        """

        if not self._settings.update_remote_commit and not self._settings.update_remote_summary:
            return
        self._settings.update_remote_commit = ""
        self._settings.update_remote_summary = ""
        save_settings(self._settings)

    def _show_update_banner(self, info: updater.UpdateInfo) -> None:
        """
        Puts the "there is a new version" strip above the panels.

        Args:
            info: The check result to announce.

        Returns:
            None
        """

        self._update_banner.set_message(
            i18n.t("update.available"), describe_update(info), "success"
        )
        self._update_banner.clear_actions()
        self._update_banner.add_action(
            i18n.t("update.install"),
            ACTION_UPDATE_INSTALL,
            primary=True,
            tip=i18n.t("tip.update_install"),
        )
        self._update_banner.add_action(
            i18n.t("update.later"), ACTION_UPDATE_LATER, tip=i18n.t("tip.update_later")
        )
        self._update_banner.setVisible(True)

    def _on_update_banner_action(self, action_id: str) -> None:
        """
        Handles a click on the update strip.

        Args:
            action_id: Identifier of the button.

        Returns:
            None
        """

        if action_id == ACTION_UPDATE_LATER:
            self._update_banner.setVisible(False)
            return
        if action_id == ACTION_UPDATE_INSTALL:
            self._show_update_dialog(self._update_info)

    def _open_update_dialog(self) -> None:
        """
        Opens the update dialog from the menu, always with a fresh check.

        Returns:
            None
        """

        self._show_update_dialog(None)

    def _show_update_dialog(self, info: updater.UpdateInfo | None) -> None:
        """
        Runs the update dialog and acts on what the user decided there.

        Args:
            info: A check result to show straight away, or None to check now.

        Returns:
            None
        """

        dialog = UpdateDialog(info, self)
        dialog.exec()

        result = dialog.info
        if result is not None:
            self._update_info = result
            if result.known:
                self._remember_update_check(result)
            if result.known and result.available and not dialog.restart_wanted:
                self._show_update_banner(result)
            else:
                self._update_banner.setVisible(False)

        if dialog.restart_wanted:
            # The new version is on disk, so nothing is pending any more; leaving
            # the note behind would announce the update the user just installed.
            self._forget_pending_update()
            self._restart_after_update = True
            self.close()

    def wants_restart(self) -> bool:
        """
        Reports whether the entry point should start Branchly again after closing.

        Returns:
            bool: True when an update was installed and asked for a restart.
        """

        return self._restart_after_update

    # -------------------------------------------------------------------- shared

    def _confirm(self, title: str, message: str, accept_label: str, destructive: bool = False) -> bool:
        """
        Asks a yes/no question, or skips it when the user turned that off.

        Args:
            title: Dialog title.
            message: The question, saying what would happen.
            accept_label: Label for the confirming button.
            destructive: Whether the action loses work irrecoverably.

        Returns:
            bool: True when the user agreed.
        """

        if not self._settings.confirm_destructive and not destructive:
            return True
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(title)
        box.setInformativeText(message)
        box.setIcon(QMessageBox.Icon.Warning if destructive else QMessageBox.Icon.Question)
        accept = box.addButton(accept_label, QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(i18n.t("action.cancel"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is accept

    def _report(self, result: GitResult, fallback_title: str = "error.title") -> None:
        """
        Shows a git failure in words the user can act on.

        Args:
            result: The failed result.
            fallback_title: Translation key for the title when nothing better fits.

        Returns:
            None
        """

        key = result.error_key()
        title = i18n.t(key) if key else i18n.t(fallback_title)
        detail = result.message.strip()
        box = QMessageBox(self)
        box.setWindowTitle(i18n.t("error.title"))
        box.setText(title)
        if detail:
            box.setDetailedText(detail)
        box.setIcon(QMessageBox.Icon.Warning)
        box.exec()

    def _save_registry(self) -> None:
        """
        Writes the registry to disk.

        Returns:
            None
        """

        self._registry.save()

    # ----------------------------------------------------------------- lifecycle

    def _restore_geometry(self) -> None:
        """
        Puts the window back where it was.

        Returns:
            None
        """

        if self._settings.window_geometry:
            try:
                self.restoreGeometry(QByteArray.fromHex(self._settings.window_geometry.encode("ascii")))
            except (ValueError, UnicodeEncodeError):
                pass
        if self._settings.window_state:
            try:
                self.restoreState(QByteArray.fromHex(self._settings.window_state.encode("ascii")))
            except (ValueError, UnicodeEncodeError):
                pass

    def collect_settings(self) -> AppSettings:
        """
        Returns the settings as they should be saved.

        Args:
            None

        Returns:
            AppSettings: Current settings including window geometry.
        """

        self._persist_diff_options()
        self._settings.sort_mode = self._sidebar.sort_mode
        self._settings.window_geometry = bytes(self.saveGeometry().toHex()).decode("ascii")
        self._settings.window_state = bytes(self.saveState().toHex()).decode("ascii")
        return self._settings

    def changeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """
        Refreshes when the window is given focus.

        Args:
            event: Change event.

        Returns:
            None
        """

        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._refresh_on_focus()

    def showEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """
        Offers the repository search the first time the window appears.

        The constructor is the wrong place for it: a window that was built but
        never shown has nobody in front of it to answer, which is exactly the
        situation in the tests and in the screenshot script.

        Args:
            event: Show event.

        Returns:
            None
        """

        super().showEvent(event)
        if self._discovery_offered_this_run:
            return
        self._discovery_offered_this_run = True
        # A moment later, so the window is painted before the dialog covers it.
        QTimer.singleShot(DISCOVERY_OFFER_DELAY_MS, self._maybe_offer_discovery)

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """
        Saves state and stops background work before closing.

        Args:
            event: Close event.

        Returns:
            None
        """

        self._closing = True
        self._switch_scan_timer.stop()
        self._pending_scan_key = None
        self._scheduler.stop()
        self._pull_requests.stop()
        self._github_runner.stop()
        if self._update_thread is not None:
            self._update_thread.quit()
            self._update_thread.wait(2000)
            self._update_thread = None
        self._github.close()
        self._scans.wait(5000)
        self._save_registry()
        save_settings(self.collect_settings())
        super().closeEvent(event)
