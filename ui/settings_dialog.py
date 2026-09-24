"""
The settings dialog.

Four tabs, each holding one kind of decision. Everything is applied to a copy of
the settings and only handed back when the user accepts, so cancelling really
does leave things as they were.

The GitHub tab is the one with side effects: saving a token writes it to the
system keychain and immediately asks the API who it belongs to, because a token
that turns out to be wrong is worth finding out about now rather than at the next
background check.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import i18n
from config.app_settings import (
    AUTO_CHECK_PRESETS,
    DIFF_SIDE_BY_SIDE,
    DIFF_UNIFIED,
    AppSettings,
)
from github_api import token as token_store
from github_api.client import (
    SCOPE_DELETE_REPO,
    SCOPE_REPO,
    SCOPE_WORKFLOW,
    GitHubClient,
)
from models.sort import SORT_MODE_LABEL_KEYS, VALID_SORT_MODES
from ui.widgets import InlineMessage


class _TokenCheckWorker(QObject):
    """
    Asks GitHub who a token belongs to, off the UI thread.

    It also reports what the token may not do and how much of the hourly budget
    is left. Both are questions the user otherwise only gets answered by an
    action failing halfway through.

    Attributes:
        finished: Emitted with ``(login, scopes, missing, budget, error_key)``.
    """

    finished = Signal(str, str, str, str, str)

    def __init__(self, token: str) -> None:
        """
        Args:
            token: Token to verify.
        """

        super().__init__()
        self._token = token

    def run(self) -> None:
        """
        Performs the check.

        Returns:
            None
        """

        client = GitHubClient(self._token)
        try:
            viewer, error = client.viewer()
            if viewer is None:
                self.finished.emit("", "", "", "", error or "settings.github_token_invalid")
                return
            missing = client.missing_scopes_for(
                SCOPE_REPO, SCOPE_WORKFLOW, SCOPE_DELETE_REPO, "read:org"
            )
            budget = ""
            limits = client.rate_limit_state()
            if limits.ok:
                core = limits.data.get("resources", {})
                core = core.get("core", {}) if isinstance(core, dict) else {}
                if isinstance(core, dict) and isinstance(core.get("limit"), int):
                    budget = i18n.t(
                        "settings.github_budget",
                        remaining=core.get("remaining", 0),
                        limit=core.get("limit", 0),
                    )
        finally:
            client.close()
        self.finished.emit(viewer.login, viewer.scope_summary, ", ".join(missing), budget, "")


class SettingsDialog(QDialog):
    """
    Collects every user preference.
    """

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        """
        Args:
            settings: Current settings; a normalized copy is edited.
            parent: Parent widget.
        """

        super().__init__(parent)
        self.setWindowTitle(i18n.t("settings.title"))
        self.setMinimumWidth(560)

        self._original = settings.normalized()
        self._thread: QThread | None = None
        self._worker: _TokenCheckWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        tabs = QTabWidget(self)
        tabs.addTab(self._build_general_tab(), i18n.t("settings.tab_general"))
        tabs.addTab(self._build_checks_tab(), i18n.t("settings.tab_checks"))
        tabs.addTab(self._build_diff_tab(), i18n.t("settings.tab_diff"))
        tabs.addTab(self._build_github_tab(), i18n.t("settings.tab_github"))
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(self)
        save = buttons.addButton(i18n.t("action.save"), QDialogButtonBox.ButtonRole.AcceptRole)
        save.setObjectName("Primary")
        buttons.addButton(i18n.t("action.cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_token_state()

    # -------------------------------------------------------------------- tabs

    def _build_general_tab(self) -> QWidget:
        """
        Builds the language and sorting tab.

        Returns:
            QWidget: The tab.
        """

        page = QWidget(self)
        form = QFormLayout(page)
        form.setSpacing(10)

        self._language = QComboBox(page)
        self._language.setToolTip(i18n.t("tip.settings_language"))
        for code, label in i18n.available_languages():
            self._language.addItem(label, code)
        index = self._language.findData(self._original.language)
        if index >= 0:
            self._language.setCurrentIndex(index)
        form.addRow(i18n.t("settings.language"), self._language)

        self._sort = QComboBox(page)
        self._sort.setToolTip(i18n.t("tip.settings_sort"))
        for mode in VALID_SORT_MODES:
            self._sort.addItem(i18n.t(SORT_MODE_LABEL_KEYS[mode]), mode)
        index = self._sort.findData(self._original.sort_mode)
        if index >= 0:
            self._sort.setCurrentIndex(index)
        form.addRow(i18n.t("settings.sort_mode"), self._sort)

        self._confirm = QCheckBox(i18n.t("settings.confirm_destructive"), page)
        self._confirm.setToolTip(i18n.t("tip.settings_confirm"))
        self._confirm.setChecked(self._original.confirm_destructive)
        form.addRow("", self._confirm)

        hint = QLabel(i18n.t("settings.confirm_destructive_hint"), page)
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        form.addRow("", hint)

        repair = QPushButton(i18n.t("settings.repair_deps"), page)
        repair.setToolTip(i18n.t("tip.settings_repair"))
        repair.clicked.connect(self._open_repair_installer)
        form.addRow("", repair)

        repair_hint = QLabel(i18n.t("settings.repair_deps_hint"), page)
        repair_hint.setObjectName("Muted")
        repair_hint.setWordWrap(True)
        form.addRow("", repair_hint)
        return page

    def _open_repair_installer(self) -> None:
        """
        Launches the dependency installer in a separate process.

        Returns:
            None
        """

        answer = QMessageBox.question(
            self,
            i18n.t("settings.repair_deps_confirm_title"),
            i18n.t("settings.repair_deps_confirm"),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        from installer_ui import launch_installer_subprocess

        launch_installer_subprocess()

    def _build_checks_tab(self) -> QWidget:
        """
        Builds the automatic-check tab.

        Returns:
            QWidget: The tab.
        """

        page = QWidget(self)
        form = QFormLayout(page)
        form.setSpacing(10)

        self._interval = QComboBox(page)
        self._interval.setToolTip(i18n.t("tip.settings_interval"))
        for minutes in AUTO_CHECK_PRESETS:
            label = (
                i18n.t("settings.auto_check_off")
                if minutes == 0
                else i18n.t("settings.auto_check_every", count=minutes)
            )
            self._interval.addItem(label, minutes)
        index = self._interval.findData(self._original.auto_check_minutes)
        if index < 0:
            # A hand-edited config may hold a value that is not a preset; keep it.
            self._interval.addItem(
                i18n.t("settings.auto_check_every", count=self._original.auto_check_minutes),
                self._original.auto_check_minutes,
            )
            index = self._interval.count() - 1
        self._interval.setCurrentIndex(index)
        form.addRow(i18n.t("settings.auto_check"), self._interval)

        hint = QLabel(i18n.t("settings.auto_check_hint"), page)
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        form.addRow("", hint)

        self._check_online = QCheckBox(i18n.t("settings.check_online"), page)
        self._check_online.setToolTip(i18n.t("tip.settings_check_online"))
        self._check_online.setChecked(self._original.check_online_automatically)
        form.addRow("", self._check_online)

        online_hint = QLabel(i18n.t("settings.check_online_hint"), page)
        online_hint.setObjectName("Muted")
        online_hint.setWordWrap(True)
        form.addRow("", online_hint)

        self._check_updates = QCheckBox(i18n.t("settings.check_updates"), page)
        self._check_updates.setToolTip(i18n.t("tip.settings_check_updates"))
        self._check_updates.setChecked(self._original.check_updates)
        form.addRow("", self._check_updates)

        updates_hint = QLabel(i18n.t("settings.check_updates_hint"), page)
        updates_hint.setObjectName("Muted")
        updates_hint.setWordWrap(True)
        form.addRow("", updates_hint)
        return page

    def _build_diff_tab(self) -> QWidget:
        """
        Builds the comparison-defaults tab.

        Returns:
            QWidget: The tab.
        """

        page = QWidget(self)
        form = QFormLayout(page)
        form.setSpacing(10)

        self._diff_mode = QComboBox(page)
        self._diff_mode.setToolTip(i18n.t("tip.diff_mode"))
        self._diff_mode.addItem(i18n.t("diff.mode_side_by_side"), DIFF_SIDE_BY_SIDE)
        self._diff_mode.addItem(i18n.t("diff.mode_unified"), DIFF_UNIFIED)
        index = self._diff_mode.findData(self._original.diff_mode)
        if index >= 0:
            self._diff_mode.setCurrentIndex(index)
        form.addRow(i18n.t("settings.diff_default"), self._diff_mode)

        self._diff_whitespace = QCheckBox(i18n.t("diff.ignore_whitespace"), page)
        self._diff_whitespace.setToolTip(i18n.t("tip.diff_whitespace"))
        self._diff_whitespace.setChecked(self._original.diff_ignore_whitespace)
        form.addRow("", self._diff_whitespace)

        self._diff_words = QCheckBox(i18n.t("diff.word_level"), page)
        self._diff_words.setToolTip(i18n.t("tip.diff_words"))
        self._diff_words.setChecked(self._original.diff_word_level)
        form.addRow("", self._diff_words)
        return page

    def _build_github_tab(self) -> QWidget:
        """
        Builds the token and GitHub-feature tab.

        Returns:
            QWidget: The tab.
        """

        page = QWidget(self)
        column = QVBoxLayout(page)
        column.setSpacing(10)

        self._github_enabled = QCheckBox(i18n.t("settings.github_enabled"), page)
        self._github_enabled.setToolTip(i18n.t("tip.settings_github_enabled"))
        self._github_enabled.setChecked(self._original.github_enabled)
        column.addWidget(self._github_enabled)

        self._show_avatars = QCheckBox(i18n.t("settings.show_avatars"), page)
        self._show_avatars.setToolTip(i18n.t("tip.settings_avatars"))
        self._show_avatars.setChecked(self._original.show_avatars)
        column.addWidget(self._show_avatars)

        form = QFormLayout()
        form.setSpacing(8)
        self._token = QLineEdit(page)
        self._token.setToolTip(i18n.t("tip.settings_token"))
        self._token.setEchoMode(QLineEdit.EchoMode.Password)
        self._token.setPlaceholderText(i18n.t("settings.github_token_placeholder"))
        form.addRow(i18n.t("settings.github_token"), self._token)
        column.addLayout(form)

        hint = QLabel(i18n.t("settings.github_token_hint"), page)
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        column.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._save_token = QPushButton(i18n.t("settings.github_token_save"), page)
        self._save_token.setToolTip(i18n.t("tip.settings_token_save"))
        self._save_token.clicked.connect(self._on_save_token)
        self._remove_token = QPushButton(i18n.t("settings.github_token_remove"), page)
        self._remove_token.setToolTip(i18n.t("tip.settings_token_remove"))
        self._remove_token.clicked.connect(self._on_remove_token)
        row.addWidget(self._save_token)
        row.addWidget(self._remove_token)
        row.addStretch(1)
        column.addLayout(row)

        self._token_notice = InlineMessage("", "", "info", page)
        column.addWidget(self._token_notice)
        column.addStretch(1)
        return page

    # ------------------------------------------------------------------- token

    def _refresh_token_state(self) -> None:
        """
        Updates the token message from what is actually stored.

        Returns:
            None
        """

        state = token_store.state()
        if not state.available:
            self._token_notice.set_message(
                i18n.t("settings.github_token_no_keyring"),
                i18n.t("settings.github_token_no_keyring_hint"),
                "warning",
            )
            self._token.setEnabled(False)
            self._save_token.setEnabled(False)
            self._remove_token.setEnabled(False)
            return
        self._remove_token.setEnabled(state.stored)
        if state.stored:
            self._token_notice.set_message(i18n.t("settings.github_token"), "", "success")
            self._verify_token(token_store.load())
        else:
            self._token_notice.set_message(i18n.t("settings.github_token_none"), "", "info")

    def _on_save_token(self) -> None:
        """
        Stores the typed token and verifies it.

        Returns:
            None
        """

        candidate = self._token.text().strip()
        if not candidate:
            return
        if not token_store.looks_like_a_token(candidate):
            self._token_notice.set_message(
                i18n.t("settings.github_token_invalid"),
                i18n.t("settings.github_token_placeholder"),
                "warning",
            )
            return
        if not token_store.save(candidate):
            self._token_notice.set_message(
                i18n.t("settings.github_token_no_keyring"),
                i18n.t("settings.github_token_no_keyring_hint"),
                "danger",
            )
            return
        self._token.clear()
        self._remove_token.setEnabled(True)
        self._verify_token(candidate)

    def _on_remove_token(self) -> None:
        """
        Deletes the stored token.

        Returns:
            None
        """

        token_store.delete()
        self._token.clear()
        self._remove_token.setEnabled(False)
        self._token_notice.set_message(i18n.t("settings.github_token_none"), "", "info")

    def _verify_token(self, candidate: str) -> None:
        """
        Asks GitHub who the token belongs to, on a worker thread.

        Args:
            candidate: Token to check.

        Returns:
            None
        """

        if not candidate or self._thread is not None:
            return
        self._token_notice.set_message(i18n.t("sync.working"), "", "info")

        worker = _TokenCheckWorker(candidate)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_token_checked)
        self._worker = worker
        self._thread = thread
        thread.start()

    def _on_token_checked(
        self, login: str, scopes: str, missing: str, budget: str, error_key: str
    ) -> None:
        """
        Shows the outcome of a token check.

        Args:
            login: Account the token belongs to.
            scopes: Readable scope list.
            missing: Scopes the token does not carry, comma separated.
            budget: How much of the hourly request budget is left.
            error_key: Translation key when the check failed.

        Returns:
            None
        """

        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
            self._thread = None
        self._worker = None

        if error_key:
            self._token_notice.set_message(i18n.t(error_key), "", "danger")
            return

        detail = " ".join(
            part
            for part in (
                i18n.t("settings.github_missing_scopes", scopes=missing) if missing else "",
                budget,
            )
            if part
        )
        self._token_notice.set_message(
            i18n.t("settings.github_token_valid", login=login, scopes=scopes),
            detail,
            "warning" if missing else "success",
        )

    # ------------------------------------------------------------------ results

    def result_settings(self) -> AppSettings:
        """
        Builds the settings the user chose.

        Returns:
            AppSettings: A normalized copy of the edited settings.
        """

        edited = AppSettings(
            # Light and dark live in the menu bar under Appearance, where the
            # effect is visible the moment it is picked. Carried through here so
            # a trip into this dialog does not undo that choice.
            theme=self._original.theme,
            language=str(self._language.currentData() or self._original.language),
            sort_mode=str(self._sort.currentData() or self._original.sort_mode),
            auto_check_minutes=int(self._interval.currentData() or 0),
            check_online_automatically=self._check_online.isChecked(),
            check_updates=self._check_updates.isChecked(),
            # Not offered here, but a hand-edited value and everything the last
            # check found must survive a trip through this dialog.
            update_check_hours=self._original.update_check_hours,
            update_checked_at=self._original.update_checked_at,
            update_remote_commit=self._original.update_remote_commit,
            update_remote_summary=self._original.update_remote_summary,
            diff_mode=str(self._diff_mode.currentData() or self._original.diff_mode),
            diff_ignore_whitespace=self._diff_whitespace.isChecked(),
            diff_word_level=self._diff_words.isChecked(),
            github_enabled=self._github_enabled.isChecked(),
            show_avatars=self._show_avatars.isChecked(),
            confirm_destructive=self._confirm.isChecked(),
            last_repo=self._original.last_repo,
            window_geometry=self._original.window_geometry,
            window_state=self._original.window_state,
        )
        return edited.normalized()

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """
        Stops a pending token check before closing.

        Args:
            event: Close event.

        Returns:
            None
        """

        self._stop_token_check()
        super().closeEvent(event)

    def done(self, result: int) -> None:
        """
        Stops a pending token check before the dialog goes away.

        ``closeEvent`` covers the window's own close button and nothing else.
        OK, Cancel and Escape all arrive here instead, so a token check started
        moments earlier would still be running when the dialog is destroyed, and
        Qt aborts the process over a thread that outlives its owner.

        Args:
            result: The dialog's result code.

        Returns:
            None
        """

        self._stop_token_check()
        super().done(result)

    def _stop_token_check(self) -> None:
        """
        Waits for the token check thread to finish, if one is running.

        Returns:
            None
        """

        if self._thread is None:
            return
        self._thread.quit()
        self._thread.wait(2000)
        self._thread = None
