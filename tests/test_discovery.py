"""
Tests for finding repositories on disk.

The walk is the part worth testing hard, because every one of its rules exists to
stop it doing something obviously wrong: descending into a project and reporting
its submodules as projects, walking into ``node_modules`` and taking a minute, or
stopping altogether because one folder could not be read.

The dialog is tested for the one promise it makes: everything new is ticked, and
what Branchly already has is shown but cannot be ticked.
"""

from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

import subprocess

from services import discovery
from tests.support import requires_git

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")


def temp_repo_at(root: Path) -> Path:
    """
    Turns a folder into a real git repository.

    The window's own add path asks git, unlike the walk, so these fixtures have
    to be genuine.

    Args:
        root: Folder to initialise.

    Returns:
        Path: The repository root.
    """

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
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=root, env=environment, check=True, capture_output=True
    )
    return root


def make_repo(root: Path, branch: str = "main", origin: str = "") -> Path:
    """
    Builds something that looks like a working tree, without running git.

    The walk never starts git, so a fixture does not have to either. What it has
    to be is exactly what the walk looks at: a ``.git`` directory with a ``HEAD``
    and a ``config``.

    Args:
        root: Folder to turn into a repository.
        branch: Branch to write into ``HEAD``.
        origin: Fetch URL to write into ``config``, empty for no remote.

    Returns:
        Path: The repository root.
    """

    git_dir = root / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)
    (git_dir / "HEAD").write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")
    config = "[core]\n\trepositoryformatversion = 0\n"
    if origin:
        config += f'[remote "origin"]\n\turl = {origin}\n\tfetch = +refs/heads/*\n'
    (git_dir / "config").write_text(config, encoding="utf-8")
    return root


