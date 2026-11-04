"""
Joining a project to a server that has a history of its own.

This is what a project looks like that was started without Git, got its first
commit, and was then pointed at an online repository that already holds a
history. The two have no starting point in common: ``git merge-base`` finds
nothing. Sending is refused, a pull is refused ("unrelated histories"), and
neither rebase nor merge, the two fixes for a branch that moved on both sides,
helps, because they would try to replay every file as a conflict.

The way out is the one a person would take by hand:

1. Point the branch at the server's history, keeping every file and the index
   exactly as they are (``reset --soft``).
2. Record what is different between the server's files and these ones as one new
   version, on top of the server's history.
3. Upload that, which is now an ordinary fast-forward.

Nothing is forced and nothing on the server is rewritten: its 800 versions stay
where they are. The version this project had made on its own is replaced by the
new one on this side only, and is put back if recording the new one fails.

What the dialog must say before any of that, because it is the one thing that
can surprise, is what the new version contains: files that exist only on the
server are removed in it, since "these files are the truth" is the whole
point. They remain in the server's history.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gitops import remote as remote_mod
from gitops.commit import CommitDraft, commit, has_staged_changes
from gitops.refname import is_valid_branch_name
from gitops.runner import GitResult, run
from gitops.status import RepositoryState, read_state

REMOTE = "origin"

#: How many places the dialog names, the rest are only counted.
GROUPS_SHOWN = 8

BLOCKED_DIRTY = "divergence.blocked_dirty"
BLOCKED_CONFLICTS = "divergence.blocked_conflicts"
BLOCKED_IN_PROGRESS = "divergence.blocked_in_progress"
BLOCKED_DETACHED = "divergence.blocked_detached"
BLOCKED_NOT_UNRELATED = "unrelated.blocked_gone"
BLOCKED_SUMMARY = "unrelated.blocked_summary"


@dataclass(frozen=True, slots=True)
class Unrelated:
    """
    The facts about a project and a server that share no history.

    Attributes:
        branch: The branch both sides call by the same name.
        remote_ref: The server's branch as this project knows it.
        local_oid: The branch's newest version here.
        server_oid: The branch's newest version on the server.
        local_commits: Versions this side has on the branch.
        server_commits: Versions the server has on the branch.
        local_subject: The summary of this side's newest version, offered as the
            summary of the new one.
        added: Files only this side has, which the new version adds.
        changed: Files both have with different content.
        removed: Files only the server has, which the new version removes.
        removed_groups: Where those files are, biggest first: the top folder
            with how many of them it holds, or the name of a file lying loose
            in the project's top folder. Thousands of single paths would hide
            that a whole folder is what goes.
    """

    branch: str
    remote_ref: str
    local_oid: str
    server_oid: str
    local_commits: int
    server_commits: int
    local_subject: str
    added: int
    changed: int
    removed: int
    removed_groups: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class ResolveResult:
    """
    Outcome of joining the project to the server's history.

    Attributes:
        ok: Whether everything that had to happen did.
        committed: Whether a new version was recorded, False when the files
            already were the server's.
        blocked_key: Translation key when a pre-check refused to start.
        step: The git step that failed, when one did.
    """

    ok: bool = False
    committed: bool = False
    blocked_key: str = ""
    step: GitResult | None = None


def _count(repo: Path | str, revision: str) -> int:
    """
    Counts the versions reachable from a revision.

    Args:
        repo: Working tree path.
        revision: Revision to count back from.

    Returns:
        int: The number, zero when it cannot be determined.
    """

    result = run(["rev-list", "--count", revision], cwd=repo, read_only=True)
    if result.failed:
        return 0
    try:
        return max(0, int(result.stdout.strip()))
    except ValueError:
        return 0


def _compare(
    repo: Path | str, remote_ref: str
) -> tuple[int, int, int, tuple[tuple[str, int], ...]]:
    """
    Compares the server's files with this side's.

    Args:
        repo: Working tree path.
        remote_ref: The server's branch.

    Returns:
        tuple[int, int, int, tuple[tuple[str, int], ...]]: Files to add, to
            change and to remove, and where the ones to remove are.
    """

    result = run(
        ["diff", "--name-status", "--no-renames", "-z", remote_ref, "HEAD"],
        cwd=repo,
        read_only=True,
    )
    if result.failed:
        return 0, 0, 0, ()
    parts = result.stdout.split("\x00")
    added = changed = removed = 0
    places: dict[str, int] = {}
    for index in range(0, len(parts) - 1, 2):
        code, path = parts[index].strip(), parts[index + 1]
        if code == "A":
            added += 1
        elif code == "D":
            removed += 1
            place = path.split("/", 1)[0] + ("/" if "/" in path else "")
            places[place] = places.get(place, 0) + 1
        elif code:
            changed += 1
    ranked = sorted(places.items(), key=lambda item: (-item[1], item[0]))
    return added, changed, removed, tuple(ranked)


def detect(repo: Path | str, state: RepositoryState | None = None) -> Unrelated | None:
    """
    Finds out whether the project and its server share no history.

    Looks only at what this computer already has: the server's branch as it was
    last fetched. A project that never fetched is found out the moment an upload
    is refused and the fetch that follows brings the branch in.

    Args:
        repo: Working tree path.
        state: A state read just now, read here when omitted.

    Returns:
        Unrelated | None: The facts, or None when the two do share a history or
            the question does not arise: no server, no branch, nothing saved yet,
            or a state that something else has to settle first.
    """

    state = state or read_state(repo)
    if not state.ok or state.detached or state.initial or not state.branch:
        return None
    if state.has_conflicts or state.operation_in_progress:
        return None
    if state.upstream and (state.ahead == 0 or state.behind == 0):
        # One side is ahead of the other, so they share a history, and the other
        # questions below are not worth the git calls they cost.
        return None
    if not is_valid_branch_name(state.branch) or not remote_mod.has_remote(repo, REMOTE):
        return None

    remote_ref = f"{REMOTE}/{state.branch}"
    server_oid = remote_mod.local_ref_oid(repo, remote_ref)
    local_oid = remote_mod.local_ref_oid(repo, "HEAD")
    if not server_oid or not local_oid or server_oid == local_oid:
        return None

    # "No common ancestor" is exit status 1 with nothing printed. Any other
    # failure is a problem of some other kind, not this one.
    base = run(["merge-base", "HEAD", remote_ref], cwd=repo, read_only=True)
    if base.returncode != 1 or base.stdout.strip():
        return None

    subject = run(["log", "-1", "--format=%s", "HEAD"], cwd=repo, read_only=True)
    added, changed, removed, groups = _compare(repo, remote_ref)
    return Unrelated(
        branch=state.branch,
        remote_ref=remote_ref,
        local_oid=local_oid,
        server_oid=server_oid,
        local_commits=_count(repo, "HEAD"),
        server_commits=_count(repo, remote_ref),
        local_subject=subject.stdout.strip() if subject.ok else "",
        added=added,
        changed=changed,
        removed=removed,
        removed_groups=groups,
    )


def _blocked(state: RepositoryState) -> str:
    """
    Names the pre-check that stops the fix from starting.

    Args:
        state: Freshly read repository state.

    Returns:
        str: Translation key, empty when nothing is in the way.
    """

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
    summary: str,
    credentials: dict[str, str] | None = None,
) -> ResolveResult:
    """
    Puts this side's files on top of the server's history and uploads them.

    Args:
        repo: Working tree path.
        summary: What to call the new version.
        credentials: Login for the server, as built by
            ``services.git_credentials``.

    Returns:
        ResolveResult: What happened. Never raises.
    """

    state = read_state(repo)
    blocked = _blocked(state)
    if blocked:
        return ResolveResult(blocked_key=blocked)
    draft = CommitDraft(summary)
    if not draft.is_valid:
        return ResolveResult(blocked_key=BLOCKED_SUMMARY)

    # Bring the server's branch up to date first, so the new version sits on what
    # the server has now and not on what it had when this was last looked at.
    fetched = remote_mod.fetch(repo, REMOTE, credentials=credentials)
    if fetched.failed:
        return ResolveResult(step=fetched)
    found = detect(repo)
    if found is None:
        return ResolveResult(blocked_key=BLOCKED_NOT_UNRELATED)

    moved = run(["reset", "--soft", found.remote_ref], cwd=repo)
    if moved.failed:
        return ResolveResult(step=moved)

    # Soft, so the index still holds this side's files: whatever differs from the
    # server's is exactly what is staged now.
    committed = has_staged_changes(repo)
    if committed:
        made = commit(repo, draft)
        if made.failed:
            # Back to where it was, so a failed attempt leaves the project as it
            # found it.
            run(["reset", "--soft", found.local_oid], cwd=repo)
            return ResolveResult(step=made)

    tracking = run(["branch", f"--set-upstream-to={found.remote_ref}", found.branch], cwd=repo)
    if tracking.failed:
        return ResolveResult(committed=committed, step=tracking)
    if not committed:
        return ResolveResult(ok=True)

    pushed = remote_mod.push(repo, credentials=credentials)
    if pushed.failed:
        return ResolveResult(committed=True, step=pushed)
    return ResolveResult(ok=True, committed=True)
