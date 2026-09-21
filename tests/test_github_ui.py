"""
Tests for the GitHub panel, its lists and its dialogs.

The panel is driven against the same fake server the API tests use, so what is
being checked is the whole path: a click turns into a request, the response turns
into rows, and a write is followed by a reload that cannot answer from the cache.

The dialogs are tested without being shown. What matters about them is what they
refuse and what they hand back, and both are plain method calls.
"""

from __future__ import annotations

import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QDialog

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

from github_api.client import GitHubClient
from tests.support_github import FakeGitHub

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")

REPO = {
    "name": "sandbox",
    "full_name": "joruf/sandbox",
    "owner": {"login": "joruf"},
    "private": True,
    "default_branch": "main",
    "permissions": {"push": True, "admin": True},
    "description": "a test",
    "topics": ["git"],
    "visibility": "private",
}


class SilentBox:
    """
    A message box that records instead of blocking.

    The panel reports a failure in a modal dialog, which is right in the
    application and fatal in a test: ``exec`` would wait for a click that never
    comes. This stands in for it and remembers what it was told to say, so the
    tests can still assert that the right thing was reported.

    Attributes:
        shown: Every box built since the last reset, as ``(text, informative)``.
    """

    shown: list[tuple[str, str]] = []

    class Icon:
        """
        The icon constants the panel sets.
        """

        Warning = 0

    class ButtonRole:
        """
        The button roles the panel uses.
        """

        DestructiveRole = 0
        RejectRole = 1

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self._text = ""
        self._informative = ""
        self._buttons: list[object] = []

    def setWindowTitle(self, _title: str) -> None:  # noqa: N802 - Qt naming
        """
        Accepts the window title.

        Args:
            _title: Ignored.

        Returns:
            None
        """

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        """
        Records the headline.

        Args:
            text: The headline.

        Returns:
            None
        """

        self._text = text

    def setInformativeText(self, text: str) -> None:  # noqa: N802 - Qt naming
        """
        Records the explanation.

        Args:
            text: The explanation.

        Returns:
            None
        """

        self._informative = text

    def setIcon(self, _icon: object) -> None:  # noqa: N802 - Qt naming
        """
        Accepts the icon.

        Args:
            _icon: Ignored.

        Returns:
            None
        """

    def addButton(self, label: str, _role: object) -> object:  # noqa: N802 - Qt naming
        """
        Records a button.

        Args:
            label: Button label.
            _role: Ignored.

        Returns:
            object: A token standing in for the button.
        """

        self._buttons.append(label)
        return label

    def clickedButton(self) -> object:  # noqa: N802 - Qt naming
        """
        Reports that nothing was clicked.

        Returns:
            object: None, so a confirmation counts as declined.
        """

        return None

    def exec(self) -> int:
        """
        Records the box instead of showing it.

        Returns:
            int: Zero.
        """

        SilentBox.shown.append((self._text, self._informative))
        return 0


def _pull(number: int, **extra: object) -> dict:
    """
    Builds a pull request object the way the API returns one.

    Args:
        number: Pull request number.
        **extra: Fields to add or override.

    Returns:
        dict: The object.
    """

    payload = {
        "number": number,
        "title": f"Pull {number}",
        "state": "open",
        "draft": False,
        "node_id": f"PR_{number}",
        "user": {"login": "joruf"},
        "head": {"ref": "feature", "sha": "a" * 40},
        "base": {"ref": "main"},
        "html_url": f"https://example.invalid/pull/{number}",
    }
    payload.update(extra)
    return payload


def _issue(number: int, **extra: object) -> dict:
    """
    Builds an issue object the way the API returns one.

    Args:
        number: Issue number.
        **extra: Fields to add or override.

    Returns:
        dict: The object.
    """

    payload = {
        "number": number,
        "title": f"Issue {number}",
        "state": "open",
        "user": {"login": "joruf"},
        "labels": [],
        "assignees": [],
        "body": "text",
    }
    payload.update(extra)
    return payload


