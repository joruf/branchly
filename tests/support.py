"""
Test helpers for building throwaway git repositories.

Fixtures deliberately pin their own identity and config: the suite must behave
the same on a developer machine with a rich ``~/.gitconfig`` and on a bare CI
runner with none.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

GIT_AVAILABLE = shutil.which("git") is not None

requires_git = unittest.skipUnless(GIT_AVAILABLE, "git is not installed")


def _fixture_env(home: Path) -> dict[str, str]:
    """
    Builds an environment that isolates git from the developer's own config.

    Args:
        home: Directory standing in for the user home.

    Returns:
        dict[str, str]: Environment for fixture git calls.
    """

    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
            "GIT_CONFIG_SYSTEM": str(home / ".gitconfig-system"),
            "GIT_AUTHOR_NAME": "Branchly Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Branchly Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    return env


class TempRepo:
    """
    A git working tree that exists for the duration of one test.
    """

    def __init__(self, root: Path, home: Path) -> None:
        """
        Args:
            root: Working tree directory.
            home: Directory standing in for the user home.
        """

        self.root = root
        self.home = home
        self.env = _fixture_env(home)

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        """
        Runs a git command inside the fixture.

        Args:
            *args: Arguments after the executable.
            check: Whether a non-zero exit should fail the test.

        Returns:
            subprocess.CompletedProcess[str]: Completed process.
        """

        completed = subprocess.run(
            ["git", *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            shell=False,
            timeout=60,
            check=False,
        )
        if check and completed.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed:\n{completed.stderr}")
        return completed

    def write(self, relative: str, content: str) -> Path:
        """
        Writes a file inside the working tree.

        Args:
            relative: Path relative to the working tree root.
            content: File content.

        Returns:
            Path: Absolute path of the written file.
        """

        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def write_bytes(self, relative: str, content: bytes) -> Path:
        """
        Writes a binary file inside the working tree.

        Args:
            relative: Path relative to the working tree root.
            content: File content.

        Returns:
            Path: Absolute path of the written file.
        """

        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def commit(self, message: str = "change") -> str:
        """
        Stages everything and commits it.

        Args:
            message: Commit message.

        Returns:
            str: Full object id of the new commit.
        """

        self.git("add", "-A")
        self.git("commit", "-m", message)
        return self.head()

    def commit_file(self, relative: str, content: str, message: str | None = None) -> str:
        """
        Writes one file and commits it.

        Args:
            relative: Path relative to the working tree root.
            content: File content.
            message: Commit message. Defaults to a message naming the file.

        Returns:
            str: Full object id of the new commit.
        """

        self.write(relative, content)
        return self.commit(message or f"update {relative}")

    def head(self) -> str:
        """
        Returns the current commit id.

        Returns:
            str: Full object id of HEAD.
        """

        return self.git("rev-parse", "HEAD").stdout.strip()

    def current_branch(self) -> str:
        """
        Returns the checked-out branch name.

        Returns:
            str: Branch name, empty when HEAD is detached.
        """

        completed = self.git("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        return completed.stdout.strip()


@contextmanager
def temp_repo(initial_commit: bool = True) -> Iterator[TempRepo]:
    """
    Creates a git repository in a temporary directory.

    Args:
        initial_commit: Whether to create a first commit, so HEAD exists. Pass
            False to test the empty-repository case, which behaves differently
            for almost every git command.

    Yields:
        TempRepo: The fixture, removed again on exit.
    """

    with tempfile.TemporaryDirectory(prefix="branchly-test-") as tmp:
        base = Path(tmp)
        home = base / "home"
        root = base / "repo"
        home.mkdir()
        root.mkdir()
        repo = TempRepo(root=root, home=home)
        repo.git("init", "-b", "main")
        repo.git("config", "user.name", "Branchly Test")
        repo.git("config", "user.email", "test@example.invalid")
        repo.git("config", "commit.gpgsign", "false")
        if initial_commit:
            repo.commit_file("README.md", "# Fixture\n", "initial commit")
        yield repo


@contextmanager
def temp_repo_pair() -> Iterator[tuple[TempRepo, TempRepo, Path]]:
    """
    Creates two clones of one bare repository.

    This is the shape every sync and conflict test needs: two working trees that
    share an origin, so pushing from one makes the other one "behind".

    Yields:
        tuple[TempRepo, TempRepo, Path]: First clone, second clone, bare origin.
    """

    with tempfile.TemporaryDirectory(prefix="branchly-pair-") as tmp:
        base = Path(tmp)
        home = base / "home"
        home.mkdir()
        origin = base / "origin.git"
        env = _fixture_env(home)
        subprocess.run(
            ["git", "init", "--bare", "-b", "main", str(origin)],
            env=env,
            capture_output=True,
            check=True,
            shell=False,
            timeout=60,
        )

        clones: list[TempRepo] = []
        for name in ("first", "second"):
            root = base / name
            subprocess.run(
                ["git", "clone", str(origin), str(root)],
                env=env,
                capture_output=True,
                check=True,
                shell=False,
                timeout=60,
            )
            clone = TempRepo(root=root, home=home)
            clone.git("config", "user.name", f"Branchly {name}")
            clone.git("config", "user.email", f"{name}@example.invalid")
            clone.git("config", "commit.gpgsign", "false")
            clones.append(clone)

        first, second = clones
        first.commit_file("README.md", "# Shared\n", "initial commit")
        first.git("push", "-u", "origin", "main")
        second.git("pull", "--no-rebase", "origin", "main")
        second.git("branch", "--set-upstream-to=origin/main", "main", check=False)
        yield first, second, origin
