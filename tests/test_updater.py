"""
Tests for the self-update service.

Nothing here reaches GitHub — the API answer and the archive are faked — and
nothing restarts: the restart command is inspected, never executed.

The important tests are the refusals. An update replaces Branchly's own files, so
"a dirty tree blocks the pull" and "an archive may not overwrite user data" are
the two checks that stand between an update and somebody's lost work.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import paths
from constants import APP_URL, UPDATE_MAX_ARCHIVE_BYTES
from services import updater
from tests.support import requires_git, temp_repo, temp_repo_pair


class _Response:
    """
    Stand-in for what ``requests.get`` returns.
    """

    def __init__(self, status_code: int = 200, payload: object = None, body: bytes = b"") -> None:
        """
        Args:
            status_code: HTTP status to report.
            payload: Object returned by ``json()``; a string raises instead.
            body: Bytes handed out by ``iter_content``.
        """

        self.status_code = status_code
        self._payload = payload
        self._body = body

    def json(self) -> object:
        """
        Returns the parsed body.

        Returns:
            object: The payload.

        Raises:
            ValueError: When the payload stands for an unparsable body.
        """

        if isinstance(self._payload, str):
            raise ValueError(self._payload)
        return self._payload

    def iter_content(self, chunk_size: int = 1) -> "list[bytes]":
        """
        Yields the body in one or more chunks.

        Args:
            chunk_size: Requested chunk size.

        Returns:
            list[bytes]: The body, split into chunks.
        """

        return [self._body[at : at + chunk_size] for at in range(0, len(self._body), chunk_size)]

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def _commit_answer(sha: str, message: str = "A newer commit\n\nwith a body") -> _Response:
    """
    Builds a fake answer for the commits endpoint.

    Args:
        sha: Commit hash to report.
        message: Full commit message.

    Returns:
        _Response: The fake response.
    """

    return _Response(payload={"sha": sha, "commit": {"message": message}})


def _archive(*members: tuple[str, str]) -> bytes:
    """
    Builds a GitHub-style source archive.

    Args:
        *members: ``(path inside the wrapper folder, content)`` pairs.

    Returns:
        bytes: A ZIP file.
    """

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for relative, content in members:
            bundle.writestr(f"branchly-main/{relative}", content)
    return buffer.getvalue()


class SlugTests(unittest.TestCase):
    def test_a_github_url_yields_owner_and_name(self) -> None:
        self.assertEqual(
            "joruf/branchly", updater.repository_slug("https://github.com/joruf/branchly")
        )

    def test_a_dot_git_suffix_is_dropped(self) -> None:
        self.assertEqual(
            "joruf/branchly", updater.repository_slug("https://github.com/joruf/branchly.git")
        )

    def test_a_foreign_url_yields_nothing(self) -> None:
        for url in ("https://example.com/joruf/branchly", "", "not a url"):
            self.assertEqual("", updater.repository_slug(url))

    def test_the_configured_project_is_on_github(self) -> None:
        self.assertEqual("joruf/branchly", updater.repository_slug(APP_URL))


class CheckoutTests(unittest.TestCase):
    def test_a_plain_directory_is_not_a_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(updater.is_git_checkout(root))
            self.assertEqual("", updater.local_commit(root))

    @requires_git
    def test_a_checkout_reports_its_commit(self) -> None:
        with temp_repo() as repo:
            self.assertTrue(updater.is_git_checkout(repo.root))
            self.assertEqual(repo.head(), updater.local_commit(repo.root))


class CheckTests(unittest.TestCase):
    def _check(self, response: _Response, local: str) -> updater.UpdateInfo:
        """
        Runs a check against a faked API answer and a faked local commit.

        Args:
            response: What ``requests.get`` should return.
            local: What the installation's commit should look like.

        Returns:
            updater.UpdateInfo: The result.
        """

        with mock.patch.object(updater.requests, "get", return_value=response):
            with mock.patch.object(updater, "local_commit", return_value=local):
                return updater.check()

    def test_a_different_commit_means_an_update(self) -> None:
        info = self._check(_commit_answer("b" * 40), "a" * 40)
        self.assertTrue(info.known)
        self.assertTrue(info.available)
        self.assertEqual("a" * 10, info.local)
        self.assertEqual("b" * 10, info.remote)
        self.assertEqual("A newer commit", info.summary)

    def test_the_same_commit_means_nothing_to_do(self) -> None:
        info = self._check(_commit_answer("a" * 40), "a" * 40)
        self.assertTrue(info.known)
        self.assertFalse(info.available)

    def test_without_a_local_commit_no_update_is_offered(self) -> None:
        info = self._check(_commit_answer("b" * 40), "")
        self.assertTrue(info.known)
        self.assertFalse(info.available, "an installation with no commit must not be replaced blind")

    def test_a_network_failure_is_an_answer_not_an_exception(self) -> None:
        error = updater.requests.ConnectionError("no route to host")
        with mock.patch.object(updater.requests, "get", side_effect=error):
            info = updater.check()
        self.assertFalse(info.known)
        self.assertEqual(updater.ERROR_OFFLINE, info.error_key)
        self.assertIn("no route", info.detail)

    def test_an_http_error_is_reported(self) -> None:
        info = self._check(_Response(status_code=403, payload={}), "a" * 40)
        self.assertFalse(info.known)
        self.assertEqual(updater.ERROR_CHECK_FAILED, info.error_key)
        self.assertIn("403", info.detail)

    def test_an_unreadable_body_is_reported(self) -> None:
        info = self._check(_Response(payload="not json"), "a" * 40)
        self.assertFalse(info.known)
        self.assertEqual(updater.ERROR_CHECK_FAILED, info.error_key)

    def test_an_answer_without_a_commit_is_reported(self) -> None:
        info = self._check(_Response(payload={"nothing": True}), "a" * 40)
        self.assertFalse(info.known)
        self.assertEqual(updater.ERROR_CHECK_FAILED, info.error_key)

    def test_a_missing_message_leaves_the_summary_empty(self) -> None:
        info = self._check(_Response(payload={"sha": "b" * 40}), "a" * 40)
        self.assertTrue(info.known)
        self.assertEqual("", info.summary)


class ThrottleTests(unittest.TestCase):
    def test_zero_hours_always_checks(self) -> None:
        self.assertTrue(updater.due(0, time.time()))

    def test_a_never_checked_installation_is_due(self) -> None:
        self.assertTrue(updater.due(24, 0.0))

    def test_a_recent_check_is_not_repeated(self) -> None:
        self.assertFalse(updater.due(24, time.time() - 3600))

    def test_an_old_check_is_repeated(self) -> None:
        self.assertTrue(updater.due(24, time.time() - 25 * 3600))

    def test_a_timestamp_from_the_future_does_not_block_forever(self) -> None:
        self.assertTrue(updater.due(24, time.time() + 10_000))


class GitApplyTests(unittest.TestCase):
    @requires_git
    def test_a_new_commit_on_the_server_is_fast_forwarded(self) -> None:
        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("ui/main_window.py", "new\n", "a newer version")
            first.git("push", "origin", "main")

            outcome = updater.apply(second.root)

            self.assertTrue(outcome.ok, outcome.detail)
            self.assertEqual(updater.METHOD_GIT, outcome.method)
            self.assertEqual(first.head(), second.head())
            self.assertEqual("new\n", (second.root / "ui" / "main_window.py").read_text())

    @requires_git
    def test_a_local_commit_is_never_buried(self) -> None:
        """
        ``--ff-only`` is the whole point: a divergent history stops the update
        instead of being merged or rewritten behind the user's back.
        """

        with temp_repo_pair() as (first, second, _origin):
            first.commit_file("shared.txt", "theirs\n", "their commit")
            first.git("push", "origin", "main")
            local = second.commit_file("mine.txt", "mine\n", "my own commit")

            outcome = updater.apply(second.root)

            self.assertFalse(outcome.ok)
            self.assertEqual(updater.ERROR_PULL_FAILED, outcome.error_key)
            self.assertEqual(local, second.head(), "the local commit must still be HEAD")

    @requires_git
    def test_uncommitted_changes_block_the_update(self) -> None:
        with temp_repo() as repo:
            repo.write("README.md", "edited but not committed\n")
            outcome = updater.apply(repo.root)
        self.assertFalse(outcome.ok)
        self.assertEqual(updater.METHOD_GIT, outcome.method)
        self.assertEqual(updater.ERROR_DIRTY, outcome.error_key)

    @requires_git
    def test_without_a_remote_the_pull_fails_cleanly(self) -> None:
        with temp_repo() as repo:
            outcome = updater.apply(repo.root)
        self.assertFalse(outcome.ok)
        self.assertEqual(updater.METHOD_GIT, outcome.method)
        self.assertEqual(updater.ERROR_PULL_FAILED, outcome.error_key)
        self.assertTrue(outcome.detail)


class ArchiveApplyTests(unittest.TestCase):
    def _install(self) -> Path:
        """
        Builds a pretend installation that is not a checkout.

        Returns:
            Path: The installation directory; the caller owns the temp dir.
        """

        root = Path(self._tmp.name) / "install"
        (root / "ui").mkdir(parents=True)
        (root / "ui" / "main_window.py").write_text("old\n", encoding="utf-8")
        (root / "settings.json").write_text("mine\n", encoding="utf-8")
        (root / "repos.json").write_text("mine\n", encoding="utf-8")
        (root / ".venv").mkdir()
        (root / ".venv" / "marker").write_text("mine\n", encoding="utf-8")
        return root

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="branchly-update-test-")
        self.addCleanup(self._tmp.cleanup)
        self.root = self._install()

    def test_the_archive_replaces_code_but_keeps_user_data(self) -> None:
        body = _archive(
            ("ui/main_window.py", "new\n"),
            ("README.md", "new readme\n"),
            ("settings.json", "SHOULD NOT LAND\n"),
            ("repos.json", "SHOULD NOT LAND\n"),
            (".venv/marker", "SHOULD NOT LAND\n"),
        )
        with mock.patch.object(updater.requests, "get", return_value=_Response(body=body)):
            outcome = updater.apply(self.root)

        self.assertTrue(outcome.ok, outcome.detail)
        self.assertEqual(updater.METHOD_ARCHIVE, outcome.method)
        self.assertEqual("new\n", (self.root / "ui" / "main_window.py").read_text())
        self.assertEqual("new readme\n", (self.root / "README.md").read_text())
        self.assertEqual("mine\n", (self.root / "settings.json").read_text())
        self.assertEqual("mine\n", (self.root / "repos.json").read_text())
        self.assertEqual("mine\n", (self.root / ".venv" / "marker").read_text())
        self.assertEqual(2, outcome.files)

    def test_a_failed_download_is_reported(self) -> None:
        error = updater.requests.ConnectionError("offline")
        with mock.patch.object(updater.requests, "get", side_effect=error):
            outcome = updater.apply(self.root)
        self.assertFalse(outcome.ok)
        self.assertEqual(updater.ERROR_OFFLINE, outcome.error_key)
        self.assertEqual("old\n", (self.root / "ui" / "main_window.py").read_text())

    def test_an_http_error_is_reported(self) -> None:
        with mock.patch.object(updater.requests, "get", return_value=_Response(status_code=404)):
            outcome = updater.apply(self.root)
        self.assertFalse(outcome.ok)
        self.assertEqual(updater.ERROR_DOWNLOAD_FAILED, outcome.error_key)

    def test_a_broken_archive_is_reported(self) -> None:
        with mock.patch.object(
            updater.requests, "get", return_value=_Response(body=b"not a zip at all")
        ):
            outcome = updater.apply(self.root)
        self.assertFalse(outcome.ok)
        self.assertEqual(updater.ERROR_ARCHIVE_BROKEN, outcome.error_key)

    def test_an_unexpected_layout_is_reported(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("first/file.txt", "one\n")
            bundle.writestr("second/file.txt", "two\n")
        with mock.patch.object(
            updater.requests, "get", return_value=_Response(body=buffer.getvalue())
        ):
            outcome = updater.apply(self.root)
        self.assertFalse(outcome.ok)
        self.assertEqual(updater.ERROR_ARCHIVE_BROKEN, outcome.error_key)

    def test_an_oversized_download_is_refused(self) -> None:
        with mock.patch.object(updater, "UPDATE_MAX_ARCHIVE_BYTES", 8):
            with mock.patch.object(
                updater.requests, "get", return_value=_Response(body=b"x" * 64)
            ):
                outcome = updater.apply(self.root)
        self.assertFalse(outcome.ok)
        self.assertEqual(updater.ERROR_DOWNLOAD_FAILED, outcome.error_key)

    def test_the_real_ceiling_is_generous_enough_for_a_source_tree(self) -> None:
        self.assertGreaterEqual(UPDATE_MAX_ARCHIVE_BYTES, 10 * 1024 * 1024)

    def test_a_traversing_member_lands_nowhere(self) -> None:
        """
        zipfile already sanitizes names; this guards the copy step as well.
        """

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr("branchly-main/../escaped.txt", "nope\n")
            bundle.writestr("branchly-main/ui/main_window.py", "new\n")
        with mock.patch.object(
            updater.requests, "get", return_value=_Response(body=buffer.getvalue())
        ):
            outcome = updater.apply(self.root)
        self.assertTrue(outcome.ok, outcome.detail)
        self.assertFalse((self.root.parent / "escaped.txt").exists())


class RestartTests(unittest.TestCase):
    def test_the_command_points_at_the_entry_point(self) -> None:
        command = updater.restart_command()
        self.assertEqual(2, len(command))
        self.assertTrue(command[1].endswith("run.py"))
        self.assertTrue(Path(command[1]).exists())

    def test_the_marker_is_cleared_so_the_child_enters_its_venv(self) -> None:
        seen: dict[str, object] = {}

        def fake_popen(command: list[str], **kwargs: object) -> None:
            seen.update(kwargs)
            return None

        with mock.patch.dict(os.environ, {updater.REEXEC_MARKER: "1"}, clear=False):
            with mock.patch.object(paths, "is_windows", return_value=True):
                with mock.patch.object(updater.subprocess, "Popen", fake_popen):
                    updater.restart()

        environment = seen.get("env")
        self.assertIsInstance(environment, dict)
        self.assertNotIn(updater.REEXEC_MARKER, environment)
        self.assertFalse(seen.get("shell", False))

    def test_windows_gets_a_windowless_child(self) -> None:
        seen: dict[str, object] = {}

        def fake_popen(command: list[str], **kwargs: object) -> None:
            seen.update(kwargs)
            return None

        with mock.patch.object(paths, "is_windows", return_value=True):
            with mock.patch.object(updater.subprocess, "Popen", fake_popen):
                updater.restart()

        expected = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.assertEqual(expected, seen.get("creationflags"))


class InterpreterTests(unittest.TestCase):
    def test_a_gui_restart_prefers_pythonw_on_windows(self) -> None:
        with mock.patch.object(paths.sys, "platform", "win32"):
            self.assertEqual(
                Path("/proj/.venv/Scripts/pythonw.exe"),
                paths.venv_python_path(Path("/proj"), gui=True),
            )

    def test_a_script_run_still_prefers_python(self) -> None:
        with mock.patch.object(paths.sys, "platform", "win32"):
            self.assertEqual(
                Path("/proj/.venv/Scripts/python.exe"), paths.venv_python_path(Path("/proj"))
            )


class PayloadTests(unittest.TestCase):
    def test_the_check_asks_for_the_configured_branch(self) -> None:
        with mock.patch.object(
            updater.requests, "get", return_value=_commit_answer("b" * 40)
        ) as get:
            with mock.patch.object(updater, "local_commit", return_value="a" * 40):
                updater.check()
        url = get.call_args.args[0]
        self.assertIn("joruf/branchly", url)
        self.assertIn(f"commits/{updater.UPDATE_BRANCH}", url)

    def test_the_request_identifies_branchly(self) -> None:
        with mock.patch.object(
            updater.requests, "get", return_value=_commit_answer("b" * 40)
        ) as get:
            with mock.patch.object(updater, "local_commit", return_value="a" * 40):
                updater.check()
        headers = get.call_args.kwargs["headers"]
        self.assertIn("Branchly", headers["User-Agent"])

    def test_no_token_is_ever_sent(self) -> None:
        """
        The update check is anonymous on purpose: it must not be able to spend the
        user's rate limit or leak their token to a redirect.
        """

        with mock.patch.object(
            updater.requests, "get", return_value=_commit_answer("b" * 40)
        ) as get:
            with mock.patch.object(updater, "local_commit", return_value="a" * 40):
                updater.check()
        headers = json.dumps(get.call_args.kwargs["headers"]).lower()
        self.assertNotIn("authorization", headers)
        self.assertNotIn("token", headers)


if __name__ == "__main__":
    unittest.main()
