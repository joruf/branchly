"""
Sending every project's unsent commits to the server in one go.

The counterpart of ``services.puller`` and just as careful, for the same reason:
sending twenty projects at once is not a decision made while looking at each.

* **Only what is already there is sent.** A branch with a tracking branch on the
  server is pushed to it. A branch the server has never seen is not published
  on the side; that is a choice to make in the project itself.
* **Nothing is forced.** A server that has commits the project does not is a
  reason to skip, with the number of commits waiting there, never to overwrite.
* **Uncommitted edits are no obstacle.** Pushing sends commits, not files, so a
  project with edits in progress is sent like any other.

The results use the same shape as a bulk update, so one window can show both.
"""

from __future__ import annotations

from pathlib import Path

from gitops import remote as remote_mod
from gitops.runner import ERROR_GENERIC, ERROR_PUSH_REJECTED
from gitops.status import read_state
from services import git_credentials
from services.puller import (
    RESULT_CURRENT,
    RESULT_FAILED,
    RESULT_PUSHED,
    RESULT_SKIPPED,
    SKIP_CONFLICTS,
    SKIP_DETACHED,
    SKIP_EMPTY,
    SKIP_IN_PROGRESS,
    SKIP_MISSING,
    SKIP_NO_REMOTE,
    PullJob,
    PullResult,
)

SKIP_UNPUBLISHED = "push_all.skip_unpublished"
SKIP_BEHIND = "push_all.skip_behind"


def blocking_reason(state) -> str:  # noqa: ANN001 - RepositoryState
    """
    Names the one thing that stops this project's commits from being sent.

    Args:
        state: Freshly read repository state.

    Returns:
        str: Translation key for the reason, empty when nothing is in the way.
    """

    if state.initial:
        return SKIP_EMPTY
    if state.has_conflicts:
        return SKIP_CONFLICTS
    if state.operation_in_progress:
        return SKIP_IN_PROGRESS
    if state.detached:
        return SKIP_DETACHED
    if not state.upstream:
        return SKIP_UNPUBLISHED
    if state.behind:
        # The server would refuse anyway. Saying so before asking it is the
        # clearer answer, and the count says how much is waiting there.
        return SKIP_BEHIND
    return ""


def push_one(job: PullJob) -> PullResult:
    """
    Sends one project's unsent commits, or explains why it did not.

    Args:
        job: Project to send.

    Returns:
        PullResult: What happened. Never raises: one unusable project must not
            take the whole run down.
    """

    path = Path(job.path)
    if not (path / ".git").exists():
        return PullResult(key=job.key, name=job.name, state=RESULT_SKIPPED, reason_key=SKIP_MISSING)

    state = read_state(path)
    if not state.ok:
        return PullResult(
            key=job.key,
            name=job.name,
            state=RESULT_FAILED,
            reason_key=state.error_key or ERROR_GENERIC,
        )

    branch = state.display_branch
    if not remote_mod.has_remote(path):
        return PullResult(
            key=job.key, name=job.name, state=RESULT_SKIPPED, reason_key=SKIP_NO_REMOTE, branch=branch
        )

    reason = blocking_reason(state)
    if reason:
        return PullResult(
            key=job.key,
            name=job.name,
            state=RESULT_SKIPPED,
            reason_key=reason,
            waiting=state.behind if reason == SKIP_BEHIND else 0,
            branch=branch,
        )
    if not state.ahead:
        return PullResult(key=job.key, name=job.name, state=RESULT_CURRENT, branch=branch)

    result = remote_mod.push(path, credentials=git_credentials.for_repository(path))
    if result.failed:
        key = result.error_key() or ERROR_GENERIC
        # Somebody sent something since the last check. Same situation as a
        # known head start on the server, so it reads the same way.
        rejected = key == ERROR_PUSH_REJECTED
        return PullResult(
            key=job.key,
            name=job.name,
            state=RESULT_SKIPPED if rejected else RESULT_FAILED,
            reason_key=SKIP_BEHIND if rejected else key,
            detail=(result.stderr or result.stdout).strip(),
            branch=branch,
        )
    return PullResult(
        key=job.key, name=job.name, state=RESULT_PUSHED, commits=state.ahead, branch=branch
    )
