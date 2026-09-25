"""
Tests for signing in to GitHub.

The device flow is a conversation with a server, and almost everything that can
go wrong in it goes wrong quietly. GitHub answers a poll that is too early with
``authorization_pending``, one that is too frequent with ``slow_down`` and a new
interval to respect, one after the user clicked "cancel" with ``access_denied``,
and one after ten minutes with ``expired_token``. Treat any of those as a
failure and the dialog gives up while the user is still typing the code; treat
them all as "keep going" and it waits forever on a sign-in that was refused.

So the conversation is driven here against a stand-in for GitHub rather than
against GitHub, with one test per answer.

The second thing worth guarding is that neither route ever stores a token it
has not seen work. A program that looks signed in and fails on the first real
action is worse than one that says the sign-in failed.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

import i18n
from github_api import oauth

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")

STARTED = {
    "device_code": "DEV-1",
    "user_code": "WXYZ-1234",
    "verification_uri": "https://github.com/login/device",
    "interval": 5,
    "expires_in": 900,
}


class _FakeGitHub:
    """
    Answers the two OAuth endpoints from a script.
    """

    def __init__(self, answers: list[dict | None]) -> None:
        """
        Args:
            answers: One answer per request, in order. None stands for a request
                that never arrived anywhere.
        """

        self.answers = list(answers)
        self.posts: list[tuple[str, dict]] = []

    def __call__(self, url: str, body: dict) -> dict | None:
        """
        Answers one request.

        Args:
            url: Endpoint that was posted to.
            body: Form fields.

        Returns:
            dict | None: The scripted answer.
        """

        self.posts.append((url, body))
        return self.answers.pop(0) if self.answers else {"error": "unknown"}


class DeviceFlowTests(unittest.TestCase):
    """
    The conversation with GitHub.
    """

    def _install(self, answers: list[dict | None], identifier: str = "Iv1.test") -> _FakeGitHub:
        """
        Puts a stand-in in place of the real endpoints.

        Args:
            answers: Scripted answers.
            identifier: Client id to pretend is configured.

        Returns:
            _FakeGitHub: The stand-in, for inspecting what was sent.
        """

        fake = _FakeGitHub(answers)
        real_post, real_id = oauth._post, oauth.client_id
        oauth._post = fake
        oauth.client_id = lambda: identifier
        self.addCleanup(setattr, oauth, "_post", real_post)
        self.addCleanup(setattr, oauth, "client_id", real_id)
        return fake

    def test_starting_returns_a_code_for_the_user(self) -> None:
        fake = self._install([STARTED])
        login = oauth.start()
        self.assertTrue(login.ok)
        self.assertEqual("WXYZ-1234", login.user_code)
        self.assertEqual("DEV-1", login.device_code)
        self.assertIn("repo", fake.posts[0][1]["scope"])

    def test_the_permissions_are_fixed_by_branchly(self) -> None:
        # The point of this route over a pasted token: nobody has to know which
        # boxes to tick.
        fake = self._install([STARTED])
        oauth.start()
        asked = fake.posts[0][1]["scope"].split()
        self.assertEqual(sorted(oauth.GITHUB_OAUTH_SCOPES), sorted(asked))

    def test_pending_means_keep_waiting(self) -> None:
        self._install([{"error": "authorization_pending"}])
        self.assertEqual(oauth.OUTCOME_PENDING, oauth.poll("DEV-1").outcome)

    def test_slow_down_widens_the_gap(self) -> None:
        # Ignoring this gets the polling refused outright.
        self._install([{"error": "slow_down", "interval": 9}])
        answer = oauth.poll("DEV-1", interval=5)
        self.assertEqual(oauth.OUTCOME_SLOW_DOWN, answer.outcome)
        self.assertEqual(9, answer.interval)

    def test_slow_down_without_a_number_still_widens_it(self) -> None:
        self._install([{"error": "slow_down"}])
        self.assertGreater(oauth.poll("DEV-1", interval=5).interval, 5)

    def test_a_refusal_is_not_a_failure_to_retry(self) -> None:
        self._install([{"error": "access_denied"}])
        answer = oauth.poll("DEV-1")
        self.assertEqual(oauth.OUTCOME_DENIED, answer.outcome)
        self.assertEqual("signin.denied", answer.error_key)

    def test_an_expired_code_says_so(self) -> None:
        self._install([{"error": "expired_token"}])
        self.assertEqual("signin.expired", oauth.poll("DEV-1").error_key)

    def test_a_token_ends_the_conversation(self) -> None:
        self._install([{"access_token": "gho_real"}])
        answer = oauth.poll("DEV-1")
        self.assertEqual(oauth.OUTCOME_TOKEN, answer.outcome)
        self.assertEqual("gho_real", answer.token)

    def test_a_dropped_request_does_not_end_it(self) -> None:
        # A lost packet in the middle of a wait is not a refused sign-in.
        self._install([None])
        self.assertEqual(oauth.OUTCOME_PENDING, oauth.poll("DEV-1").outcome)

    def test_waiting_polls_until_the_user_agrees(self) -> None:
        self._install(
            [
                STARTED,
                {"error": "authorization_pending"},
                {"error": "slow_down", "interval": 7},
                {"access_token": "gho_real"},
            ]
        )
        slept: list[int] = []
        answer = oauth.wait_for_token(oauth.start(), sleep=slept.append)

        self.assertEqual("gho_real", answer.token)
        self.assertEqual([5, 5, 7], slept)

    def test_waiting_stops_when_the_dialog_is_closed(self) -> None:
        self._install([STARTED])
        answer = oauth.wait_for_token(oauth.start(), should_stop=lambda: True, sleep=lambda _s: None)
        # Cancelled, so no message: the user knows what they just did.
        self.assertEqual("", answer.error_key)

    def test_nothing_happens_without_a_registered_app(self) -> None:
        real = oauth.client_id
        oauth.client_id = lambda: ""
        self.addCleanup(setattr, oauth, "client_id", real)

        self.assertFalse(oauth.is_configured())
        self.assertEqual("signin.not_configured", oauth.start().error_key)
        self.assertEqual("signin.not_configured", oauth.poll("DEV-1").error_key)

    def test_the_environment_can_name_the_app(self) -> None:
        os.environ[oauth.CLIENT_ID_VARIABLE] = "Iv1.fromenvironment"
        self.addCleanup(os.environ.pop, oauth.CLIENT_ID_VARIABLE, None)
        self.assertEqual("Iv1.fromenvironment", oauth.client_id())

    def test_nonsense_numbers_from_the_server_are_survived(self) -> None:
        # The interval and the lifetime come off the wire and are used as a sleep
        # and a deadline. A zero or a word there would spin or never finish.
        self._install([{**STARTED, "interval": "nonsense", "expires_in": 0}])
        login = oauth.start()
        self.assertGreater(login.interval, 0)
        self.assertGreater(login.expires_in, 0)


@requires_qt
class SignInDialogTests(unittest.TestCase):
    """
    What the dialog offers and what it refuses to keep.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _dialog(self, configured: bool):  # noqa: ANN202 - Qt at import time
        """
        Builds a dialog with or without a registered OAuth app.

        Args:
            configured: Whether browser sign-in should be available.

        Returns:
            SignInDialog: The dialog.
        """

        from ui.signin_dialog import SignInDialog

        real = oauth.client_id
        oauth.client_id = (lambda: "Iv1.test") if configured else (lambda: "")
        self.addCleanup(setattr, oauth, "client_id", real)

        dialog = SignInDialog()
        self.addCleanup(lambda: dialog.done(0))
        return dialog

    def test_the_token_route_is_always_there(self) -> None:
        # It needs nothing set up, so it is never the fallback behind a link.
        dialog = self._dialog(configured=False)
        self.assertTrue(dialog._token.isEnabled())
        self.assertTrue(dialog._open_page.isEnabled())

    def test_the_browser_route_is_off_without_an_app_and_says_why(self) -> None:
        dialog = self._dialog(configured=False)
        self.assertFalse(dialog._browser_button.isEnabled())
        self.assertEqual(i18n.t("signin.not_configured"), dialog._browser_hint.text())

    def test_the_browser_route_is_on_with_one(self) -> None:
        dialog = self._dialog(configured=True)
        self.assertTrue(dialog._browser_button.isEnabled())

    def test_the_use_button_waits_for_a_token(self) -> None:
        dialog = self._dialog(configured=False)
        self.assertFalse(dialog._token_button.isEnabled())
        dialog._token.setText("  ")
        self.assertFalse(dialog._token_button.isEnabled())
        dialog._token.setText("ghp_something")
        self.assertTrue(dialog._token_button.isEnabled())

    def test_the_token_is_not_shown_while_it_is_typed(self) -> None:
        from PySide6.QtWidgets import QLineEdit

        dialog = self._dialog(configured=False)
        self.assertEqual(QLineEdit.EchoMode.Password, dialog._token.echoMode())

    def test_a_token_github_rejects_is_not_stored(self) -> None:
        from github_api import token as token_store

        dialog = self._dialog(configured=False)
        saved: list[str] = []
        real = token_store.save
        token_store.save = lambda value: saved.append(value) or True
        self.addCleanup(setattr, token_store, "save", real)

        dialog._on_checked("ghp_bad", "", "settings.github_token_invalid")
        self.assertEqual([], saved)
        self.assertEqual("", dialog.login)

    def test_a_token_that_names_its_owner_is_stored(self) -> None:
        from github_api import token as token_store

        dialog = self._dialog(configured=False)
        saved: list[str] = []
        real = token_store.save
        token_store.save = lambda value: saved.append(value) or True
        self.addCleanup(setattr, token_store, "save", real)

        dialog._on_checked("ghp_good", "joruf", "")
        self.assertEqual(["ghp_good"], saved)
        self.assertEqual("joruf", dialog.login)

    def test_a_keychain_that_refuses_is_reported_not_ignored(self) -> None:
        from github_api import token as token_store

        dialog = self._dialog(configured=False)
        real = token_store.save
        token_store.save = lambda _value: False
        self.addCleanup(setattr, token_store, "save", real)

        dialog._on_checked("ghp_good", "joruf", "")
        self.assertEqual("", dialog.login)
        # The dialog is never shown in a test, so ``isVisible`` stays False.
        # What matters is that it says something rather than closing quietly.
        self.assertTrue(dialog._notice.isVisibleTo(dialog))
        self.assertEqual(
            i18n.t("settings.github_keyring_missing"), dialog._notice._title.text()
        )


