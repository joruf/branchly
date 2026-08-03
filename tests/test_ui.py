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
from tests.support import requires_git, temp_repo

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
        settings = AppSettings(auto_check_minutes=0, github_enabled=False, language="en")
        window = MainWindow(settings)
        outcome, entry = window._registry.add(repo_path)  # noqa: SLF001 - setup shortcut
        self.assertIsNotNone(entry)
        del outcome
        window._sidebar.refresh()  # noqa: SLF001
        assert entry is not None
        window._activate(entry)  # noqa: SLF001
        return window

    def test_clean_repository_shows_nothing_changed(self) -> None:
        import tempfile

        with temp_repo() as repo:
            with tempfile.TemporaryDirectory() as config:
                window = self._window(repo.root, config)
                try:
                    self.assertEqual("main", window._branch_label.text())  # noqa: SLF001
                    self.assertEqual([], window._changes.checked_paths())  # noqa: SLF001
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
                    ticked = window._changes.checked_paths()  # noqa: SLF001
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
                    window._show_file_diff("app.py", False)  # noqa: SLF001
                    self.assertEqual("app.py", window._diff.current_path())  # noqa: SLF001
                finally:
                    window.close()

    def test_graph_tab_lists_commits(self) -> None:
        import tempfile

        with temp_repo() as repo:
            repo.commit_file("second.txt", "x\n", "second commit")
            with tempfile.TemporaryDirectory() as config:
                window = self._window(repo.root, config)
                try:
                    window._tabs.setCurrentIndex(1)  # noqa: SLF001
                    self.app.processEvents()
                    tree = window._graph._tree  # noqa: SLF001
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
                    window._do_commit(CommitDraft(summary="add a note"), ["note.txt"])  # noqa: SLF001
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
                    entry = window._registry.entries[0]  # noqa: SLF001
                    window._activate(entry)  # noqa: SLF001
                    # The window is never shown in tests, so isVisible() is
                    # always False; isVisibleTo asks the question that matters.
                    self.assertTrue(window._notice.isVisibleTo(window))  # noqa: SLF001
                finally:
                    window.close()


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
                dialog._show_current_decision()  # noqa: SLF001
                self.assertIn("1", dialog._progress_label.text())  # noqa: SLF001
                ours = dialog._ours_view.toPlainText()  # noqa: SLF001
                theirs = dialog._theirs_view.toPlainText()  # noqa: SLF001
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
                dialog._show_current_decision()  # noqa: SLF001
                dialog._choose(conflict_mod.CHOICE_OURS)  # noqa: SLF001
                dialog._write_and_finish()  # noqa: SLF001
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
                dialog._show_current_decision()  # noqa: SLF001
                self.assertEqual("", dialog._result_view.toPlainText())  # noqa: SLF001
                dialog._decisions[0].set_choice(conflict_mod.CHOICE_BOTH)  # noqa: SLF001
                dialog._show_current_decision()  # noqa: SLF001
                result = dialog._result_view.toPlainText()  # noqa: SLF001
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
                self.assertEqual(2, dialog._stack.currentIndex())  # noqa: SLF001
            finally:
                dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
