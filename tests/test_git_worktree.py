"""
Tests for status reading, diffing, staging and committing.
"""

from __future__ import annotations

import unittest

from gitops import diff as diff_mod
from gitops import stage as stage_mod
from gitops.commit import (
    CommitDraft,
    changed_paths,
    commit,
    commit_details,
    has_staged_changes,
    previous_message,
)
from gitops.status import (
    CHANGE_ADDED,
    CHANGE_DELETED,
    CHANGE_MODIFIED,
    CHANGE_RENAMED,
    CHANGE_UNTRACKED,
    current_branch,
    last_commit_timestamp,
    parse_porcelain_v2,
    read_state,
)
from tests.support import requires_git, temp_repo


class PorcelainParsingTests(unittest.TestCase):
    def test_branch_headers(self) -> None:
        payload = "\x00".join(
            [
                "# branch.oid abc123",
                "# branch.head main",
                "# branch.upstream origin/main",
                "# branch.ab +2 -3",
                "",
            ]
        )
        state = parse_porcelain_v2(payload)
        self.assertEqual("abc123", state.oid)
        self.assertEqual("main", state.branch)
        self.assertEqual("origin/main", state.upstream)
        self.assertEqual(2, state.ahead)
        self.assertEqual(3, state.behind)
        self.assertFalse(state.detached)

    def test_initial_repository(self) -> None:
        state = parse_porcelain_v2("# branch.oid (initial)\x00# branch.head main\x00")
        self.assertTrue(state.initial)
        self.assertEqual("", state.oid)

    def test_detached_head(self) -> None:
        state = parse_porcelain_v2("# branch.head (detached)\x00")
        self.assertTrue(state.detached)
        self.assertEqual("", state.branch)

    def test_ordinary_change(self) -> None:
        entry = "1 .M N... 100644 100644 100644 aaa bbb src/app.py"
        state = parse_porcelain_v2(entry + "\x00")
        self.assertEqual(1, len(state.files))
        item = state.files[0]
        self.assertEqual("src/app.py", item.path)
        self.assertEqual(CHANGE_MODIFIED, item.kind)
        self.assertTrue(item.unstaged)
        self.assertFalse(item.staged)

    def test_rename_carries_the_old_path(self) -> None:
        payload = "2 R. N... 100644 100644 100644 aaa bbb R100 new/name.py\x00old/name.py\x00"
        state = parse_porcelain_v2(payload)
        self.assertEqual(1, len(state.files))
        item = state.files[0]
        self.assertEqual("new/name.py", item.path)
        self.assertEqual("old/name.py", item.orig_path)
        self.assertEqual(CHANGE_RENAMED, item.kind)
        self.assertIn("→", item.display_path)

    def test_unmerged_entry(self) -> None:
        payload = "u UU N... 100644 100644 100644 100644 aaa bbb ccc conflicted.txt\x00"
        state = parse_porcelain_v2(payload)
        self.assertTrue(state.has_conflicts)
        self.assertEqual("conflicted.txt", state.conflicted_files[0].path)

    def test_untracked_entry(self) -> None:
        state = parse_porcelain_v2("? new.txt\x00")
        self.assertEqual(CHANGE_UNTRACKED, state.files[0].kind)
        self.assertTrue(state.files[0].untracked)

    def test_path_with_a_newline_survives(self) -> None:
        payload = "1 .M N... 100644 100644 100644 aaa bbb weird\nname.txt\x00"
        state = parse_porcelain_v2(payload)
        self.assertEqual("weird\nname.txt", state.files[0].path)

    def test_garbage_lines_are_skipped(self) -> None:
        state = parse_porcelain_v2("1 too short\x00nonsense\x00? ok.txt\x00")
        self.assertEqual(1, len(state.files))