@requires_qt
class AccountMenuTests(unittest.TestCase):
    """
    The sign-in entry in the menu bar.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _window(self, base, stored: str):  # noqa: ANN001, ANN202 - Qt at import time
        """
        Builds a window that believes a particular token is stored.

        Args:
            base: Directory to keep the config in.
            stored: Token the keychain should report.

        Returns:
            MainWindow: The window.
        """

        from config.app_settings import AppSettings
        from github_api import token as token_store
        from ui.main_window import MainWindow

        real = token_store.load
        token_store.load = lambda: stored
        self.addCleanup(setattr, token_store, "load", real)

        os.environ["XDG_CONFIG_HOME"] = str(base / "config")
        window = MainWindow(
            AppSettings(auto_check_minutes=0, github_enabled=False, language="en")
        )
        self.addCleanup(window.close)
        return window

    def test_the_menu_bar_has_an_account_menu(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from PySide6.QtWidgets import QMenu

        with TemporaryDirectory() as base:
            window = self._window(Path(base), "")
            titles = [menu.title() for menu in window.menuBar().findChildren(QMenu)]
            self.assertIn(i18n.t("menu.account"), titles)

    def test_signed_out_offers_signing_in_and_nothing_else(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as base:
            window = self._window(Path(base), "")
            self.assertEqual(i18n.t("signin.menu"), window._signin_action.text())
            self.assertFalse(window._signout_action.isEnabled())
            self.assertEqual(i18n.t("signin.state_out"), window._account_label.text())

    def test_signed_in_says_who(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as base:
            window = self._window(Path(base), "ghp_stored")
            window._viewer_login = "joruf"
            window._refresh_account_menu()

            self.assertTrue(window._signout_action.isEnabled())
            self.assertIn("joruf", window._account_label.text())
            # Signing in again means signing in as somebody else.
            self.assertEqual(i18n.t("signin.menu_change"), window._signin_action.text())

    def test_signed_in_without_a_name_yet_still_reads_as_signed_in(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as base:
            window = self._window(Path(base), "ghp_stored")
            self.assertEqual(i18n.t("signin.state_in_unknown"), window._account_label.text())

    def test_signing_out_forgets_the_token_and_updates_the_menu(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from github_api import token as token_store

        with TemporaryDirectory() as base:
            window = self._window(Path(base), "ghp_stored")
            window._settings.confirm_destructive = False
            deleted: list[int] = []
            real_delete = token_store.delete
            token_store.delete = lambda: deleted.append(1) or True
            self.addCleanup(setattr, token_store, "delete", real_delete)
            token_store.load = lambda: ""

            window._sign_out()
            self.assertEqual([1], deleted)
            self.assertEqual("", window._viewer_login)
            self.assertFalse(window._signout_action.isEnabled())


if __name__ == "__main__":
    unittest.main()
