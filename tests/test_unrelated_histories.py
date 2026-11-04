"""
Tests for a project and a server that share no history.

The situation: a project was started without Git, saved once, and then pointed at
an online repository that already held a long history. Uploading is refused and
so is every way of combining the two, and nothing in Branchly said why or what to
do. What has to hold:

* **It is recognised**, and only when it is true: a project that is merely behind,
  merely ahead or diverged is not this, and neither is one with no server.
* **The dialog says what the fix does to the files** before anything happens.
* **The fix keeps the server's history** and puts the project's files on top of it
  as one new version, uploaded as an ordinary fast-forward.
* **A failed fix leaves the project as it found it.**
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
from services import unrelated as unrelated_mod
from tests.support import requires_git, temp_repo, temp_repo_pair

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")


def make_unrelated(first, mine) -> None:  # noqa: ANN001 - TempRepo fixtures
    """
    Gives ``mine`` a server whose history it does not share.

    ``first`` is a clone that puts three versions on the server, two files among
    them that ``mine`` does not have. ``mine`` is a project of its own, with a
    different first version, that then learns about the server.

    Args:
        first: Clone that fills the server.
        mine: Repository that was started on its own.

    Returns:
        None
    """

    first.commit_file("server-only.txt", "from the server\n", "Server one")
    first.commit_file("docs/guide.txt", "guide\n", "Server two")
    first.commit_file("README.md", "# the server's readme\n", "Server three")
    first.git("push", "-q", "origin", "main")
    mine.commit_file("mine.txt", "only here\n", "My whole project")
    mine.git("remote", "add", "origin", first.git("remote", "get-url", "origin").stdout.strip())
    mine.git("fetch", "-q", "origin")


def server_tip(first, origin: Path) -> str:  # noqa: ANN001 - TempRepo fixture
    """
    Reads what the server has for ``main``, without going through any clone.

    Args:
        first: Any fixture, for its git environment.
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
    When it is this situation and when it is not.
    """

    def test_two_histories_with_no_start_in_common_are_found(self) -> None:
        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)

            found = unrelated_mod.detect(mine.root)

            self.assertIsNotNone(found)
            self.assertEqual("main", found.branch)
            self.assertEqual("origin/main", found.remote_ref)
            # The fixture's own first version and the one that is "my project".
            self.assertEqual(2, found.local_commits)
            self.assertEqual(4, found.server_commits)
            self.assertEqual("My whole project", found.local_subject)

    def test_it_counts_what_the_new_version_would_do_to_the_files(self) -> None:
        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)

            found = unrelated_mod.detect(mine.root)

            # mine.txt is only here, README.md exists on both sides with another
            # content, and the two server files are only there.
            self.assertEqual(1, found.added)
            self.assertEqual(1, found.changed)
            self.assertEqual(2, found.removed)
            self.assertEqual({"server-only.txt": 1, "docs/": 1}, dict(found.removed_groups))

    def test_a_project_that_is_only_behind_is_not_this(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("new.txt", "x\n", "News")
            first.git("push", "-q", "origin", "main")
            second.git("fetch", "-q", "origin")
            self.assertIsNone(unrelated_mod.detect(second.root))

    def test_a_project_that_is_only_ahead_is_not_this(self) -> None:
        with temp_repo_pair() as (_first, second, _origin):
            second.commit_file("mine.txt", "x\n", "Mine")
            self.assertIsNone(unrelated_mod.detect(second.root))

    def test_a_project_that_moved_on_both_sides_is_not_this(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("theirs.txt", "x\n", "Theirs")
            first.git("push", "-q", "origin", "main")
            second.commit_file("mine.txt", "y\n", "Mine")
            second.git("fetch", "-q", "origin")
            self.assertIsNone(unrelated_mod.detect(second.root))

    def test_a_project_without_a_server_is_not_this(self) -> None:
        with temp_repo() as mine:
            self.assertIsNone(unrelated_mod.detect(mine.root))

    def test_a_server_that_was_never_fetched_is_not_known_to_be_this(self) -> None:
        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)
            mine.git("update-ref", "-d", "refs/remotes/origin/main")
            self.assertIsNone(unrelated_mod.detect(mine.root))

    def test_a_project_with_nothing_saved_yet_is_not_this(self) -> None:
        with temp_repo_pair() as (first, _second, _origin), temp_repo(initial_commit=False) as mine:
            first.commit_file("a.txt", "a\n", "A")
            first.git("push", "-q", "origin", "main")
            mine.git("remote", "add", "origin", first.git("remote", "get-url", "origin").stdout.strip())
            mine.git("fetch", "-q", "origin")
            self.assertIsNone(unrelated_mod.detect(mine.root))

    def test_it_is_also_found_when_an_upstream_is_already_set(self) -> None:
        # The divergence dialog would take this for an ordinary split, and its
        # two fixes do not work here.
        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)
            mine.git("branch", "--set-upstream-to=origin/main", "main")
            state = read_state(mine.root)
            self.assertGreater(state.ahead, 0)
            self.assertGreater(state.behind, 0)
            self.assertIsNotNone(unrelated_mod.detect(mine.root, state))


@requires_git
class ResolveTests(unittest.TestCase):
    """
    Putting the project's files on top of the server's history.
    """

    def test_the_server_keeps_its_history_and_gets_one_new_version(self) -> None:
        with temp_repo_pair() as (first, _second, origin), temp_repo() as mine:
            make_unrelated(first, mine)
            before = server_tip(first, origin)

            outcome = unrelated_mod.resolve(mine.root, "Bring my project in")

            self.assertTrue(outcome.ok, str(outcome.step and outcome.step.message))
            self.assertTrue(outcome.committed)
            after = server_tip(first, origin)
            self.assertNotEqual(before, after)
            self.assertEqual(mine.head(), after)
            parent = mine.git("rev-parse", "HEAD^").stdout.strip()
            self.assertEqual(before, parent)
            self.assertEqual("5", mine.git("rev-list", "--count", "HEAD").stdout.strip())

    def test_the_files_are_exactly_what_they_were(self) -> None:
        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)
            mine.write("mine.txt", "only here\n")
            snapshot = {p.name: p.read_text() for p in mine.root.iterdir() if p.is_file()}

            unrelated_mod.resolve(mine.root, "Bring my project in")

            after = {p.name: p.read_text() for p in mine.root.iterdir() if p.is_file()}
            self.assertEqual(snapshot, after)
            self.assertFalse((mine.root / "server-only.txt").exists())

    def test_the_new_version_removes_what_only_the_server_had_but_history_keeps_it(self) -> None:
        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)

            unrelated_mod.resolve(mine.root, "Bring my project in")

            self.assertNotIn("server-only.txt", mine.git("ls-files").stdout.split())
            old = mine.git("show", "origin/main~1:server-only.txt", check=False)
            self.assertEqual("from the server\n", old.stdout)

    def test_it_ends_in_a_project_that_is_level_with_the_server(self) -> None:
        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)

            unrelated_mod.resolve(mine.root, "Bring my project in")

            state = read_state(mine.root)
            self.assertEqual("origin/main", state.upstream)
            self.assertEqual((0, 0), (state.ahead, state.behind))
            self.assertIsNone(unrelated_mod.detect(mine.root))
            self.assertEqual("Bring my project in", mine.git("log", "-1", "--format=%s").stdout.strip())

    def test_the_summary_is_required(self) -> None:
        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)
            head = mine.head()

            outcome = unrelated_mod.resolve(mine.root, "   ")

            self.assertEqual(unrelated_mod.BLOCKED_SUMMARY, outcome.blocked_key)
            self.assertEqual(head, mine.head())

    def test_unsaved_work_stops_it_before_anything_moves(self) -> None:
        with temp_repo_pair() as (first, _second, origin), temp_repo() as mine:
            make_unrelated(first, mine)
            mine.write("mine.txt", "edited, not saved\n")
            head, before = mine.head(), server_tip(first, origin)

            outcome = unrelated_mod.resolve(mine.root, "Bring my project in")

            self.assertEqual(unrelated_mod.BLOCKED_DIRTY, outcome.blocked_key)
            self.assertEqual(head, mine.head())
            self.assertEqual(before, server_tip(first, origin))
            self.assertEqual("edited, not saved\n", (mine.root / "mine.txt").read_text())

    def test_nothing_happens_when_the_histories_are_shared(self) -> None:
        with temp_repo_pair() as (_first, second, _origin):
            head = second.head()
            outcome = unrelated_mod.resolve(second.root, "Whatever")
            self.assertEqual(unrelated_mod.BLOCKED_NOT_UNRELATED, outcome.blocked_key)
            self.assertEqual(head, second.head())

    def test_a_refused_upload_keeps_the_new_version_and_says_so(self) -> None:
        with temp_repo_pair() as (first, _second, origin), temp_repo() as mine:
            make_unrelated(first, mine)
            hook = Path(origin) / "hooks" / "pre-receive"
            hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)

            outcome = unrelated_mod.resolve(mine.root, "Bring my project in")

            self.assertFalse(outcome.ok)
            self.assertTrue(outcome.committed)
            self.assertIsNotNone(outcome.step)
            self.assertEqual("Bring my project in", mine.git("log", "-1", "--format=%s").stdout.strip())

    def test_a_failed_record_puts_the_branch_back_where_it_was(self) -> None:
        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)
            head = mine.head()
            hook = mine.root / ".git" / "hooks" / "pre-commit"
            hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)

            outcome = unrelated_mod.resolve(mine.root, "Bring my project in")

            self.assertFalse(outcome.ok)
            self.assertEqual(head, mine.head())
            self.assertEqual("only here\n", (mine.root / "mine.txt").read_text())

    def test_identical_files_need_no_new_version(self) -> None:
        with temp_repo_pair() as (first, _second, origin), temp_repo(initial_commit=False) as mine:
            first.commit_file("same.txt", "same\n", "Server")
            first.git("push", "-q", "origin", "main")
            mine.commit_file("same.txt", "same\n", "Mine, same content")
            mine.commit_file("README.md", first.git("show", "HEAD:README.md").stdout, "Same readme")
            mine.git("remote", "add", "origin", first.git("remote", "get-url", "origin").stdout.strip())
            mine.git("fetch", "-q", "origin")
            before = server_tip(first, origin)

            outcome = unrelated_mod.resolve(mine.root, "Unused")

            self.assertTrue(outcome.ok)
            self.assertFalse(outcome.committed)
            self.assertEqual(before, mine.head())
            self.assertEqual(before, server_tip(first, origin))


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

    def _found(self, removed: int = 2, **overrides):  # noqa: ANN003, ANN202
        values = dict(
            branch="main",
            remote_ref="origin/main",
            local_oid="a" * 40,
            server_oid="b" * 40,
            local_commits=1,
            server_commits=800,
            local_subject="Add per-country charts",
            added=3,
            changed=5,
            removed=removed,
            removed_groups=(("vendor/", removed - 1), ("TODO.txt", 1))[: 2 if removed else 0],
        )
        values.update(overrides)
        return unrelated_mod.Unrelated(**values)

    def _dialog(self, found=None, dirty: bool = False):  # noqa: ANN001, ANN202
        from ui.unrelated_dialog import UnrelatedDialog

        dialog = UnrelatedDialog("chartscheck", found or self._found(), dirty=dirty)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_the_summary_starts_as_the_name_of_the_newest_version(self) -> None:
        self.assertEqual("Add per-country charts", self._dialog().summary)

    def test_the_counts_and_the_removed_files_are_on_screen(self) -> None:
        from PySide6.QtWidgets import QLabel

        dialog = self._dialog()
        text = " ".join(label.text() for label in dialog.findChildren(QLabel))
        self.assertIn("800", text)
        self.assertIn("adds 3", text)
        self.assertIn("changes 5", text)
        self.assertIn("removes 2", text)
        self.assertIn("vendor/", dialog._removed_list.text())
        self.assertIn("TODO.txt", dialog._removed_list.text())
        self.assertTrue(dialog._removed.isVisibleTo(dialog))

    def test_nothing_is_said_about_removed_files_when_none_are(self) -> None:
        dialog = self._dialog(self._found(removed=0))
        self.assertFalse(dialog._removed.isVisibleTo(dialog))
        self.assertFalse(dialog._removed_list.isVisibleTo(dialog))

    def test_a_long_list_of_places_is_shortened_and_the_rest_counted(self) -> None:
        groups = tuple((f"folder{n}/", 100 - n) for n in range(12))
        total = sum(count for _place, count in groups)
        dialog = self._dialog(self._found(removed=total, removed_groups=groups))
        shown = sum(count for _place, count in groups[:8])
        self.assertIn("folder0/", dialog._removed_list.text())
        self.assertNotIn("folder9/", dialog._removed_list.text())
        self.assertIn(i18n.t("unrelated.and_more", count=total - shown), dialog._removed_list.text())

    def test_without_a_summary_the_fix_cannot_go_ahead(self) -> None:
        dialog = self._dialog()
        dialog._summary.setText("   ")
        self.assertFalse(dialog._apply_button.isEnabled())
        dialog._summary.setText("Something")
        self.assertTrue(dialog._apply_button.isEnabled())

    def test_unsaved_work_keeps_the_fix_off_and_says_why(self) -> None:
        dialog = self._dialog(dirty=True)
        self.assertFalse(dialog._apply_button.isEnabled())
        self.assertTrue(dialog._dirty_label.isVisibleTo(dialog))

    def test_the_window_is_tall_enough_for_everything_it_says(self) -> None:
        # A wrapped text cannot tell its height before it knows its width, and the
        # window used to open a line or two short, with the end of the list cut.
        groups = tuple((f"folder{n}/", 100 - n) for n in range(12))
        dialog = self._dialog(self._found(removed=sum(c for _p, c in groups), removed_groups=groups))
        dialog.show()
        self.app.processEvents()
        self.assertGreaterEqual(dialog.height(), dialog.layout().totalHeightForWidth(dialog.width()))

    def test_cancel_is_the_default_so_a_stray_return_changes_nothing(self) -> None:
        self.assertTrue(self._dialog()._cancel_button.isDefault())

    def test_applying_accepts_and_cancelling_does_not(self) -> None:
        dialog = self._dialog()
        dialog._apply_button.click()
        self.assertEqual(QDialog.DialogCode.Accepted, dialog.result())
        other = self._dialog()
        other.reject()
        self.assertEqual(QDialog.DialogCode.Rejected, other.result())

    def test_every_text_exists_in_both_languages(self) -> None:
        keys = (
            "unrelated.title", "unrelated.heading", "unrelated.hint", "unrelated.plan",
            "unrelated.compare", "unrelated.removed_hint", "unrelated.and_more", "unrelated.files",
            "unrelated.summary_label", "unrelated.apply", "unrelated.done",
            "unrelated.blocked_gone", "unrelated.blocked_summary", "unrelated.notice",
            "unrelated.notice_hint", "unrelated.none", "unrelated.menu",
            "tip.unrelated_menu", "tip.unrelated_apply", "tip.unrelated_summary",
        )
        for language in ("en", "de"):
            i18n.set_language(language)
            self.addCleanup(i18n.set_language, "en")
            for key in keys:
                with self.subTest(language=language, key=key):
                    self.assertNotEqual(key, i18n.t(key, local=1, server=2, added=1, changed=1, removed=1, count=1, name="x"))


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

    def _stub_dialog(self, accept: bool, summary: str = "Bring my project in") -> list:
        """
        Replaces the window by one that answers at once.

        Args:
            accept: Whether to press the button that applies the fix.
            summary: What to enter as the name of the new version.

        Returns:
            list: Every window shown, with what it was shown.
        """

        import ui.main_window as module

        shown: list = []

        class Stub:
            DialogCode = QDialog.DialogCode

            def __init__(self, name, found, dirty=False, parent=None) -> None:  # noqa: ANN001
                shown.append((name, found, dirty))
                self.summary = summary

            def exec(self) -> int:  # noqa: A003
                return int(QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected)

        real = module.UnrelatedDialog
        module.UnrelatedDialog = Stub
        self.addCleanup(setattr, module, "UnrelatedDialog", real)
        return shown

    def test_opening_the_project_offers_the_fix(self) -> None:
        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)
            window = self._window()
            shown = self._stub_dialog(accept=False)
            _o, entry = window._registry.add(mine.root)
            window._sidebar.refresh()

            window._activate(entry)

            self.assertEqual(1, len(shown))
            self.assertEqual("origin/main", shown[0][1].remote_ref)

    def test_cancelling_is_remembered_but_the_reason_stays_on_screen(self) -> None:
        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)
            window = self._window()
            shown = self._stub_dialog(accept=False)
            _o, entry = window._registry.add(mine.root)
            window._sidebar.refresh()
            window._activate(entry)

            window._reload_current()
            window._offer_unrelated_fix()

            self.assertEqual(1, len(shown))
            self.assertTrue(window._notice.isVisibleTo(window))
            self.assertEqual(i18n.t("unrelated.notice"), window._notice._title.text())

    def test_the_menu_entry_asks_again_by_hand(self) -> None:
        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)
            window = self._window()
            shown = self._stub_dialog(accept=False)
            _o, entry = window._registry.add(mine.root)
            window._sidebar.refresh()
            window._activate(entry)

            window._unrelated_action.trigger()

            self.assertEqual(2, len(shown))

    def test_accepting_joins_the_server_and_uploads(self) -> None:
        with temp_repo_pair() as (first, _second, origin), temp_repo() as mine:
            make_unrelated(first, mine)
            window = self._window()
            self._stub_dialog(accept=True)
            _o, entry = window._registry.add(mine.root)
            window._sidebar.refresh()

            window._activate(entry)

            self.assertEqual(mine.head(), server_tip(first, origin))
            self.assertEqual("origin/main", read_state(mine.root).upstream)
            self.assertIsNone(unrelated_mod.detect(mine.root))
            self.assertFalse(window._notice.isVisibleTo(window))

    def test_a_refused_upload_finds_the_cause_and_offers_the_fix(self) -> None:
        # The situation as it arose: the first upload of a project that was
        # joined to a server with a history of its own.
        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)
            mine.git("update-ref", "-d", "refs/remotes/origin/main")
            window = self._window()
            shown = self._stub_dialog(accept=False)
            _o, entry = window._registry.add(mine.root)
            window._sidebar.refresh()
            window._activate(entry)
            self.assertEqual([], shown)

            window._do_push()

            self.assertEqual(1, len(shown))

    def test_a_project_that_is_merely_behind_is_not_asked_about(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("news.txt", "x\n", "News")
            first.git("push", "-q", "origin", "main")
            second.git("fetch", "-q", "origin")
            window = self._window()
            shown = self._stub_dialog(accept=False)
            _o, entry = window._registry.add(second.root)
            window._sidebar.refresh()

            window._activate(entry)

            self.assertEqual([], shown)
            window._refresh_branch_menu()
            self.assertFalse(window._unrelated_action.isEnabled())


@requires_git
class ListMarkTests(unittest.TestCase):
    """
    The check, the update and the badge that tells it in the project list.
    """

    def _scan(self, root: Path):  # noqa: ANN202
        from services import scanner

        return scanner.scan(scanner.ScanRequest(path=root, key=str(root), check_online=False))

    def test_the_check_marks_a_project_whose_history_the_server_does_not_share(self) -> None:
        from services import scanner

        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)
            result = self._scan(mine.root)
            self.assertTrue(result.status.unrelated)
            self.assertIn(("unrelated", "warning", 0), scanner.describe(result.status))

    def test_an_ordinary_project_is_not_marked(self) -> None:
        from services import scanner

        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("news.txt", "x\n", "News")
            first.git("push", "-q", "origin", "main")
            second.git("fetch", "-q", "origin")
            result = self._scan(second.root)
            self.assertFalse(result.status.unrelated)
            self.assertNotIn("unrelated", [kind for kind, _t, _c in scanner.describe(result.status)])

    def test_the_mark_survives_a_restart(self) -> None:
        from models.repository import RepoStatus

        again = RepoStatus.from_dict(RepoStatus(unrelated=True).to_dict())
        self.assertTrue(again.unrelated)
        self.assertFalse(RepoStatus.from_dict({}).unrelated)

    def test_the_mark_stands_with_the_other_things_that_need_attention(self) -> None:
        from models.repository import RepoEntry, RepoStatus
        from models.sort import changes_rank

        marked = RepoEntry(path="/tmp/a", status=RepoStatus(unrelated=True))
        clean = RepoEntry(path="/tmp/b", status=RepoStatus())
        dirty = RepoEntry(path="/tmp/c", status=RepoStatus(changed_files=2))
        self.assertLess(changes_rank(marked), changes_rank(clean))
        self.assertEqual(changes_rank(dirty), changes_rank(marked))

    def test_the_overview_counts_them(self) -> None:
        from models.repository import RepoEntry, RepoStatus
        from services.registry import Registry

        registry = Registry()
        for name, status in (("a", RepoStatus(unrelated=True)), ("b", RepoStatus(unrelated=True)), ("c", RepoStatus())):
            registry._entries.append(RepoEntry(path=f"/tmp/{name}", status=status))
        self.assertEqual(2, registry.summary()["unrelated"])

    def test_an_update_fetches_and_says_why_it_did_not_update(self) -> None:
        from services import puller

        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)
            # Never fetched: the update is what brings the server's branch in.
            mine.git("update-ref", "-d", "refs/remotes/origin/main")
            self.assertIsNone(unrelated_mod.detect(mine.root))

            result = puller.pull_one(puller.PullJob(path=mine.root, key="k", name="mine"))

            self.assertEqual(puller.RESULT_SKIPPED, result.state)
            self.assertEqual(puller.SKIP_UNRELATED, result.reason_key)
            self.assertEqual(0, result.waiting)
            self.assertIsNotNone(unrelated_mod.detect(mine.root))

    def test_an_update_of_an_ordinary_project_is_not_called_unrelated(self) -> None:
        from services import puller

        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("news.txt", "x\n", "News")
            first.git("push", "-q", "origin", "main")
            second.git("branch", "--unset-upstream", check=False)
            second.git("branch", "--set-upstream-to=origin/main", "main", check=False)
            second.commit_file("mine.txt", "y\n", "Mine")
            result = puller.pull_one(puller.PullJob(path=second.root, key="k", name="second"))
            self.assertNotEqual(puller.SKIP_UNRELATED, result.reason_key)

    def test_sending_everything_says_the_same(self) -> None:
        from services import pusher, puller

        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)

            result = pusher.push_one(puller.PullJob(path=mine.root, key="k", name="mine"))

            self.assertEqual(puller.SKIP_UNRELATED, result.reason_key)

    def test_every_new_text_exists_in_both_languages(self) -> None:
        keys = (
            "unrelated.badge", "sidebar.summary_unrelated_one",
            "sidebar.summary_unrelated_many", "pull_all.skip_unrelated",
        )
        for language in ("en", "de"):
            i18n.set_language(language)
            self.addCleanup(i18n.set_language, "en")
            for key in keys:
                with self.subTest(language=language, key=key):
                    self.assertNotEqual(key, i18n.t(key, count=2))


@requires_qt
@requires_git
class ListMarkWindowTests(unittest.TestCase):
    """
    The badge as the project list shows it, after a check and after an update.
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

    def _badges(self, window, key: str) -> list[str]:  # noqa: ANN001
        from ui.widgets import Badge

        row = window._sidebar._rows[key]
        return [badge._kind for badge in row.findChildren(Badge)]

    def _settle(self, window) -> None:  # noqa: ANN001
        import time

        deadline = time.time() + 15
        while time.time() < deadline:
            self.app.processEvents()
            if not window._scans.is_running and window._pending_scan_key is None:
                break
            time.sleep(0.01)
        self.app.processEvents()

    def test_a_check_puts_the_attention_mark_on_the_row(self) -> None:
        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)
            window = self._window()
            _o, entry = window._registry.add(mine.root)
            window._sidebar.refresh()
            self.assertNotIn("unrelated", self._badges(window, entry.key))

            window._start_scan("")
            self._settle(window)

            self.assertIn("unrelated", self._badges(window, entry.key))
            from ui.widgets import Badge

            badge = next(
                b for b in window._sidebar._rows[entry.key].findChildren(Badge) if b._kind == "unrelated"
            )
            self.assertEqual("\u26a0", badge.text())
            self.assertEqual(i18n.t("unrelated.badge"), badge.toolTip())

    def test_the_overview_under_the_list_says_so(self) -> None:
        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)
            window = self._window()
            window._registry.add(mine.root)
            window._sidebar.refresh()

            window._start_scan("")
            self._settle(window)

            self.assertIn(
                i18n.t("sidebar.summary_unrelated_one"), window._sidebar._summary.text()
            )

    def test_updating_all_projects_finds_it_for_a_project_never_fetched(self) -> None:
        import ui.main_window as module
        from services import puller

        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)
            mine.git("update-ref", "-d", "refs/remotes/origin/main")
            window = self._window()
            _o, entry = window._registry.add(mine.root)
            window._sidebar.refresh()

            class Immediate:
                def __init__(self, jobs: list, *_args: object, **_kwargs: object) -> None:
                    self._jobs = jobs
                    self.results: list = []
                    self.summary = None

                def exec(self) -> int:
                    self.results = [puller.pull_one(job) for job in self._jobs]
                    return 1

            real = module.PullAllDialog
            module.PullAllDialog = Immediate
            self.addCleanup(setattr, module, "PullAllDialog", real)

            window._pull_all()
            self._settle(window)

            self.assertTrue(entry.status.unrelated)
            self.assertIn("unrelated", self._badges(window, entry.key))

    def test_a_project_that_is_fixed_loses_the_mark_after_the_next_check(self) -> None:
        with temp_repo_pair() as (first, _second, _origin), temp_repo() as mine:
            make_unrelated(first, mine)
            window = self._window()
            _o, entry = window._registry.add(mine.root)
            window._sidebar.refresh()
            window._start_scan("")
            self._settle(window)
            self.assertTrue(entry.status.unrelated)

            unrelated_mod.resolve(mine.root, "Bring my project in")
            window._start_scan("")
            self._settle(window)

            self.assertFalse(entry.status.unrelated)
            self.assertNotIn("unrelated", self._badges(window, entry.key))


if __name__ == "__main__":
    unittest.main()