@requires_git
class ReadStateTests(unittest.TestCase):
    def test_clean_repository(self) -> None:
        with temp_repo() as repo:
            state = read_state(repo.root)
            self.assertTrue(state.ok)
            self.assertTrue(state.is_clean)
            self.assertEqual("main", state.branch)
            self.assertFalse(state.operation_in_progress)

    def test_modified_file_shows_up(self) -> None:
        with temp_repo() as repo:
            repo.write("README.md", "# Changed\n")
            state = read_state(repo.root)
            self.assertEqual(1, len(state.files))
            self.assertEqual(CHANGE_MODIFIED, state.files[0].kind)
            self.assertEqual(1, len(state.unstaged_files))
            self.assertEqual(0, len(state.staged_files))

    def test_staged_file_shows_up(self) -> None:
        with temp_repo() as repo:
            repo.write("added.txt", "new\n")
            repo.git("add", "added.txt")
            state = read_state(repo.root)
            self.assertEqual(1, len(state.staged_files))
            self.assertEqual(CHANGE_ADDED, state.files[0].kind)

    def test_deleted_file_shows_up(self) -> None:
        with temp_repo() as repo:
            (repo.root / "README.md").unlink()
            state = read_state(repo.root)
            self.assertEqual(CHANGE_DELETED, state.files[0].kind)

    def test_untracked_file_shows_up(self) -> None:
        with temp_repo() as repo:
            repo.write("scratch.txt", "temp\n")
            state = read_state(repo.root)
            self.assertEqual(CHANGE_UNTRACKED, state.files[0].kind)

    def test_detached_head_is_reported(self) -> None:
        with temp_repo() as repo:
            repo.commit_file("second.txt", "x\n")
            repo.git("switch", "--detach", "HEAD~1")
            state = read_state(repo.root)
            self.assertTrue(state.detached)
            self.assertEqual("", state.branch)
            self.assertEqual(7, len(state.display_branch))

    def test_missing_repository_reports_an_error(self) -> None:
        state = read_state("/nonexistent/branchly")
        self.assertFalse(state.ok)
        self.assertTrue(state.error_key)

    def test_current_branch_and_timestamp(self) -> None:
        with temp_repo() as repo:
            self.assertEqual("main", current_branch(repo.root))
            self.assertIsNotNone(last_commit_timestamp(repo.root))

    def test_timestamp_is_none_without_commits(self) -> None:
        with temp_repo(initial_commit=False) as repo:
            self.assertIsNone(last_commit_timestamp(repo.root))


class UnifiedDiffParsingTests(unittest.TestCase):
    PAYLOAD = (
        "diff --git a/app.py b/app.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,4 +1,4 @@\n"
        " import os\n"
        "-port = 8080\n"
        "+port = 3000\n"
        " debug = True\n"
        " run()\n"
    )

    def test_paths_and_counts(self) -> None:
        parsed = diff_mod.parse_unified(self.PAYLOAD)
        self.assertEqual("app.py", parsed.path)
        self.assertEqual(1, parsed.added)
        self.assertEqual(1, parsed.removed)
        self.assertEqual(1, len(parsed.hunks))

    def test_line_numbers(self) -> None:
        hunk = diff_mod.parse_unified(self.PAYLOAD).hunks[0]
        removed = [line for line in hunk.lines if line.kind == diff_mod.LINE_REMOVED]
        added = [line for line in hunk.lines if line.kind == diff_mod.LINE_ADDED]
        self.assertEqual(2, removed[0].old_lineno)
        self.assertEqual(2, added[0].new_lineno)

    def test_word_spans_mark_only_the_changed_part(self) -> None:
        hunk = diff_mod.parse_unified(self.PAYLOAD).hunks[0]
        added = next(line for line in hunk.lines if line.kind == diff_mod.LINE_ADDED)
        self.assertTrue(added.spans)
        start, end = added.spans[0]
        self.assertEqual("3000", added.text[start:end])

    def test_binary_diff_is_flagged(self) -> None:
        payload = "diff --git a/logo.png b/logo.png\nBinary files a/logo.png and b/logo.png differ\n"
        parsed = diff_mod.parse_unified(payload)
        self.assertTrue(parsed.binary)
        self.assertTrue(parsed.is_image)

    def test_empty_diff(self) -> None:
        parsed = diff_mod.parse_unified("")
        self.assertTrue(parsed.is_empty)

    def test_side_by_side_pairing(self) -> None:
        hunk = diff_mod.parse_unified(self.PAYLOAD).hunks[0]
        rows = diff_mod.pair_lines(hunk)
        changed = [row for row in rows if row[0] and row[1] and row[0].text != row[1].text]
        self.assertEqual(1, len(changed))
        self.assertEqual("port = 8080", changed[0][0].text)
        self.assertEqual("port = 3000", changed[0][1].text)

    def test_unequal_runs_leave_blanks(self) -> None:
        payload = (
            "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,1 +1,3 @@\n"
            "-one\n+one\n+two\n+three\n"
        )
        rows = diff_mod.pair_lines(diff_mod.parse_unified(payload).hunks[0])
        self.assertEqual(3, len(rows))
        self.assertIsNone(rows[1][0])
        self.assertIsNone(rows[2][0])

    def test_intra_line_spans_for_identical_lines(self) -> None:
        old_spans, new_spans = diff_mod.intra_line_spans("same", "same")
        self.assertEqual([], old_spans)
        self.assertEqual([], new_spans)

    def test_intra_line_spans_for_empty_side(self) -> None:
        old_spans, new_spans = diff_mod.intra_line_spans("", "added")
        self.assertEqual([], old_spans)
        self.assertEqual([(0, 5)], new_spans)


