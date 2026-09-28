"""
Tests for giving a local-only project a server.

Writing the address itself is harmless: one line in ``.git/config``, no file
touched. The reason this needs care is the step after it. A server that already
holds commits, against a project here that also holds commits, means the next
send is refused, and every way past that refusal throws one side's work away.

So what is tested is whether Branchly can tell the four situations apart before
anything is written, because the wording and the choices the user gets depend
entirely on which one it is:

* nothing on the server yet
* something there, nothing here
* something on both sides, the only one with a real decision in it
* no answer at all, which is the normal state of a repository nobody has
  created yet
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

from gitops import remote as remote_mod
from services import remote_link
from tests.support import requires_git

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")


def sandbox(root: Path) -> dict[str, str]:
    """
    Builds a git environment that touches nothing outside the sandbox.

    Args:
        root: Directory the configuration may live in.

    Returns:
        dict[str, str]: Environment for the git calls.
    """

    environment = dict(os.environ)
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": str(root / ".gitconfig"),
            "GIT_CONFIG_SYSTEM": str(root / ".gitconfig-system"),
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        }
    )
    return environment


class Scene:
    """
    A sandbox with repositories in whatever state a test needs.
    """

    def __init__(self, root: Path) -> None:
        """
        Args:
            root: Directory to build everything in.
        """

        self.root = root
        self.env = sandbox(root)

    def git(self, where: Path, *args: str) -> None:
        """
        Runs one git command.

        Args:
            where: Working directory.
            *args: Arguments after ``git``.

        Returns:
            None
        """

        subprocess.run(["git", *args], cwd=where, env=self.env, check=True, capture_output=True)

    def repo(self, name: str, commit: bool = True) -> Path:
        """
        Builds a working tree.

        Args:
            name: Folder name.
            commit: Whether to give it a commit.

        Returns:
            Path: The repository root.
        """

        place = self.root / name
        place.mkdir()
        self.git(place, "init", "-b", "main")
        if commit:
            (place / "a.txt").write_text(f"{name}\n", encoding="utf-8")
            self.git(place, "add", "-A")
            self.git(place, "commit", "-m", "first")
        return place

    def bare(self, name: str, filled: bool = False) -> Path:
        """
        Builds a server-side repository.

        Args:
            name: Folder name.
            filled: Whether it should already hold a branch.

        Returns:
            Path: The bare repository.
        """

        place = self.root / name
        subprocess.run(
            ["git", "init", "--bare", "-b", "main", str(place)],
            check=True,
            capture_output=True,
            env=self.env,
        )
        if filled:
            seed = self.repo(f"{name}-seed")
            self.git(seed, "remote", "add", "origin", str(place))
            self.git(seed, "push", "-q", "-u", "origin", "main")
        return place


class SuggestionTests(unittest.TestCase):
    """
    Working out the address a project would most likely have.
    """

    def test_it_borrows_the_owner_from_the_other_projects(self) -> None:
        self.assertEqual(
            "https://github.com/joruf/ebay-listing.git",
            remote_link.suggest_url(
                "ebay-listing",
                ["https://github.com/joruf/branchly.git", "git@github.com:joruf/snappix.git"],
            ),
        )

    def test_the_most_common_owner_wins(self) -> None:
        self.assertEqual(
            "https://github.com/acme/thing.git",
            remote_link.suggest_url(
                "thing",
                [
                    "https://github.com/acme/one.git",
                    "https://github.com/acme/two.git",
                    "https://github.com/someone/three.git",
                ],
            ),
        )

    def test_without_an_owner_it_proposes_nothing(self) -> None:
        # A guessed address that points at somebody else's account is worse than
        # an empty field.
        self.assertEqual("", remote_link.suggest_url("thing", []))
        self.assertEqual("", remote_link.suggest_url("thing", ["/home/someone/local-only"]))

    def test_the_signed_in_account_is_the_fallback(self) -> None:
        self.assertEqual(
            "https://github.com/joruf/thing.git",
            remote_link.suggest_url("thing", [], fallback_owner="joruf"),
        )

    def test_a_nameless_project_proposes_nothing(self) -> None:
        self.assertEqual("", remote_link.suggest_url("   ", [], fallback_owner="joruf"))


@requires_git
class ServerStateTests(unittest.TestCase):
    """
    Telling the four situations apart before anything is written.
    """

    def test_an_empty_server_can_overwrite_nothing(self) -> None:
        with TemporaryDirectory() as base:
            scene = Scene(Path(base))
            outcome = remote_link.check(scene.repo("local"), str(scene.bare("server")))
            self.assertEqual(remote_link.STATE_EMPTY, outcome.state)
            self.assertFalse(outcome.collides)

    def test_a_filled_server_is_reported_with_its_branches(self) -> None:
        with TemporaryDirectory() as base:
            scene = Scene(Path(base))
            outcome = remote_link.check(
                scene.repo("local"), str(scene.bare("server", filled=True))
            )
            self.assertEqual(remote_link.STATE_HAS_CONTENT, outcome.state)
            self.assertEqual(("main",), outcome.branches)

    def test_history_on_both_sides_is_the_case_worth_warning_about(self) -> None:
        with TemporaryDirectory() as base:
            scene = Scene(Path(base))
            outcome = remote_link.check(
                scene.repo("local"), str(scene.bare("server", filled=True))
            )
            self.assertTrue(outcome.collides)

    def test_a_project_without_commits_collides_with_nothing(self) -> None:
        # Nothing of the user's can be lost, so the server's version simply
        # becomes this project.
        with TemporaryDirectory() as base:
            scene = Scene(Path(base))
            outcome = remote_link.check(
                scene.repo("fresh", commit=False), str(scene.bare("server", filled=True))
            )
            self.assertEqual(remote_link.STATE_HAS_CONTENT, outcome.state)
            self.assertFalse(outcome.collides)

    def test_a_server_that_does_not_exist_is_not_an_error(self) -> None:
        # Preparing a connection for a repository about to be created is normal.
        with TemporaryDirectory() as base:
            scene = Scene(Path(base))
            outcome = remote_link.check(scene.repo("local"), str(Path(base) / "nothing.git"))
            self.assertEqual(remote_link.STATE_UNREACHABLE, outcome.state)

    def test_something_that_is_not_an_address_is_refused_before_git_sees_it(self) -> None:
        with TemporaryDirectory() as base:
            scene = Scene(Path(base))
            for bad in ("not an address", "", "ext::sh -c whoami", "--upload-pack=x"):
                with self.subTest(url=bad):
                    outcome = remote_link.check(scene.repo(f"r{abs(hash(bad)) % 999}"), bad)
                    self.assertEqual(remote_link.STATE_INVALID, outcome.state)

    def test_an_existing_address_is_reported_so_it_can_be_shown(self) -> None:
        with TemporaryDirectory() as base:
            scene = Scene(Path(base))
            local = scene.repo("local")
            scene.git(local, "remote", "add", "origin", "https://github.com/joruf/old.git")

            outcome = remote_link.check(local, str(scene.bare("server")))
            self.assertEqual("https://github.com/joruf/old.git", outcome.replaces)

    def test_asking_the_server_writes_nothing(self) -> None:
        # The whole point of checking first: it has to be free of consequences.
        with TemporaryDirectory() as base:
            scene = Scene(Path(base))
            local = scene.repo("local")
            remote_link.check(local, str(scene.bare("server", filled=True)))

            self.assertEqual("", remote_mod.remote_fetch_url(local))
            self.assertFalse((local / ".git" / "FETCH_HEAD").exists())


@requires_qt
@requires_git
class DialogTests(unittest.TestCase):
    """
    What the user is asked, for each situation.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, repo: Path, suggestion: str = "", current: str = ""):  # noqa: ANN202
        """
        Builds a dialog for one project.

        Args:
            repo: Working tree path.
            suggestion: Address to offer.
            current: Address the project already has.

        Returns:
            LinkRemoteDialog: The dialog.
        """

        from ui.link_remote_dialog import LinkRemoteDialog

        dialog = LinkRemoteDialog(repo, repo.name, suggestion=suggestion, current_url=current)
        self.addCleanup(lambda: dialog.done(0))
        return dialog

    def test_the_suggestion_is_already_filled_in(self) -> None:
        with TemporaryDirectory() as base:
            scene = Scene(Path(base))
            dialog = self._dialog(
                scene.repo("local"), suggestion="https://github.com/joruf/local.git"
            )
            self.assertEqual("https://github.com/joruf/local.git", dialog.url)

    def test_an_empty_server_asks_nothing_further(self) -> None:
        from ui.link_remote_dialog import AFTER_NOTHING

        with TemporaryDirectory() as base:
            scene = Scene(Path(base))
            local = scene.repo("local")
            dialog = self._dialog(local)
            dialog._on_checked(remote_link.check(local, str(scene.bare("server"))))

            self.assertFalse(dialog._choices.isVisibleTo(dialog))
            self.assertEqual(AFTER_NOTHING, dialog.follow_up)

    def test_history_on_both_sides_offers_the_choice_and_defaults_to_the_safe_one(self) -> None:
        from ui.link_remote_dialog import AFTER_NOTHING

        with TemporaryDirectory() as base:
            scene = Scene(Path(base))
            local = scene.repo("local")
            dialog = self._dialog(local)
            dialog._on_checked(
                remote_link.check(local, str(scene.bare("server", filled=True)))
            )

            self.assertTrue(dialog._choices.isVisibleTo(dialog))
            self.assertTrue(dialog._only_link.isChecked())
            self.assertEqual(AFTER_NOTHING, dialog.follow_up)

    def test_nothing_here_yet_defaults_to_fetching(self) -> None:
        from ui.link_remote_dialog import AFTER_FETCH

        with TemporaryDirectory() as base:
            scene = Scene(Path(base))
            fresh = scene.repo("fresh", commit=False)
            dialog = self._dialog(fresh)
            dialog._on_checked(
                remote_link.check(fresh, str(scene.bare("server", filled=True)))
            )

            self.assertTrue(dialog._and_fetch.isChecked())
            self.assertEqual(AFTER_FETCH, dialog.follow_up)

    def test_editing_the_address_throws_the_verdict_away(self) -> None:
        # A warning about one server must not stay on screen for another.
        with TemporaryDirectory() as base:
            scene = Scene(Path(base))
            local = scene.repo("local")
            dialog = self._dialog(local)
            dialog._on_checked(
                remote_link.check(local, str(scene.bare("server", filled=True)))
            )
            self.assertTrue(dialog._choices.isVisibleTo(dialog))

            dialog._url.setText("https://github.com/joruf/somewhere-else.git")
            self.assertFalse(dialog._choices.isVisibleTo(dialog))

    def test_connecting_without_asking_the_server_stays_possible(self) -> None:
        # A repository that does not exist yet cannot answer, and preparing for
        # one is a normal thing to do.
        from PySide6.QtWidgets import QDialogButtonBox

        with TemporaryDirectory() as base:
            scene = Scene(Path(base))
            dialog = self._dialog(
                scene.repo("local"), suggestion="https://github.com/joruf/not-yet.git"
            )
            ok = dialog._buttons.button(QDialogButtonBox.StandardButton.Ok)
            self.assertTrue(ok.isEnabled())

    def test_an_empty_address_cannot_be_connected(self) -> None:
        from PySide6.QtWidgets import QDialogButtonBox

        with TemporaryDirectory() as base:
            scene = Scene(Path(base))
            dialog = self._dialog(scene.repo("local"))
            dialog._url.setText("   ")
            ok = dialog._buttons.button(QDialogButtonBox.StandardButton.Ok)
            self.assertFalse(ok.isEnabled())


