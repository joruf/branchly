"""
Cloning, including into a directory that already holds files.

``git clone`` refuses a non-empty destination outright. The equivalent that does
work is the long way round: create the repository in place, add the remote, fetch,
then check the branch out. Git's checkout then refuses to overwrite any existing
untracked file, so nothing already in the folder can be lost — if a name collides,
the checkout stops and the folder is left as it was.

Both routes end in the same place, so the caller just calls ``clone`` and the
right one is picked from what is actually at the destination. The inspection runs
*before* anything happens, so the warning shown to the user is accurate rather
than a guess.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from constants import GIT_TIMEOUT_CLONE, GIT_TIMEOUT_NETWORK
from gitops import remote_url
from gitops.refname import is_valid_branch_name
from gitops.runner import GitResult, run, run_streaming

TARGET_OK_EMPTY = "empty"
TARGET_OK_MISSING = "missing"
TARGET_NON_EMPTY = "non_empty"
TARGET_IS_REPOSITORY = "is_repository"
TARGET_NOT_A_DIRECTORY = "not_a_directory"
TARGET_UNREADABLE = "unreadable"

# States the user may proceed from, one of them only after confirming.
PROCEEDABLE_STATES = frozenset({TARGET_OK_EMPTY, TARGET_OK_MISSING, TARGET_NON_EMPTY})
NEEDS_CONFIRMATION_STATES = frozenset({TARGET_NON_EMPTY})


@dataclass(frozen=True, slots=True)
class TargetInspection:
    """
    What is at the clone destination right now.

    Attributes:
        state: One of the ``TARGET_*`` constants.
        entry_count: Number of visible entries already present.
        sample: A few entry names, for showing the user what is in there.
    """

    state: str
    entry_count: int = 0
    sample: tuple[str, ...] = ()

    @property
    def can_proceed(self) -> bool:
        """
        Reports whether cloning here is possible at all.

        Returns:
            bool: True when the destination is usable.
        """

        return self.state in PROCEEDABLE_STATES

    @property
    def needs_confirmation(self) -> bool:
        """
        Reports whether the user has to agree before cloning.

        Returns:
            bool: True when the directory already holds files.
        """

        return self.state in NEEDS_CONFIRMATION_STATES


def inspect_target(target: Path | str) -> TargetInspection:
    """
    Examines a clone destination.

    Args:
        target: Directory the project should end up in.

    Returns:
        TargetInspection: What is there, and whether cloning may go ahead.
    """

    path = Path(target).expanduser()
    if not path.exists():
        return TargetInspection(state=TARGET_OK_MISSING)
    if not path.is_dir():
        return TargetInspection(state=TARGET_NOT_A_DIRECTORY)
    if (path / ".git").exists():
        return TargetInspection(state=TARGET_IS_REPOSITORY)
    try:
        entries = sorted(item.name for item in path.iterdir())
    except OSError:
        return TargetInspection(state=TARGET_UNREADABLE)
    if not entries:
        return TargetInspection(state=TARGET_OK_EMPTY)
    return TargetInspection(
        state=TARGET_NON_EMPTY,
        entry_count=len(entries),
        sample=tuple(entries[:5]),
    )


@dataclass(frozen=True, slots=True)
class CloneRequest:
    """
    A validated clone job.

    Attributes:
        url: Remote URL, already checked by ``gitops.remote_url``.
        target: Absolute destination directory.
    """

    url: str
    target: Path


def prepare(url: str, parent: Path | str, directory_name: str = "") -> CloneRequest | None:
    """
    Turns user input into a validated clone job.

    Args:
        url: Remote URL as typed.
        parent: Directory the project folder is created in.
        directory_name: Folder name. Defaults to the name derived from the URL.

    Returns:
        CloneRequest | None: The job, or None when the URL was refused or no
            usable folder name could be determined.
    """

    clean_url = remote_url.normalized(url)
    if not clean_url:
        return None
    name = directory_name.strip() or remote_url.suggested_directory_name(clean_url)
    if not name:
        return None
    # A name is a single folder, never a path: no separators, no traversal.
    if "/" in name or "\\" in name or name in {".", ".."}:
        return None
    target = (Path(parent).expanduser() / name).resolve()
    return CloneRequest(url=clean_url, target=target)


def default_remote_branch(url: str) -> str:
    """
    Asks the server which branch it considers the main one.

    Args:
        url: Remote URL, already validated.

    Returns:
        str: Branch name, empty when the server did not say.
    """

    result = run(["ls-remote", "--symref", "--", url, "HEAD"], timeout=GIT_TIMEOUT_NETWORK, read_only=True)
    if result.failed:
        return ""
    for line in result.out_lines:
        if line.startswith("ref: ") and "\tHEAD" in line:
            ref = line[len("ref: "):].split("\t", 1)[0].strip()
            if ref.startswith("refs/heads/"):
                return ref[len("refs/heads/"):]
    return ""


def clone(
    request: CloneRequest,
    on_progress: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> GitResult:
    """
    Clones a repository, reporting progress as it goes.

    Nothing in the cloned repository is executed afterwards, and submodules are
    deliberately not initialised: a repository from a stranger can carry hooks and
    a local config that would run commands, and cloning it must not be enough to
    trigger any of that.

    Args:
        request: Validated clone job.
        on_progress: Called with each progress line git prints.
        should_cancel: Polled between progress lines; True terminates the clone.

    Returns:
        GitResult: Outcome.
    """

    inspection = inspect_target(request.target)
    if not inspection.can_proceed:
        return GitResult(
            returncode=-1,
            stdout="",
            stderr=f"target not usable: {inspection.state}",
            args=("clone",),
        )

    sink = on_progress if on_progress is not None else (lambda _line: None)
    if inspection.state == TARGET_NON_EMPTY:
        return _clone_into_existing(request, sink, should_cancel)

    parent = request.target.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return GitResult(returncode=-1, stdout="", stderr=str(error), args=("clone",))

    args = [
        "clone",
        "--progress",
        # Submodules stay untouched until the user asks for them.
        "--no-recurse-submodules",
        "--",
        request.url,
        str(request.target),
    ]
    return run_streaming(
        args,
        on_progress=sink,
        cwd=parent,
        timeout=GIT_TIMEOUT_CLONE,
        should_cancel=should_cancel,
    )


def _clone_into_existing(
    request: CloneRequest,
    on_progress: Callable[[str], None],
    should_cancel: Callable[[], bool] | None,
) -> GitResult:
    """
    Sets a repository up inside a directory that already holds files.

    On any failure the ``.git`` directory that was just created is removed again,
    so a half-finished attempt does not leave the user's folder looking like a
    broken repository.

    Args:
        request: Validated clone job.
        on_progress: Called with each progress line.
        should_cancel: Polled between progress lines.

    Returns:
        GitResult: Outcome of the step that failed, or of the final checkout.
    """

    target = request.target
    branch = default_remote_branch(request.url) or "main"
    if not is_valid_branch_name(branch):
        return GitResult(returncode=-1, stdout="", stderr="unusable default branch", args=("clone",))

    created_git_dir = not (target / ".git").exists()

    def _cleanup() -> None:
        if created_git_dir:
            shutil.rmtree(target / ".git", ignore_errors=True)

    steps: list[list[str]] = [
        ["init", "-b", branch],
        ["remote", "add", "origin", request.url],
    ]
    for args in steps:
        result = run(args, cwd=target)
        if result.failed:
            _cleanup()
            return result

    on_progress("Fetching…")
    fetched = run_streaming(
        ["fetch", "--progress", "--tags", "origin"],
        on_progress=on_progress,
        cwd=target,
        timeout=GIT_TIMEOUT_CLONE,
        should_cancel=should_cancel,
    )
    if fetched.failed:
        _cleanup()
        return fetched

    # Checkout refuses to overwrite an existing untracked file, which is exactly
    # the protection promised in the warning shown before this ran.
    checked_out = run(["checkout", "-b", branch, "--track", f"origin/{branch}"], cwd=target)
    if checked_out.failed:
        _cleanup()
    return checked_out


def parse_progress(line: str) -> tuple[str, int] | None:
    """
    Extracts a phase name and percentage from a git progress line.

    Args:
        line: A line as printed by ``git clone --progress``.

    Returns:
        tuple[str, int] | None: Phase and percentage, or None when the line
            carries no percentage.
    """

    if ":" not in line or "%" not in line:
        return None
    phase, _, rest = line.partition(":")
    digits = ""
    for char in rest:
        if char.isdigit():
            digits += char
        elif digits and char == "%":
            break
        elif digits:
            digits = ""
    if not digits:
        return None
    try:
        percent = int(digits)
    except ValueError:
        return None
    return phase.strip(), max(0, min(100, percent))
