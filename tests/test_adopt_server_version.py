"""
Tests for taking over the server's version.

The situation: a project's history was rewritten on the server (a force push that
removed something from every version), and a second computer still has the old
versions. Branchly then reports "no shared history" or a diverged branch, and
every fix it had put *this* side's files on top of the server's, which uploads
the very thing that was taken out. What has to hold:

* **It is recognised**, for histories that share nothing and for diverged ones,
  and only when both sides really have versions the other lacks.
* **A rewritten history is called what it is**: the same summaries, other identities.
* **The project becomes the server's version**, with nothing uploaded and nothing
  changed on the server.
* **The old versions stay reachable** in a branch of their own.
* **Unsaved work and a failed step stop it** with the project as it was.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QDialog

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

import i18n
from config.app_settings import AppSettings
from gitops.status import read_state
from services import adopt as adopt_mod
from tests.support import requires_git, temp_repo, temp_repo_pair

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")


def rewrite_on_server(first, second) -> None:  # noqa: ANN001 - TempRepo fixtures
    """
    Rewrites the server's last two versions, as a force push that scrubs a name does.

    ``second`` keeps the old versions. ``first`` replaces them by two new ones with
    the same summaries and cleaned files, and force-pushes.

    Args:
        first: Clone that rewrites and pushes.
        second: Clone that still has the old versions, and then fetches.

    Returns:
        None
    """

    first.commit_file("notes.txt", "base\n", "Base")
    first.commit_file("tests.txt", "org emis\n", "Add tests")
    first.commit_file("docs.txt", "path /home/x/emis/y\n", "Add docs")
    first.git("push", "-q", "origin", "main")
    second.git("pull", "-q", "origin", "main")

    first.git("reset", "-q", "--hard", "HEAD~2")
    first.commit_file("tests.txt", "org acme\n", "Add tests")
    first.commit_file("docs.txt", "path /home/x/y\n", "Add docs")
    first.git("push", "-q", "--force", "origin", "main")
    second.git("fetch", "-q", "origin")


def make_unrelated(first, mine) -> None:  # noqa: ANN001 - TempRepo fixtures
    """
    Gives ``mine`` a server whose history it does not share.

    Args:
        first: Clone that fills the server.
        mine: Repository that was started on its own.

    Returns:
        None
    """

    first.commit_file("server-only.txt", "from the server\n", "Server one")
    first.commit_file("README.md", "# the server's readme\n", "Server two")
    first.git("push", "-q", "origin", "main")
    mine.commit_file("mine.txt", "only here\n", "My whole project")
    mine.git("remote", "add", "origin", first.git("remote", "get-url", "origin").stdout.strip())
    mine.git("fetch", "-q", "origin")


def server_tip(origin: Path) -> str:
    """
    Reads what the server has for ``main``, without going through any clone.

    Args:
        origin: The server's repository.

    Returns:
        str: The commit id.
    """

    return subprocess.run(
        ["git", "--git-dir", str(origin), "rev-parse", "refs/heads/main"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@requires_git
class DetectionTests(unittest.TestCase):
    """
    When there is something to take over and when there is not.
    """

    def test_a_rewritten_history_is_found_as_a_diverged_branch(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            rewrite_on_server(first, second)

            found = adopt_mod.detect(second.root)

            self.assertIsNotNone(found)
            self.assertEqual("origin/main", found.remote_ref)
            self.assertTrue(found.shared)
            self.assertEqual(2, found.local_only)
            self.assertEqual(2, found.server_only)

    def test_a_rewritten_history_is_called_that(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            rewrite_on_server(first, second)

            found = adopt_mod.detect(second.root)

            self.assertEqual(2, found.matching)
            self.assertTrue(found.rewritten)

    def test_histories_that_share_nothing_are_found_too(self) -> None:
        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)

            found = adopt_mod.detect(mine.root)

            self.assertIsNotNone(found)
            self.assertFalse(found.shared)
            # Both projects were started with a version of the same name, which proves nothing.
            self.assertFalse(found.rewritten)

    def test_it_counts_what_the_server_version_does_to_the_files(self) -> None:
        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)

            found = adopt_mod.detect(mine.root)

            # server-only.txt comes in, README.md differs, mine.txt is gone afterwards.
            self.assertEqual(1, found.will_add)
            self.assertEqual(1, found.will_change)
            self.assertEqual(1, found.will_remove)

    def test_a_project_that_is_only_behind_is_not_this(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("new.txt", "x\n", "News")
            first.git("push", "-q", "origin", "main")
            second.git("fetch", "-q", "origin")
            self.assertIsNone(adopt_mod.detect(second.root))

    def test_a_project_that_is_only_ahead_is_not_this(self) -> None:
        with temp_repo_pair() as (_first, second, _origin):
            second.commit_file("mine.txt", "x\n", "Mine")
            self.assertIsNone(adopt_mod.detect(second.root))

    def test_a_project_level_with_the_server_is_not_this(self) -> None:
        with temp_repo_pair() as (_first, second, _origin):
            self.assertIsNone(adopt_mod.detect(second.root))

    def test_a_project_without_a_server_is_not_this(self) -> None:
        with temp_repo() as mine:
            mine.commit_file("a.txt", "a\n", "A")
            self.assertIsNone(adopt_mod.detect(mine.root))

    def test_a_server_that_was_never_fetched_is_not_known_to_be_this(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("a.txt", "a\n", "A")
            first.git("push", "-q", "origin", "main")
            second.commit_file("b.txt", "b\n", "B")
            self.assertIsNone(adopt_mod.detect(second.root))


@requires_git
class AdoptTests(unittest.TestCase):
    """
    What taking over the server's version does.
    """

    def test_the_project_becomes_the_servers_version(self) -> None:
        with temp_repo_pair() as (first, second, origin):
            rewrite_on_server(first, second)

            outcome = adopt_mod.adopt(second.root)

            self.assertTrue(outcome.ok, outcome)
            self.assertEqual(server_tip(origin), second.head())
            self.assertEqual("org acme\n", (second.root / "tests.txt").read_text())
            self.assertEqual("path /home/x/y\n", (second.root / "docs.txt").read_text())

    def test_the_old_versions_stay_reachable_in_a_branch_of_their_own(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            rewrite_on_server(first, second)
            before = second.head()

            outcome = adopt_mod.adopt(second.root)

            self.assertTrue(outcome.backup.startswith("backup/main-before-server-"))
            kept = second.git("rev-parse", outcome.backup).stdout.strip()
            self.assertEqual(before, kept)
            self.assertIn(
                "org emis",
                second.git("show", f"{outcome.backup}:tests.txt").stdout,
            )

    def test_it_ends_in_a_project_that_is_level_with_the_server(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            rewrite_on_server(first, second)

            adopt_mod.adopt(second.root)
            state = read_state(second.root)

            self.assertEqual("origin/main", state.upstream)
            self.assertEqual(0, state.ahead)
            self.assertEqual(0, state.behind)
            self.assertTrue(state.is_clean)

    def test_nothing_is_uploaded_and_the_server_is_untouched(self) -> None:
        with temp_repo_pair() as (first, second, origin):
            rewrite_on_server(first, second)
            on_server = server_tip(origin)

            adopt_mod.adopt(second.root)

            self.assertEqual(on_server, server_tip(origin))

    def test_it_also_works_for_histories_that_share_nothing(self) -> None:
        with temp_repo_pair() as (first, _second, origin), temp_repo() as mine:
            make_unrelated(first, mine)

            outcome = adopt_mod.adopt(mine.root)

            self.assertTrue(outcome.ok, outcome)
            self.assertEqual(server_tip(origin), mine.head())
            self.assertFalse((mine.root / "mine.txt").exists())
            self.assertTrue((mine.root / "server-only.txt").exists())

    def test_unsaved_work_stops_it_before_anything_moves(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            rewrite_on_server(first, second)
            before = second.head()
            second.write("tests.txt", "unsaved change\n")

            outcome = adopt_mod.adopt(second.root)

            self.assertFalse(outcome.ok)
            self.assertEqual(adopt_mod.BLOCKED_DIRTY, outcome.blocked_key)
            self.assertEqual(before, second.head())
            self.assertEqual("unsaved change\n", (second.root / "tests.txt").read_text())
            self.assertEqual("", second.git("branch", "--list", "backup/*").stdout.strip())

    def test_nothing_happens_when_there_is_nothing_to_take_over(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("new.txt", "x\n", "News")
            first.git("push", "-q", "origin", "main")
            before = second.head()

            outcome = adopt_mod.adopt(second.root)

            self.assertFalse(outcome.ok)
            self.assertEqual(adopt_mod.BLOCKED_NOT_ADOPTABLE, outcome.blocked_key)
            self.assertEqual(before, second.head())

    def test_a_failed_fetch_leaves_the_project_as_it_was(self) -> None:
        with temp_repo_pair() as (first, second, origin):
            rewrite_on_server(first, second)
            before = second.head()
            second.git("remote", "set-url", "origin", str(origin) + "-gone")

            outcome = adopt_mod.adopt(second.root)

            self.assertFalse(outcome.ok)
            self.assertIsNotNone(outcome.step)
            self.assertEqual(before, second.head())
            self.assertEqual("", second.git("branch", "--list", "backup/*").stdout.strip())

    def test_the_backup_name_is_a_valid_branch_name(self) -> None:
        from gitops.refname import is_valid_branch_name

        self.assertTrue(is_valid_branch_name(adopt_mod.backup_name("main")))
        self.assertTrue(is_valid_branch_name(adopt_mod.backup_name("feature/x", "20261111-120000")))


@requires_qt
@requires_git
class DialogTests(unittest.TestCase):
    """
    What the window says and when it lets the fix go ahead.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _found(self, **overrides):  # noqa: ANN003, ANN202
        values = dict(
            branch="main",
            remote_ref="origin/main",
            local_oid="a" * 40,
            server_oid="b" * 40,
            local_only=14,
            server_only=14,
            matching=14,
            shared=True,
            will_add=1,
            will_change=3,
            will_remove=0,
        )
        values.update(overrides)
        return adopt_mod.Adoptable(**values)

    def _dialog(self, found=None, dirty: bool = False):  # noqa: ANN001, ANN202
        from ui.adopt_dialog import AdoptDialog

        dialog = AdoptDialog("claude-privacy-check", found or self._found(), dirty=dirty)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def _texts(self, dialog) -> str:  # noqa: ANN001
        from PySide6.QtWidgets import QLabel

        return "\n".join(label.text() for label in dialog.findChildren(QLabel))

    def test_a_rewritten_history_is_explained_as_one(self) -> None:
        texts = self._texts(self._dialog())
        self.assertIn("rewritten", texts)

    def test_other_histories_get_the_general_explanation(self) -> None:
        texts = self._texts(self._dialog(self._found(matching=0)))
        self.assertNotIn("rewritten on the server", texts)

    def test_it_says_where_the_old_versions_are_kept_and_what_changes(self) -> None:
        texts = self._texts(self._dialog())
        self.assertIn("backup/main-before-server-", texts)
        self.assertIn("adds 1 file(s), changes 3 and removes 0", texts)

    def test_unsaved_work_keeps_it_off_and_says_why(self) -> None:
        dialog = self._dialog(dirty=True)
        self.assertFalse(dialog._apply_button.isEnabled())
        self.assertTrue(dialog._dirty_label.isVisibleTo(dialog))

    def test_cancel_is_the_default_so_a_stray_return_changes_nothing(self) -> None:
        self.assertTrue(self._dialog()._cancel_button.isDefault())

    def test_applying_accepts_and_cancelling_does_not(self) -> None:
        dialog = self._dialog()
        dialog._apply_button.click()
        self.assertEqual(QDialog.DialogCode.Accepted, dialog.result())
        other = self._dialog()
        other.reject()
        self.assertEqual(QDialog.DialogCode.Rejected, other.result())

    def test_the_other_windows_offer_it_too(self) -> None:
        from ui.divergence_dialog import MODE_ADOPT, DivergenceDialog
        from ui.unrelated_dialog import ADOPT_RESULT, UnrelatedDialog

        divergence = DivergenceDialog("x", 2, 2)
        self.addCleanup(divergence.deleteLater)
        divergence._adopt_button.click()
        self.assertEqual(MODE_ADOPT, divergence.chosen_mode)

        import services.unrelated as unrelated_mod

        found = unrelated_mod.Unrelated(
            branch="main", remote_ref="origin/main", local_oid="a" * 40, server_oid="b" * 40,
            local_commits=1, server_commits=2, local_subject="x", added=0, changed=0, removed=0,
        )
        unrelated = UnrelatedDialog("x", found)
        self.addCleanup(unrelated.deleteLater)
        unrelated._adopt_button.click()
        self.assertEqual(ADOPT_RESULT, unrelated.result())

    def test_unsaved_work_keeps_the_other_windows_choice_off(self) -> None:
        from ui.divergence_dialog import DivergenceDialog

        divergence = DivergenceDialog("x", 2, 2, dirty=True)
        self.addCleanup(divergence.deleteLater)
        self.assertFalse(divergence._adopt_button.isEnabled())

    def test_every_text_exists_in_both_languages(self) -> None:
        keys = (
            "adopt.menu", "tip.adopt_menu", "adopt.title", "adopt.heading", "adopt.hint",
            "adopt.hint_rewritten", "adopt.plan", "adopt.backup_name", "adopt.compare",
            "adopt.apply", "adopt.done", "adopt.blocked_gone", "adopt.blocked_backup",
            "adopt.none", "tip.adopt_apply", "unrelated.adopt", "tip.unrelated_adopt",
            "divergence.adopt", "tip.divergence_adopt",
        )
        for language in ("en", "de"):
            i18n.set_language(language)
            self.addCleanup(i18n.set_language, "en")
            for key in keys:
                with self.subTest(language=language, key=key):
                    self.assertNotEqual(
                        key,
                        i18n.t(key, local=1, server=2, matching=1, added=1, changed=1, removed=1,
                               branch="main", backup="b", name="x"),
                    )


