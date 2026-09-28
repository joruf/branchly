"""
Giving a local-only project a server.

Connecting itself is harmless: it writes one line into ``.git/config`` and
touches no file. What the dialog is really for is the step after it. If the
server already holds commits and the project here also holds commits, the next
send is refused, and the only ways past that refusal throw one side's work away.
That is worth knowing before the connection is made, not after.

So the address is checked against the server before anything is written, and
what comes back decides what the user is asked:

* **Nothing there yet.** Connect, and the first send fills it.
* **Something there, nothing here.** Connect and fetch, and the project is the
  server's.
* **Something on both sides.** The one case with a real decision in it, so the
  choices are spelled out rather than left to the next error message.
* **Not reachable.** Possibly a repository that does not exist yet, which is a
  perfectly normal thing to prepare for, so connecting stays possible and is
  labelled as the guess it is.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

import i18n
from services import remote_link
from ui.widgets import InlineMessage

# What to do once the address is set.
AFTER_NOTHING = "nothing"
AFTER_FETCH = "fetch"


class _CheckWorker(QObject):
    """
    Asks the server what it has, off the UI thread.

    Attributes:
        finished: Emitted with the ``LinkCheck``.
    """

    finished = Signal(object)

    def __init__(self, repo: Path, url: str, credentials: dict[str, str]) -> None:
        """
        Args:
            repo: Working tree path.
            url: Address to ask about.
            credentials: Login for the server.
        """

        super().__init__()
        self._repo = repo
        self._url = url
        self._credentials = credentials

    def run(self) -> None:
        """
        Performs the check.

        Returns:
            None
        """

        self.finished.emit(remote_link.check(self._repo, self._url, self._credentials))


class LinkRemoteDialog(QDialog):
    """
    Asks for a server address and says what connecting to it would mean.
    """

    def __init__(
        self,
        repo: Path,
        name: str,
        suggestion: str = "",
        current_url: str = "",
        credentials: Callable[[], dict[str, str]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            repo: Working tree path.
            name: Display name of the project.
            suggestion: Address to offer, usually built from the folder name.
            current_url: Address the project has now, empty when it has none.
            credentials: Callable returning the login for the server.
            parent: Parent widget.
        """

        super().__init__(parent)
        self._repo = repo
        self._credentials = credentials or dict
        self._check: remote_link.LinkCheck | None = None
        self._thread: QThread | None = None

        self.setWindowTitle(i18n.t("link.title"))
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        intro = QLabel(i18n.t("link.intro", name=name), self)
        intro.setWordWrap(True)
        intro.setObjectName("Muted")
        layout.addWidget(intro)

        if current_url:
            # Not destructive, but a decision, and the old address belongs on
            # screen while it is made.
            replaced = QLabel(i18n.t("link.replaces", url=current_url), self)
            replaced.setWordWrap(True)
            replaced.setObjectName("Muted")
            layout.addWidget(replaced)

        self._url = QLineEdit(suggestion or current_url, self)
        self._url.setPlaceholderText(i18n.t("link.placeholder"))
        self._url.setToolTip(i18n.t("tip.link_url"))
        self._url.textChanged.connect(self._on_url_changed)
        self._url.returnPressed.connect(self._start_check)
        layout.addWidget(self._url)

        self._check_button = QPushButton(i18n.t("link.check"), self)
        self._check_button.setToolTip(i18n.t("tip.link_check"))
        self._check_button.clicked.connect(self._start_check)
        layout.addWidget(self._check_button)

        self._notice = InlineMessage(self)
        self._notice.setVisible(False)
        layout.addWidget(self._notice)

        self._choices = QWidget(self)
        choice_box = QVBoxLayout(self._choices)
        choice_box.setContentsMargins(0, 0, 0, 0)
        choice_box.setSpacing(4)
        self._group = QButtonGroup(self)
        self._only_link = QRadioButton(i18n.t("link.only_connect"), self._choices)
        self._only_link.setToolTip(i18n.t("tip.link_only"))
        self._only_link.setChecked(True)
        self._and_fetch = QRadioButton(i18n.t("link.and_fetch"), self._choices)
        self._and_fetch.setToolTip(i18n.t("tip.link_fetch"))
        for button in (self._only_link, self._and_fetch):
            self._group.addButton(button)
            choice_box.addWidget(button)
        self._choices.setVisible(False)
        layout.addWidget(self._choices)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setText(i18n.t("link.connect"))
            ok.setObjectName("Primary")
        cancel = self._buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel is not None:
            cancel.setText(i18n.t("action.cancel"))
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._on_url_changed(self._url.text())

    # ------------------------------------------------------------------ results

    @property
    def url(self) -> str:
        """
        Returns the address the user settled on.

        Returns:
            str: The trimmed address.
        """

        return self._url.text().strip()

    @property
    def follow_up(self) -> str:
        """
        Returns what should happen once the address is set.

        Returns:
            str: ``AFTER_FETCH`` when the server's version should be fetched,
                ``AFTER_NOTHING`` otherwise.
        """

        if self._choices.isVisibleTo(self) and self._and_fetch.isChecked():
            return AFTER_FETCH
        return AFTER_NOTHING

    # ------------------------------------------------------------------ the check

    def _on_url_changed(self, text: str) -> None:
        """
        Resets the verdict whenever the address is edited.

        Args:
            text: The address as it stands.

        Returns:
            None
        """

        self._check = None
        self._choices.setVisible(False)
        self._notice.setVisible(False)
        usable = bool(text.strip())
        self._check_button.setEnabled(usable and self._thread is None)
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            # Connecting without asking the server is allowed, because a
            # repository that does not exist yet cannot answer. It just should
            # not be the effortless path.
            ok.setEnabled(usable)

    def _start_check(self) -> None:
        """
        Asks the server what it has.

        Returns:
            None
        """

        url = self.url
        if not url or self._thread is not None:
            return
        self._check_button.setEnabled(False)
        self._notice.set_message(i18n.t("link.checking"), "", "info")
        self._notice.setVisible(True)

        worker = _CheckWorker(self._repo, url, self._credentials())
        worker.finished.connect(self._on_checked)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_checked(self, outcome: object) -> None:
        """
        Puts the verdict on screen.

        Args:
            outcome: The ``LinkCheck`` the worker produced.

        Returns:
            None
        """

        self._stop_thread()
        self._check = outcome
        self._check_button.setEnabled(bool(self.url))

        state = outcome.state
        if state == remote_link.STATE_INVALID:
            self._say("link.invalid", "link.invalid_hint", "danger")
            return
        if state == remote_link.STATE_UNREACHABLE:
            self._say("link.unreachable", "link.unreachable_hint", "warning")
            return
        if state == remote_link.STATE_EMPTY:
            self._say("link.empty", "link.empty_hint", "success")
            return

        branches = ", ".join(outcome.branches[:6])
        if not outcome.collides:
            # Nothing here yet, so the server's version simply becomes this one.
            self._say("link.server_only", "link.server_only_hint", "info", branches=branches)
            self._choices.setVisible(True)
            self._and_fetch.setChecked(True)
            return

        self._say("link.both_sides", "link.both_sides_hint", "warning", branches=branches)
        self._choices.setVisible(True)
        self._only_link.setChecked(True)

    def _say(self, title_key: str, detail_key: str, token: str, **params: object) -> None:
        """
        Shows one verdict.

        Args:
            title_key: Translation key for the headline.
            detail_key: Translation key for the explanation.
            token: Theme token naming the accent colour.
            **params: Placeholder values.

        Returns:
            None
        """

        self._notice.set_message(
            i18n.t(title_key, **params), i18n.t(detail_key, **params), token
        )
        self._notice.setVisible(True)

    # ---------------------------------------------------------------- the plumbing

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

    def done(self, result: int) -> None:
        """
        Stops a running check before the dialog goes away.

        Args:
            result: The dialog's result code.

        Returns:
            None
        """

        self._stop_thread()
        super().done(result)

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """
        Stops a running check before the window closes.

        Args:
            event: Close event.

        Returns:
            None
        """

        self._stop_thread()
        super().closeEvent(event)
