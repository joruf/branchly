"""
The only place in Branchly that starts a git process.

Every hardening measure lives here rather than being repeated at each of the
dozens of call sites:

* ``shell=False`` with an argument list — never a command string, so quoting can
  never be got wrong.
* No argument may carry a NUL byte, and none of the transport-helper or
  ``--upload-pack`` style options may appear. If one shows up, it came from
  unvalidated user input and the call is refused before git sees it.
* ``GIT_TERMINAL_PROMPT=0`` so a missing credential fails fast instead of
  hanging on an invisible prompt no one can answer.
* ``protocol.ext.allow=never`` so the ``ext`` transport, which executes commands,
  stays unavailable. Deliberately *not* ``GIT_PROTOCOL_FROM_USER=0``: that would
  also block plain local paths and network shares, which are perfectly ordinary
  places to keep a repository. The ``ext`` transport is already refused twice —
  once by ``gitops.remote_url`` and once by this config option.
* ``LC_ALL=C`` so parsing does not depend on the user's locale.
* Pager and editor forced to something non-interactive.
* A timeout on everything: a wedged git must not freeze the window.

Git credentials are deliberately *not* handled here. Branchly stores none —
git's own credential helper does that job.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from constants import GIT_TIMEOUT_LOCAL

# Options that no Branchly command ever needs. Their presence means user input
# was interpolated somewhere it should not have been.
_FORBIDDEN_ARGUMENTS = (
    "--upload-pack",
    "--receive-pack",
    "--exec",
)

# Prepended to read-only commands so a background scan cannot fight the user's
# own git for the index lock.
_READ_ONLY_PREFIX = ("--no-optional-locks",)

# Prepended to every command: the ext transport runs arbitrary commands and is
# never legitimate here.
_PROTOCOL_PREFIX = ("-c", "protocol.ext.allow=never")

ERROR_NOT_A_REPO = "error.not_a_repo"
ERROR_LOCKED = "error.locked"
ERROR_TIMEOUT = "error.timeout"
ERROR_GENERIC = "error.git_failed"
ERROR_AUTH = "sync.auth_failed"
ERROR_OFFLINE = "sync.offline"
ERROR_PUSH_REJECTED = "sync.push_rejected"


class UnsafeGitArgument(ValueError):
    """
    Raised when an argument is refused before git is started.

    This is a programming error surfaced loudly on purpose: it means a validator
    was skipped somewhere upstream.
    """


@dataclass(frozen=True, slots=True)
class GitResult:
    """
    Outcome of one git invocation.

    Attributes:
        returncode: Process exit code, or -1 when git never ran.
        stdout: Decoded standard output.
        stderr: Decoded standard error.
        args: Arguments passed after the git executable.
        timed_out: Whether the process was killed for exceeding its timeout.
        cwd: Directory the command ran in.
    """

    returncode: int
    stdout: str
    stderr: str
    args: tuple[str, ...] = field(default_factory=tuple)
    timed_out: bool = False
    cwd: str = ""

    @property
    def ok(self) -> bool:
        """
        Reports whether git succeeded.

        Returns:
            bool: True on exit code 0.
        """

        return self.returncode == 0 and not self.timed_out

    @property
    def failed(self) -> bool:
        """
        Reports whether git failed.

        Returns:
            bool: True on any non-zero exit or a timeout.
        """

        return not self.ok

    @property
    def out_lines(self) -> list[str]:
        """
        Splits standard output into lines.

        Returns:
            list[str]: Output lines.
        """

        return self.stdout.splitlines()

    @property
    def message(self) -> str:
        """
        Returns the most useful text git produced.

        Returns:
            str: Standard error when present, otherwise standard output.
        """

        return (self.stderr or self.stdout).strip()

    def error_key(self) -> str:
        """
        Maps the failure onto a translation key.

        Turning git's wording into one of a handful of known cases is what lets
        the UI explain the problem instead of pasting a raw error at the user.

        Returns:
            str: Translation key describing the failure, empty on success.
        """

        if self.ok:
            return ""
        if self.timed_out:
            return ERROR_TIMEOUT
        text = f"{self.stderr}\n{self.stdout}".lower()
        if "not a git repository" in text:
            return ERROR_NOT_A_REPO
        if "index.lock" in text or ("unable to create" in text and ".lock" in text):
            return ERROR_LOCKED
        if "authentication failed" in text or "could not read username" in text:
            return ERROR_AUTH
        if "permission denied (publickey)" in text or "invalid username or password" in text:
            return ERROR_AUTH
        if "could not resolve host" in text or "network is unreachable" in text:
            return ERROR_OFFLINE
        if "connection timed out" in text or "failed to connect" in text:
            return ERROR_OFFLINE
        if "non-fast-forward" in text or ("rejected" in text and "fetch first" in text):
            return ERROR_PUSH_REJECTED
        return ERROR_GENERIC


_git_path: str | None = None
_git_path_resolved = False


def git_executable() -> str | None:
    """
    Locates the git binary, caching the answer.

    Returns:
        str | None: Absolute path to git, or None when it is not installed.
    """

    global _git_path, _git_path_resolved
    if not _git_path_resolved:
        _git_path = shutil.which("git")
        _git_path_resolved = True
    return _git_path


def reset_executable_cache() -> None:
    """
    Forgets the cached git location.

    Only used by tests that simulate a machine without git.

    Returns:
        None
    """

    global _git_path, _git_path_resolved
    _git_path = None
    _git_path_resolved = False


def validate_arguments(args: list[str] | tuple[str, ...]) -> None:
    """
    Refuses an argument list that could redirect what git does.

    Args:
        args: Arguments destined for git.

    Returns:
        None

    Raises:
        UnsafeGitArgument: When an argument is not a string, carries a NUL byte,
            or is one of the forbidden options.
    """

    for arg in args:
        if not isinstance(arg, str):
            raise UnsafeGitArgument(f"git argument is not a string: {arg!r}")
        if "\x00" in arg:
            raise UnsafeGitArgument("git argument contains a NUL byte")
        lowered = arg.lower()
        for forbidden in _FORBIDDEN_ARGUMENTS:
            if lowered == forbidden or lowered.startswith(forbidden + "="):
                raise UnsafeGitArgument(f"refused git option: {arg!r}")


def build_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    """
    Builds the environment every git process runs in.

    Args:
        extra: Additional variables to set.

    Returns:
        dict[str, str]: Environment for the child process.
    """

    env = dict(os.environ)
    env.update(
        {
            # Never wait for input nobody can give.
            "GIT_TERMINAL_PROMPT": "0",
            # Stable, parseable output regardless of the user's locale.
            "LC_ALL": "C",
            "LANG": "C",
            # No pager, no editor, no interactive anything.
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "GIT_EDITOR": "true",
        }
    )
    if extra:
        env.update(extra)
    return env


def _decode(raw: bytes) -> str:
    """
    Decodes git output without ever raising.

    Paths and commit messages are not guaranteed to be UTF-8, and one bad byte
    must not hide the rest of the output.

    Args:
        raw: Raw bytes from git.

    Returns:
        str: Decoded text with undecodable bytes replaced.
    """

    return raw.decode("utf-8", errors="replace")


def _spawn(
    args: list[str],
    cwd: Path | str | None,
    timeout: int,
    input_bytes: bytes | None,
    env_extra: dict[str, str] | None,
) -> tuple[int, bytes, bytes, bool]:
    """
    Runs git and collects its output.

    Args:
        args: Full argument list, without the executable.
        cwd: Working directory.
        timeout: Seconds before the process is killed.
        input_bytes: Data for standard input, or None.
        env_extra: Extra environment variables.

    Returns:
        tuple[int, bytes, bytes, bool]: Exit code, stdout, stderr, timed-out flag.
    """

    executable = git_executable()
    if executable is None:
        return -1, b"", b"git executable not found", False

    try:
        completed = subprocess.run(
            [executable, *args],
            cwd=str(cwd) if cwd is not None else None,
            input=input_bytes,
            capture_output=True,
            shell=False,
            timeout=timeout,
            env=build_environment(env_extra),
            check=False,
        )
    except subprocess.TimeoutExpired as expired:
        return -1, expired.stdout or b"", expired.stderr or b"", True
    except (OSError, ValueError) as error:
        return -1, b"", str(error).encode("utf-8", errors="replace"), False
    return completed.returncode, completed.stdout, completed.stderr, False


def run(
    args: list[str] | tuple[str, ...],
    cwd: Path | str | None = None,
    timeout: int = GIT_TIMEOUT_LOCAL,
    input_text: str | None = None,
    read_only: bool = False,
    env_extra: dict[str, str] | None = None,
) -> GitResult:
    """
    Runs a git command and returns its decoded result.

    Args:
        args: Arguments after the executable, e.g. ``["status", "--porcelain=v2"]``.
        cwd: Repository directory to run in.
        timeout: Seconds before the process is killed.
        input_text: Text fed to standard input, e.g. a commit message.
        read_only: Whether to add ``--no-optional-locks``, for commands that only
            read state and must not fight the user's own git for the index lock.
        env_extra: Extra environment variables.

    Returns:
        GitResult: Outcome. An ordinary git failure is a result, not an exception.

    Raises:
        UnsafeGitArgument: When an argument is refused by ``validate_arguments``.
    """

    validate_arguments(args)
    prefix = list(_PROTOCOL_PREFIX)
    if read_only:
        prefix.extend(_READ_ONLY_PREFIX)
    payload = input_text.encode("utf-8") if input_text is not None else None
    code, out, err, timed_out = _spawn([*prefix, *args], cwd, timeout, payload, env_extra)
    return GitResult(
        returncode=code,
        stdout=_decode(out),
        stderr=_decode(err),
        args=tuple(args),
        timed_out=timed_out,
        cwd=str(cwd) if cwd is not None else "",
    )


def run_binary(
    args: list[str] | tuple[str, ...],
    cwd: Path | str | None = None,
    timeout: int = GIT_TIMEOUT_LOCAL,
    read_only: bool = True,
) -> tuple[bool, bytes]:
    """
    Runs a git command whose output is not text.

    Needed for image previews and for reading the conflicting sides of a binary
    file, where decoding would corrupt the content.

    Args:
        args: Arguments after the executable.
        cwd: Repository directory to run in.
        timeout: Seconds before the process is killed.
        read_only: Whether to add ``--no-optional-locks``.

    Returns:
        tuple[bool, bytes]: Success flag and raw standard output.

    Raises:
        UnsafeGitArgument: When an argument is refused.
    """

    validate_arguments(args)
    prefix = list(_PROTOCOL_PREFIX)
    if read_only:
        prefix.extend(_READ_ONLY_PREFIX)
    code, out, _err, timed_out = _spawn([*prefix, *args], cwd, timeout, None, None)
    return (code == 0 and not timed_out), out


def run_streaming(
    args: list[str] | tuple[str, ...],
    on_progress: Callable[[str], None],
    cwd: Path | str | None = None,
    timeout: int = GIT_TIMEOUT_LOCAL,
    should_cancel: Callable[[], bool] | None = None,
) -> GitResult:
    """
    Runs a git command and reports its progress lines as they arrive.

    Cloning a large repository can take minutes; a dialog that says nothing for
    that long looks broken. Git writes progress to standard error, using carriage
    returns to overwrite the current line, so both separators are treated as line
    ends here.

    Args:
        args: Arguments after the executable.
        on_progress: Called with each progress line. Must not block — it is
            invoked from the calling thread between reads.
        cwd: Repository directory to run in.
        timeout: Seconds before the process is killed.
        should_cancel: Polled between lines; returning True terminates git.

    Returns:
        GitResult: Outcome, with the collected progress text in ``stderr``.

    Raises:
        UnsafeGitArgument: When an argument is refused.
    """

    validate_arguments(args)
    executable = git_executable()
    if executable is None:
        return GitResult(returncode=-1, stdout="", stderr="git executable not found", args=tuple(args))

    full_args = [*_PROTOCOL_PREFIX, *args]
    collected: list[str] = []
    deadline = time.monotonic() + timeout
    cancelled = False

    try:
        process = subprocess.Popen(
            [executable, *full_args],
            cwd=str(cwd) if cwd is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            shell=False,
            env=build_environment(),
            bufsize=0,
        )
    except (OSError, ValueError) as error:
        return GitResult(returncode=-1, stdout="", stderr=str(error), args=tuple(args))

    buffer = bytearray()
    stream = process.stdout
    assert stream is not None
    try:
        while True:
            if should_cancel is not None and should_cancel():
                cancelled = True
                process.terminate()
                break
            if time.monotonic() > deadline:
                process.kill()
                process.wait(timeout=5)
                return GitResult(
                    returncode=-1,
                    stdout="",
                    stderr="".join(collected),
                    args=tuple(args),
                    timed_out=True,
                    cwd=str(cwd) if cwd is not None else "",
                )
            chunk = stream.read(256)
            if not chunk:
                break
            buffer.extend(chunk)
            # Git separates progress updates with \r and finished lines with \n.
            while True:
                position = min(
                    (index for index in (buffer.find(b"\n"), buffer.find(b"\r")) if index != -1),
                    default=-1,
                )
                if position == -1:
                    break
                line = _decode(bytes(buffer[:position])).strip()
                del buffer[: position + 1]
                if line:
                    collected.append(line + "\n")
                    on_progress(line)
    finally:
        try:
            stream.close()
        except OSError:
            pass

    if buffer:
        tail = _decode(bytes(buffer)).strip()
        if tail:
            collected.append(tail + "\n")
            on_progress(tail)

    try:
        returncode = process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        returncode = -1

    return GitResult(
        returncode=-1 if cancelled else returncode,
        stdout="",
        stderr="".join(collected) if not cancelled else "cancelled",
        args=tuple(args),
        cwd=str(cwd) if cwd is not None else "",
    )


def run_lines(
    args: list[str] | tuple[str, ...],
    cwd: Path | str | None = None,
    timeout: int = GIT_TIMEOUT_LOCAL,
) -> list[str]:
    """
    Runs a read-only git command and returns its non-empty output lines.

    A failure yields an empty list, for the callers that treat "no data" and
    "could not read" alike — listing branches for a dropdown, for instance.

    Args:
        args: Arguments after the executable.
        cwd: Repository directory to run in.
        timeout: Seconds before the process is killed.

    Returns:
        list[str]: Output lines, empty when the command failed.
    """

    result = run(args, cwd=cwd, timeout=timeout, read_only=True)
    if result.failed:
        return []
    return [line for line in result.out_lines if line]


def is_repository(path: Path | str) -> bool:
    """
    Reports whether a directory sits inside a git working tree.

    Args:
        path: Directory to test.

    Returns:
        bool: True when git reports it as a working tree.
    """

    target = Path(path)
    if not target.is_dir():
        return False
    result = run(["rev-parse", "--is-inside-work-tree"], cwd=target, read_only=True)
    return result.ok and result.stdout.strip() == "true"


def repository_root(path: Path | str) -> Path | None:
    """
    Finds the working tree root a directory belongs to.

    Lets the user pick any subdirectory when adding a project and still get the
    project itself registered.

    Args:
        path: Directory inside a repository.

    Returns:
        Path | None: Working tree root, or None when the path is not in one.
    """

    target = Path(path)
    if not target.is_dir():
        return None
    result = run(["rev-parse", "--show-toplevel"], cwd=target, read_only=True)
    if result.failed:
        return None
    root = result.stdout.strip()
    return Path(root) if root else None


def git_version() -> str:
    """
    Returns the installed git version string.

    Returns:
        str: Version as reported by git, or an empty string when unavailable.
    """

    result = run(["--version"])
    if result.failed:
        return ""
    return result.stdout.strip()