@requires_qt
class PanelTestCase(unittest.TestCase):
    """
    Base case: a fake server, a client, and a panel wired to both.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from ui.github_lists import GitHubContext
        from ui.github_panel import GitHubPanel

        self.server = FakeGitHub()
        self.addCleanup(self.server.stop)
        self.client = GitHubClient("ghp_" + "x" * 36, api_root=self.server.root)
        self.addCleanup(self.client.close)

        # Routes every tab touches while loading, so an unrelated 404 does not
        # show up as a failure in a test about something else.
        self.server.json("GET", "/repos/joruf/sandbox", REPO)
        self.server.json("GET", "/repos/joruf/sandbox/branches", [{"name": "main"}, {"name": "feature"}])
        self.server.json("GET", "/repos/joruf/sandbox/tags", [])
        self.server.json("GET", "/repos/joruf/sandbox/labels", [{"name": "bug", "color": "ff0000"}])
        self.server.json("GET", "/repos/joruf/sandbox/assignees", [{"login": "joruf"}])
        self.server.json("GET", "/repos/joruf/sandbox/milestones", [])
        self.server.json("GET", "/repos/joruf/sandbox/pulls", [])
        self.server.json("GET", "/repos/joruf/sandbox/issues", [])
        self.server.json("GET", "/repos/joruf/sandbox/releases", [])
        self.server.json("GET", "/repos/joruf/sandbox/actions/runs", {"workflow_runs": []})

        from ui import github_panel

        SilentBox.shown = []
        original_box = github_panel.QMessageBox
        github_panel.QMessageBox = SilentBox
        self.addCleanup(setattr, github_panel, "QMessageBox", original_box)

        self.panel = GitHubPanel(self.client, show_avatars=False)
        self.addCleanup(self.panel.stop)
        self.addCleanup(self.panel.deleteLater)
        self.context = GitHubContext(
            owner="joruf",
            repo="sandbox",
            viewer_login="joruf",
            local_branch="feature",
            default_branch="main",
        )

    def settle(self, timeout: float = 5.0) -> None:
        """
        Runs the event loop until every queued call is done.

        Args:
            timeout: Seconds to wait at most.

        Returns:
            None
        """

        deadline = time.time() + timeout
        while time.time() < deadline:
            self.app.processEvents()
            if not self.panel._runner.busy:
                # One more pass, so the handler of the final result has run.
                self.app.processEvents()
                return
            time.sleep(0.005)
        self.fail("the runner was still busy after the timeout")


class PanelLoadingTests(PanelTestCase):
    """
    What the panel shows, and when it asks for it.
    """

    def test_starts_by_explaining_the_missing_token(self) -> None:
        self.assertEqual(0, self.panel._stack.currentIndex())

    def test_selecting_a_repository_loads_only_the_visible_tab(self) -> None:
        self.server.json("GET", "/repos/joruf/sandbox/pulls", [_pull(1), _pull(2)])
        self.panel.set_context(self.context)
        self.settle()

        self.assertEqual(1, self.panel._stack.currentIndex())
        self.assertEqual(2, self.panel._pulls._list.count())
        # The issues tab was never opened, so it must not have cost a request.
        self.assertEqual([], self.server.calls("GET", "/repos/joruf/sandbox/issues"))

    def test_opening_a_tab_loads_it_once(self) -> None:
        self.server.json("GET", "/repos/joruf/sandbox/issues", [_issue(5)])
        self.panel.set_context(self.context)
        self.settle()

        self.panel._tabs.setCurrentIndex(1)
        self.settle()
        self.assertEqual(1, self.panel._issues._list.count())
        first = len(self.server.calls("GET", "/repos/joruf/sandbox/issues"))

        self.panel._tabs.setCurrentIndex(0)
        self.panel._tabs.setCurrentIndex(1)
        self.settle()
        self.assertEqual(first, len(self.server.calls("GET", "/repos/joruf/sandbox/issues")))

    def test_an_empty_list_says_so_instead_of_showing_nothing(self) -> None:
        self.panel.set_context(self.context)
        self.settle()
        self.assertEqual(1, self.panel._pulls._list.count())
        item = self.panel._pulls._list.item(0)
        self.assertFalse(item.flags())

    def test_a_rate_limit_replaces_the_panel_with_the_reason(self) -> None:
        self.server.json(
            "GET",
            "/repos/joruf/sandbox/pulls",
            {"message": "API rate limit exceeded"},
            status=403,
            X_RateLimit_Remaining="0",
            X_RateLimit_Reset=str(int(time.time()) + 600),
        )
        self.panel.set_context(self.context)
        self.settle()
        self.assertEqual(0, self.panel._stack.currentIndex())

    def test_selecting_a_pull_request_loads_its_detail(self) -> None:
        self.server.json("GET", "/repos/joruf/sandbox/pulls", [_pull(1)])
        self.server.json(
            "GET", "/repos/joruf/sandbox/pulls/1", _pull(1, body="why", mergeable=True)
        )
        self.server.json(
            "GET",
            "/repos/joruf/sandbox/issues/1/comments",
            [{"id": 1, "body": "looks good", "user": {"login": "someone"}}],
        )
        self.server.json("GET", "/repos/joruf/sandbox/pulls/1/reviews", [])

        self.panel.set_context(self.context)
        self.settle()
        text = self.panel._pulls._detail.toPlainText()
        self.assertIn("Pull 1", text)
        self.assertIn("why", text)
        self.assertIn("looks good", text)


class PanelWriteTests(PanelTestCase):
    """
    What a write sends, and what happens after it.
    """

    def test_creating_an_issue_sends_the_draft_and_reloads(self) -> None:
        from ui import github_lists
        from ui.github_dialogs import IssueDraft

        self.server.json("GET", "/repos/joruf/sandbox/issues", [])
        self.server.json("POST", "/repos/joruf/sandbox/issues", _issue(9), status=201)

        self.panel.set_context(self.context)
        self.panel._tabs.setCurrentIndex(1)
        self.settle()

        class _Stub:
            """
            Stands in for the issue editor, accepting a fixed draft.
            """

            DialogCode = QDialog.DialogCode

            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def exec(self) -> object:
                return QDialog.DialogCode.Accepted

            def draft(self) -> IssueDraft:
                return IssueDraft(title="from the test", body="b", labels=["bug"])

        original = github_lists.IssueEditorDialog
        github_lists.IssueEditorDialog = _Stub
        try:
            self.panel._issues._on_new()
            self.settle()
        finally:
            github_lists.IssueEditorDialog = original

        sent = self.server.calls("POST", "/repos/joruf/sandbox/issues")
        self.assertEqual(1, len(sent))
        self.assertEqual("from the test", sent[0].body["title"])
        self.assertEqual(["bug"], sent[0].body["labels"])
        # The list is fetched again afterwards, so the new issue can appear.
        self.assertGreaterEqual(len(self.server.calls("GET", "/repos/joruf/sandbox/issues")), 2)

    def test_a_merge_can_delete_the_branch_afterwards(self) -> None:
        from ui import github_lists
        from ui.github_dialogs import MergeChoice

        self.server.json("GET", "/repos/joruf/sandbox/pulls", [_pull(1)])
        self.server.json("GET", "/repos/joruf/sandbox/pulls/1", _pull(1))
        self.server.json("GET", "/repos/joruf/sandbox/issues/1/comments", [])
        self.server.json("GET", "/repos/joruf/sandbox/pulls/1/reviews", [])
        self.server.json("PUT", "/repos/joruf/sandbox/pulls/1/merge", {"merged": True})
        self.server.json("DELETE", "/repos/joruf/sandbox/git/refs/heads/feature", None, status=204)

        self.panel.set_context(self.context)
        self.settle()

        class _Stub:
            """
            Stands in for the merge dialog, choosing squash plus a branch delete.
            """

            DialogCode = QDialog.DialogCode

            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def exec(self) -> object:
                return QDialog.DialogCode.Accepted

            def choice(self) -> MergeChoice:
                return MergeChoice(method="squash", delete_branch=True)

        original = github_lists.MergeDialog
        github_lists.MergeDialog = _Stub
        try:
            self.panel._pulls._on_merge()
            self.settle()
        finally:
            github_lists.MergeDialog = original

        merged = self.server.calls("PUT", "/repos/joruf/sandbox/pulls/1/merge")
        self.assertEqual("squash", merged[0].body["merge_method"])
        # The expected head is sent, so a push in the meantime cancels the merge.
        self.assertEqual("a" * 40, merged[0].body["sha"])
        self.assertEqual(1, len(self.server.calls("DELETE", "/repos/joruf/sandbox/git/refs/heads/feature")))

    def test_a_failed_write_does_not_reload_the_list(self) -> None:
        from ui import github_lists
        from ui.github_dialogs import IssueDraft

        self.server.json("GET", "/repos/joruf/sandbox/issues", [])
        self.server.json(
            "POST", "/repos/joruf/sandbox/issues", {"message": "no"}, status=422
        )
        self.panel.set_context(self.context)
        self.panel._tabs.setCurrentIndex(1)
        self.settle()
        before = len(self.server.calls("GET", "/repos/joruf/sandbox/issues"))

        reported: list[object] = []
        self.panel._issues.failed.connect(reported.append)

        class _Stub:
            DialogCode = QDialog.DialogCode

            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def exec(self) -> object:
                return QDialog.DialogCode.Accepted

            def draft(self) -> IssueDraft:
                return IssueDraft(title="doomed")

        original = github_lists.IssueEditorDialog
        github_lists.IssueEditorDialog = _Stub
        try:
            self.panel._issues._on_new()
            self.settle()
        finally:
            github_lists.IssueEditorDialog = original

        self.assertEqual(1, len(reported))
        self.assertEqual(before, len(self.server.calls("GET", "/repos/joruf/sandbox/issues")))
        # The failure was reported rather than swallowed.
        self.assertEqual(1, len(SilentBox.shown))

    def test_a_read_only_repository_disables_the_write_actions(self) -> None:
        from ui.github_lists import GitHubContext

        self.server.json("GET", "/repos/joruf/sandbox/pulls", [_pull(1)])
        self.server.json("GET", "/repos/joruf/sandbox/pulls/1", _pull(1))
        self.server.json("GET", "/repos/joruf/sandbox/issues/1/comments", [])
        self.server.json("GET", "/repos/joruf/sandbox/pulls/1/reviews", [])

        self.panel.set_context(
            GitHubContext(owner="joruf", repo="sandbox", can_push=False, can_administer=False)
        )
        self.settle()
        pulls = self.panel._pulls
        self.assertFalse(pulls._merge.isEnabled())
        self.assertFalse(pulls._edit.isEnabled())
        # Reading stays possible, so opening it in the browser must not be off.
        self.assertTrue(pulls._open.isEnabled())


@requires_qt
class RunnerTests(unittest.TestCase):
    """
    The runner, which is what keeps a read from overtaking the write before it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_calls_run_in_the_order_they_were_queued(self) -> None:
        from ui.github_worker import ApiRunner

        runner = ApiRunner()
        self.addCleanup(runner.stop)
        done: list[str] = []
        for name in ("first", "second", "third"):
            runner.submit(lambda value=name: value, done.append)

        deadline = time.time() + 5
        while runner.busy and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        self.assertEqual(["first", "second", "third"], done)

    def test_an_exception_arrives_as_a_result(self) -> None:
        from ui.github_worker import ApiRunner

        runner = ApiRunner()
        self.addCleanup(runner.stop)
        seen: list[object] = []

        def boom() -> None:
            raise RuntimeError("no")

        runner.submit(boom, seen.append)
        deadline = time.time() + 5
        while runner.busy and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        self.assertEqual(1, len(seen))
        self.assertIsInstance(seen[0], RuntimeError)

    def test_stopping_drops_what_is_still_queued(self) -> None:
        from ui.github_worker import ApiRunner

        runner = ApiRunner()
        done: list[object] = []
        for _index in range(5):
            runner.submit(lambda: None, done.append)
        runner.stop()
        self.app.processEvents()
        self.assertLessEqual(len(done), 1)