class WalkTests(unittest.TestCase):
    """
    What the walk finds and, more importantly, what it does not.
    """

    def test_it_finds_repositories_below_a_folder(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            make_repo(root / "alpha")
            make_repo(root / "nested" / "beta")
            (root / "plain").mkdir()

            found = discovery.scan([root])
            self.assertEqual(["alpha", "beta"], sorted(item.name for item in found))

    def test_it_does_not_look_inside_a_repository(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            make_repo(root / "project")
            # A submodule or a vendored checkout. Reporting it as a project of
            # its own is the mistake this rule prevents.
            make_repo(root / "project" / "vendor" / "library")

            found = discovery.scan([root])
            self.assertEqual(["project"], [item.name for item in found])

    def test_noisy_folders_are_skipped(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            make_repo(root / "node_modules" / "package")
            make_repo(root / ".venv" / "src" / "thing")
            make_repo(root / "real")

            found = discovery.scan([root])
            self.assertEqual(["real"], [item.name for item in found])

    def test_the_depth_limit_holds(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            make_repo(root / "a" / "b" / "c" / "deep")

            self.assertEqual([], discovery.scan([root], max_depth=2))
            self.assertEqual(["deep"], [item.name for item in discovery.scan([root], max_depth=6)])

    def test_a_known_repository_is_marked_not_dropped(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            existing = make_repo(root / "already").resolve()
            make_repo(root / "fresh")

            found = discovery.scan([root], known_keys={str(existing)})
            marks = {item.name: item.known for item in found}
            # A list missing it would look like the scan overlooked a project the
            # user can see in the sidebar.
            self.assertEqual({"already": True, "fresh": False}, marks)

    def test_a_missing_folder_is_not_an_error(self) -> None:
        self.assertEqual([], discovery.scan([Path("/nonexistent/for/branchly")]))

    def test_an_unreadable_folder_does_not_stop_the_walk(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("running as root, permissions do not apply")
        with TemporaryDirectory() as base:
            root = Path(base)
            blocked = root / "blocked"
            blocked.mkdir()
            make_repo(blocked / "hidden")
            make_repo(root / "visible")
            blocked.chmod(0o000)
            try:
                found = discovery.scan([root])
            finally:
                blocked.chmod(0o700)
            self.assertEqual(["visible"], [item.name for item in found])

    def test_a_symlink_loop_terminates(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            make_repo(root / "project")
            (root / "loop").symlink_to(root, target_is_directory=True)

            started = time.time()
            found = discovery.scan([root])
            self.assertLess(time.time() - started, 10)
            self.assertEqual(["project"], [item.name for item in found])

    def test_cancelling_stops_the_walk(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            for index in range(40):
                make_repo(root / f"repo{index:02d}")
            found = discovery.scan([root], should_cancel=lambda: True)
            self.assertEqual([], found)

    def test_progress_is_reported(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            for index in range(60):
                (root / f"folder{index:02d}").mkdir()
            seen: list[int] = []
            discovery.scan([root], on_progress=lambda count, _where: seen.append(count))
            self.assertTrue(seen)
            self.assertEqual(sorted(seen), seen)


class DetailTests(unittest.TestCase):
    """
    Reading the branch and the remote out of the repository's own files.
    """

    def test_the_branch_comes_from_head(self) -> None:
        with TemporaryDirectory() as base:
            make_repo(Path(base) / "p", branch="main")
            self.assertEqual("main", discovery.scan([Path(base)])[0].branch)

    def test_a_slash_in_the_branch_name_survives(self) -> None:
        # Only "refs/heads/" is a prefix. Cutting at the last slash would turn
        # "feature/login" into "login", which is a different branch.
        with TemporaryDirectory() as base:
            make_repo(Path(base) / "p", branch="feature/login")
            self.assertEqual("feature/login", discovery.scan([Path(base)])[0].branch)

    def test_a_detached_head_shows_the_commit(self) -> None:
        with TemporaryDirectory() as base:
            root = make_repo(Path(base) / "p")
            (root / ".git" / "HEAD").write_text("a" * 40 + "\n", encoding="utf-8")
            found = discovery.scan([Path(base)])
            self.assertEqual("aaaaaaaa", found[0].branch)

    def test_the_remote_comes_from_the_config(self) -> None:
        with TemporaryDirectory() as base:
            make_repo(Path(base) / "p", origin="https://github.com/joruf/branchly.git")
            found = discovery.scan([Path(base)])
            self.assertEqual("https://github.com/joruf/branchly.git", found[0].remote_url)

    def test_a_repository_without_a_remote_says_nothing(self) -> None:
        with TemporaryDirectory() as base:
            make_repo(Path(base) / "p")
            self.assertEqual("", discovery.scan([Path(base)])[0].remote_url)

    def test_another_remote_is_not_mistaken_for_origin(self) -> None:
        with TemporaryDirectory() as base:
            root = make_repo(Path(base) / "p")
            (root / ".git" / "config").write_text(
                '[remote "upstream"]\n\turl = https://example.invalid/up.git\n',
                encoding="utf-8",
            )
            self.assertEqual("", discovery.scan([Path(base)])[0].remote_url)

    def test_a_git_file_points_at_the_real_directory(self) -> None:
        # A submodule and a linked worktree both have a .git file rather than a
        # directory, and the branch lives where it points.
        with TemporaryDirectory() as base:
            root = Path(base)
            real = root / "storage" / "modules" / "thing"
            real.mkdir(parents=True)
            (real / "HEAD").write_text("ref: refs/heads/side\n", encoding="utf-8")
            (real / "config").write_text(
                '[remote "origin"]\n\turl = https://example.invalid/t.git\n', encoding="utf-8"
            )

            tree = root / "work" / "thing"
            tree.mkdir(parents=True)
            (tree / ".git").write_text(f"gitdir: {real}\n", encoding="utf-8")

            found = discovery.scan([root / "work"])
            self.assertEqual(1, len(found))
            self.assertEqual("side", found[0].branch)
            self.assertEqual("https://example.invalid/t.git", found[0].remote_url)

    def test_a_broken_git_file_is_still_listed(self) -> None:
        with TemporaryDirectory() as base:
            tree = Path(base) / "thing"
            tree.mkdir()
            (tree / ".git").write_text("gitdir: /nowhere/at/all\n", encoding="utf-8")
            found = discovery.scan([Path(base)])
            # It is a working tree by every sign the walk can see; it simply has
            # nothing to say about its branch.
            self.assertEqual(["thing"], [item.name for item in found])
            self.assertEqual("", found[0].branch)


class CandidateTests(unittest.TestCase):
    """
    What a candidate reports about itself.
    """

    def test_the_key_matches_the_registry(self) -> None:
        candidate = discovery.Candidate(path=Path("/tmp/x"), name="x")
        self.assertEqual("/tmp/x", candidate.path_key)

    def test_the_home_directory_is_shortened(self) -> None:
        candidate = discovery.Candidate(path=Path.home() / "Projects" / "x", name="x")
        self.assertEqual("~/Projects/x", candidate.display_path)

    def test_a_path_outside_home_is_shown_in_full(self) -> None:
        candidate = discovery.Candidate(path=Path("/opt/x"), name="x")
        self.assertEqual("/opt/x", candidate.display_path)


@requires_qt
class DialogTests(unittest.TestCase):
    """
    What the dialog offers and what it hands back.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, root: Path, known: set[str] | None = None):  # noqa: ANN202
        """
        Builds a dialog and waits for its scan.

        Args:
            root: Folder to search.
            known: Registry keys Branchly already has.

        Returns:
            DiscoverDialog: The dialog, with its list filled.
        """

        from ui.discover_dialog import DiscoverDialog

        dialog = DiscoverDialog(known or set(), [], [root])
        self.addCleanup(dialog.deleteLater)
        deadline = time.time() + 15
        while time.time() < deadline:
            self.app.processEvents()
            if dialog._thread is None and dialog._list.count():
                break
            time.sleep(0.01)
        self.app.processEvents()
        return dialog

    def test_everything_new_starts_ticked(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            for name in ("alpha", "beta", "gamma"):
                make_repo(root / name)

            dialog = self._dialog(root)
            self.assertEqual(3, dialog._list.count())
            self.assertEqual(3, len(dialog.checked_paths()))

    def test_what_branchly_has_is_shown_but_not_offered(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            known_path = make_repo(root / "already").resolve()
            make_repo(root / "fresh")

            dialog = self._dialog(root, {str(known_path)})
            self.assertEqual(2, dialog._list.count())
            # Listed, so the result does not look incomplete, but there is
            # nothing to do with it.
            self.assertEqual(1, len(dialog.checked_paths()))
            self.assertTrue(dialog.checked_paths()[0].name == "fresh")

    def test_select_none_and_select_all(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            for name in ("a", "b"):
                make_repo(root / name)

            dialog = self._dialog(root)
            dialog._set_all_checked(False)
            self.assertEqual([], dialog.checked_paths())
            dialog._set_all_checked(True)
            self.assertEqual(2, len(dialog.checked_paths()))

    def test_selecting_all_does_not_tick_the_known_ones(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            known_path = make_repo(root / "already").resolve()
            make_repo(root / "fresh")

            dialog = self._dialog(root, {str(known_path)})
            dialog._set_all_checked(True)
            self.assertEqual(["fresh"], [item.name for item in dialog.checked_paths()])

    def test_the_button_says_how_many(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            for name in ("a", "b"):
                make_repo(root / name)

            dialog = self._dialog(root)
            self.assertTrue(dialog._apply_button.isEnabled())
            self.assertIn("2", dialog._apply_button.text())

            dialog._set_all_checked(False)
            self.assertFalse(dialog._apply_button.isEnabled())

    def test_an_empty_folder_says_so(self) -> None:
        from ui.discover_dialog import DiscoverDialog

        with TemporaryDirectory() as base:
            dialog = DiscoverDialog(set(), [], [Path(base)])
            self.addCleanup(dialog.deleteLater)
            deadline = time.time() + 15
            while time.time() < deadline:
                self.app.processEvents()
                if dialog._thread is None:
                    break
                time.sleep(0.01)
            self.app.processEvents()
            self.assertEqual(0, dialog._list.count())
            self.assertTrue(dialog._status.text())
            self.assertFalse(dialog._apply_button.isEnabled())


@requires_qt
@requires_git
class WindowTests(unittest.TestCase):
    """
    What the window does with the paths the dialog hands back.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, base: Path, **overrides: object):  # noqa: ANN202
        """
        Builds a window with its own settings and registry.

        Args:
            base: Directory to keep the config in.
            **overrides: Settings to override.

        Returns:
            MainWindow: The window.
        """

        from config.app_settings import AppSettings
        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = str(base / "config")
        settings = AppSettings(
            auto_check_minutes=0, github_enabled=False, language="en", **overrides
        )
        window = MainWindow(settings)
        self.addCleanup(window.close)
        return window

    def _stub_dialog(self, chosen: list[Path], accepted: bool = True) -> object:
        """
        Builds a stand-in for the discovery dialog.

        Args:
            chosen: Paths it should hand back.
            accepted: Whether the user confirmed.

        Returns:
            type: A class the window can construct in place of the real dialog.
        """

        from PySide6.QtWidgets import QDialog

        class _Stub:
            DialogCode = QDialog.DialogCode

            def __init__(self, *_args: object, **_kwargs: object) -> None:
                self.chosen = list(chosen)
                self.category = ""

            def exec(self) -> object:
                return (
                    QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected
                )

        return _Stub

    def test_the_picked_repositories_are_registered(self) -> None:
        from ui import main_window as window_module

        with TemporaryDirectory() as base:
            root = Path(base) / "projects"
            root.mkdir()
            paths = []
            for name in ("alpha", "beta"):
                target = root / name
                target.mkdir()
                repo = temp_repo_at(target)
                paths.append(repo)

            window = self._window(Path(base))
            original = window_module.DiscoverDialog
            window_module.DiscoverDialog = self._stub_dialog(paths)
            try:
                window._discover_repositories()
            finally:
                window_module.DiscoverDialog = original

            names = sorted(entry.name for entry in window._registry.entries)
            self.assertEqual(["alpha", "beta"], names)

    def test_cancelling_registers_nothing(self) -> None:
        from ui import main_window as window_module

        with TemporaryDirectory() as base:
            target = Path(base) / "alpha"
            target.mkdir()
            repo = temp_repo_at(target)

            window = self._window(Path(base))
            original = window_module.DiscoverDialog
            window_module.DiscoverDialog = self._stub_dialog([repo], accepted=False)
            try:
                window._discover_repositories()
            finally:
                window_module.DiscoverDialog = original

            self.assertEqual([], window._registry.entries)

    def test_a_path_that_is_not_a_repository_is_skipped_quietly(self) -> None:
        from ui import main_window as window_module

        with TemporaryDirectory() as base:
            plain = Path(base) / "not-a-repo"
            plain.mkdir()

            window = self._window(Path(base))
            original = window_module.DiscoverDialog
            window_module.DiscoverDialog = self._stub_dialog([plain])
            try:
                window._discover_repositories()
            finally:
                window_module.DiscoverDialog = original

            self.assertEqual([], window._registry.entries)

    def test_the_offer_is_made_once_and_only_when_the_list_is_empty(self) -> None:
        from ui import main_window as window_module

        with TemporaryDirectory() as base:
            window = self._window(Path(base))
            calls: list[int] = []
            window_module.DiscoverDialog = self._stub_dialog([])
            original = window._discover_repositories
            window._discover_repositories = lambda *_a, **_k: calls.append(1)
            try:
                window._maybe_offer_discovery()
                self.assertEqual(1, len(calls))
                self.assertTrue(window._settings.discovery_offered)

                # Asking again changes nothing: the flag is what stops it nagging.
                window._maybe_offer_discovery()
                self.assertEqual(1, len(calls))
            finally:
                window._discover_repositories = original

    def test_a_filled_list_is_never_offered_the_search(self) -> None:
        with TemporaryDirectory() as base:
            target = Path(base) / "alpha"
            target.mkdir()
            repo = temp_repo_at(target)

            window = self._window(Path(base))
            window._registry.add(repo)
            calls: list[int] = []
            original = window._discover_repositories
            window._discover_repositories = lambda *_a, **_k: calls.append(1)
            try:
                window._maybe_offer_discovery()
            finally:
                window._discover_repositories = original
            self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
