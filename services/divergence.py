"""
Bringing a diverged branch back into one line of history.

A project that is both ahead and behind cannot be sent and cannot be
fast-forwarded. The user has already decided to fix that by picking one of two
moves in the dialog:

* put the local commits on top of the server's (rebase, then push)
* join both sides with a merge commit (merge pull, then push)

Nothing here invents a third option such as force-push. That would throw one
side away, and Branchly refuses to do that from a one-click button.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gitops import remote as remote_mod
from gitops.runner import GitResult
from gitops.status import RepositoryState, read_state

#: Put local commits on top of the server's, then upload.
MODE_REBASE_PUSH = "rebase_push"
#: Join both sides with a merge, then upload.
MODE_MERGE_PUSH = "merge_push"

BLOCKED_DIRTY = "divergence.blocked_dirty"
BLOCKED_CONFLICTS = "divergence.blocked_conflicts"
BLOCKED_IN_PROGRESS = "divergence.blocked_in_progress"
BLOCKED_DETACHED = "divergence.blocked_detached"
BLOCKED_NOT_DIVERGED = "divergence.blocked_not_diverged"


@dataclass(frozen=True, slots=True)
class ResolveResult:
    """
    Outcome of one divergence fix.

    Attributes:
        ok: Whether pull and push both succeeded.
        needs_conflict_assistant: Whether the pull stopped on conflicts the user
            must decide.
        blocked_key: Translation key when a pre-check refused to start.
        pull: Result of the pull step, when it ran.
        push: Result of the push step, when it ran.
    """

    ok: bool = False
    needs_conflict_assistant: bool = False
    blocked_key: str = ""
    pull: GitResult | None = None
    push: GitResult | None = None

    @property
    def failed_step(self) -> GitResult | None:
        """
        Returns the git result that failed, if any.

        Returns:
            GitResult | None: Failed pull or push, else None.
        """

        if self.pull is not None and self.pull.failed:
            return self.pull
        if self.push is not None and self.push.failed:
            return self.push
        return None


def is_diverged(state: RepositoryState) -> bool:
    """
    Reports whether local and upstream each have commits the other lacks.

    Args:
        state: Freshly read repository state.

    Returns:
        bool: True when ahead and behind are both positive.
    """

    return state.ok and state.ahead > 0 and state.behind > 0


def should_offer(state: RepositoryState) -> bool:
    """
    Reports whether the divergence dialog is appropriate for this state.

    Dirty trees are still offered: the dialog explains that the fix cannot run
    until the tree is clean. Conflicts and half-finished operations take the
    conflict assistant instead.

    Args:
        state: Freshly read repository state.

    Returns:
        bool: True when the dialog should open.
    """

    if not is_diverged(state):
        return False
    if state.detached or state.has_conflicts or state.operation_in_progress:
        return False
    return True


def _blocked(state: RepositoryState) -> str:
    """
    Names the pre-check that stops a fix from starting.

    Args:
        state: Freshly read repository state.

    Returns:
        str: Translation key, empty when nothing is in the way.
    """

    if not is_diverged(state):
        return BLOCKED_NOT_DIVERGED
    if state.detached:
        return BLOCKED_DETACHED
    if state.has_conflicts:
        return BLOCKED_CONFLICTS
    if state.operation_in_progress:
        return BLOCKED_IN_PROGRESS
    if not state.is_clean:
        return BLOCKED_DIRTY
    return ""


def resolve(
    repo: Path | str,
    mode: str,
    credentials: dict[str, str] | None = None,
) -> ResolveResult:
    """
    Integrates the server's commits, then uploads the result.

    Args:
        repo: Working tree path.
        mode: ``MODE_REBASE_PUSH`` or ``MODE_MERGE_PUSH``.
        credentials: Login for the server, as built by
            ``services.git_credentials``.

    Returns:
        ResolveResult: What happened. Never raises.
    """

    state = read_state(repo)
    blocked = _blocked(state)
    if blocked:
        return ResolveResult(blocked_key=blocked)

    rebase = mode == MODE_REBASE_PUSH
    pull = remote_mod.pull(
        repo,
        rebase=rebase,
        ff_only=False,
        credentials=credentials,
    )
    if pull.failed:
        after = read_state(repo)
        if after.has_conflicts or after.operation_in_progress:
            return ResolveResult(needs_conflict_assistant=True, pull=pull)
        return ResolveResult(pull=pull)

    after_pull = read_state(repo)
    set_upstream = not after_pull.upstream
    branch = after_pull.branch if set_upstream else ""
    push = remote_mod.push(
        repo,
        branch=branch,
        set_upstream=set_upstream,
        credentials=credentials,
    )
    if push.failed:
        return ResolveResult(pull=pull, push=push)
    return ResolveResult(ok=True, pull=pull, push=push)