class DiffArgumentTests(unittest.TestCase):
    def test_every_target_builds_arguments(self) -> None:
        for target in diff_mod.VALID_TARGETS:
            args = diff_mod.build_diff_args(target, rev_a="HEAD~1", rev_b="HEAD")
            self.assertIsNotNone(args, target)

    def test_the_three_local_targets_differ(self) -> None:
        # Commit-to-commit and branch-to-branch are the same git call by design;
        # they differ only in what the UI puts into the two revisions.
        local = [
            diff_mod.TARGET_WORKTREE_INDEX,
            diff_mod.TARGET_INDEX_HEAD,
            diff_mod.TARGET_WORKTREE_HEAD,
        ]
        built = {tuple(diff_mod.build_diff_args(target) or ()) for target in local}
        self.assertEqual(3, len(built))

    def test_revision_pair_is_passed_through(self) -> None:
        for target in (diff_mod.TARGET_COMMITS, diff_mod.TARGET_BRANCHES):
            args = diff_mod.build_diff_args(target, rev_a="abc1234", rev_b="def5678")
            assert args is not None
            self.assertEqual(["abc1234", "def5678"], args[-2:])

    def test_ignore_whitespace_flag(self) -> None:
        args = diff_mod.build_diff_args(diff_mod.TARGET_WORKTREE_HEAD, ignore_whitespace=True)
        assert args is not None
        self.assertIn("--ignore-all-space", args)

    def test_path_is_separated_by_double_dash(self) -> None:
        args = diff_mod.build_diff_args(diff_mod.TARGET_WORKTREE_HEAD, path="src/app.py")
        assert args is not None
        self.assertEqual(["--", "src/app.py"], args[-2:])

    def test_unsafe_path_is_refused(self) -> None:
        self.assertIsNone(diff_mod.build_diff_args(diff_mod.TARGET_WORKTREE_HEAD, path="--output=/tmp/x"))

    def test_unsafe_revision_is_refused(self) -> None:
        self.assertIsNone(
            diff_mod.build_diff_args(diff_mod.TARGET_COMMITS, rev_a="--all", rev_b="HEAD")
        )

    def test_unknown_target_falls_back(self) -> None:
        self.assertEqual(diff_mod.TARGET_WORKTREE_HEAD, diff_mod.normalize_target("nonsense"))


