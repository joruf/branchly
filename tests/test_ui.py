"""
Tests for the widgets.

The diff renderer is tested as a plain function, which is where the interesting
logic sits. The window itself gets a smoke test: build it against a real
repository, drive the panels, and check that nothing blows up and the texts land
where they should.

Requires an offscreen Qt platform, which the CI workflow sets via
``QT_QPA_PLATFORM=offscreen``.
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
from config.theme import THEME_DARK, THEME_LIGHT, get_theme_colors
from gitops.diff import parse_unified
from tests.support import requires_git, temp_repo, temp_repo_pair

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")

SAMPLE_DIFF = (
    "diff --git a/config.py b/config.py\n"
    "--- a/config.py\n"
    "+++ b/config.py\n"
    "@@ -1,3 +1,3 @@\n"
    " import os\n"
    "-port = 8080\n"
    "+port = 3000\n"
)


@requires_qt
class DiffRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        from ui import diff_view

        self.diff_view = diff_view
        self.diff = parse_unified(SAMPLE_DIFF)
        self.colors = get_theme_colors(THEME_DARK)

    def test_side_by_side_has_both_versions(self) -> None:
        html = self.diff_view.render_side_by_side(self.diff, self.colors)
        # The changed number is wrapped in highlight markup, so the line is not
        # present as one literal string.
        self.assertIn("8080", html)
        self.assertIn("3000", html)
        self.assertEqual(2, html.count("port = "))
        self.assertIn("<table>", html)

    def test_unified_has_markers(self) -> None:
        html = self.diff_view.render_unified(self.diff, self.colors)
        self.assertIn("8080", html)
        self.assertIn("−", html)
        self.assertIn(">+<", html)

    def test_word_level_highlight_is_present(self) -> None:
        html = self.diff_view.render_side_by_side(self.diff, self.colors, word_level=True)
        self.assertIn(self.colors.diff_word_added_bg, html)

    def test_word_level_can_be_switched_off(self) -> None:
        html = self.diff_view.render_side_by_side(self.diff, self.colors, word_level=False)
        self.assertNotIn(self.colors.diff_word_added_bg, html)

    def test_html_is_escaped(self) -> None:
        payload = (
            "diff --git a/x.html b/x.html\n--- a/x.html\n+++ b/x.html\n@@ -1 +1 @@\n"
            "-<script>alert('a')</script>\n+<b>safe</b>\n"
        )
        html = self.diff_view.render_side_by_side(parse_unified(payload), self.colors)
        # No raw tag from the file may survive into the document. The word-level
        # highlight splits the text, so the angle brackets are checked on their
        # own rather than as part of a whole tag.
        self.assertNotIn("<script", html)
        self.assertNotIn("</script", html)
        self.assertNotIn("<b>", html)
        self.assertIn("&lt;", html)
        self.assertIn("&gt;", html)
        self.assertIn("alert(&#x27;a&#x27;)".replace("&#x27;", "'"), html)

    def test_tabs_become_spaces(self) -> None:
        payload = "diff --git a/t b/t\n--- a/t\n+++ b/t\n@@ -1 +1 @@\n-\tindented\n+\tchanged\n"
        html = self.diff_view.render_unified(parse_unified(payload), self.colors)
        self.assertNotIn("\t", html)
        self.assertIn(" " * self.diff_view.TAB_WIDTH, html)

    def test_both_themes_render(self) -> None:
        for theme in (THEME_DARK, THEME_LIGHT):
            html = self.diff_view.render_side_by_side(self.diff, get_theme_colors(theme))
            self.assertIn(get_theme_colors(theme).surface, html)

    def test_many_files_are_concatenated(self) -> None:
        payload = SAMPLE_DIFF + (
            "diff --git a/other.py b/other.py\n--- a/other.py\n+++ b/other.py\n@@ -1 +1 @@\n"
            "-one\n+two\n"
        )
        from gitops.diff import parse_multi

        diffs = parse_multi(payload)
        self.assertEqual(2, len(diffs))
        html = self.diff_view.render_many(diffs, "side_by_side", self.colors)
        self.assertIn("config.py", html)
        self.assertIn("other.py", html)

    def test_empty_list_still_produces_a_document(self) -> None:
        html = self.diff_view.render_many([], "unified", self.colors)
        self.assertIn("<style>", html)


@requires_qt
class WidgetHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])

    def test_relative_check_time_wording(self) -> None:
        from ui.widgets import format_relative_check_time

        i18n.set_language("en")
        self.assertIn("not checked", format_relative_check_time(None, 1000.0))
        self.assertIn("just now", format_relative_check_time(1000.0, 1030.0))
        self.assertIn("5", format_relative_check_time(1000.0, 1000.0 + 5 * 60))
        self.assertIn("2", format_relative_check_time(1000.0, 1000.0 + 2 * 3600))
        self.assertIn("3", format_relative_check_time(1000.0, 1000.0 + 3 * 86400))

    def test_unknown_token_falls_back_instead_of_raising(self) -> None:
        from ui.widgets import token_color

        self.assertEqual(get_theme_colors().text, token_color("no_such_token"))

    def test_badges_carry_a_tooltip(self) -> None:
        from ui.widgets import Badge

        badge = Badge("changed", 3)
        self.assertIn("3", badge.text())
        self.assertTrue(badge.toolTip())


@requires_qt
@requires_git
class MainWindowSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, repo_path, tmp_config):  # noqa: ANN001, ANN202 - test helper
        """
        Builds a window whose registry holds exactly one repository.

        Args:
            repo_path: Working tree to register.
            tmp_config: Directory used for settings and registry files.

        Returns:
            MainWindow: The window, already showing the repository.
        """

        from config.app_settings import AppSettings
        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = str(tmp_config)
        # No automatic checks of any kind: a test must never depend on a network.
        settings = AppSettings(
            auto_check_minutes=0, github_enabled=False, check_updates=False, language="en"
        )
        window = MainWindow(settings)
        outcome, entry = window._registry.add(repo_path)
        self.assertIsNotNone(entry)
        del outcome
        window._sidebar.refresh()
        assert entry is not None
        window._activate(entry)
        return window

    def test_clean_repository_shows_nothing_changed(self) -> None:
        import tempfile

        with temp_repo() as repo:
            with tempfile.TemporaryDirectory() as config:
                window = self._window(repo.root, config)
                try:
                    self.assertEqual("main", window._branch_label.text())
                    self.assertEqual([], window._changes.checked_paths())
                finally:
                    window.close()

    def test_changed_files_appear_and_are_ticked(self) -> None:
        import tempfile

        with temp_repo() as repo:
            repo.write("README.md", "# changed\n")
            repo.write("extra.txt", "new file\n")
            with tempfile.TemporaryDirectory() as config:
                window = self._window(repo.root, config)
                try:
                    ticked = window._changes.checked_paths()
                    self.assertEqual({"README.md", "extra.txt"}, set(ticked))
                finally:
                    window.close()

    def test_selecting_a_file_fills_the_diff(self) -> None:
        import tempfile

        with temp_repo() as repo:
            repo.commit_file("app.py", "port = 8080\n")
            repo.write("app.py", "port = 3000\n")
            with tempfile.TemporaryDirectory() as config:
                window = self._window(repo.root, config)
                try:
                    window._show_file_diff("app.py", False)
                    self.assertEqual("app.py", window._diff.current_path())
                finally:
                    window.close()

    def test_graph_tab_lists_commits(self) -> None:
        import tempfile

        with temp_repo() as repo:
            repo.commit_file("second.txt", "x\n", "second commit")
            with tempfile.TemporaryDirectory() as config:
                window = self._window(repo.root, config)
                try:
                    window._tabs.setCurrentIndex(1)
                    self.app.processEvents()
                    tree = window._graph._tree
                    self.assertEqual(2, tree.topLevelItemCount())
                    self.assertIn("second commit", tree.topLevelItem(0).text(1))
                finally:
                    window.close()

    def test_commit_flow_creates_a_commit(self) -> None:
        import tempfile

        from gitops.commit import CommitDraft, commit_details

        with temp_repo() as repo:
            repo.write("note.txt", "content\n")
            with tempfile.TemporaryDirectory() as config:
                window = self._window(repo.root, config)
                try:
                    window._do_commit(CommitDraft(summary="add a note"), ["note.txt"])
                    details = commit_details(repo.root, "HEAD")
                    self.assertIsNotNone(details)
                    assert details is not None
                    self.assertEqual("add a note", details["subject"])
                finally:
                    window.close()

    def test_missing_repository_says_so(self) -> None:
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as outer:
            from pathlib import Path

            from gitops.runner import run

            repo_path = Path(outer) / "gone"
            repo_path.mkdir()
            run(["init", "-b", "main"], cwd=repo_path)
            with tempfile.TemporaryDirectory() as config:
                window = self._window(repo_path, config)
                try:
                    shutil.rmtree(repo_path)
                    entry = window._registry.entries[0]
                    window._activate(entry)
                    # The window is never shown in tests, so isVisible() is
                    # always False; isVisibleTo asks the question that matters.
                    self.assertTrue(window._notice.isVisibleTo(window))
                finally:
                    window.close()

    def test_the_bulk_pull_is_offered_in_the_menu(self) -> None:
        import tempfile

        from PySide6.QtWidgets import QMenu

        with temp_repo() as repo:
            with tempfile.TemporaryDirectory() as config:
                window = self._window(repo.root, config)
                try:
                    labels = [
                        action.text()
                        for menu in window.menuBar().findChildren(QMenu)
                        for action in menu.actions()
                    ]
                    self.assertIn(i18n.t("sidebar.pull_all"), labels)
                finally:
                    window.close()

    def test_the_sidebar_button_reaches_the_window(self) -> None:
        """
        Proves the wiring without opening the modal dialog: with a check running,
        the request is answered by a message instead of by a window.
        """

        import tempfile

        with temp_repo() as repo:
            with tempfile.TemporaryDirectory() as config:
                window = self._window(repo.root, config)
                try:
                    window._scans._running = True
                    window._sidebar.pull_all_requested.emit()
                    self.assertEqual(
                        i18n.t("pull_all.busy"), window.statusBar().currentMessage()
                    )
                finally:
                    window._scans._running = False
                    window.close()

    def test_a_bulk_pull_without_projects_says_so(self) -> None:
        import tempfile

        with temp_repo() as repo:
            with tempfile.TemporaryDirectory() as config:
                window = self._window(repo.root, config)
                try:
                    for entry in list(window._registry.entries):
                        window._registry.remove(entry)
                    window._pull_all()
                    self.assertTrue(window._notice.isVisibleTo(window))
                finally:
                    window.close()

    def test_the_update_banner_appears_and_can_be_dismissed(self) -> None:
        import tempfile

        from services.updater import UpdateInfo
        from ui.main_window import ACTION_UPDATE_LATER

        info = UpdateInfo(available=True, local="a" * 10, remote="b" * 10, summary="Faster graph")
        with temp_repo() as repo:
            with tempfile.TemporaryDirectory() as config:
                window = self._window(repo.root, config)
                try:
                    self.assertFalse(window._update_banner.isVisibleTo(window))
                    window._show_update_banner(info)
                    self.assertTrue(window._update_banner.isVisibleTo(window))
                    window._on_update_banner_action(ACTION_UPDATE_LATER)
                    self.assertFalse(window._update_banner.isVisibleTo(window))
                    self.assertFalse(window.wants_restart())
                finally:
                    window.close()

    def test_a_quiet_failed_check_says_nothing(self) -> None:
        import tempfile

        from services.updater import ERROR_OFFLINE, UpdateInfo

        with temp_repo() as repo:
            with tempfile.TemporaryDirectory() as config:
                window = self._window(repo.root, config)
                try:
                    window._on_update_checked(UpdateInfo(error_key=ERROR_OFFLINE))
                    self.assertFalse(window._update_banner.isVisibleTo(window))
                finally:
                    window.close()

    def test_a_check_remembers_what_it_found(self) -> None:
        import tempfile

        from services.updater import UpdateInfo

        info = UpdateInfo(available=True, local="a" * 10, remote="b" * 10, summary="Faster graph")
        with temp_repo() as repo:
            with tempfile.TemporaryDirectory() as config:
                window = self._window(repo.root, config)
                try:
                    window._on_update_checked(info)
                    self.assertEqual("b" * 10, window._settings.update_remote_commit)
                    self.assertEqual("Faster graph", window._settings.update_remote_summary)

                    # A later check that finds nothing must clear the note again.
                    window._on_update_checked(
                        UpdateInfo(available=False, local="b" * 10, remote="b" * 10)
                    )
                    self.assertEqual("", window._settings.update_remote_commit)
                finally:
                    window.close()

    def test_a_remembered_update_is_announced_again_without_a_network(self) -> None:
        """
        The regression this guards: dismissing the notice with "not now" used to
        silence Branchly for a day, because the throttle stopped the next check
        and nothing else remembered the find.
        """

        import tempfile

        with temp_repo() as repo:
            with tempfile.TemporaryDirectory() as config:
                window = self._window(repo.root, config)
                try:
                    window._settings.update_remote_commit = "b" * 10
                    window._settings.update_remote_summary = "Faster graph"
                    window._show_pending_update()
                    self.assertTrue(window._update_banner.isVisibleTo(window))
                    # Re-checking is left to the install click, so the live result
                    # stays empty and cannot be installed from stale data.
                    self.assertIsNone(window._update_info)
                finally:
                    window.close()

    def test_an_installed_update_is_forgotten(self) -> None:
        import tempfile

        with temp_repo() as repo:
            with tempfile.TemporaryDirectory() as config:
                window = self._window(repo.root, config)
                try:
                    # Branchly itself is the checkout here, so its own HEAD is what
                    # a pending commit is compared against.
                    from services import updater

                    head = updater.local_commit()
                    window._settings.update_remote_commit = head[:10]
                    window._settings.update_remote_summary = "Already installed"
                    window._show_pending_update()
                    self.assertFalse(window._update_banner.isVisibleTo(window))
                    self.assertEqual("", window._settings.update_remote_commit)
                finally:
                    window.close()


@requires_qt
class UpdateDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_an_available_update_offers_the_install_button(self) -> None:
        from services.updater import UpdateInfo
        from ui.update_dialog import UpdateDialog

        info = UpdateInfo(available=True, local="a" * 10, remote="b" * 10, summary="Faster graph")
        dialog = UpdateDialog(info)
        try:
            self.assertTrue(dialog._install.isVisibleTo(dialog))
            self.assertIn("b" * 10, dialog._detail.text())
            self.assertFalse(dialog.restart_wanted)
        finally:
            dialog.close()

    def test_the_newest_version_offers_nothing(self) -> None:
        from services.updater import UpdateInfo
        from ui.update_dialog import UpdateDialog

        info = UpdateInfo(available=False, local="a" * 10, remote="a" * 10)
        dialog = UpdateDialog(info)
        try:
            self.assertFalse(dialog._install.isVisibleTo(dialog))
        finally:
            dialog.close()

    def test_a_failed_check_is_shown_without_an_install_button(self) -> None:
        from services.updater import ERROR_OFFLINE, UpdateInfo
        from ui.update_dialog import UpdateDialog

        dialog = UpdateDialog(UpdateInfo(error_key=ERROR_OFFLINE, detail="no route to host"))
        try:
            self.assertFalse(dialog._install.isVisibleTo(dialog))
            self.assertIn("no route", dialog._detail.text())
        finally:
            dialog.close()

    def test_the_description_names_the_commit_and_what_happens(self) -> None:
        from services.updater import UpdateInfo
        from ui.update_dialog import describe_update

        text = describe_update(UpdateInfo(available=True, summary="Faster graph"))
        self.assertIn("Faster graph", text)
        self.assertIn(i18n.t("update.available_hint"), text)

    def test_a_commit_without_a_subject_still_describes_the_update(self) -> None:
        from services.updater import UpdateInfo
        from ui.update_dialog import describe_update

        self.assertEqual(
            i18n.t("update.available_hint"), describe_update(UpdateInfo(available=True))
        )

    def test_several_commits_are_counted_instead_of_named(self) -> None:
        from services.updater import UpdateInfo
        from ui.update_dialog import describe_update

        text = describe_update(
            UpdateInfo(available=True, summary="Newest", count=7, changes=("Newest", "Older"))
        )
        self.assertIn("7", text)
        self.assertNotIn("Newest", text, "one subject must not stand in for seven commits")

    def test_the_dialog_lists_what_is_new(self) -> None:
        from services.updater import UpdateInfo
        from ui.update_dialog import UpdateDialog

        info = UpdateInfo(
            available=True,
            local="a" * 10,
            remote="b" * 10,
            summary="Faster graph",
            count=3,
            changes=("Faster graph", "Fix the sidebar", "Update the manual"),
        )
        dialog = UpdateDialog(info)
        try:
            self.assertTrue(dialog._changes_area.isVisibleTo(dialog))
            listed = dialog._changes.text()
            for subject in info.changes:
                self.assertIn(subject, listed)
            self.assertNotIn("…and", listed, "nothing was left out here")
        finally:
            dialog.close()

    def test_a_capped_list_says_how_many_were_left_out(self) -> None:
        from services.updater import UpdateInfo
        from ui.update_dialog import UpdateDialog

        info = UpdateInfo(
            available=True,
            local="a" * 10,
            remote="b" * 10,
            count=25,
            changes=tuple(f"Change {number}" for number in range(10)),
        )
        dialog = UpdateDialog(info)
        try:
            self.assertIn(i18n.t("update.changes_more", count=15), dialog._changes.text())
        finally:
            dialog.close()

    def test_without_changes_the_list_stays_away(self) -> None:
        from services.updater import UpdateInfo
        from ui.update_dialog import UpdateDialog

        dialog = UpdateDialog(UpdateInfo(available=True, local="a" * 10, remote="b" * 10))
        try:
            self.assertFalse(dialog._changes_area.isVisibleTo(dialog))
            self.assertFalse(dialog._changes_title.isVisibleTo(dialog))
        finally:
            dialog.close()


@requires_qt
class PullAllWordingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_a_pulled_project_says_how_many_commits(self) -> None:
        from services.puller import RESULT_PULLED, PullResult
        from ui.pull_all_dialog import describe_result

        text = describe_result(PullResult(key="a", name="pmtool", state=RESULT_PULLED, commits=3))
        self.assertIn("pmtool", text)
        self.assertIn("3", text)

    def test_a_current_project_says_so(self) -> None:
        from services.puller import RESULT_CURRENT, PullResult
        from ui.pull_all_dialog import describe_result

        text = describe_result(PullResult(key="a", name="pmtool", state=RESULT_CURRENT))
        self.assertIn(i18n.t("pull_all.already_current"), text)

    def test_a_skipped_project_names_the_reason_and_what_waits(self) -> None:
        from services.puller import RESULT_SKIPPED, SKIP_DIRTY, PullResult
        from ui.pull_all_dialog import describe_result

        text = describe_result(
            PullResult(key="a", name="snappix", state=RESULT_SKIPPED, reason_key=SKIP_DIRTY, waiting=2)
        )
        self.assertIn("snappix", text)
        self.assertIn(i18n.t(SKIP_DIRTY), text)
        self.assertIn(i18n.t("pull_all.waiting", count=2), text)

    def test_an_empty_run_cannot_be_started(self) -> None:
        from ui.pull_all_dialog import PullAllDialog

        dialog = PullAllDialog([])
        try:
            self.assertFalse(dialog._start.isEnabled())
            self.assertEqual([], dialog.results)
        finally:
            dialog.close()


@requires_qt
@requires_git
class PullAllRunTests(unittest.TestCase):
    """
    The whole action end to end: real repositories, real git, real event loop.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _drain(self, dialog, timeout: float = 90.0) -> None:  # noqa: ANN001 - PullAllDialog
        """
        Spins the event loop until the batch reports itself finished.

        Args:
            dialog: Dialog whose run should complete.
            timeout: Seconds before giving up.

        Returns:
            None
        """

        import time

        deadline = time.monotonic() + timeout
        while dialog._running and time.monotonic() < deadline:
            self.app.processEvents()
        self.assertFalse(dialog._running, "the batch never finished")

    def test_one_project_is_pulled_and_another_is_skipped(self) -> None:
        from services.puller import RESULT_PULLED, RESULT_SKIPPED, SKIP_NO_REMOTE, PullJob
        from ui.pull_all_dialog import PullAllDialog

        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("shared.txt", "theirs\n", "their commit")
            first.git("push", "origin", "main")
            with temp_repo() as lonely:
                jobs = [
                    PullJob(path=second.root, key="pair", name="pair"),
                    PullJob(path=lonely.root, key="lonely", name="lonely"),
                ]
                dialog = PullAllDialog(jobs)
                try:
                    dialog._begin()
                    self._drain(dialog)

                    by_key = {item.key: item for item in dialog.results}
                    self.assertEqual(2, len(by_key))
                    self.assertEqual(RESULT_PULLED, by_key["pair"].state)
                    self.assertEqual(1, by_key["pair"].commits)
                    self.assertEqual(RESULT_SKIPPED, by_key["lonely"].state)
                    self.assertEqual(SKIP_NO_REMOTE, by_key["lonely"].reason_key)

                    # The report is on screen, and the window can be left again.
                    self.assertEqual(2, dialog._list.count())
                    self.assertTrue(dialog._close.isEnabled())
                    self.assertFalse(dialog._start.isVisibleTo(dialog))
                    self.assertEqual(first.head(), second.head())
                finally:
                    dialog.close()

    def test_the_window_refuses_to_close_mid_run(self) -> None:
        """
        Closing while workers are writing would leave them updating projects
        nobody is watching, so the way out is to let the batch finish.
        """

        from services.puller import PullJob
        from ui.pull_all_dialog import PullAllDialog

        with temp_repo() as repo:
            dialog = PullAllDialog([PullJob(path=repo.root, key="one", name="one")])
            left: list[bool] = []
            dialog.rejected.connect(lambda: left.append(True))
            try:
                dialog._running = True
                dialog.reject()
                self.assertEqual([], left, "the dialog left while a batch was running")

                dialog._running = False
                dialog.reject()
                self.assertEqual([True], left, "and closes normally once it is done")
            finally:
                dialog._running = False
                dialog.close()


