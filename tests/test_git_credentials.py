"""
Tests for letting git log in with the token Branchly already holds.

The failure this fixes looked like a wrong password and was not one. Branchly
runs git with ``GIT_TERMINAL_PROMPT=0``, so on a machine whose credential helper
has no entry for github.com, git gives up with "could not read Username" before
any password is sent. Branchly could only report that as "the server did not
accept your login", which sent people off to check a login that was never
offered.

Two things therefore have to hold and neither can be read off the code. The
token has to actually reach the server, which is checked against a real HTTP
server that demands one. And it must not reach anywhere else: not another host,
not an SSH remote, and above all not the command line, where any other user on
the machine can read it out of ``ps``.
"""

from __future__ import annotations

import base64
import http.server
import os
import subprocess
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gitops import askpass
from gitops import remote as remote_mod
from services import git_credentials
from tests.support import requires_git

# Shaped like a real token so nothing rejects it early, and obviously not one.
TEST_TOKEN = "ghp_0000000000000000000000000000000000TEST"


class RemoteUrlTests(unittest.TestCase):
    """
    Which remotes a GitHub token is the right credential for.
    """

    def test_github_over_https_is_the_case_it_is_for(self) -> None:
        for url in (
            "https://github.com/joruf/branchly.git",
            "https://github.com/joruf/branchly",
            "https://www.github.com/joruf/branchly.git",
            "HTTPS://GitHub.com/joruf/branchly.git",
        ):
            with self.subTest(url=url):
                self.assertTrue(git_credentials.is_github_https(url))

    def test_ssh_is_not(self) -> None:
        # An SSH remote authenticates with a key. A token means nothing there.
        for url in (
            "git@github.com:joruf/branchly.git",
            "ssh://git@github.com/joruf/branchly.git",
        ):
            with self.subTest(url=url):
                self.assertFalse(git_credentials.is_github_https(url))

    def test_another_host_is_not(self) -> None:
        # Sending a GitHub token anywhere else is handing a credential to a
        # stranger, so the host is matched exactly rather than searched for.
        for url in (
            "https://gitlab.com/joruf/branchly.git",
            "https://git.example.invalid/joruf/branchly.git",
            "https://github.com.evil.invalid/joruf/branchly.git",
            "https://evil.invalid/?x=github.com",
            "https://notgithub.com/joruf/branchly.git",
        ):
            with self.subTest(url=url):
                self.assertFalse(git_credentials.is_github_https(url))

    def test_plain_http_is_not(self) -> None:
        self.assertFalse(git_credentials.is_github_https("http://github.com/joruf/branchly.git"))

    def test_nonsense_is_not(self) -> None:
        for url in ("", "   ", None, 7, "/home/joruf/projects/thing"):
            with self.subTest(url=url):
                self.assertFalse(git_credentials.is_github_https(url))


class TokenLookupTests(unittest.TestCase):
    """
    When a token is offered and when it is held back.
    """

    def _with_token(self, stored: str) -> None:
        """
        Pretends the keychain holds a particular token.

        Args:
            stored: What ``token.load()`` should return.

        Returns:
            None
        """

        real = git_credentials.token_store.load
        git_credentials.token_store.load = lambda: stored
        self.addCleanup(setattr, git_credentials.token_store, "load", real)

    def test_a_github_url_with_a_token_gets_a_login(self) -> None:
        self._with_token(TEST_TOKEN)
        env = git_credentials.for_url("https://github.com/joruf/branchly.git")
        self.assertEqual(git_credentials.TOKEN_USERNAME, env["BRANCHLY_GIT_USERNAME"])
        self.assertEqual(TEST_TOKEN, env["BRANCHLY_GIT_PASSWORD"])
        self.assertTrue(env["GIT_ASKPASS"])

    def test_no_token_means_no_login(self) -> None:
        self._with_token("")
        self.assertEqual({}, git_credentials.for_url("https://github.com/joruf/branchly.git"))

    def test_another_host_never_sees_the_token(self) -> None:
        self._with_token(TEST_TOKEN)
        self.assertEqual({}, git_credentials.for_url("https://gitlab.com/joruf/thing.git"))
        self.assertEqual({}, git_credentials.for_url("git@github.com:joruf/branchly.git"))

    def test_wants_token_is_the_case_worth_explaining(self) -> None:
        # A GitHub project and nothing stored: "check your saved credentials" is
        # the wrong advice, there is nothing to check.
        with TemporaryDirectory() as base:
            repo = Path(base)
            real = remote_mod.remote_fetch_url
            remote_mod.remote_fetch_url = lambda *_a, **_k: (
                "https://github.com/joruf/branchly.git"
            )
            self.addCleanup(setattr, remote_mod, "remote_fetch_url", real)

            self._with_token("")
            self.assertTrue(git_credentials.wants_token(repo))
            self._with_token(TEST_TOKEN)
            self.assertFalse(git_credentials.wants_token(repo))


