"""
Tests for the git process layer.

The hardening tests run without touching a repository; the rest use throwaway
repositories from ``tests.support``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gitops import runner
from gitops.runner import (
    ERROR_LOCKED,
    ERROR_NOT_A_REPO,
    ERROR_OFFLINE,
    ERROR_TIMEOUT,
    GitResult,
    UnsafeGitArgument,
    build_environment,
    git_executable,
    git_version,
    is_repository,
    repository_root,
    run,
    run_binary,
    run_lines,
    validate_arguments,
)
from tests.support import requires_git, temp_repo


class ArgumentValidationTests(unittest.TestCase):
    def test_ordinary_arguments_pass(self) -> None:
        validate_arguments(["status", "--porcelain=v2", "--", "some/file.txt"])

    def test_nul_byte_is_refused(self) -> None:
        with self.assertRaises(UnsafeGitArgument):
            validate_arguments(["log", "main\x00--all"])

    def test_non_string_is_refused(self) -> None:
        with self.assertRaises(UnsafeGitArgument):
            validate_arguments(["log", 5])  # type: ignore[list-item]

    def test_upload_pack_is_refused(self) -> None:
        for arg in ("--upload-pack", "--upload-pack=/bin/sh", "--UPLOAD-PACK=x"):
            with self.assertRaises(UnsafeGitArgument, msg=arg):
                validate_arguments(["fetch", arg])

    def test_receive_pack_and_exec_are_refused(self) -> None:
        for arg in ("--receive-pack=evil", "--exec=evil"):
            with self.assertRaises(UnsafeGitArgument, msg=arg):
                validate_arguments(["push", arg])

    def test_run_refuses_before_starting_git(self) -> None:
        with mock.patch.object(runner, "_spawn") as spawn:
            with self.assertRaises(UnsafeGitArgument):
                run(["fetch", "--upload-pack=/bin/sh"])
            spawn.assert_not_called()

    def test_run_binary_refuses_too(self) -> None:
        with mock.patch.object(runner, "_spawn") as spawn:
            with self.assertRaises(UnsafeGitArgument):
                run_binary(["show", "--exec=evil"])
            spawn.assert_not_called()


class EnvironmentTests(unittest.TestCase):
    def test_interactive_prompts_are_disabled(self) -> None:
        env = build_environment()
        self.assertEqual("0", env["GIT_TERMINAL_PROMPT"])
        self.assertEqual("true", env["GIT_EDITOR"])
        self.assertEqual("cat", env["GIT_PAGER"])

    def test_local_paths_stay_usable(self) -> None:
        # GIT_PROTOCOL_FROM_USER=0 would block local paths and network shares,
        # so the ext transport is refused by config instead (see the prefix test).
        self.assertNotIn("GIT_PROTOCOL_FROM_USER", build_environment())

    def test_locale_is_pinned(self) -> None:
        env = build_environment()
        self.assertEqual("C", env["LC_ALL"])
        self.assertEqual("C", env["LANG"])

    def test_extra_variables_are_merged(self) -> None:
        env = build_environment({"GIT_AUTHOR_NAME": "Someone"})
        self.assertEqual("Someone", env["GIT_AUTHOR_NAME"])
        self.assertEqual("0", env["GIT_TERMINAL_PROMPT"])

    def test_no_shell_is_used(self) -> None:
        with mock.patch("gitops.runner.subprocess.run") as spawn:
            spawn.return_value = mock.Mock(returncode=0, stdout=b"", stderr=b"")
            run(["status"])
            kwargs = spawn.call_args.kwargs
            self.assertFalse(kwargs["shell"])
            self.assertIsInstance(spawn.call_args.args[0], list)

    def test_protocol_guard_precedes_the_subcommand(self) -> None:
        with mock.patch("gitops.runner.subprocess.run") as spawn:
            spawn.return_value = mock.Mock(returncode=0, stdout=b"", stderr=b"")
            run(["fetch"])
            argv = spawn.call_args.args[0]
            self.assertEqual("-c", argv[1])
            self.assertEqual("protocol.ext.allow=never", argv[2])
            self.assertIn("fetch", argv)

    def test_read_only_adds_the_lock_guard(self) -> None:
        with mock.patch("gitops.runner.subprocess.run") as spawn:
            spawn.return_value = mock.Mock(returncode=0, stdout=b"", stderr=b"")
            run(["status"], read_only=True)
            self.assertIn("--no-optional-locks", spawn.call_args.args[0])

    def test_write_commands_do_not_add_the_lock_guard(self) -> None:
        with mock.patch("gitops.runner.subprocess.run") as spawn:
            spawn.return_value = mock.Mock(returncode=0, stdout=b"", stderr=b"")
            run(["commit", "-m", "x"])
            self.assertNotIn("--no-optional-locks", spawn.call_args.args[0])


class GitResultTests(unittest.TestCase):
    def test_success_flags(self) -> None:
        result = GitResult(returncode=0, stdout="fine", stderr="")
        self.assertTrue(result.ok)
        self.assertFalse(result.failed)
        self.assertEqual("", result.error_key())

    def test_timeout_wins_over_exit_code(self) -> None:
        result = GitResult(returncode=0, stdout="", stderr="", timed_out=True)
        self.assertTrue(result.failed)
        self.assertEqual(ERROR_TIMEOUT, result.error_key())

    def test_recognises_missing_repository(self) -> None:
        result = GitResult(returncode=128, stdout="", stderr="fatal: not a git repository")
        self.assertEqual(ERROR_NOT_A_REPO, result.error_key())

    def test_recognises_index_lock(self) -> None:
        result = GitResult(
            returncode=128,
            stdout="",
            stderr="fatal: Unable to create '.git/index.lock': File exists",
        )
        self.assertEqual(ERROR_LOCKED, result.error_key())

    def test_recognises_offline(self) -> None:
        result = GitResult(
            returncode=128,
            stdout="",
            stderr="fatal: unable to access: Could not resolve host: github.com",
        )
        self.assertEqual(ERROR_OFFLINE, result.error_key())

    def test_message_prefers_stderr(self) -> None:
        self.assertEqual("boom", GitResult(returncode=1, stdout="out", stderr=" boom ").message)
        self.assertEqual("out", GitResult(returncode=1, stdout=" out ", stderr="").message)

    def test_out_lines_drops_trailing_blank(self) -> None:
        result = GitResult(returncode=0, stdout="a\nb\n", stderr="")
        self.assertEqual(["a", "b"], result.out_lines)


class MissingGitTests(unittest.TestCase):
    def tearDown(self) -> None:
        runner.reset_executable_cache()

    def test_absent_git_yields_a_failed_result(self) -> None:
        runner.reset_executable_cache()
        with mock.patch("gitops.runner.shutil.which", return_value=None):
            self.assertIsNone(git_executable())
            result = run(["status"])
            self.assertTrue(result.failed)
            self.assertIn("not found", result.stderr)

    def test_executable_lookup_is_cached(self) -> None:
        runner.reset_executable_cache()
        with mock.patch("gitops.runner.shutil.which", return_value="/usr/bin/git") as which:
            git_executable()
            git_executable()
            which.assert_called_once()


@requires_git
class RealRepositoryTests(unittest.TestCase):
    def test_version_is_reported(self) -> None:
        self.assertIn("git version", git_version())

    def test_repository_detection(self) -> None:
        with temp_repo() as repo:
            self.assertTrue(is_repository(repo.root))
            self.assertEqual(repo.root.resolve(), Path(str(repository_root(repo.root))).resolve())

    def test_subdirectory_resolves_to_the_root(self) -> None:
        with temp_repo() as repo:
            nested = repo.root / "deep" / "nested"
            nested.mkdir(parents=True)
            self.assertEqual(repo.root.resolve(), Path(str(repository_root(nested))).resolve())

    def test_plain_directory_is_not_a_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(is_repository(tmp))
            self.assertIsNone(repository_root(tmp))

    def test_missing_directory_is_handled(self) -> None:
        self.assertFalse(is_repository("/nonexistent/branchly/path"))
        self.assertIsNone(repository_root("/nonexistent/branchly/path"))

    def test_run_reads_real_output(self) -> None:
        with temp_repo() as repo:
            result = run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo.root, read_only=True)
            self.assertTrue(result.ok)
            self.assertEqual("main", result.stdout.strip())

    def test_run_lines_filters_blanks(self) -> None:
        with temp_repo() as repo:
            repo.commit_file("second.txt", "content\n")
            lines = run_lines(["log", "--format=%s"], cwd=repo.root)
            self.assertIn("initial commit", lines)
            self.assertTrue(all(line for line in lines))

    def test_run_lines_returns_empty_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual([], run_lines(["log"], cwd=tmp))

    def test_binary_output_is_not_decoded(self) -> None:
        payload = bytes(range(256))
        with temp_repo() as repo:
            repo.write_bytes("blob.bin", payload)
            repo.commit("add binary")
            ok, raw = run_binary(["show", "HEAD:blob.bin"], cwd=repo.root)
            self.assertTrue(ok)
            self.assertEqual(payload, raw)

    def test_commit_message_travels_through_stdin(self) -> None:
        with temp_repo() as repo:
            repo.write("note.txt", "hello\n")
            run(["add", "-A"], cwd=repo.root)
            message = "subject line\n\nbody with a \"quote\" and a ; semicolon\n"
            result = run(["commit", "-F", "-"], cwd=repo.root, input_text=message)
            self.assertTrue(result.ok, result.message)
            subject = run(["log", "-1", "--format=%s"], cwd=repo.root, read_only=True)
            self.assertEqual("subject line", subject.stdout.strip())

    def test_undecodable_output_does_not_raise(self) -> None:
        with temp_repo() as repo:
            repo.write_bytes("weird.txt", b"\xff\xfe not utf8\n")
            repo.commit("add weird")
            result = run(["show", "HEAD:weird.txt"], cwd=repo.root, read_only=True)
            self.assertTrue(result.ok)
            self.assertIn("not utf8", result.stdout)

    def test_timeout_is_reported(self) -> None:
        with temp_repo() as repo:
            # `git help --all` is harmless; a zero timeout guarantees the kill path.
            with mock.patch("gitops.runner.subprocess.run") as spawn:
                spawn.side_effect = runner.subprocess.TimeoutExpired(cmd="git", timeout=0)
                result = run(["log"], cwd=repo.root, timeout=1)
            self.assertTrue(result.timed_out)
            self.assertEqual(ERROR_TIMEOUT, result.error_key())

    def test_branch_name_with_shell_metacharacters_is_literal(self) -> None:
        # Proves the no-shell promise: a name full of shell syntax is just a name.
        with temp_repo() as repo:
            tricky = "feature/semi;colon&and|pipe$dollar"
            result = run(["branch", tricky], cwd=repo.root)
            self.assertTrue(result.ok, result.message)
            branches = run_lines(["branch", "--format=%(refname:short)"], cwd=repo.root)
            self.assertIn(tricky, branches)
            self.assertFalse((repo.root / "colon&and|pipe$dollar").exists())


if __name__ == "__main__":
    unittest.main()
