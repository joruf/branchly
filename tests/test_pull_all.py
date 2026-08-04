"""
Tests for bringing every project up to the server's version at once.

These run against real repositories, because the whole feature is about what git
does and refuses to do. The refusals carry the weight: a bulk pull that quietly
merged, or that moved a branch with unsaved work in the tree, would be a way to
lose work without being asked — which is the one thing Branchly must not do.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from gitops import branch as branch_mod
from gitops import remote as remote_mod
from gitops.status import read_state
from services import puller
from tests.support import requires_git, temp_repo, temp_repo_pair


def _job(repo, key: str = "one") -> puller.PullJob:  # noqa: ANN001 - TempRepo fixture
    """
    Builds a pull job for a fixture repository.

    Args:
        repo: Fixture whose working tree should be updated.
        key: Registry key to carry through.

    Returns:
        puller.PullJob: The job.
    """

    return puller.PullJob(path=repo.root, key=key, name=key)


@requires_git
class FastForwardTests(unittest.TestCase):
    def test_a_new_server_commit_arrives(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("shared.txt", "theirs\n", "their commit")
            first.git("push", "origin", "main")

            result = puller.pull_one(_job(second))

            self.assertEqual(puller.RESULT_PULLED, result.state, result.reason_key)
            self.assertTrue(result.changed)
            self.assertEqual(1, result.commits)
            self.assertEqual(first.head(), second.head())
            self.assertEqual("theirs\n", (second.root / "shared.txt").read_text())

    def test_several_commits_are_counted(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            for number in range(3):
                first.commit_file(f"file{number}.txt", "x\n", f"commit {number}")
            first.git("push", "origin", "main")

            result = puller.pull_one(_job(second))

            self.assertEqual(3, result.commits)

    def test_nothing_new_is_reported_as_current(self) -> None:
        with temp_repo_pair() as (_first, second, _origin):
            result = puller.pull_one(_job(second))

            self.assertEqual(puller.RESULT_CURRENT, result.state, result.reason_key)
            self.assertFalse(result.changed)
            self.assertEqual(0, result.commits)
            self.assertFalse(result.needs_attention)

    def test_the_branch_is_named_in_the_report(self) -> None:
        with temp_repo_pair() as (_first, second, _origin):
            self.assertEqual("main", puller.pull_one(_job(second)).branch)


@requires_git
class RefusalTests(unittest.TestCase):
    """
    Every case where the run leaves a project alone rather than guessing.
    """

    def test_uncommitted_work_is_left_alone(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("shared.txt", "theirs\n", "their commit")
            first.git("push", "origin", "main")
            second.write("README.md", "my unsaved edit\n")
            before = second.head()

            result = puller.pull_one(_job(second))

            self.assertEqual(puller.RESULT_SKIPPED, result.state)
            self.assertEqual(puller.SKIP_DIRTY, result.reason_key)
            self.assertEqual(before, second.head(), "HEAD must not move")
            self.assertEqual("my unsaved edit\n", (second.root / "README.md").read_text())

    def test_a_skipped_project_still_learns_what_is_waiting(self) -> None:
        """
        Skipping must not mean staying blind: the fetch is safe, so the badge ends
        up telling the truth about the commits sitting on the server.
        """

        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("shared.txt", "theirs\n", "their commit")
            first.commit_file("more.txt", "theirs\n", "another")
            first.git("push", "origin", "main")
            second.write("README.md", "my unsaved edit\n")

            result = puller.pull_one(_job(second))

            self.assertEqual(puller.SKIP_DIRTY, result.reason_key)
            self.assertEqual(2, result.waiting)

    def test_own_commits_are_left_alone(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("shared.txt", "theirs\n", "their commit")
            first.git("push", "origin", "main")
            mine = second.commit_file("mine.txt", "mine\n", "my own commit")

            result = puller.pull_one(_job(second))

            self.assertEqual(puller.RESULT_SKIPPED, result.state)
            self.assertEqual(puller.SKIP_OWN_COMMITS, result.reason_key)
            self.assertEqual(mine, second.head(), "a merge commit must not appear")

    def test_a_detached_head_is_left_alone(self) -> None:
        with temp_repo_pair() as (_first, second, _origin):
            second.commit_file("later.txt", "later\n", "a second commit")
            second.git("checkout", "--detach", "HEAD~1")

            result = puller.pull_one(_job(second))

            self.assertEqual(puller.SKIP_DETACHED, result.reason_key)

    def test_a_branch_without_an_upstream_is_left_alone(self) -> None:
        with temp_repo_pair() as (_first, second, _origin):
            branch_mod.create_branch(second.root, "side", checkout=True)

            result = puller.pull_one(_job(second))

            self.assertEqual(puller.SKIP_NO_UPSTREAM, result.reason_key)

    def test_a_project_without_a_server_is_left_alone(self) -> None:
        with temp_repo() as repo:
            result = puller.pull_one(_job(repo))

            self.assertEqual(puller.RESULT_SKIPPED, result.state)
            self.assertEqual(puller.SKIP_NO_REMOTE, result.reason_key)

    def test_a_project_without_commits_is_left_alone(self) -> None:
        # No server either, and that is the more useful thing to say first.
        with temp_repo(initial_commit=False) as repo:
            result = puller.pull_one(_job(repo))

            self.assertEqual(puller.RESULT_SKIPPED, result.state)
            self.assertEqual(puller.SKIP_NO_REMOTE, result.reason_key)

    def test_conflicts_outrank_everything_else(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("shared.txt", "theirs\n", "their version")
            first.git("push", "origin", "main")
            second.commit_file("shared.txt", "mine\n", "my version")
            # Leaves the working tree in a conflicted merge.
            second.git("fetch", "origin", check=False)
            second.git("merge", "origin/main", check=False)
            self.assertTrue(read_state(second.root).has_conflicts, "fixture did not conflict")

            result = puller.pull_one(_job(second))

            self.assertEqual(puller.SKIP_CONFLICTS, result.reason_key)

    def test_a_missing_folder_is_reported_not_crashed(self) -> None:
        job = puller.PullJob(path=Path("/nonexistent/branchly-project"), key="gone", name="gone")

        result = puller.pull_one(job)

        self.assertEqual(puller.RESULT_SKIPPED, result.state)
        self.assertEqual(puller.SKIP_MISSING, result.reason_key)


@requires_git
class BlockingReasonTests(unittest.TestCase):
    def test_a_clean_tracking_branch_has_no_reason(self) -> None:
        with temp_repo_pair() as (_first, second, _origin):
            self.assertEqual("", puller.blocking_reason(read_state(second.root)))

    def test_a_repository_without_commits_is_named(self) -> None:
        with temp_repo(initial_commit=False) as repo:
            self.assertEqual(puller.SKIP_EMPTY, puller.blocking_reason(read_state(repo.root)))

    def test_conflicts_are_named_before_dirt(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("shared.txt", "theirs\n", "their version")
            first.git("push", "origin", "main")
            second.commit_file("shared.txt", "mine\n", "my version")
            second.git("fetch", "origin", check=False)
            second.git("merge", "origin/main", check=False)

            # A conflicted tree is also a dirty tree; the conflict is the useful
            # thing to say, so it has to win.
            self.assertEqual(
                puller.SKIP_CONFLICTS, puller.blocking_reason(read_state(second.root))
            )


class SummaryTests(unittest.TestCase):
    def test_every_outcome_is_counted(self) -> None:
        results = [
            puller.PullResult(key="a", state=puller.RESULT_PULLED, commits=3),
            puller.PullResult(key="b", state=puller.RESULT_PULLED, commits=1),
            puller.PullResult(key="c", state=puller.RESULT_CURRENT),
            puller.PullResult(key="d", state=puller.RESULT_SKIPPED, reason_key=puller.SKIP_DIRTY),
            puller.PullResult(key="e", state=puller.RESULT_FAILED),
        ]

        summary = puller.summarize(results)

        self.assertEqual(2, summary.pulled)
        self.assertEqual(4, summary.commits)
        self.assertEqual(1, summary.current)
        self.assertEqual(1, summary.skipped)
        self.assertEqual(1, summary.failed)
        self.assertEqual(5, summary.total)

    def test_an_empty_run_summarizes_to_nothing(self) -> None:
        summary = puller.summarize([])
        self.assertEqual(0, summary.total)

    def test_jobs_carry_the_display_name(self) -> None:
        class _Entry:
            def __init__(self) -> None:
                self.path = Path("/tmp/demo")
                self.key = "demo"
                self.name = "Demo project"

        jobs = puller.build_jobs([_Entry()])

        self.assertEqual(1, len(jobs))
        self.assertEqual("Demo project", jobs[0].name)
        self.assertEqual("demo", jobs[0].key)


@requires_git
class FastForwardOnlyTests(unittest.TestCase):
    """
    The git-level guarantee the whole feature rests on.
    """

    def test_a_diverged_branch_is_refused_and_left_where_it_was(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("theirs.txt", "theirs\n", "their commit")
            first.git("push", "origin", "main")
            mine = second.commit_file("mine.txt", "mine\n", "my commit")

            result = remote_mod.pull(second.root, ff_only=True)

            self.assertTrue(result.failed)
            self.assertEqual(mine, second.head(), "HEAD must not move")
            state = read_state(second.root)
            self.assertTrue(state.is_clean, "a refused pull must leave no half-merge behind")
            self.assertFalse(state.merging)

    def test_the_plain_pull_still_merges(self) -> None:
        # Guards the single-project button: it must keep its old behaviour.
        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("theirs.txt", "theirs\n", "their commit")
            first.git("push", "origin", "main")
            mine = second.commit_file("mine.txt", "mine\n", "my commit")

            result = remote_mod.pull(second.root)

            self.assertTrue(result.ok, result.stderr)
            self.assertNotEqual(mine, second.head(), "a merge commit was expected here")


if __name__ == "__main__":
    unittest.main()