@requires_git
class RealDiffTests(unittest.TestCase):
    def test_worktree_against_head(self) -> None:
        with temp_repo() as repo:
            repo.commit_file("app.py", "port = 8080\ndebug = True\n")
            repo.write("app.py", "port = 3000\ndebug = True\n")
            parsed = diff_mod.file_diff(repo.root, "app.py", diff_mod.TARGET_WORKTREE_HEAD)
            self.assertEqual(1, parsed.added)
            self.assertEqual(1, parsed.removed)

    def test_staged_against_head(self) -> None:
        with temp_repo() as repo:
            repo.commit_file("app.py", "one\n")
            repo.write("app.py", "two\n")
            repo.git("add", "app.py")
            staged = diff_mod.file_diff(repo.root, "app.py", diff_mod.TARGET_INDEX_HEAD)
            self.assertEqual(1, staged.added)
            unstaged = diff_mod.file_diff(repo.root, "app.py", diff_mod.TARGET_WORKTREE_INDEX)
            self.assertTrue(unstaged.is_empty)

    def test_two_commits(self) -> None:
        with temp_repo() as repo:
            first = repo.commit_file("app.py", "one\n")
            second = repo.commit_file("app.py", "two\n")
            parsed = diff_mod.file_diff(
                repo.root, "app.py", diff_mod.TARGET_COMMITS, rev_a=first, rev_b=second
            )
            self.assertEqual(1, parsed.added)

    def test_untracked_file_shows_as_all_new(self) -> None:
        with temp_repo() as repo:
            repo.write("fresh.txt", "line one\nline two\n")
            parsed = diff_mod.untracked_file_diff(repo.root, "fresh.txt")
            self.assertEqual(2, parsed.added)
            self.assertEqual(0, parsed.removed)

    def test_numstat_reports_counts(self) -> None:
        with temp_repo() as repo:
            repo.commit_file("a.txt", "one\n")
            repo.write("a.txt", "one\ntwo\n")
            counts = diff_mod.numstat(repo.root, diff_mod.TARGET_WORKTREE_HEAD)
            self.assertEqual((1, 0), counts.get("a.txt"))

    def test_blob_bytes_reads_content(self) -> None:
        with temp_repo() as repo:
            repo.commit_file("a.txt", "content\n")
            self.assertEqual(b"content\n", diff_mod.blob_bytes(repo.root, "HEAD", "a.txt"))

    def test_blob_bytes_refuses_unsafe_input(self) -> None:
        with temp_repo() as repo:
            self.assertIsNone(diff_mod.blob_bytes(repo.root, "HEAD", "--evil"))
            self.assertIsNone(diff_mod.blob_bytes(repo.root, "--all", "a.txt"))


@requires_git
class StageTests(unittest.TestCase):
    def test_stage_and_unstage_a_file(self) -> None:
        with temp_repo() as repo:
            repo.write("a.txt", "one\n")
            self.assertTrue(stage_mod.stage_files(repo.root, ["a.txt"]).ok)
            self.assertTrue(has_staged_changes(repo.root))
            self.assertTrue(stage_mod.unstage_files(repo.root, ["a.txt"]).ok)
            self.assertFalse(has_staged_changes(repo.root))

    def test_unstage_works_without_any_commits(self) -> None:
        with temp_repo(initial_commit=False) as repo:
            repo.write("a.txt", "one\n")
            stage_mod.stage_files(repo.root, ["a.txt"])
            self.assertTrue(stage_mod.unstage_files(repo.root, ["a.txt"]).ok)

    def test_discard_restores_the_committed_content(self) -> None:
        with temp_repo() as repo:
            repo.commit_file("a.txt", "original\n")
            repo.write("a.txt", "broken\n")
            self.assertTrue(stage_mod.discard_changes(repo.root, ["a.txt"]).ok)
            self.assertEqual("original\n", (repo.root / "a.txt").read_text(encoding="utf-8"))

    def test_delete_untracked_removes_the_file(self) -> None:
        with temp_repo() as repo:
            repo.write("scratch.txt", "temp\n")
            ok, failed = stage_mod.delete_untracked(repo.root, ["scratch.txt"])
            self.assertTrue(ok)
            self.assertEqual([], failed)
            self.assertFalse((repo.root / "scratch.txt").exists())

    def test_delete_untracked_refuses_to_escape_the_worktree(self) -> None:
        with temp_repo() as repo:
            ok, failed = stage_mod.delete_untracked(repo.root, ["../outside.txt"])
            self.assertFalse(ok)
            self.assertEqual(["../outside.txt"], failed)

    def test_unsafe_paths_are_refused(self) -> None:
        with temp_repo() as repo:
            self.assertTrue(stage_mod.stage_files(repo.root, ["--force"]).failed)
            self.assertTrue(stage_mod.discard_changes(repo.root, ["-x"]).failed)

    def test_stage_a_single_hunk(self) -> None:
        with temp_repo() as repo:
            repo.commit_file("a.txt", "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten\n")
            repo.write(
                "a.txt",
                "ONE\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nTEN\n",
            )
            parsed = diff_mod.file_diff(repo.root, "a.txt", diff_mod.TARGET_WORKTREE_HEAD)
            self.assertEqual(2, len(parsed.hunks), "expected two separate hunks")
            result = stage_mod.stage_hunk(repo.root, parsed, parsed.hunks[0])
            self.assertTrue(result.ok, result.message)
            staged = diff_mod.file_diff(repo.root, "a.txt", diff_mod.TARGET_INDEX_HEAD)
            self.assertEqual(1, staged.added, "only the first hunk should be staged")

    def test_hunk_patch_is_empty_without_lines(self) -> None:
        self.assertEqual("", stage_mod.build_hunk_patch(diff_mod.FileDiff(path="a"), diff_mod.DiffHunk()))

    def test_stage_all_and_unstage_all(self) -> None:
        with temp_repo() as repo:
            repo.write("a.txt", "one\n")
            repo.write("b.txt", "two\n")
            self.assertTrue(stage_mod.stage_all(repo.root).ok)
            self.assertEqual(2, len(read_state(repo.root).staged_files))
            self.assertTrue(stage_mod.unstage_all(repo.root).ok)
            self.assertEqual(0, len(read_state(repo.root).staged_files))


