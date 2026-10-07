"""
Tests for the way from "not signed in to GitHub" to signing in.

The note used to say what was missing and stop there: an OK button, and a hint
pointing at a settings field that no longer is the way to sign in. Both places
that say it now carry the step itself:

* **The GitHub tab** shows a "Sign in to GitHub" button on its note.
* **Creating a repository on GitHub** asks whether to sign in, and once that
  worked, carries on with the creation instead of making the user start over.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QPushButton

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

import i18n
from config.app_settings import AppSettings

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")


@requires_qt
class PanelTests(unittest.TestCase):
    """
    The note in the GitHub tab.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _panel(self):  # noqa: ANN202 - Qt at import time
        """
        Builds a panel without a token.

        Returns:
            GitHubPanel: The panel.
        """

        from github_api.client import GitHubClient
        from ui.github_panel import GitHubPanel

        panel = GitHubPanel(GitHubClient(""), show_avatars=False)
        self.addCleanup(panel.deleteLater)
        return panel

    def _buttons(self, panel) -> list[QPushButton]:  # noqa: ANN001
        """
        Returns the buttons on the panel's note.

        Args:
            panel: The panel.

        Returns:
            list[QPushButton]: The note's buttons.
        """

        return panel._notice.findChildren(QPushButton)

    def test_the_note_offers_to_sign_in(self) -> None:
        panel = self._panel()
        labels = [button.text() for button in self._buttons(panel)]
        self.assertEqual([i18n.t("signin.menu")], labels)

    def test_the_button_asks_the_window_to_sign_in(self) -> None:
        panel = self._panel()
        asked: list[bool] = []
        panel.signin_requested.connect(lambda: asked.append(True))
        self._buttons(panel)[0].click()
        self.assertEqual([True], asked)

    def test_other_notes_carry_no_sign_in_button(self) -> None:
        panel = self._panel()
        panel.show_message("github.not_github", "github.not_github_hint", "info")
        self.assertEqual([], [b for b in self._buttons(panel) if b.isVisibleTo(panel)])

    def test_the_hint_no_longer_points_at_the_settings(self) -> None:
        self.assertNotIn("settings", i18n.t("github.no_token_hint").lower())


@requires_qt
class CreateRepositoryTests(unittest.TestCase):
    """
    "New repository on GitHub" without a login.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _window(self):  # noqa: ANN202 - Qt at import time
        """
        Builds a window that is not signed in, with the network call recorded.

        Returns:
            tuple: The window and the list of submitted calls.
        """

        from ui.main_window import MainWindow

        base = Path(tempfile.mkdtemp(prefix="branchly-test-"))
        os.environ["XDG_CONFIG_HOME"] = str(base / "config")
        window = MainWindow(AppSettings(auto_check_minutes=0, github_enabled=False, language="en"))
        self.addCleanup(window.close)
        window._github.set_token("")
        submitted: list[object] = []
        window._github_runner.submit = lambda work, done: submitted.append(work)
        return window, submitted

    def test_declining_stops_without_signing_in(self) -> None:
        window, submitted = self._window()
        opened: list[bool] = []
        window.ask_signin_for_repository = lambda: False
        window._open_signin = lambda: opened.append(True) or True
        window._create_github_repository()
        self.assertEqual(([], []), (opened, submitted))

    def test_signing_in_carries_on_with_the_creation(self) -> None:
        window, submitted = self._window()

        def sign_in() -> bool:
            window._github.set_token("ghp_test_token")
            return True

        window.ask_signin_for_repository = lambda: True
        window._open_signin = sign_in
        window._create_github_repository()
        self.assertEqual(1, len(submitted))

    def test_a_cancelled_sign_in_stops(self) -> None:
        window, submitted = self._window()
        window.ask_signin_for_repository = lambda: True
        window._open_signin = lambda: False
        window._create_github_repository()
        self.assertEqual([], submitted)


if __name__ == "__main__":
    unittest.main()