@requires_qt
@requires_git
class WindowTests(unittest.TestCase):
    """
    The project as the main window meets it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _window(self):  # noqa: ANN202 - Qt at import time
        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="branchly-test-")
        window = MainWindow(
            AppSettings(auto_check_minutes=0, github_enabled=False, check_updates=False, language="en")
        )
        self.addCleanup(window.close)
        window._switch_scan_timer.stop()
        return window

    def _stub_adopt_dialog(self, accept: bool) -> list:
        import ui.main_window as module

        shown: list = []

        class Stub:
            DialogCode = QDialog.DialogCode

            def __init__(self, name, found, dirty=False, parent=None) -> None:  # noqa: ANN001
                shown.append((name, found, dirty))

            def exec(self) -> int:  # noqa: A003
                return int(QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected)

        real = module.AdoptDialog
        module.AdoptDialog = Stub
        self.addCleanup(setattr, module, "AdoptDialog", real)
        return shown

    def _stub_unrelated_dialog(self, result: int) -> None:
        import ui.main_window as module

        class Stub:
            DialogCode = QDialog.DialogCode
            summary = ""

            def __init__(self, name, found, dirty=False, parent=None) -> None:  # noqa: ANN001
                pass

            def exec(self) -> int:  # noqa: A003
                return result

        real = module.UnrelatedDialog
        module.UnrelatedDialog = Stub
        self.addCleanup(setattr, module, "UnrelatedDialog", real)

    def test_the_menu_entry_is_on_only_where_it_applies(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            window = self._window()
            _o, entry = window._registry.add(second.root)
            window._sidebar.refresh()
            window._activate(entry)
            window._refresh_branch_menu()
            self.assertFalse(window._adopt_action.isEnabled())

            rewrite_on_server(first, second)
            window._reload_current()
            window._refresh_branch_menu()
            self.assertTrue(window._adopt_action.isEnabled())

    def test_the_menu_entry_opens_the_window_and_accepting_takes_over(self) -> None:
        with temp_repo_pair() as (first, second, origin):
            rewrite_on_server(first, second)
            window = self._window()
            shown = self._stub_adopt_dialog(accept=True)
            self._stub_dialog_noise()
            _o, entry = window._registry.add(second.root)
            window._sidebar.refresh()
            window._activate(entry)

            window._adopt_action.trigger()

            self.assertEqual(1, len(shown))
            self.assertEqual(server_tip(origin), second.head())

    def test_cancelling_changes_nothing(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            rewrite_on_server(first, second)
            before = second.head()
            window = self._window()
            shown = self._stub_adopt_dialog(accept=False)
            self._stub_dialog_noise()
            _o, entry = window._registry.add(second.root)
            window._sidebar.refresh()
            window._activate(entry)

            window._adopt_action.trigger()

            self.assertEqual(1, len(shown))
            self.assertEqual(before, second.head())

    def test_the_window_for_histories_that_share_nothing_leads_here(self) -> None:
        from ui.unrelated_dialog import ADOPT_RESULT

        with temp_repo_pair() as (first, _second, origin), temp_repo() as mine:
            make_unrelated(first, mine)
            window = self._window()
            self._stub_unrelated_dialog(ADOPT_RESULT)
            shown = self._stub_adopt_dialog(accept=True)
            _o, entry = window._registry.add(mine.root)
            window._sidebar.refresh()

            window._activate(entry)

            self.assertEqual(1, len(shown))
            self.assertEqual(server_tip(origin), mine.head())

    def _stub_dialog_noise(self) -> None:
        """
        Keeps the other fix windows from opening while a project is activated.

        Returns:
            None
        """

        import ui.main_window as module

        for name in ("DivergenceDialog", "UnrelatedDialog"):
            class Quiet:
                DialogCode = QDialog.DialogCode
                chosen_mode = ""
                summary = ""

                def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
                    pass

                def exec(self) -> int:  # noqa: A003
                    return int(QDialog.DialogCode.Rejected)

            real = getattr(module, name)
            setattr(module, name, Quiet)
            self.addCleanup(setattr, module, name, real)


if __name__ == "__main__":
    unittest.main()
