"""
Tests for fixing a branch that is both ahead and behind.

A project that has its own unsent commits and also waits for the server's cannot
be sent and cannot be fast-forwarded. The dialog offers two fixes; these tests
check that Branchly detects that state, refuses to start while the tree is
dirty, and that each chosen fix leaves one shared history that is uploaded.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QDialog

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

from gitops.status import RepositoryState, read_state
from services import divergence as divergence_mod
from tests.support import requires_git, temp_repo_pair

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")


def make_diverged(first, second) -> None:  # noqa: ANN001 - TempRepo fixtures
    """
    Gives ``second`` one local commit and one waiting server commit.

    Args:
        first: Clone that pushes the server-only commit.
        second: Clone that keeps a local-only commit and fetches.

    Returns:
        None
    """

    first.commit_file("server-only.txt", "from first\n")
    first.git("push", "origin", "main")
    second.commit_file("local-only.txt", "from second\n")
    second.git("fetch", "origin")


@requires_git
class DetectionTests(unittest.TestCase):
    """
    When the dialog should appear.
    """

    def test_ahead_and_behind_counts_as_diverged(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            make_diverged(first, second)
            state = read_state(second.root)
            self.assertEqual(1, state.ahead)
            self.assertEqual(1, state.behind)
            self.assertTrue(divergence_mod.is_diverged(state))
            self.assertTrue(divergence_mod.should_offer(state))

    def test_only_ahead_is_not_diverged(self) -> None:
        with temp_repo_pair() as (_first, second, _origin):
            second.commit_file("mine.txt", "only local\n")
            state = read_state(second.root)
            self.assertGreater(state.ahead, 0)
            self.assertEqual(0, state.behind)
            self.assertFalse(divergence_mod.is_diverged(state))
            self.assertFalse(divergence_mod.should_offer(state))

    def test_conflicts_suppress_the_offer(self) -> None:
        state = RepositoryState(ahead=1, behind=1, files=[])
        state.merging = True
        # operation_in_progress alone is enough; also cover has_conflicts via a
        # conflicted file would need a FileChange. Keep this to the flag path.
        self.assertTrue(divergence_mod.is_diverged(state))
        self.assertFalse(divergence_mod.should_offer(state))


@requires_git
class ResolveTests(unittest.TestCase):
    """
    Applying each of the two fixes.
    """

    def test_rebase_then_push_leaves_a_shared_line(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            make_diverged(first, second)
            outcome = divergence_mod.resolve(second.root, divergence_mod.MODE_REBASE_PUSH)
            self.assertTrue(outcome.ok, msg=str(outcome.failed_step and outcome.failed_step.message))
            state = read_state(second.root)
            self.assertEqual(0, state.ahead)
            self.assertEqual(0, state.behind)
            self.assertTrue((second.root / "server-only.txt").exists())
            self.assertTrue((second.root / "local-only.txt").exists())
            # No merge commit: one parent on HEAD.
            parents = second.git("rev-list", "--parents", "-n", "1", "HEAD").stdout.strip().split()
            self.assertEqual(2, len(parents))

    def test_merge_then_push_keeps_both_sides(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            make_diverged(first, second)
            outcome = divergence_mod.resolve(second.root, divergence_mod.MODE_MERGE_PUSH)
            self.assertTrue(outcome.ok, msg=str(outcome.failed_step and outcome.failed_step.message))
            state = read_state(second.root)
            self.assertEqual(0, state.ahead)
            self.assertEqual(0, state.behind)
            self.assertTrue((second.root / "server-only.txt").exists())
            self.assertTrue((second.root / "local-only.txt").exists())
            parents = second.git("rev-list", "--parents", "-n", "1", "HEAD").stdout.strip().split()
            self.assertGreaterEqual(len(parents), 3)

    def test_dirty_tree_is_refused(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            make_diverged(first, second)
            second.write("local-only.txt", "edited but not committed\n")
            outcome = divergence_mod.resolve(second.root, divergence_mod.MODE_REBASE_PUSH)
            self.assertFalse(outcome.ok)
            self.assertEqual(divergence_mod.BLOCKED_DIRTY, outcome.blocked_key)
            state = read_state(second.root)
            self.assertEqual(1, state.ahead)
            self.assertEqual(1, state.behind)

    def test_not_diverged_is_refused(self) -> None:
        with temp_repo_pair() as (_first, second, _origin):
            outcome = divergence_mod.resolve(second.root, divergence_mod.MODE_REBASE_PUSH)
            self.assertFalse(outcome.ok)
            self.assertEqual(divergence_mod.BLOCKED_NOT_DIVERGED, outcome.blocked_key)


@requires_qt
class DialogTests(unittest.TestCase):
    """
    The three buttons and what they return.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        import i18n

        i18n.set_language("en")

    def test_rebase_button_accepts_with_rebase_mode(self) -> None:
        from ui.divergence_dialog import DivergenceDialog

        dialog = DivergenceDialog("cardock", ahead=1, behind=2)
        self.addCleanup(dialog.deleteLater)
        dialog._rebase_button.click()
        self.assertEqual(QDialog.DialogCode.Accepted, dialog.result())
        self.assertEqual(divergence_mod.MODE_REBASE_PUSH, dialog.chosen_mode)

    def test_merge_button_accepts_with_merge_mode(self) -> None:
        from ui.divergence_dialog import DivergenceDialog

        dialog = DivergenceDialog("cardock", ahead=1, behind=2)
        self.addCleanup(dialog.deleteLater)
        dialog._merge_button.click()
        self.assertEqual(QDialog.DialogCode.Accepted, dialog.result())
        self.assertEqual(divergence_mod.MODE_MERGE_PUSH, dialog.chosen_mode)

    def test_cancel_leaves_no_mode(self) -> None:
        from ui.divergence_dialog import DivergenceDialog

        dialog = DivergenceDialog("cardock", ahead=1, behind=2)
        self.addCleanup(dialog.deleteLater)
        dialog.reject()
        self.assertEqual(QDialog.DialogCode.Rejected, dialog.result())
        self.assertEqual("", dialog.chosen_mode)

    def test_dirty_disables_the_apply_buttons(self) -> None:
        from ui.divergence_dialog import DivergenceDialog

        dialog = DivergenceDialog("cardock", ahead=1, behind=1, dirty=True)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog._rebase_button.isEnabled())
        self.assertFalse(dialog._merge_button.isEnabled())
        self.assertTrue(dialog._dirty_label.isVisibleTo(dialog))