@requires_qt
@requires_git
class ConflictAssistantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _conflicted(repo) -> None:  # noqa: ANN001 - TempRepo fixture
        """
        Produces a text conflict in ``config.py`` on the main branch.

        Args:
            repo: Fixture to work in.

        Returns:
            None
        """

        from gitops import branch as branch_mod

        repo.commit_file("config.py", "port = 1234\ndebug = True\n", "base")
        branch_mod.create_branch(repo.root, "side")
        repo.commit_file("config.py", "port = 3000\ndebug = True\n", "their change")
        branch_mod.checkout_branch(repo.root, "main")
        repo.commit_file("config.py", "port = 8080\ndebug = True\n", "our change")
        branch_mod.merge(repo.root, "side")

    def test_dialog_lists_one_decision_and_hides_the_markers(self) -> None:
        from pathlib import Path

        from gitops import conflict as conflict_mod
        from ui.conflict_dialog import ConflictDialog

        with temp_repo() as repo:
            self._conflicted(repo)
            files = conflict_mod.load_all(repo.root)
            dialog = ConflictDialog(Path(repo.root), files, None)
            try:
                dialog._show_current_decision()
                self.assertIn("1", dialog._progress_label.text())
                ours = dialog._ours_view.toPlainText()
                theirs = dialog._theirs_view.toPlainText()
                self.assertIn("8080", ours)
                self.assertIn("3000", theirs)
                for view_text in (ours, theirs):
                    self.assertNotIn("<<<<<<<", view_text)
                    self.assertNotIn(">>>>>>>", view_text)
                    self.assertNotIn("=======", view_text)
            finally:
                dialog.deleteLater()

    def test_choosing_and_finishing_completes_the_merge(self) -> None:
        from pathlib import Path

        from gitops import conflict as conflict_mod
        from gitops.status import read_state
        from ui.conflict_dialog import ConflictDialog

        with temp_repo() as repo:
            self._conflicted(repo)
            files = conflict_mod.load_all(repo.root)
            dialog = ConflictDialog(Path(repo.root), files, None)
            try:
                dialog._show_current_decision()
                dialog._choose(conflict_mod.CHOICE_OURS)
                dialog._write_and_finish()
            finally:
                dialog.deleteLater()

            written = (repo.root / "config.py").read_text(encoding="utf-8")
            self.assertIn("8080", written)
            self.assertNotIn("<<<<<<<", written)
            state = read_state(repo.root)
            self.assertFalse(state.merging)
            self.assertFalse(state.has_conflicts)

    def test_result_column_reflects_the_choice(self) -> None:
        from pathlib import Path

        from gitops import conflict as conflict_mod
        from ui.conflict_dialog import ConflictDialog

        with temp_repo() as repo:
            self._conflicted(repo)
            files = conflict_mod.load_all(repo.root)
            dialog = ConflictDialog(Path(repo.root), files, None)
            try:
                dialog._show_current_decision()
                self.assertEqual("", dialog._result_view.toPlainText())
                dialog._decisions[0].set_choice(conflict_mod.CHOICE_BOTH)
                dialog._show_current_decision()
                result = dialog._result_view.toPlainText()
                self.assertIn("8080", result)
                self.assertIn("3000", result)
            finally:
                dialog.deleteLater()

    def test_a_repository_without_conflicts_goes_straight_to_the_end(self) -> None:
        from pathlib import Path

        from ui.conflict_dialog import ConflictDialog

        with temp_repo() as repo:
            dialog = ConflictDialog(Path(repo.root), [], None)
            try:
                self.assertEqual(2, dialog._stack.currentIndex())
            finally:
                dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