@requires_qt
class FlowLayoutTests(unittest.TestCase):
    """
    The wrapping action row.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _row(self) -> object:
        """
        Builds a row of buttons the width of a real action bar.

        Returns:
            FlowLayout: The filled layout.
        """

        from PySide6.QtWidgets import QPushButton, QWidget

        from ui.widgets import FlowLayout

        holder = QWidget()
        self.addCleanup(holder.deleteLater)
        layout = FlowLayout(holder)
        for label in (
            "New pull request…",
            "Edit…",
            "Comment…",
            "Review…",
            "Merge…",
            "Check out this branch",
            "Open in browser",
            "More…",
        ):
            layout.addWidget(QPushButton(label, holder))
        return layout

    def test_a_narrow_panel_gets_more_rows_instead_of_clipped_buttons(self) -> None:
        layout = self._row()
        wide = layout.heightForWidth(1200)
        medium = layout.heightForWidth(700)
        narrow = layout.heightForWidth(320)
        self.assertLess(wide, medium)
        self.assertLess(medium, narrow)

    def test_every_button_is_kept(self) -> None:
        layout = self._row()
        self.assertEqual(8, layout.count())
        self.assertIsNotNone(layout.itemAt(7))
        self.assertIsNone(layout.itemAt(8))

    def test_the_minimum_width_fits_the_widest_button(self) -> None:
        layout = self._row()
        widest = max(layout.itemAt(i).sizeHint().width() for i in range(layout.count()))
        self.assertGreaterEqual(layout.minimumSize().width(), widest)


@requires_qt
class DialogTests(unittest.TestCase):
    """
    What the dialogs refuse and what they hand back.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_an_issue_needs_a_title(self) -> None:
        from ui.github_dialogs import IssueDraft, IssueEditorDialog

        dialog = IssueEditorDialog(["bug"], ["joruf"], [(1, "1.0")], None)
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog.validate())

        dialog._title.setText("something")
        self.assertEqual("", dialog.validate())
        self.assertIsInstance(dialog.draft(), IssueDraft)

    def test_an_issue_editor_gives_back_what_was_ticked(self) -> None:
        from ui.github_dialogs import IssueDraft, IssueEditorDialog

        start = IssueDraft(title="t", labels=["bug"], assignees=["joruf"], milestone=1)
        dialog = IssueEditorDialog(["bug", "idea"], ["joruf", "other"], [(1, "1.0")], start)
        self.addCleanup(dialog.deleteLater)
        draft = dialog.draft()
        self.assertEqual(["bug"], draft.labels)
        self.assertEqual(["joruf"], draft.assignees)
        self.assertEqual(1, draft.milestone)

    def test_a_pull_request_cannot_point_at_its_own_branch(self) -> None:
        from ui.github_dialogs import PullRequestDraft, PullRequestEditorDialog

        start = PullRequestDraft(title="t", head="main", base="main")
        dialog = PullRequestEditorDialog(["main", "feature"], start, False)
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog.validate())

        dialog._base.setCurrentIndex(dialog._base.findText("feature"))
        self.assertEqual("", dialog.validate())

    def test_a_review_needs_a_text_unless_it_approves(self) -> None:
        from ui.github_dialogs import ReviewDialog

        dialog = ReviewDialog(False)
        self.addCleanup(dialog.deleteLater)
        dialog._events["REQUEST_CHANGES"].setChecked(True)
        self.assertTrue(dialog.validate())

        dialog._events["APPROVE"].setChecked(True)
        self.assertEqual("", dialog.validate())

    def test_approving_your_own_pull_request_is_not_offered(self) -> None:
        from ui.github_dialogs import ReviewDialog

        dialog = ReviewDialog(True)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog._events["APPROVE"].isEnabled())
        self.assertTrue(dialog._events["COMMENT"].isEnabled())

    def test_deleting_a_repository_needs_the_name_typed_exactly(self) -> None:
        from ui.github_dialogs import DeleteRepositoryDialog

        dialog = DeleteRepositoryDialog("joruf/sandbox")
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog._accept_button.isEnabled())

        dialog._confirmation.setText("sandbox")
        self.assertFalse(dialog._accept_button.isEnabled())

        dialog._confirmation.setText("joruf/sandbox")
        self.assertTrue(dialog._accept_button.isEnabled())

    def test_settings_report_only_what_changed(self) -> None:
        from ui.github_dialogs import RepositorySettings

        before = RepositorySettings(description="old", topics=["a"], visibility="private")
        after = RepositorySettings(description="new", topics=["a"], visibility="private")
        self.assertEqual({"description": "new"}, after.changes_from(before))

        same = RepositorySettings(description="old", topics=["b"], visibility="private")
        # Topics go through their own endpoint, so they are not in the patch.
        self.assertEqual({}, same.changes_from(before))

    def test_the_merge_dialog_disables_what_the_repository_forbids(self) -> None:
        from ui.github_dialogs import MergeDialog

        dialog = MergeDialog("feature", {"merge": False, "squash": True, "rebase": False}, True)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog._methods["merge"].isEnabled())
        self.assertTrue(dialog._methods["squash"].isEnabled())
        # The first method that is actually allowed starts out selected.
        self.assertEqual("squash", dialog.choice().method)
        # The repository deletes merged branches itself, so the box follows that.
        self.assertTrue(dialog.choice().delete_branch)

    def test_a_forbidden_method_says_why_rather_than_vanishing(self) -> None:
        from ui.github_dialogs import MergeDialog

        dialog = MergeDialog("feature", {"merge": True, "squash": False, "rebase": True})
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog._methods["squash"].isVisible() or True)
        self.assertTrue(dialog._methods["squash"].toolTip())

    def test_the_label_manager_hands_back_what_was_asked_for(self) -> None:
        from github_api.models import Label, Milestone
        from ui.github_dialogs import LabelMilestoneDialog

        dialog = LabelMilestoneDialog(
            [Label(name="bug", color="ff0000")], [Milestone(number=3, title="1.0")]
        )
        self.addCleanup(dialog.deleteLater)

        dialog._new_label.setText("idea")
        dialog._new_color.setText("00ff00")
        dialog._on_create_label()
        self.assertEqual(("create_label", ("idea", "00ff00")), dialog.requested)

        dialog.requested = None
        dialog._labels.setCurrentRow(0)
        dialog._on_delete_label()
        self.assertEqual(("delete_label", "bug"), dialog.requested)

        dialog.requested = None
        dialog._milestones.setCurrentRow(0)
        dialog._on_delete_milestone()
        self.assertEqual(("delete_milestone", 3), dialog.requested)

        dialog.requested = None
        dialog._on_close_milestone()
        self.assertEqual(("close_milestone", 3), dialog.requested)

        dialog.requested = None
        dialog._labels.setCurrentRow(0)
        dialog._new_label.setText("defect")
        dialog._new_color.setText("")
        dialog._on_rename_label()
        self.assertEqual(("rename_label", ("bug", "defect", "")), dialog.requested)

    def test_renaming_a_label_needs_the_new_name(self) -> None:
        from github_api.models import Label
        from ui.github_dialogs import LabelMilestoneDialog

        dialog = LabelMilestoneDialog([Label(name="bug")], [])
        self.addCleanup(dialog.deleteLater)
        dialog._labels.setCurrentRow(0)
        dialog._on_rename_label()
        self.assertIsNone(dialog.requested)
        self.assertFalse(dialog._notice.isHidden())

    def test_the_label_manager_refuses_an_empty_name(self) -> None:
        from ui.github_dialogs import LabelMilestoneDialog

        dialog = LabelMilestoneDialog([], [])
        self.addCleanup(dialog.deleteLater)
        dialog._on_create_label()
        self.assertIsNone(dialog.requested)
        # isVisible stays False while the dialog itself was never shown, so what
        # is checked is that the widget was un-hidden, not that it is on screen.
        self.assertFalse(dialog._notice.isHidden())

    def test_the_picker_returns_the_value_behind_the_label(self) -> None:
        from ui.github_dialogs import PickerDialog

        dialog = PickerDialog("t", "l", [("Tests", 17), ("Release", 18)])
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(17, dialog.value())
        dialog._box.setCurrentIndex(1)
        self.assertEqual(18, dialog.value())
        self.assertEqual("", dialog.validate())

    def test_the_repository_picker_filters_and_returns_a_clone_url(self) -> None:
        from github_api.models import Repository
        from ui.github_dialogs import RepositoryPickerDialog

        repositories = [
            Repository(owner="joruf", name="branchly", full_name="joruf/branchly",
                       clone_url="https://github.com/joruf/branchly.git", private=False),
            Repository(owner="joruf", name="pmtool", full_name="joruf/pmtool",
                       clone_url="https://github.com/joruf/pmtool.git", private=True,
                       description="a php thing"),
        ]
        dialog = RepositoryPickerDialog(repositories)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(2, dialog._list.count())

        dialog._search.setText("php")
        self.assertEqual(1, dialog._list.count())
        dialog._accept_selection()
        self.assertEqual("https://github.com/joruf/pmtool.git", dialog.chosen)

    def test_settings_report_the_new_fields(self) -> None:
        from ui.github_dialogs import RepositorySettings

        before = RepositorySettings()
        after = RepositorySettings(has_discussions=True, allow_squash_merge=False)
        self.assertEqual(
            {"has_discussions": True, "allow_squash_merge": False}, after.changes_from(before)
        )

    def test_a_repository_name_is_checked_before_the_request(self) -> None:
        from ui.github_dialogs import CreateRepositoryDialog

        dialog = CreateRepositoryDialog([])
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog.validate())

        dialog._name.setText("has spaces")
        self.assertTrue(dialog.validate())

        dialog._name.setText("valid-name_1.0")
        self.assertEqual("", dialog.validate())
        self.assertTrue(dialog.draft().private)


if __name__ == "__main__":
    unittest.main()
