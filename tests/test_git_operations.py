"""
Tests for branches, remotes, history layout, conflicts and cloning.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gitops import branch as branch_mod
from gitops import clone as clone_mod
from gitops import conflict as conflict_mod
from gitops import history as history_mod
from gitops import remote as remote_mod
from gitops.status import read_state
from tests.support import requires_git, temp_repo, temp_repo_pair


@requires_git
class BranchTests(unittest.TestCase):
    def test_create_and_switch(self) -> None:
        with temp_repo() as repo:
            self.assertTrue(branch_mod.create_branch(repo.root, "feature/login").ok)
            self.assertEqual("feature/login", repo.current_branch())
            self.assertTrue(branch_mod.branch_exists(repo.root, "feature/login"))

    def test_create_without_switching(self) -> None:
        with temp_repo() as repo:
            branch_mod.create_branch(repo.root, "side", checkout=False)
            self.assertEqual("main", repo.current_branch())
            self.assertTrue(branch_mod.branch_exists(repo.root, "side"))

    def test_invalid_names_are_refused(self) -> None:
        with temp_repo() as repo:
            for name in ("bad name", "--force", "a..b", "ends."):
                self.assertTrue(branch_mod.create_branch(repo.root, name).failed, name)

    def test_list_branches_marks_the_current_one(self) -> None:
        with temp_repo() as repo:
            branch_mod.create_branch(repo.root, "second", checkout=False)
            branches = branch_mod.list_branches(repo.root)
            names = {item.name for item in branches}
            self.assertEqual({"main", "second"}, names)
            current = [item for item in branches if item.is_current]
            self.assertEqual(["main"], [item.name for item in current])

    def test_local_branch_with_slash_is_not_remote(self) -> None:
        with temp_repo() as repo:
            branch_mod.create_branch(repo.root, "feature/login", checkout=False)
            branch_mod.invalidate_branch_cache(repo.root)
            found = [item for item in branch_mod.list_branches(repo.root) if item.name == "feature/login"]
            self.assertEqual(1, len(found))
            self.assertFalse(found[0].is_remote)

    def test_rename_and_delete(self) -> None:
        with temp_repo() as repo:
            branch_mod.create_branch(repo.root, "old", checkout=False)
            self.assertTrue(branch_mod.rename_branch(repo.root, "old", "new").ok)
            self.assertTrue(branch_mod.branch_exists(repo.root, "new"))
            self.assertTrue(branch_mod.delete_branch(repo.root, "new", force=True).ok)
            self.assertFalse(branch_mod.branch_exists(repo.root, "new"))

    def test_checkout_revision_detaches(self) -> None:
        with temp_repo() as repo:
            repo.commit_file("second.txt", "x\n")
            first = repo.git("rev-parse", "HEAD~1").stdout.strip()
            self.assertTrue(branch_mod.checkout_revision(repo.root, first).ok)
            self.assertTrue(read_state(repo.root).detached)

    def test_merge_brings_a_branch_in(self) -> None:
        with temp_repo() as repo:
            branch_mod.create_branch(repo.root, "side")
            repo.commit_file("side.txt", "from side\n")
            branch_mod.checkout_branch(repo.root, "main")
            self.assertTrue(branch_mod.merge(repo.root, "side").ok)
            self.assertTrue((repo.root / "side.txt").exists())

    def test_cherry_pick_copies_one_commit(self) -> None:
        with temp_repo() as repo:
            branch_mod.create_branch(repo.root, "side")
            repo.commit_file("picked.txt", "content\n")
            picked = repo.head()
            branch_mod.checkout_branch(repo.root, "main")
            self.assertTrue(branch_mod.cherry_pick(repo.root, picked).ok)
            self.assertTrue((repo.root / "picked.txt").exists())

    def test_revert_adds_an_undoing_commit(self) -> None:
        with temp_repo() as repo:
            repo.commit_file("temp.txt", "unwanted\n")
            self.assertTrue(branch_mod.revert(repo.root, "HEAD").ok)
            self.assertFalse((repo.root / "temp.txt").exists())

    def test_reset_hard_discards_work(self) -> None:
        with temp_repo() as repo:
            repo.commit_file("keep.txt", "kept\n")
            base = repo.head()
            repo.commit_file("throwaway.txt", "gone\n")
            self.assertTrue(branch_mod.reset(repo.root, base, branch_mod.RESET_HARD).ok)
            self.assertFalse((repo.root / "throwaway.txt").exists())
            self.assertTrue((repo.root / "keep.txt").exists())

    def test_reset_soft_keeps_the_changes_staged(self) -> None:
        with temp_repo() as repo:
            base = repo.head()
            repo.commit_file("staged.txt", "content\n")
            self.assertTrue(branch_mod.reset(repo.root, base, branch_mod.RESET_SOFT).ok)
            self.assertEqual(1, len(read_state(repo.root).staged_files))

    def test_invalid_reset_mode_is_refused(self) -> None:
        with temp_repo() as repo:
            self.assertTrue(branch_mod.reset(repo.root, "HEAD", "nuclear").failed)

    def test_tags(self) -> None:
        with temp_repo() as repo:
            self.assertTrue(branch_mod.create_tag(repo.root, "v1.0").ok)
            self.assertIn("v1.0", branch_mod.list_tags(repo.root))
            self.assertTrue(branch_mod.create_tag(repo.root, "v1.1", message="annotated").ok)
            self.assertIn("v1.1", branch_mod.list_tags(repo.root))
            self.assertTrue(branch_mod.delete_tag(repo.root, "v1.0").ok)
            self.assertNotIn("v1.0", branch_mod.list_tags(repo.root))

    def test_stash_round_trip(self) -> None:
        with temp_repo() as repo:
            repo.commit_file("a.txt", "original\n")
            repo.write("a.txt", "modified\n")
            self.assertTrue(branch_mod.stash_push(repo.root, "wip").ok)
            self.assertEqual("original\n", (repo.root / "a.txt").read_text(encoding="utf-8"))
            self.assertEqual(1, len(branch_mod.stash_list(repo.root)))
            self.assertTrue(branch_mod.stash_pop(repo.root).ok)
            self.assertEqual("modified\n", (repo.root / "a.txt").read_text(encoding="utf-8"))


@requires_git
class RemoteTests(unittest.TestCase):
    def test_repository_without_remote(self) -> None:
        with temp_repo() as repo:
            self.assertEqual([], remote_mod.remotes(repo.root))
            self.assertFalse(remote_mod.has_remote(repo.root))
            state = remote_mod.check_remote_state(repo.root)
            self.assertFalse(state.reachable)
            self.assertEqual("", state.error_key)

    def test_clone_pair_shares_an_origin(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            self.assertIn("origin", remote_mod.remotes(first.root))
            self.assertTrue(remote_mod.remote_fetch_url(first.root).endswith("origin.git"))
            self.assertEqual("origin/main", remote_mod.upstream_of(first.root))

    def test_ls_remote_lists_branches(self) -> None:
        with temp_repo_pair() as (first, _second, _origin):
            heads = remote_mod.ls_remote_heads(first.root)
            self.assertIn("main", heads)
            self.assertEqual(40, len(heads["main"]))

    def test_online_check_sees_news_without_fetching(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("news.txt", "fresh\n")
            first.git("push", "origin", "main")

            before = remote_mod.local_ref_oid(second.root, "origin/main")
            state = remote_mod.check_remote_state(second.root)
            self.assertTrue(state.reachable)
            self.assertTrue(state.has_news)
            # The point of ls-remote: the local tracking ref did not move.
            self.assertEqual(before, remote_mod.local_ref_oid(second.root, "origin/main"))

    def test_fetch_then_pull(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("news.txt", "fresh\n")
            first.git("push", "origin", "main")
            self.assertTrue(remote_mod.fetch(second.root).ok)
            self.assertEqual(1, remote_mod.count_between(second.root, "main", "origin/main"))
            self.assertTrue(remote_mod.pull(second.root).ok)
            self.assertTrue((second.root / "news.txt").exists())

    def test_push_sends_commits(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            second.commit_file("mine.txt", "content\n")
            self.assertTrue(remote_mod.push(second.root, branch="main").ok)
            self.assertTrue(remote_mod.fetch(first.root).ok)
            self.assertEqual(1, remote_mod.count_between(first.root, "main", "origin/main"))

    def test_invalid_remote_name_is_refused(self) -> None:
        with temp_repo() as repo:
            self.assertTrue(remote_mod.fetch(repo.root, "--upload-pack=evil").failed)
            self.assertTrue(remote_mod.push(repo.root, "-x").failed)

    def test_set_remote_url_refuses_unsafe_urls(self) -> None:
        with temp_repo() as repo:
            self.assertTrue(remote_mod.set_remote_url(repo.root, "ext::sh -c evil").failed)
            self.assertTrue(remote_mod.set_remote_url(repo.root, "https://h/p.git\revil").failed)


class LaneLayoutTests(unittest.TestCase):
    def test_linear_history_uses_one_lane(self) -> None:
        commits = [
            history_mod.Commit(oid="c", parents=("b",)),
            history_mod.Commit(oid="b", parents=("a",)),
            history_mod.Commit(oid="a", parents=()),
        ]
        laid_out = history_mod.assign_lanes(commits)
        self.assertEqual([0, 0, 0], [row.lane for row in laid_out.rows])
        self.assertEqual(1, laid_out.max_width)

    def test_merge_opens_a_second_lane(self) -> None:
        commits = [
            history_mod.Commit(oid="m", parents=("a", "b")),
            history_mod.Commit(oid="b", parents=("root",)),
            history_mod.Commit(oid="a", parents=("root",)),
            history_mod.Commit(oid="root", parents=()),
        ]
        laid_out = history_mod.assign_lanes(commits)
        self.assertGreaterEqual(laid_out.max_width, 2)
        merge_row = laid_out.rows[0]
        self.assertEqual(2, len(merge_row.edges))
        lanes = {lane for lane, _row in merge_row.edges}
        self.assertEqual(2, len(lanes))

    def test_edges_point_at_real_rows(self) -> None:
        commits = [
            history_mod.Commit(oid="b", parents=("a",)),
            history_mod.Commit(oid="a", parents=()),
        ]
        laid_out = history_mod.assign_lanes(commits)
        self.assertEqual(((0, 1),), laid_out.rows[0].edges)
        self.assertEqual((), laid_out.rows[1].edges)

    def test_parent_outside_the_page_is_marked(self) -> None:
        commits = [history_mod.Commit(oid="b", parents=("missing",))]
        laid_out = history_mod.assign_lanes(commits)
        self.assertEqual(-1, laid_out.rows[0].edges[0][1])

    def test_row_lookup(self) -> None:
        laid_out = history_mod.assign_lanes([history_mod.Commit(oid="x")])
        self.assertEqual(0, laid_out.row_of("x"))
        self.assertEqual(-1, laid_out.row_of("nope"))

    def test_empty_history(self) -> None:
        laid_out = history_mod.assign_lanes([])
        self.assertEqual([], laid_out.rows)
        self.assertTrue(laid_out.ok)


class LogParsingTests(unittest.TestCase):
    def test_decoration_is_split(self) -> None:
        unit = history_mod._UNIT
        record = unit.join(
            ["a" * 40, "aaaaaaa", "b" * 40, "Jo", "jo@example.com", "2026-01-01T00:00:00+00:00",
             "1767225600", "HEAD -> main, origin/main, tag: v1.0", "the subject"]
        )
        commits = history_mod.parse_log(record + "\x00")
        self.assertEqual(1, len(commits))
        item = commits[0]
        self.assertTrue(item.is_head)
        self.assertEqual("main", item.head_branch)
        self.assertIn("origin/main", item.refs)
        self.assertIn("v1.0", item.refs)
        self.assertEqual("the subject", item.subject)

    def test_merge_detection(self) -> None:
        self.assertTrue(history_mod.Commit(oid="m", parents=("a", "b")).is_merge)
        self.assertFalse(history_mod.Commit(oid="m", parents=("a",)).is_merge)

    def test_malformed_records_are_skipped(self) -> None:
        self.assertEqual([], history_mod.parse_log("too\x1ffew\x00"))


@requires_git
class RealHistoryTests(unittest.TestCase):
    def test_reads_commits_newest_first(self) -> None:
        with temp_repo() as repo:
            repo.commit_file("second.txt", "x\n", "second commit")
            repo.commit_file("third.txt", "y\n", "third commit")
            read = history_mod.read_history(repo.root)
            self.assertTrue(read.ok)
            subjects = [row.commit.subject for row in read.rows]
            self.assertEqual(["third commit", "second commit", "initial commit"], subjects)

    def test_head_is_marked(self) -> None:
        with temp_repo() as repo:
            read = history_mod.read_history(repo.root)
            self.assertTrue(read.rows[0].commit.is_head)
            self.assertEqual("main", read.rows[0].commit.head_branch)

    def test_repository_without_commits(self) -> None:
        with temp_repo(initial_commit=False) as repo:
            read = history_mod.read_history(repo.root)
            self.assertTrue(read.ok)
            self.assertEqual([], read.rows)

    def test_limit_marks_truncation(self) -> None:
        with temp_repo() as repo:
            for index in range(4):
                repo.commit_file(f"file{index}.txt", "x\n")
            read = history_mod.read_history(repo.root, limit=2)
            self.assertEqual(2, len(read.rows))
            self.assertTrue(read.truncated)

    def test_real_merge_graph(self) -> None:
        with temp_repo() as repo:
            branch_mod.create_branch(repo.root, "side")
            repo.commit_file("side.txt", "s\n", "side work")
            branch_mod.checkout_branch(repo.root, "main")
            repo.commit_file("main.txt", "m\n", "main work")
            branch_mod.merge(repo.root, "side")
            read = history_mod.read_history(repo.root)
            self.assertTrue(read.ok)
            self.assertGreaterEqual(read.max_width, 2)
            merges = [row for row in read.rows if row.commit.is_merge]
            self.assertEqual(1, len(merges))

    def test_unsafe_revision_is_refused(self) -> None:
        with temp_repo() as repo:
            read = history_mod.read_history(repo.root, revision="--all")
            self.assertFalse(read.ok)


class Diff3ParsingTests(unittest.TestCase):
    MARKED = (
        "line one\n"
        "<<<<<<< ours\n"
        "port = 8080\n"
        "||||||| base\n"
        "port = 1234\n"
        "=======\n"
        "port = 3000\n"
        ">>>>>>> theirs\n"
        "line last\n"
    )

    def test_regions_and_plain_text_alternate(self) -> None:
        segments = conflict_mod.parse_diff3(self.MARKED)
        self.assertEqual(3, len(segments))
        self.assertEqual(["line one"], segments[0])
        self.assertIsInstance(segments[1], conflict_mod.ConflictRegion)
        self.assertEqual(["line last"], segments[2])

    def test_all_three_sides_are_captured(self) -> None:
        region = conflict_mod.parse_diff3(self.MARKED)[1]
        assert isinstance(region, conflict_mod.ConflictRegion)
        self.assertEqual(["port = 8080"], region.ours)
        self.assertEqual(["port = 1234"], region.base)
        self.assertEqual(["port = 3000"], region.theirs)
        self.assertEqual(conflict_mod.REASON_BOTH_CHANGED, region.reason_key)

    def test_both_added_has_no_base(self) -> None:
        marked = "<<<<<<< ours\nmine\n=======\ntheirs\n>>>>>>> theirs\n"
        region = conflict_mod.parse_diff3(marked)[0]
        assert isinstance(region, conflict_mod.ConflictRegion)
        self.assertEqual([], region.base)
        self.assertEqual(conflict_mod.REASON_BOTH_ADDED, region.reason_key)

    def test_no_markers_means_one_plain_segment(self) -> None:
        segments = conflict_mod.parse_diff3("just\ntext\n")
        self.assertEqual(1, len(segments))
        self.assertEqual(["just", "text"], segments[0])

    def test_choices_produce_the_right_lines(self) -> None:
        region = conflict_mod.parse_diff3(self.MARKED)[1]
        assert isinstance(region, conflict_mod.ConflictRegion)
        region.choice = conflict_mod.CHOICE_OURS
        self.assertEqual(["port = 8080"], region.resolution_lines())
        region.choice = conflict_mod.CHOICE_THEIRS
        self.assertEqual(["port = 3000"], region.resolution_lines())
        region.choice = conflict_mod.CHOICE_BOTH
        self.assertEqual(["port = 8080", "port = 3000"], region.resolution_lines())
        region.custom = ["port = 9999"]
        region.choice = conflict_mod.CHOICE_CUSTOM
        self.assertEqual(["port = 9999"], region.resolution_lines())

    def test_pending_region_contributes_nothing(self) -> None:
        region = conflict_mod.ConflictRegion(ours=["a"], theirs=["b"])
        self.assertFalse(region.resolved)
        self.assertEqual([], region.resolution_lines())

    def test_assembly_uses_the_decisions(self) -> None:
        item = conflict_mod.ConflictedFile(path="x", segments=conflict_mod.parse_diff3(self.MARKED))
        item.regions[0].choice = conflict_mod.CHOICE_THEIRS
        self.assertTrue(item.resolved)
        self.assertEqual("line one\nport = 3000\nline last\n", item.assemble())

    def test_unresolved_file_is_not_ready(self) -> None:
        item = conflict_mod.ConflictedFile(path="x", segments=conflict_mod.parse_diff3(self.MARKED))
        self.assertFalse(item.resolved)
        self.assertEqual(1, item.pending_count)


@requires_git
class RealConflictTests(unittest.TestCase):
    def _conflicting_repo(self, repo) -> None:
        """
        Creates a text conflict on the ``main`` branch.

        Args:
            repo: Fixture to work in.

        Returns:
            None
        """

        repo.commit_file("config.py", "port = 1234\ndebug = True\n", "base config")
        branch_mod.create_branch(repo.root, "side")
        repo.commit_file("config.py", "port = 3000\ndebug = True\n", "their change")
        branch_mod.checkout_branch(repo.root, "main")
        repo.commit_file("config.py", "port = 8080\ndebug = True\n", "our change")
        branch_mod.merge(repo.root, "side")

    def test_conflict_is_detected(self) -> None:
        with temp_repo() as repo:
            self._conflicting_repo(repo)
            state = read_state(repo.root)
            self.assertTrue(state.has_conflicts)
            self.assertTrue(state.merging)
            self.assertEqual(["config.py"], conflict_mod.conflicted_paths(repo.root))

    def test_three_stages_are_readable(self) -> None:
        with temp_repo() as repo:
            self._conflicting_repo(repo)
            base = conflict_mod.stage_content(repo.root, "config.py", conflict_mod.STAGE_BASE)
            ours = conflict_mod.stage_content(repo.root, "config.py", conflict_mod.STAGE_OURS)
            theirs = conflict_mod.stage_content(repo.root, "config.py", conflict_mod.STAGE_THEIRS)
            self.assertIn(b"1234", base or b"")
            self.assertIn(b"8080", ours or b"")
            self.assertIn(b"3000", theirs or b"")

    def test_loaded_file_offers_one_decision(self) -> None:
        with temp_repo() as repo:
            self._conflicting_repo(repo)
            item = conflict_mod.load_file(repo.root, "config.py")
            self.assertEqual("", item.error_key)
            self.assertEqual(conflict_mod.KIND_TEXT, item.kind)
            self.assertEqual(1, len(item.regions))
            region = item.regions[0]
            self.assertIn("port = 8080", region.ours)
            self.assertIn("port = 3000", region.theirs)
            self.assertIn("port = 1234", region.base)

    def test_resolution_is_written_and_staged(self) -> None:
        with temp_repo() as repo:
            self._conflicting_repo(repo)
            item = conflict_mod.load_file(repo.root, "config.py")
            item.regions[0].choice = conflict_mod.CHOICE_OURS
            self.assertTrue(conflict_mod.write_resolution(repo.root, item).ok)
            written = (repo.root / "config.py").read_text(encoding="utf-8")
            self.assertIn("port = 8080", written)
            self.assertNotIn("<<<<<<<", written)
            self.assertEqual([], conflict_mod.conflicted_paths(repo.root))

    def test_merge_can_be_finished(self) -> None:
        with temp_repo() as repo:
            self._conflicting_repo(repo)
            item = conflict_mod.load_file(repo.root, "config.py")
            item.regions[0].choice = conflict_mod.CHOICE_BOTH
            conflict_mod.write_resolution(repo.root, item)
            self.assertTrue(conflict_mod.finish_merge(repo.root, "merged by hand").ok)
            state = read_state(repo.root)
            self.assertFalse(state.merging)
            self.assertFalse(state.has_conflicts)

    def test_abort_restores_the_previous_state(self) -> None:
        with temp_repo() as repo:
            self._conflicting_repo(repo)
            self.assertTrue(conflict_mod.abort(repo.root).ok)
            state = read_state(repo.root)
            self.assertFalse(state.merging)
            self.assertFalse(state.has_conflicts)
            self.assertIn("8080", (repo.root / "config.py").read_text(encoding="utf-8"))

    def test_delete_modify_conflict_is_a_whole_file_decision(self) -> None:
        with temp_repo() as repo:
            repo.commit_file("doomed.txt", "content\n", "add file")
            branch_mod.create_branch(repo.root, "side")
            repo.git("rm", "doomed.txt")
            repo.commit("they deleted it")
            branch_mod.checkout_branch(repo.root, "main")
            repo.commit_file("doomed.txt", "changed content\n", "we changed it")
            branch_mod.merge(repo.root, "side")

            item = conflict_mod.load_file(repo.root, "doomed.txt")
            self.assertEqual(conflict_mod.KIND_DELETED_BY_THEM, item.kind)
            self.assertTrue(item.needs_whole_file_choice)
            self.assertFalse(item.resolved)
            item.whole_file_choice = conflict_mod.CHOICE_OURS
            self.assertTrue(item.resolved)
            self.assertTrue(conflict_mod.write_resolution(repo.root, item).ok)
            self.assertTrue((repo.root / "doomed.txt").exists())

    def test_binary_conflict_is_a_whole_file_decision(self) -> None:
        with temp_repo() as repo:
            repo.write_bytes("logo.bin", bytes([0, 1, 2, 3]))
            repo.commit("add binary")
            branch_mod.create_branch(repo.root, "side")
            repo.write_bytes("logo.bin", bytes([9, 9, 0, 9]))
            repo.commit("their binary")
            branch_mod.checkout_branch(repo.root, "main")
            repo.write_bytes("logo.bin", bytes([5, 5, 0, 5]))
            repo.commit("our binary")
            branch_mod.merge(repo.root, "side")

            item = conflict_mod.load_file(repo.root, "logo.bin")
            self.assertEqual(conflict_mod.KIND_BINARY, item.kind)
            item.whole_file_choice = conflict_mod.CHOICE_THEIRS
            self.assertTrue(conflict_mod.write_resolution(repo.root, item).ok)
            self.assertEqual(bytes([9, 9, 0, 9]), (repo.root / "logo.bin").read_bytes())

    def test_unsafe_path_is_refused(self) -> None:
        with temp_repo() as repo:
            item = conflict_mod.load_file(repo.root, "--evil")
            self.assertEqual("error.unsafe_argument", item.error_key)


class CloneTargetTests(unittest.TestCase):
    def test_missing_directory_is_fine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            found = clone_mod.inspect_target(Path(tmp) / "new-project")
            self.assertEqual(clone_mod.TARGET_OK_MISSING, found.state)
            self.assertTrue(found.can_proceed)
            self.assertFalse(found.needs_confirmation)

    def test_empty_directory_is_fine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            found = clone_mod.inspect_target(tmp)
            self.assertEqual(clone_mod.TARGET_OK_EMPTY, found.state)
            self.assertTrue(found.can_proceed)
            self.assertFalse(found.needs_confirmation)

    def test_non_empty_directory_needs_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "notes.txt").write_text("keep me", encoding="utf-8")
            (Path(tmp) / "data").mkdir()
            found = clone_mod.inspect_target(tmp)
            self.assertEqual(clone_mod.TARGET_NON_EMPTY, found.state)
            self.assertTrue(found.can_proceed)
            self.assertTrue(found.needs_confirmation)
            self.assertEqual(2, found.entry_count)
            self.assertIn("notes.txt", found.sample)

    def test_existing_repository_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir()
            found = clone_mod.inspect_target(tmp)
            self.assertEqual(clone_mod.TARGET_IS_REPOSITORY, found.state)
            self.assertFalse(found.can_proceed)

    def test_a_file_is_not_a_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "file.txt"
            target.write_text("x", encoding="utf-8")
            self.assertEqual(clone_mod.TARGET_NOT_A_DIRECTORY, clone_mod.inspect_target(target).state)


class ClonePrepareTests(unittest.TestCase):
    def test_folder_name_is_derived_from_the_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            request = clone_mod.prepare("https://github.com/user/project.git", tmp)
            self.assertIsNotNone(request)
            assert request is not None
            self.assertEqual("project", request.target.name)

    def test_explicit_name_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            request = clone_mod.prepare("https://github.com/user/project.git", tmp, "my-copy")
            assert request is not None
            self.assertEqual("my-copy", request.target.name)

    def test_unsafe_url_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(clone_mod.prepare("ext::sh -c evil", tmp))
            self.assertIsNone(clone_mod.prepare("https://h/p.git\revil", tmp))

    def test_name_may_not_be_a_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(clone_mod.prepare("https://github.com/u/p.git", tmp, "../escape"))
            self.assertIsNone(clone_mod.prepare("https://github.com/u/p.git", tmp, "nested/deep"))
            self.assertIsNone(clone_mod.prepare("https://github.com/u/p.git", tmp, ".."))

    def test_progress_percentages_are_parsed(self) -> None:
        self.assertEqual(("Receiving objects", 47), clone_mod.parse_progress("Receiving objects:  47% (94/200)"))
        self.assertEqual(("Resolving deltas", 100), clone_mod.parse_progress("Resolving deltas: 100% (12/12), done."))

    def test_lines_without_a_percentage_are_ignored(self) -> None:
        self.assertIsNone(clone_mod.parse_progress("Cloning into 'project'..."))
        self.assertIsNone(clone_mod.parse_progress(""))


@requires_git
class RealCloneTests(unittest.TestCase):
    def test_clone_from_a_local_origin(self) -> None:
        with temp_repo_pair() as (first, _second, origin):
            with tempfile.TemporaryDirectory() as tmp:
                request = clone_mod.prepare(str(origin), tmp, "fresh")
                assert request is not None
                lines: list[str] = []
                result = clone_mod.clone(request, on_progress=lines.append)
                self.assertTrue(result.ok, result.message)
                self.assertTrue((request.target / ".git").exists())
                self.assertTrue((request.target / "README.md").exists())

    def test_clone_into_a_non_empty_directory_keeps_what_is_there(self) -> None:
        with temp_repo_pair() as (_first, _second, origin):
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "mixed"
                target.mkdir()
                (target / "my-notes.txt").write_text("do not lose me", encoding="utf-8")

                inspection = clone_mod.inspect_target(target)
                self.assertTrue(inspection.needs_confirmation)

                request = clone_mod.prepare(str(origin), tmp, "mixed")
                assert request is not None
                result = clone_mod.clone(request)
                self.assertTrue(result.ok, result.message)
                self.assertTrue((target / ".git").exists())
                self.assertTrue((target / "README.md").exists())
                self.assertEqual("do not lose me", (target / "my-notes.txt").read_text(encoding="utf-8"))

    def test_clone_into_an_existing_repository_is_refused(self) -> None:
        with temp_repo_pair() as (first, _second, origin):
            request = clone_mod.CloneRequest(url=str(origin), target=first.root)
            result = clone_mod.clone(request)
            self.assertTrue(result.failed)
            self.assertIn("is_repository", result.stderr)


if __name__ == "__main__":
    unittest.main()
