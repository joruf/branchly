"""
Signing in to GitHub.

Two routes, because one of them needs something Branchly cannot ship.

**With the browser**, through GitHub's device flow: Branchly shows a short code,
the user types it into a page in their own browser, and Branchly waits until
GitHub says they agreed. Nobody handles a credential and the scopes are fixed
rather than left to whichever boxes somebody ticked. It needs a registered OAuth
app, whose client id is public by design but still names one particular app.

**With a token**, pasted from the GitHub settings page, which the dialog opens
with the right boxes already ticked. Needs nothing and works everywhere, which
is why it is always offered and not hidden behind a "having trouble?" link.

Either way the result is one thing: a token in the system keychain, checked
against the API before it is kept. A token that cannot name its owner is not
stored, because the alternative is a program that looks signed in and fails on
the first action.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import i18n
from config.theme import get_theme_colors
from constants import GITHUB_TOKEN_PAGE
from github_api import oauth
from github_api import token as token_store
from github_api.client import GitHubClient
from ui.widgets import InlineMessage


class _DeviceFlowWorker(QObject):
    """
    Waits for the user to finish in the browser, off the UI thread.

    Attributes:
        started_flow: Emitted with ``(user_code, verification_url, error_key)``.
        finished: Emitted with ``(token, error_key)``.
    """

    started_flow = Signal(str, str, str)
    finished = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self._cancelled = False

    def cancel(self) -> None:
        """
        Asks the wait to stop at the next opportunity.

        Returns:
            None
        """

        self._cancelled = True

    def run(self) -> None:
        """
        Starts the flow and waits for its answer.

        Returns:
            None
        """

        login = oauth.start()
        self.started_flow.emit(login.user_code, login.verification_url, login.error_key)
        if not login.ok:
            self.finished.emit("", login.error_key or "signin.failed")
            return

        answer = oauth.wait_for_token(login, should_stop=lambda: self._cancelled)
        if answer.outcome == oauth.OUTCOME_TOKEN:
            self.finished.emit(answer.token, "")
            return
        self.finished.emit("", answer.error_key)


class _TokenCheckWorker(QObject):
    """
    Asks GitHub who a token belongs to, off the UI thread.

    Attributes:
        finished: Emitted with ``(login_name, error_key)``.
    """

    finished = Signal(str, str)

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
        finally:
            client.close()
        if viewer is None:
            self.finished.emit("", error or "settings.github_token_invalid")
            return
        self.finished.emit(viewer.login, "")


class SignInDialog(QDialog):
    """
    Gets a working GitHub token into the keychain, one way or the other.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """
        Args:
            parent: Parent widget.
        """

        super().__init__(parent)
        self.setWindowTitle(i18n.t("signin.title"))
        self.setMinimumWidth(480)
        self._thread: QThread | None = None
        self._worker: QObject | None = None
        self._login = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        intro = QLabel(i18n.t("signin.intro"), self)
        intro.setWordWrap(True)
        intro.setObjectName("Muted")
        layout.addWidget(intro)

        self._notice = InlineMessage(self)
        self._notice.setVisible(False)
        layout.addWidget(self._notice)

        layout.addWidget(self._build_browser_section())
        layout.addWidget(self._build_token_section())

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        # Qt's own label for a standard button comes from Qt's translations,
        # which Branchly does not ship. Left alone it reads "Close" in a German
        # window.
        close_button = self._buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_button is not None:
            close_button.setText(i18n.t("action.close"))
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._update_enabled()

    # -------------------------------------------------------------- the sections

    def _build_browser_section(self) -> QWidget:
        """
        Builds the browser route.

        Returns:
            QWidget: The section.
        """

        holder = QWidget(self)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        heading = QLabel(i18n.t("signin.browser_title"), holder)
        heading.setObjectName("Heading")
        column.addWidget(heading)

        self._browser_hint = QLabel("", holder)
        self._browser_hint.setWordWrap(True)
        self._browser_hint.setObjectName("Muted")
        column.addWidget(self._browser_hint)

        code_row = QHBoxLayout()
        code_row.setSpacing(8)
        self._code = QLabel("", holder)
        self._code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._code.setVisible(False)
        code_row.addWidget(self._code)

        self._copy_code = QPushButton(i18n.t("signin.copy_code"), holder)
        self._copy_code.setToolTip(i18n.t("tip.signin_copy_code"))
        self._copy_code.clicked.connect(self._copy_user_code)
        self._copy_code.setVisible(False)
        code_row.addWidget(self._copy_code)
        code_row.addStretch(1)
        column.addLayout(code_row)

        self._browser_button = QPushButton(i18n.t("signin.browser_start"), holder)
        self._browser_button.setObjectName("Primary")
        self._browser_button.setToolTip(i18n.t("tip.signin_browser"))
        self._browser_button.clicked.connect(self._start_browser_flow)
        column.addWidget(self._browser_button)
        return holder

    def _build_token_section(self) -> QWidget:
        """
        Builds the token route.

        Returns:
            QWidget: The section.
        """

        holder = QWidget(self)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        heading = QLabel(i18n.t("signin.token_title"), holder)
        heading.setObjectName("Heading")
        column.addWidget(heading)

        hint = QLabel(i18n.t("signin.token_hint"), holder)
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        column.addWidget(hint)

        self._open_page = QPushButton(i18n.t("signin.token_open"), holder)
        self._open_page.setToolTip(i18n.t("tip.signin_token_page"))
        self._open_page.clicked.connect(
            lambda: QDesktopServices.openUrl(_url(GITHUB_TOKEN_PAGE))
        )
        column.addWidget(self._open_page)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._token = QLineEdit(holder)
        self._token.setEchoMode(QLineEdit.EchoMode.Password)
        self._token.setPlaceholderText(i18n.t("signin.token_placeholder"))
        self._token.setToolTip(i18n.t("tip.signin_token_field"))
        self._token.textChanged.connect(lambda _text: self._update_enabled())
        self._token.returnPressed.connect(self._use_token)
        row.addWidget(self._token, 1)

        self._token_button = QPushButton(i18n.t("signin.token_use"), holder)
        self._token_button.setToolTip(i18n.t("tip.signin_token_use"))
        self._token_button.clicked.connect(self._use_token)
        row.addWidget(self._token_button)
        column.addLayout(row)
        return holder

    # --------------------------------------------------------------- the results

    @property
    def login(self) -> str:
        """
        Returns who was signed in.

        Returns:
            str: The GitHub login name, empty when nobody was.
        """

        return self._login

    # ---------------------------------------------------------------- the routes

    def _start_browser_flow(self) -> None:
        """
        Asks GitHub for a code and waits for the user to type it in.

        Returns:
            None
        """

        if self._thread is not None:
            return
        self._browser_button.setEnabled(False)
        self._browser_hint.setText(i18n.t("signin.browser_starting"))
        self._notice.setVisible(False)

        worker = _DeviceFlowWorker()
        worker.started_flow.connect(self._on_flow_started)
        worker.finished.connect(self._on_token_received)
        self._run(worker)

    def _use_token(self) -> None:
        """
        Checks a pasted token and keeps it when it works.

        Returns:
            None
        """

        pasted = self._token.text().strip()
        if not pasted or self._thread is not None:
            return
        self._token_button.setEnabled(False)
        self._notice.set_message(i18n.t("signin.checking"), "", "info")
        self._notice.setVisible(True)

        worker = _TokenCheckWorker(pasted)
        worker.finished.connect(lambda name, error: self._on_checked(pasted, name, error))
        self._run(worker)

    def _run(self, worker: QObject) -> None:
        """
        Puts a worker on its own thread and starts it.

        Args:
            worker: The worker; it must have a ``run`` slot.

        Returns:
            None
        """

        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        self._thread = thread
        self._worker = worker
        thread.start()

    # --------------------------------------------------------------- the answers

    def _on_flow_started(self, code: str, url: str, error_key: str) -> None:
        """
        Shows the code and opens the browser.

        Args:
            code: What the user has to type in.
            url: Where to type it.
            error_key: Why the flow could not be started, empty on success.

        Returns:
            None
        """

        if error_key:
            return
        colors = get_theme_colors()
        self._code.setText(code)
        self._code.setStyleSheet(
            f"color: {colors.text}; background-color: {colors.surface_alt};"
            f"border: 1px solid {colors.border}; border-radius: 5px;"
            "padding: 6px 12px; font-size: 18px; font-weight: 700; letter-spacing: 3px;"
        )
        self._code.setVisible(True)
        self._copy_code.setVisible(True)
        self._browser_hint.setText(i18n.t("signin.browser_waiting", url=url))
        QGuiApplication.clipboard().setText(code)
        QDesktopServices.openUrl(_url(url))

    def _on_token_received(self, token: str, error_key: str) -> None:
        """
        Handles the end of the browser flow.

        Args:
            token: The token, empty when the flow did not produce one.
            error_key: Why it did not, empty on success.

        Returns:
            None
        """

        self._stop_thread()
        self._browser_button.setEnabled(True)
        if not token:
            if error_key:
                self._fail(error_key)
            return
        # A token from the device flow is GitHub's own answer, so it is known
        # good. It still gets named, because the dialog reports who was signed in.
        self._check_and_store(token)

    def _check_and_store(self, token: str) -> None:
        """
        Asks who a token belongs to and keeps it when the answer arrives.

        Args:
            token: Token to verify and store.

        Returns:
            None
        """

        worker = _TokenCheckWorker(token)
        worker.finished.connect(lambda name, error: self._on_checked(token, name, error))
        self._run(worker)

    def _on_checked(self, token: str, login: str, error_key: str) -> None:
        """
        Stores a token that named its owner.

        Args:
            token: The token that was checked.
            login: Who it belongs to, empty when the check failed.
            error_key: Why it failed, empty on success.

        Returns:
            None
        """

        self._stop_thread()
        self._token_button.setEnabled(True)
        if not login:
            self._fail(error_key or "settings.github_token_invalid")
            return
        if not token_store.save(token):
            self._fail("settings.github_keyring_missing")
            return
        self._login = login
        self.accept()

    def _fail(self, error_key: str) -> None:
        """
        Shows why signing in did not work.

        Args:
            error_key: Translation key for the reason.

        Returns:
            None
        """

        self._notice.set_message(i18n.t(error_key), "", "danger")
        self._notice.setVisible(True)
        self._update_enabled()

    # ---------------------------------------------------------------- the plumbing

    def _copy_user_code(self) -> None:
        """
        Puts the code on the clipboard again.

        Returns:
            None
        """

        QGuiApplication.clipboard().setText(self._code.text())

    def _update_enabled(self) -> None:
        """
        Matches the buttons to what is currently possible.

        Returns:
            None
        """

        configured = oauth.is_configured()
        self._browser_button.setEnabled(configured and self._thread is None)
        if not configured:
            self._browser_button.setToolTip(i18n.t("signin.not_configured"))
            self._browser_hint.setText(i18n.t("signin.not_configured"))
        elif not self._code.isVisible():
            self._browser_hint.setText(i18n.t("signin.browser_hint"))
        self._token_button.setEnabled(bool(self._token.text().strip()) and self._thread is None)

    def _stop_thread(self) -> None:
        """
        Winds the worker thread down.

        Returns:
            None
        """

        if self._thread is None:
            return
        self._thread.quit()
        self._thread.wait(5000)
        self._thread = None
        self._worker = None

    def done(self, result: int) -> None:
        """
        Stops a waiting sign-in before the dialog goes away.

        Qt aborts the process over a thread that outlives its owner, and the
        device flow's thread spends most of its life asleep between polls.

        Args:
            result: The dialog's result code.

        Returns:
            None
        """

        worker = self._worker
        if isinstance(worker, _DeviceFlowWorker):
            worker.cancel()
        self._stop_thread()
        super().done(result)

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """
        Stops a waiting sign-in before the window closes.

        Args:
            event: Close event.

        Returns:
            None
        """

        worker = self._worker
        if isinstance(worker, _DeviceFlowWorker):
            worker.cancel()
        self._stop_thread()
        super().closeEvent(event)


def _url(text: str) -> QUrl:
    """
    Wraps a string as a URL Qt can open.

    Args:
        text: The address.

    Returns:
        QUrl: The address as Qt wants it.
    """

    return QUrl(text)