class AskpassHelperTests(unittest.TestCase):
    """
    The program git runs when it needs a name or a password.
    """

    def test_a_helper_is_available_and_runnable(self) -> None:
        helper = askpass.helper_path()
        self.assertIsNotNone(helper)
        self.assertTrue(os.access(helper, os.X_OK), f"{helper} is not executable")

    def _ask(self, prompt: str) -> str:
        """
        Runs the helper the way git does.

        Args:
            prompt: The question git would ask.

        Returns:
            str: What the helper answered.
        """

        helper = askpass.helper_path()
        environment = dict(os.environ)
        environment.update(askpass.environment("someone", TEST_TOKEN))
        completed = subprocess.run(
            [str(helper), prompt],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        return completed.stdout.strip()

    def test_it_answers_the_username_question_with_the_username(self) -> None:
        self.assertEqual("someone", self._ask("Username for 'https://github.com': "))

    def test_it_answers_anything_else_with_the_secret(self) -> None:
        self.assertEqual(
            TEST_TOKEN, self._ask("Password for 'https://someone@github.com': ")
        )

    def test_nothing_stored_means_no_environment_at_all(self) -> None:
        # Half a login is worse than none: git would send an empty password and
        # the user would be told their credentials were rejected.
        self.assertEqual({}, askpass.environment("someone", ""))


@requires_git
class LoginReachesTheServerTests(unittest.TestCase):
    """
    The part that cannot be read off the code: does git send it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.seen: list[str] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            """
            Demands a login, records it, then gets out of the way.
            """

            def do_GET(self) -> None:
                """
                Answers 401 until a login arrives.

                Returns:
                    None
                """

                header = self.headers.get("Authorization", "")
                if not header.startswith("Basic "):
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="test"')
                    self.end_headers()
                    return
                cls.seen.append(
                    base64.b64decode(header[len("Basic "):]).decode("utf-8", "replace")
                )
                # The repository need not exist. Getting this far is the point.
                self.send_response(404)
                self.end_headers()

            def log_message(self, *_args: object) -> None:
                """
                Keeps the server out of the test output.

                Returns:
                    None
                """

        cls.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self) -> None:
        type(self).seen.clear()

    def _repo(self, base: Path) -> tuple[Path, dict[str, str]]:
        """
        Builds a repository pointed at the test server.

        Args:
            base: Directory to build it in.

        Returns:
            tuple[Path, dict[str, str]]: The working tree and a git environment
                that ignores the machine's own configuration.
        """

        isolation = {
            "GIT_CONFIG_GLOBAL": str(base / ".gitconfig"),
            "GIT_CONFIG_SYSTEM": str(base / ".gitconfig-system"),
            "no_proxy": "*",
            "NO_PROXY": "*",
        }
        work = base / "work"
        work.mkdir()
        environment = dict(os.environ)
        environment.update(isolation)
        for args in (
            ["init", "-b", "main"],
            ["remote", "add", "origin", f"http://127.0.0.1:{self.port}/repo.git"],
        ):
            subprocess.run(
                ["git", *args], cwd=work, env=environment, check=True, capture_output=True
            )
        return work, isolation

    def test_without_a_login_nothing_is_sent(self) -> None:
        with TemporaryDirectory() as base:
            work, isolation = self._repo(Path(base))
            remote_mod.ls_remote_heads(work, credentials=isolation)
            self.assertEqual([], self.seen)

    def test_the_token_arrives_at_the_server(self) -> None:
        with TemporaryDirectory() as base:
            work, isolation = self._repo(Path(base))
            credentials = dict(askpass.environment("x-access-token", TEST_TOKEN))
            credentials.update(isolation)

            remote_mod.ls_remote_heads(work, credentials=credentials)
            self.assertEqual([f"x-access-token:{TEST_TOKEN}"], self.seen)

    def test_the_token_is_never_put_on_the_command_line(self) -> None:
        # Process arguments are readable by anyone on the machine through ``ps``.
        # The environment of a process is readable only by its owner, which is
        # why the token travels there.
        with TemporaryDirectory() as base:
            work, isolation = self._repo(Path(base))
            credentials = dict(askpass.environment("x-access-token", TEST_TOKEN))
            credentials.update(isolation)

            captured: list[list[str]] = []
            real_run = remote_mod.run

            def spy(args, **kwargs):  # noqa: ANN001, ANN003, ANN202 - passthrough
                captured.append(list(args))
                return real_run(args, **kwargs)

            remote_mod.run = spy
            self.addCleanup(setattr, remote_mod, "run", real_run)

            remote_mod.ls_remote_heads(work, credentials=credentials)
            self.assertTrue(captured)
            for args in captured:
                for part in args:
                    self.assertNotIn(TEST_TOKEN, part)


if __name__ == "__main__":
    unittest.main()
