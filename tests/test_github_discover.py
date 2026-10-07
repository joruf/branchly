"""
Tests for finding the account's repositories on GitHub and cloning the new ones.

What has to hold:

* **The list is the account's own work.** Forks and archived repositories are
  left out.
* **Each repository lands in the right group.** New ones are offered and ticked,
  one already in Branchly is shown but not offered (matched by its address, in
  any spelling), one whose clone already lies in the target folder is offered
  for adding only, and one whose folder holds something else is not offered.
* **Ticked ones are cloned and come back for the project list**, with the token
  Branchly holds, since most of an account's repositories are private.
* **The search on disk and this one share the tick for all rows.**
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

import i18n
from config.app_settings import AppSettings
from services import github_discovery as discovery
from services.github_discovery import RemoteRepository
from tests.support import requires_git, temp_repo_pair

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")


def repository(name: str, **fields: object) -> RemoteRepository:
    """
    Builds a repository of the joruf account.

    Args:
        name: Repository name.
        **fields: Fields to override.

    Returns:
        RemoteRepository: The repository.
    """

    values: dict[str, object] = {
        "name": name,
        "full_name": f"joruf/{name}",
        "clone_url": f"https://github.com/joruf/{name}.git",
    }
    values.update(fields)
    return RemoteRepository(**values)  # type: ignore[arg-type]


def make_clone(folder: Path, url: str) -> Path:
    """
    Creates a repository whose origin is the given address.

    Args:
        folder: Where it goes.
        url: Its origin.

    Returns:
        Path: The folder.
    """

    folder.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(folder)], check=True)
    subprocess.run(["git", "-C", str(folder), "remote", "add", "origin", url], check=True)
    return folder


class PlanTests(unittest.TestCase):
    """
    Sorting the repositories into what can be done with each.
    """

    def test_forks_and_archived_ones_are_left_out(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            offers = discovery.plan(
                [repository("own"), repository("forked", fork=True), repository("old", archived=True)],
                {},
                Path(base),
            )
            self.assertEqual(["own"], [offer.repository.name for offer in offers])

    def test_a_new_one_is_offered_with_its_target(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            offer = discovery.plan([repository("mindor")], {}, Path(base))[0]
            self.assertEqual(discovery.STATE_NEW, offer.state)
            self.assertEqual(Path(base) / "mindor", offer.target)
            self.assertTrue(offer.offered)

    def test_one_branchly_has_is_known_in_any_spelling(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            known = {discovery.slug_key("git@github.com:JORUF/Mindor.git"): "Mindor (work)"}
            offer = discovery.plan([repository("mindor")], known, Path(base))[0]
            self.assertEqual(discovery.STATE_KNOWN, offer.state)
            self.assertEqual("Mindor (work)", offer.known_name)
            self.assertFalse(offer.offered)

    @requires_git
    def test_a_clone_already_in_the_folder_is_only_added(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            make_clone(Path(base) / "mindor", "git@github.com:joruf/mindor.git")
            offer = discovery.plan([repository("mindor")], {}, Path(base))[0]
            self.assertEqual(discovery.STATE_ON_DISK, offer.state)
            self.assertTrue(offer.offered)

    @requires_git
    def test_a_folder_holding_something_else_is_not_offered(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            make_clone(Path(base) / "mindor", "https://github.com/somebody/else.git")
            (Path(base) / "plain").mkdir()
            offers = {o.repository.name: o for o in discovery.plan(
                [repository("mindor"), repository("plain")], {}, Path(base)
            )}
            self.assertEqual(discovery.STATE_BLOCKED, offers["mindor"].state)
            self.assertEqual(discovery.STATE_BLOCKED, offers["plain"].state)
            self.assertFalse(offers["plain"].offered)

    def test_new_ones_come_first_then_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            known = {discovery.slug_key("https://github.com/joruf/alpha.git"): "alpha"}
            offers = discovery.plan(
                [repository("zeta"), repository("alpha"), repository("Beta")], known, Path(base)
            )
            self.assertEqual(["Beta", "zeta", "alpha"], [o.repository.name for o in offers])

    def test_a_renamed_repository_is_recognised_under_its_new_name(self) -> None:
        # Branchly still has the old address: GitHub lists the new name only.
        known = {"joruf/mindwright": "mindwright", "joruf/stays": "stays"}
        asked: list[tuple[str, str]] = []

        def lookup(owner: str, repo: str) -> str:
            asked.append((owner, repo))
            return "joruf/mindor"

        moved = discovery.follow_moves(known, [repository("mindor"), repository("stays")], lookup)
        self.assertEqual([("joruf", "mindwright")], asked)
        with tempfile.TemporaryDirectory() as base:
            offer = discovery.plan([repository("mindor")], moved, Path(base))[0]
        self.assertEqual(discovery.STATE_KNOWN, offer.state)
        self.assertEqual("mindwright", offer.known_name)

    def test_the_default_folder_is_where_most_projects_live(self) -> None:
        paths = [Path("/home/x/Applications/a"), Path("/home/x/Applications/b"), Path("/home/x/Dokumente/c")]
        self.assertEqual(Path("/home/x/Applications"), discovery.default_folder(paths))
        self.assertEqual(Path.home(), discovery.default_folder([]))


@requires_git
class CloneCredentialTests(unittest.TestCase):
    """
    A private repository needs the token for the clone.
    """

    def test_the_login_reaches_git(self) -> None:
        from gitops import clone as clone_mod

        seen: list[object] = []
        real = clone_mod.run_streaming

        def spy(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            seen.append(kwargs.get("env_extra"))
            return real(*args, **kwargs)

        clone_mod.run_streaming = spy
        self.addCleanup(setattr, clone_mod, "run_streaming", real)
        with temp_repo_pair() as (_first, _second, origin), tempfile.TemporaryDirectory() as base:
            request = clone_mod.prepare(origin.as_uri(), base, "copy")
            result = clone_mod.clone(request, credentials={"GIT_TEST_LOGIN": "yes"})
            self.assertFalse(result.failed)
        self.assertEqual([{"GIT_TEST_LOGIN": "yes"}], seen)


@requires_qt
@requires_git
class DialogTests(unittest.TestCase):
    """
    The window: list, ticks and the clone run.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _dialog(  # noqa: ANN202
        self, folder: Path, repositories: list[RemoteRepository], known: dict[str, str] | None = None
    ):
        """
        Opens the window with a given list, without asking GitHub.

        Args:
            folder: Target folder.
            repositories: What GitHub would have listed.
            known: Projects Branchly already has.

        Returns:
            GitHubDiscoverDialog: The window.
        """

        from github_api.client import GitHubClient
        from ui.github_discover_dialog import GitHubDiscoverDialog

        dialog = GitHubDiscoverDialog(GitHubClient(""), known or {}, [], folder, load=False)
        self.addCleanup(dialog.deleteLater)
        dialog.set_repositories(repositories)
        return dialog

    def test_new_ones_start_ticked_and_known_ones_cannot_be(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            known = {discovery.slug_key("https://github.com/joruf/old.git"): "old"}
            dialog = self._dialog(Path(base), [repository("fresh"), repository("old")], known)
            self.assertEqual(["fresh"], [offer.repository.name for _i, offer in dialog.checked_offers()])
            self.assertEqual(Qt.CheckState.Checked, dialog._select_all.checkState())
            self.assertIn(i18n.t("github_discover.state_known", name="old"), dialog._list.item(1).text())

    def test_the_tick_for_all_shows_some_and_switches(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            dialog = self._dialog(Path(base), [repository("a"), repository("b")])
            dialog._list.item(0).setCheckState(Qt.CheckState.Unchecked)
            self.assertEqual(Qt.CheckState.PartiallyChecked, dialog._select_all.checkState())
            dialog._select_all.click()
            self.assertEqual(2, len(dialog.checked_offers()))
            dialog._select_all.click()
            self.assertEqual([], dialog.checked_offers())
            self.assertFalse(dialog._apply_button.isEnabled())

    def test_ticked_ones_are_cloned_and_handed_back(self) -> None:
        with temp_repo_pair() as (_first, _second, origin), tempfile.TemporaryDirectory() as base:
            make_clone(Path(base) / "already", "https://github.com/joruf/already.git")
            dialog = self._dialog(
                Path(base),
                [
                    RemoteRepository(name="fresh", full_name="joruf/fresh", clone_url=origin.as_uri()),
                    repository("already"),
                ],
            )
            app = QApplication.instance()
            deadline = QTimer()
            deadline.setSingleShot(True)
            deadline.timeout.connect(app.quit)
            dialog.start_cloning()
            # Wait until the run is over or the deadline passes.
            poll = QTimer()
            poll.timeout.connect(lambda: app.quit() if dialog._thread is None else None)
            poll.start(50)
            deadline.start(30_000)
            app.exec()
            poll.stop()
            deadline.stop()

            self.assertIsNone(dialog._thread, "the clone run never finished")
            self.assertEqual(
                sorted([Path(base) / "fresh", Path(base) / "already"]), sorted(dialog.added)
            )
            self.assertTrue((Path(base) / "fresh" / ".git").exists())
            self.assertIn(i18n.t("github_discover.done"), dialog._list.item(0).text())


@requires_qt
@requires_git
class DiskSearchTests(unittest.TestCase):
    """
    The search on disk now has the same tick for all rows.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def test_the_box_takes_all_out_and_puts_all_back(self) -> None:
        from PySide6.QtWidgets import QPushButton

        from ui.discover_dialog import DiscoverDialog

        with tempfile.TemporaryDirectory() as base:
            for name in ("a", "b"):
                make_clone(Path(base) / name, f"https://github.com/joruf/{name}.git")
            dialog = DiscoverDialog(set(), [], [Path(base)])
            self.addCleanup(dialog.reject)
            app = QApplication.instance()
            poll = QTimer()
            poll.timeout.connect(lambda: app.quit() if dialog._thread is None else None)
            poll.start(50)
            app.exec()
            poll.stop()

            self.assertEqual(2, len(dialog.checked_paths()))
            dialog._select_all.click()
            self.assertEqual([], dialog.checked_paths())
            dialog._select_all.click()
            self.assertEqual(2, len(dialog.checked_paths()))
            labels = [button.text() for button in dialog.findChildren(QPushButton)]
            self.assertNotIn("Select all", labels)


@requires_qt
class WindowTests(unittest.TestCase):
    """
    The menu entry and what the window does with the result.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _window(self):  # noqa: ANN202
        """
        Builds a signed-in window with an isolated configuration.

        Returns:
            MainWindow: The window.
        """

        from ui.main_window import MainWindow

        base = Path(tempfile.mkdtemp(prefix="branchly-test-"))
        os.environ["XDG_CONFIG_HOME"] = str(base / "config")
        # Built with GitHub off, so no request is in flight when the test closes
        # the window, and only then marked as signed in.
        window = MainWindow(AppSettings(auto_check_minutes=0, github_enabled=False, language="en"))
        self.addCleanup(window.close)
        window._settings.github_enabled = True
        window._github.set_token("ghp_test_token")
        return window

    def test_the_project_menu_offers_it(self) -> None:
        window = self._window()
        entries = window.menuBar().actions()
        holder = next(item for item in entries if item.text() == i18n.t("menu.repository"))
        texts = [action.text() for action in holder.menu().actions()]
        self.assertIn(i18n.t("github_discover.menu"), texts)
        self.assertEqual(
            texts.index(i18n.t("discover.menu")) + 1, texts.index(i18n.t("github_discover.menu"))
        )

    @requires_git
    def test_the_cloned_ones_are_registered_under_the_category(self) -> None:
        import ui.main_window as module

        window = self._window()
        target = Path(tempfile.mkdtemp(prefix="branchly-test-")) / "mindor"
        make_clone(target, "https://github.com/joruf/mindor.git")

        class Stub:
            """Stands in for the window, as if one clone had worked."""

            def __init__(self, *_args: object) -> None:
                self.added = [target]
                self.category = "Work"
                self.folder = target.parent

            def exec(self) -> int:
                return 0

        real = module.GitHubDiscoverDialog
        module.GitHubDiscoverDialog = Stub
        self.addCleanup(setattr, module, "GitHubDiscoverDialog", real)
        # Opening the new project would ask GitHub about it, and a request still
        # running when the test ends takes the interpreter down with it.
        window._reload_github = lambda: None

        window._discover_github()
        entry = window._registry.find(target)
        self.assertIsNotNone(entry)
        self.assertEqual("Work", entry.category)
        self.assertEqual(str(target.parent), window._settings.github_clone_folder)

    def test_without_a_sign_in_it_asks_first(self) -> None:
        window = self._window()
        window._github.set_token("")
        asked: list[str] = []
        window.ask_signin = lambda key: asked.append(key) or False
        window._discover_github()
        self.assertEqual(["github.signin_for_discover_hint"], asked)


if __name__ == "__main__":
    unittest.main()