@requires_qt
@requires_git
class WindowTests(unittest.TestCase):
    """
    Opening the dialog when a diverged project is activated.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):  # noqa: ANN202 - Qt at import time
        """
        Builds an empty main window with isolated config.

        Returns:
            MainWindow: The window.
        """

        import tempfile

        import i18n
        from config.app_settings import AppSettings
        from ui.main_window import MainWindow

        i18n.set_language("en")
        os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="branchly-test-")
        window = MainWindow(AppSettings(auto_check_minutes=0, github_enabled=False, language="en"))
        self.addCleanup(window.close)
        window._switch_scan_timer.stop()
        return window

    def test_activating_a_diverged_project_opens_the_dialog(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            make_diverged(first, second)
            window = self._window()
            _outcome, entry = window._registry.add(second.root)
            window._sidebar.refresh()

            opened: list[str] = []

            class FakeDialog:
                def __init__(self, *_args, **_kwargs) -> None:
                    opened.append("yes")

                def exec(self) -> int:
                    return QDialog.DialogCode.Rejected

                @property
                def chosen_mode(self) -> str:
                    return ""

            with patch("ui.main_window.DivergenceDialog", FakeDialog):
                window._activate(entry)

            self.assertEqual(["yes"], opened)
            self.assertEqual(
                (entry.key, 1, 1),
                window._divergence_dismissed,
            )

    def test_applying_rebase_mode_calls_resolve(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            make_diverged(first, second)
            window = self._window()
            _outcome, entry = window._registry.add(second.root)
            window._entry = entry
            window._state = read_state(second.root)

            fake = MagicMock(
                ok=True,
                blocked_key="",
                needs_conflict_assistant=False,
                failed_step=None,
            )
            with patch("ui.main_window.divergence_mod.resolve", return_value=fake) as resolve:
                window._apply_divergence_fix(divergence_mod.MODE_REBASE_PUSH)

            resolve.assert_called_once()
            self.assertEqual(divergence_mod.MODE_REBASE_PUSH, resolve.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
