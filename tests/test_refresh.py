"""
Tests for when the window brings itself up to date.

Two triggers were added to the ones that already existed: giving the window
focus, and picking another project. Both are things the user does incidentally
rather than on purpose, which is exactly why they need guarding. A refresh per
alt-tab would mean a round of ``git status`` every time somebody looks at their
editor, and a scan per keystroke would start one for every project passed while
arrowing down the list.

So what is tested here is less "does it refresh" and more "does it refuse to
refresh when refreshing would be silly", plus the one case that is easy to get
wrong: a scan asked for while another batch is running must be made good, not
dropped.
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

from services.scheduler import RefreshThrottle
from tests.support import requires_git

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")


def init_repo(root: Path) -> Path:
    """
    Creates a repository with one commit.

    Args:
        root: Folder to initialise.

    Returns:
        Path: The repository root.
    """

    root.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.update(
        {
            "HOME": str(root),
            "GIT_CONFIG_GLOBAL": str(root / ".gitconfig"),
            "GIT_CONFIG_SYSTEM": str(root / ".gitconfig-system"),
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        }
    )

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, env=environment, check=True, capture_output=True)

    git("init", "-b", "main")
    (root / "kept.txt").write_text("one\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "first")
    return root


class ThrottleTests(unittest.TestCase):
    """
    The guard that keeps a repeated trigger from becoming a storm.
    """

    def test_the_first_trigger_always_fires(self) -> None:
        self.assertTrue(RefreshThrottle(5.0).allow(now=100.0))

    def test_a_second_trigger_inside_the_window_is_refused(self) -> None:
        throttle = RefreshThrottle(5.0)
        self.assertTrue(throttle.allow(now=100.0))
        self.assertFalse(throttle.allow(now=101.0))
        self.assertFalse(throttle.allow(now=104.9))

    def test_it_fires_again_once_the_window_passed(self) -> None:
        throttle = RefreshThrottle(5.0)
        throttle.allow(now=100.0)
        self.assertTrue(throttle.allow(now=105.0))

    def test_a_refused_trigger_does_not_extend_the_wait(self) -> None:
        # Recording a refused attempt would let constant alt-tabbing hold the
        # refresh off forever.
        throttle = RefreshThrottle(5.0)
        throttle.allow(now=100.0)
        for moment in (101.0, 102.0, 103.0, 104.0):
            self.assertFalse(throttle.allow(now=moment))
        self.assertTrue(throttle.allow(now=105.0))

    def test_keys_do_not_block_each_other(self) -> None:
        throttle = RefreshThrottle(5.0)
        self.assertTrue(throttle.allow("focus", now=100.0))
        self.assertTrue(throttle.allow("switch", now=100.0))

    def test_zero_means_no_throttling(self) -> None:
        throttle = RefreshThrottle(0)
        self.assertTrue(all(throttle.allow(now=100.0) for _index in range(5)))

    def test_a_negative_cooldown_is_read_as_none(self) -> None:
        self.assertEqual(0.0, RefreshThrottle(-3).cooldown)

    def test_forgetting_lets_it_fire_at_once(self) -> None:
        throttle = RefreshThrottle(5.0)
        throttle.allow(now=100.0)
        throttle.forget()
        self.assertTrue(throttle.allow(now=100.1))

    def test_reset_forgets_everything(self) -> None:
        throttle = RefreshThrottle(5.0)
        throttle.allow("a", now=100.0)
        throttle.allow("b", now=100.0)
        throttle.reset()
        self.assertTrue(throttle.allow("a", now=100.1))
        self.assertTrue(throttle.allow("b", now=100.1))


@requires_qt
@requires_git
class WindowRefreshTests(unittest.TestCase):
    """
    What the window does on focus and on switching project.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, base: Path):  # noqa: ANN202 - the type needs Qt at import time
        """
        Builds a window with two repositories registered.

        Args:
            base: Directory to keep the config and the repositories in.

        Returns:
            tuple: The window and the two entries.
        """

        from config.app_settings import AppSettings
        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = str(base / "config")
        first = init_repo(base / "alpha")
        second = init_repo(base / "beta")

        window = MainWindow(
            AppSettings(auto_check_minutes=0, github_enabled=False, language="en")
        )
        self.addCleanup(window.close)
        _outcome, entry_a = window._registry.add(first)
        _outcome, entry_b = window._registry.add(second)
        window._sidebar.refresh()
        return window, entry_a, entry_b

    def test_switching_project_asks_for_a_scan(self) -> None:
        with TemporaryDirectory() as base:
            window, entry_a, _entry_b = self._window(Path(base))
            window._switch_scan_timer.stop()

            window._activate(entry_a)
            # Debounced, so it is armed rather than already gone.
            self.assertTrue(window._switch_scan_timer.isActive())

    def test_the_debounce_collapses_a_run_through_the_list(self) -> None:
        with TemporaryDirectory() as base:
            window, entry_a, entry_b = self._window(Path(base))
            scanned: list[str] = []
            window._start_scan = lambda key: scanned.append(key) or True

            # Arrowing down the list: three switches in quick succession.
            window._activate(entry_a)
            window._activate(entry_b)
            window._activate(entry_a)
            self.assertEqual([], scanned)

            window._switch_scan_timer.stop()
            window._scan_current()
            # Only the project actually landed on is scanned.
            self.assertEqual([entry_a.key], scanned)

    def test_focus_refreshes_what_is_on_screen(self) -> None:
        with TemporaryDirectory() as base:
            window, entry_a, _entry_b = self._window(Path(base))
            window._activate(entry_a)
            window._switch_scan_timer.stop()

            calls: list[str] = []
            window._reload_current = lambda *_a, **_k: calls.append("state")
            window._reload_diff = lambda: calls.append("diff")

            window._refresh_on_focus()
            self.assertEqual(["state", "diff"], calls)
            self.assertTrue(window._switch_scan_timer.isActive())

    def test_focus_is_throttled(self) -> None:
        with TemporaryDirectory() as base:
            window, entry_a, _entry_b = self._window(Path(base))
            window._activate(entry_a)

            calls: list[str] = []
            window._reload_current = lambda *_a, **_k: calls.append("state")
            window._reload_diff = lambda: calls.append("diff")

            window._refresh_on_focus()
            window._refresh_on_focus()
            window._refresh_on_focus()
            # Three alt-tabs in a row, one read of the working tree.
            self.assertEqual(["state", "diff"], calls)

            window._focus_throttle.forget()
            window._refresh_on_focus()
            self.assertEqual(["state", "diff", "state", "diff"], calls)

    def test_focus_does_nothing_without_a_project(self) -> None:
        with TemporaryDirectory() as base:
            window, _entry_a, _entry_b = self._window(Path(base))
            window._entry = None
            calls: list[str] = []
            window._reload_current = lambda *_a, **_k: calls.append("state")

            window._refresh_on_focus()
            self.assertEqual([], calls)

    def test_focus_leaves_github_alone(self) -> None:
        with TemporaryDirectory() as base:
            window, entry_a, _entry_b = self._window(Path(base))
            window._activate(entry_a)
            window._focus_throttle.forget()

            calls: list[int] = []
            window._reload_github = lambda: calls.append(1)
            window._refresh_on_focus()
            # Network requests against a rate limit, for data that changes in
            # minutes. The panel has its own refresh button.
            self.assertEqual([], calls)

    def test_a_refused_scan_is_made_good_afterwards(self) -> None:
        with TemporaryDirectory() as base:
            window, entry_a, _entry_b = self._window(Path(base))
            window._activate(entry_a)
            window._switch_scan_timer.stop()

            attempts: list[str] = []

            def busy(key: str) -> bool:
                attempts.append(key)
                return False

            window._start_scan = busy
            window._scan_current()
            self.assertEqual([entry_a.key], attempts)
            self.assertEqual(entry_a.key, window._pending_scan_key)

            # The batch that was in the way finishes.
            window._start_scan = lambda key: attempts.append(key) or True
            window._run_pending_scan()
            self.assertEqual([entry_a.key, entry_a.key], attempts)
            self.assertIsNone(window._pending_scan_key)

    def test_a_pending_scan_for_a_removed_project_is_dropped(self) -> None:
        with TemporaryDirectory() as base:
            window, _entry_a, _entry_b = self._window(Path(base))
            window._pending_scan_key = "/gone/for/good"
            attempts: list[str] = []
            window._start_scan = lambda key: attempts.append(key) or True

            window._run_pending_scan()
            self.assertEqual([], attempts)
            self.assertIsNone(window._pending_scan_key)

    def test_the_scan_reports_whether_it_started(self) -> None:
        with TemporaryDirectory() as base:
            window, entry_a, _entry_b = self._window(Path(base))
            self.assertTrue(window._start_scan(entry_a.key))
            # A second batch is refused while the first one runs.
            self.assertFalse(window._start_scan(entry_a.key))
            window._scans.wait(20_000)

    def test_closing_stops_new_work_from_starting(self) -> None:
        # Refreshing now happens on focus and on every switch, so a scan could
        # still be asked for after closeEvent waited for the running ones. Qt
        # aborts the process when a pool thread outlives its pool.
        with TemporaryDirectory() as base:
            window, entry_a, _entry_b = self._window(Path(base))
            window._activate(entry_a)

            attempts: list[str] = []
            window._start_scan = lambda key: attempts.append(key) or True
            # The switch above armed the timer; the question is whether anything
            # new can be armed once closing has begun.
            window._switch_scan_timer.stop()
            window._closing = True
            window._pending_scan_key = entry_a.key

            window._schedule_current_scan()
            self.assertFalse(window._switch_scan_timer.isActive())

            window._scan_current()
            window._run_pending_scan()
            window._refresh_on_focus()
            self.assertEqual([], attempts)

    def test_the_close_handler_disarms_everything(self) -> None:
        from PySide6.QtGui import QCloseEvent

        with TemporaryDirectory() as base:
            window, entry_a, _entry_b = self._window(Path(base))
            window._activate(entry_a)
            window._pending_scan_key = entry_a.key
            self.assertTrue(window._switch_scan_timer.isActive())

            window.closeEvent(QCloseEvent())
            self.assertTrue(window._closing)
            self.assertFalse(window._switch_scan_timer.isActive())
            self.assertIsNone(window._pending_scan_key)

    def test_an_unknown_key_starts_nothing(self) -> None:
        with TemporaryDirectory() as base:
            window, _entry_a, _entry_b = self._window(Path(base))
            self.assertFalse(window._start_scan("/not/registered"))


if __name__ == "__main__":
    unittest.main()
