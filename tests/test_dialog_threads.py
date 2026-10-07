"""
Tests that the sign-in and clone dialogs close when their background work ends.

Both dialogs do their slow part on a QThread and close themselves once it
reports back. The answer used to be connected to a lambda, and PySide runs a
lambda on the thread that emits, which is the worker's own. The handler then
waited for that very thread to end ("QThread::wait: Thread tried to wait on
itself") and closed the dialog from outside the GUI thread, and the window
froze after "Save". The answer now goes to a method of the dialog, which Qt
queues to the GUI thread.

Each test runs the real event loop with a deadline, so a regression shows up as
a failure instead of a suite that never ends.
"""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

import i18n
from tests.support import requires_git, temp_repo_pair

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")

DEADLINE_MS = 20_000


def run_until_finished(dialog, start) -> dict[str, object]:  # noqa: ANN001
    """
    Shows a dialog, starts its work and spins the event loop until it closes.

    Args:
        dialog: The dialog.
        start: Callable that kicks off the background work.

    Returns:
        dict[str, object]: ``result`` (the dialog's result code, None when the
            deadline passed first).
    """

    app = QApplication.instance()
    outcome: dict[str, object] = {"result": None}
    dialog.finished.connect(lambda code: (outcome.update(result=code), QTimer.singleShot(0, app.quit)))
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(app.quit)
    deadline.start(DEADLINE_MS)
    dialog.show()
    QTimer.singleShot(0, start)
    app.exec()
    deadline.stop()
    return outcome


@requires_qt
class SignInTests(unittest.TestCase):
    """
    Saving a pasted token.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def test_saving_a_token_closes_the_dialog_from_the_gui_thread(self) -> None:
        import ui.signin_dialog as module
        from github_api import token as token_store

        class Viewer:
            login = "octo"

        class FakeClient:
            def __init__(self, _token: str) -> None:
                pass

            def viewer(self):  # noqa: ANN202
                return Viewer(), ""

            def close(self) -> None:
                pass

        handled: list[str] = []
        real_client, real_save = module.GitHubClient, token_store.save
        real_checked = module.SignInDialog._on_checked
        module.GitHubClient = FakeClient
        token_store.save = lambda _token: True

        def spy(dialog, token, login, error) -> None:  # noqa: ANN001
            handled.append(threading.current_thread().name)
            real_checked(dialog, token, login, error)

        module.SignInDialog._on_checked = spy
        self.addCleanup(setattr, module, "GitHubClient", real_client)
        self.addCleanup(setattr, token_store, "save", real_save)
        self.addCleanup(setattr, module.SignInDialog, "_on_checked", real_checked)

        dialog = module.SignInDialog()
        self.addCleanup(dialog.deleteLater)
        dialog._token.setText("ghp_" + "a" * 36)
        outcome = run_until_finished(dialog, dialog._use_token)

        self.assertEqual(dialog.DialogCode.Accepted, outcome["result"])
        self.assertEqual([threading.main_thread().name], handled)
        self.assertEqual("octo", dialog.login)


@requires_qt
@requires_git
class CloneTests(unittest.TestCase):
    """
    Cloning a repository.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def test_a_finished_clone_closes_the_dialog(self) -> None:
        from ui.clone_dialog import CloneDialog

        with temp_repo_pair() as (_first, _second, origin), tempfile.TemporaryDirectory() as parent:
            dialog = CloneDialog([], origin.as_uri())
            self.addCleanup(dialog.deleteLater)
            dialog._parent_dir.setText(parent)
            dialog._folder.setText("copy")

            outcome = run_until_finished(dialog, dialog._start)

            self.assertEqual(dialog.DialogCode.Accepted, outcome["result"])
            self.assertEqual(Path(parent) / "copy", dialog.cloned_path)
            self.assertTrue((Path(parent) / "copy" / ".git").exists())

    def test_an_address_given_up_front_suggests_the_folder(self) -> None:
        # The address used to be filled in before the folder field existed, so
        # the suggestion failed with an AttributeError and the field stayed empty.
        from ui.clone_dialog import CloneDialog

        dialog = CloneDialog([], "https://github.com/joruf/mindor.git")
        self.addCleanup(dialog.deleteLater)
        self.assertEqual("mindor", dialog._folder.text())


if __name__ == "__main__":
    unittest.main()