@requires_git
class CommitTests(unittest.TestCase):
    def test_commit_staged_changes(self) -> None:
        with temp_repo() as repo:
            repo.write("a.txt", "one\n")
            stage_mod.stage_files(repo.root, ["a.txt"])
            result = commit(repo.root, CommitDraft(summary="add a"))
            self.assertTrue(result.ok, result.message)
            details = commit_details(repo.root, "HEAD")
            self.assertIsNotNone(details)
            assert details is not None
            self.assertEqual("add a", details["subject"])

    def test_summary_and_body_are_separated(self) -> None:
        draft = CommitDraft(summary="  a   summary  ", description="line one\nline two")
        self.assertEqual("a summary\n\nline one\nline two\n", draft.message)

    def test_empty_summary_is_refused(self) -> None:
        with temp_repo() as repo:
            self.assertTrue(commit(repo.root, CommitDraft(summary="   ")).failed)

    def test_message_with_shell_syntax_is_stored_literally(self) -> None:
        with temp_repo() as repo:
            repo.write("a.txt", "one\n")
            stage_mod.stage_files(repo.root, ["a.txt"])
            tricky = 'fix "quoted" thing; rm -rf $HOME && echo `whoami`'
            self.assertTrue(commit(repo.root, CommitDraft(summary=tricky)).ok)
            details = commit_details(repo.root, "HEAD")
            assert details is not None
            self.assertEqual(tricky, details["subject"])
            self.assertTrue((repo.root / "a.txt").exists())

    def test_amend_replaces_the_previous_commit(self) -> None:
        with temp_repo() as repo:
            before = len(repo.git("log", "--format=%H").stdout.split())
            repo.write("a.txt", "one\n")
            stage_mod.stage_files(repo.root, ["a.txt"])
            commit(repo.root, CommitDraft(summary="first try"))
            commit(repo.root, CommitDraft(summary="better message", amend=True))
            after = len(repo.git("log", "--format=%H").stdout.split())
            self.assertEqual(before + 1, after)
            details = commit_details(repo.root, "HEAD")
            assert details is not None
            self.assertEqual("better message", details["subject"])

    def test_previous_message_is_read_back(self) -> None:
        with temp_repo() as repo:
            repo.write("a.txt", "one\n")
            stage_mod.stage_files(repo.root, ["a.txt"])
            commit(repo.root, CommitDraft(summary="subject here", description="body here"))
            draft = previous_message(repo.root)
            self.assertIsNotNone(draft)
            assert draft is not None
            self.assertEqual("subject here", draft.summary)
            self.assertEqual("body here", draft.description)
            self.assertTrue(draft.amend)

    def test_changed_paths_of_a_commit(self) -> None:
        with temp_repo() as repo:
            repo.commit_file("only.txt", "x\n")
            self.assertEqual(["only.txt"], changed_paths(repo.root, "HEAD"))

    def test_commit_details_refuses_unsafe_revision(self) -> None:
        with temp_repo() as repo:
            self.assertIsNone(commit_details(repo.root, "--all"))


if __name__ == "__main__":
    unittest.main()