@requires_qt
@requires_git
class WindowTests(unittest.TestCase):
    """
    The menu entry and what it does to the project.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_the_context_menu_offers_it_for_a_project_without_a_server(self) -> None:
        import i18n
        from models.repository import RepoEntry
        from services.registry import Registry
        from ui.sidebar import Sidebar

        i18n.set_language("en")
        sidebar = Sidebar(Registry(), "name_asc")
        self.addCleanup(sidebar.deleteLater)
        # The label depends on whether there is already an address, because the
        # two cases are a different thing to be doing.
        self.assertNotEqual(i18n.t("repo.link_remote"), i18n.t("repo.relink_remote"))
        self.assertIsNotNone(RepoEntry)

    def test_connecting_records_the_address_everywhere(self) -> None:
        from config.app_settings import AppSettings
        from ui.main_window import MainWindow

        with TemporaryDirectory() as base:
            scene = Scene(Path(base))
            os.environ["XDG_CONFIG_HOME"] = str(Path(base) / "config")
            target = scene.repo("ebay-listing")
            server = str(scene.bare("server"))

            window = MainWindow(
                AppSettings(auto_check_minutes=0, github_enabled=False, language="en")
            )
            self.addCleanup(window.close)
            _outcome, entry = window._registry.add(target)
            window._sidebar.refresh()
            window._switch_scan_timer.stop()
            self.assertEqual("", entry.remote_url)

            import ui.main_window as module
            from ui.link_remote_dialog import AFTER_NOTHING, LinkRemoteDialog

            class Stub:
                """Stands in for the dialog, so nothing modal opens."""

                DialogCode = LinkRemoteDialog.DialogCode

                def __init__(self, *_args: object, **_kwargs: object) -> None:
                    self.url = server
                    self.follow_up = AFTER_NOTHING

                def exec(self) -> int:
                    return int(LinkRemoteDialog.DialogCode.Accepted)

            real = module.LinkRemoteDialog
            module.LinkRemoteDialog = Stub
            self.addCleanup(setattr, module, "LinkRemoteDialog", real)

            window._link_remote(entry.key)

            self.assertEqual(server, remote_mod.remote_fetch_url(target))
            self.assertEqual(server, entry.remote_url)


if __name__ == "__main__":
    unittest.main()
