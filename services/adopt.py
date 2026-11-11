"""
Taking over the server's version when it is the server that is right.

Two situations end up here, and they look alike from the outside:

* The project and the server share no history at all (``services.unrelated``).
* The project and the server both moved on from a common point (the
  "diverged" case), because the server's history was **rewritten**: a force
  push replaced versions that this computer still has, with new ones that carry
  the same summaries but other identities.

The two fixes Branchly already offers for them both keep *this* side's files and
put them on top of the server's history. That is the wrong way round when the
server was rewritten on purpose, for example to remove a name or a secret from
its history: it would upload the old files, and with them the very thing that
was taken out.

What a person does by hand in that case is to throw the local branch away and
use the server's:

1. Keep the local versions reachable under a branch of their own, so that
   nothing is lost when the branch moves (``branch backup/...``).
2. Move the branch and the files to the server's newest version
   (``reset --hard``).
3. Track the server's branch again.

Nothing is uploaded and nothing on the server changes. The project must have no
unsaved work, because ``reset --hard`` would throw it away.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from gitops import branch as branch_mod
from gitops import remote as remote_mod
from gitops.refname import is_valid_branch_name
from gitops.runner import GitResult, run
from gitops.status import RepositoryState, read_state
from services.unrelated import (
    BLOCKED_CONFLICTS,
    BLOCKED_DETACHED,
    BLOCKED_DIRTY,
    BLOCKED_IN_PROGRESS,
    REMOTE,
)

BLOCKED_NOT_ADOPTABLE = "adopt.blocked_gone"
BLOCKED_BACKUP = "adopt.blocked_backup"

#: How many summaries are compared, so that a very long history stays quick.
SUBJECTS_COMPARED = 2000

#: Share of this side's versions that must have a twin on the server before the
#: dialog calls it a rewritten history.
REWRITTEN_SHARE = 0.6

#: Fewer twins than this prove nothing: two projects that were both started with a
#: version called "Initial commit" have one.
MIN_MATCHING = 2


@dataclass(frozen=True, slots=True)
class Adoptable:
    """
    The facts about a project whose server holds a version it should take over.

    Attributes:
        branch: The branch both sides call by the same name.
        remote_ref: The server's branch as this project knows it.
        local_oid: The branch's newest version here.
        server_oid: The branch's newest version on the server.
        local_only: Versions only this side has.
        server_only: Versions only the server has.
        matching: How many of this side's versions have a version with the same
            summary on the server, which is what a rewritten history looks like.
        shared: Whether the two still have a starting point in common.
        will_add: Files the server has that this side does not.
        will_change: Files both have with different content.
        will_remove: Files only this side has, which are gone afterwards.
    """

    branch: str
    remote_ref: str
    local_oid: str
    server_oid: str
    local_only: int
    server_only: int
    matching: int
    shared: bool
    will_add: int
    will_change: int
    will_remove: int

    @property
    def rewritten(self) -> bool:
        """
        Whether the server's history looks like a rewritten copy of this one.

        Returns:
            bool: True when most of this side's versions have a twin there.
        """

        needed = max(MIN_MATCHING, -(-int(self.local_only * REWRITTEN_SHARE * 100) // 100))
        return self.local_only > 0 and self.matching >= needed


@dataclass(frozen=True, slots=True)
class AdoptResult:
    """
    Outcome of taking over the server's version.

    Attributes:
        ok: Whether everything that had to happen did.
        backup: Name of the branch that keeps this side's old versions, empty
            when none was needed or the fix did not start.
        blocked_key: Translation key when a pre-check refused to start.
        step: The git step that failed, when one did.
    """

    ok: bool = False
    backup: str = ""
    blocked_key: str = ""
    step: GitResult | None = None


def _subjects(repo: Path | str, revisions: str) -> list[str]:
    """
    Lists the summaries of a range of versions.

    Args:
        repo: Working tree path.
        revisions: Revision range, as git takes it.

    Returns:
        list[str]: Summaries, newest first, at most ``SUBJECTS_COMPARED``.
    """

    result = run(
        ["log", f"--max-count={SUBJECTS_COMPARED}", "--format=%s", revisions],
        cwd=repo,
        read_only=True,
    )
    if result.failed:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _files(repo: Path | str, remote_ref: str) -> tuple[int, int, int]:
    """
    Compares this side's files with the server's.

    Args:
        repo: Working tree path.
        remote_ref: The server's branch.

    Returns:
        tuple[int, int, int]: Files the server adds, changes and removes,
            seen from this side.
    """

    result = run(
        ["diff", "--name-status", "--no-renames", "-z", remote_ref, "HEAD"],
        cwd=repo,
        read_only=True,
    )
    if result.failed:
        return 0, 0, 0
    parts = result.stdout.split("\x00")
    add = change = remove = 0
    for index in range(0, len(parts) - 1, 2):
        code = parts[index].strip()
        if code == "A":
            remove += 1  # only here: gone once the server's version is taken
        elif code == "D":
            add += 1  # only on the server: comes in
        elif code:
            change += 1
    return add, change, remove


def detect(repo: Path | str, state: RepositoryState | None = None) -> Adoptable | None:
    """
    Finds out whether the server has a version this project could take over.

    Looks only at what this computer already has: the server's branch as it was
    last fetched. It applies when both sides have versions the other lacks, which
    is the case for histories that share nothing and for histories that were
    rewritten on one side. A project that is only behind is pulled, one that is
    only ahead is pushed, and neither is this.

    Args:
        repo: Working tree path.
        state: A state read just now, read here when omitted.

    Returns:
        Adoptable | None: The facts, or None when the question does not arise.
    """

    state = state or read_state(repo)
    if not state.ok or state.detached or state.initial or not state.branch:
        return None
    if state.has_conflicts or state.operation_in_progress:
        return None
    if not is_valid_branch_name(state.branch) or not remote_mod.has_remote(repo, REMOTE):
        return None

    remote_ref = f"{REMOTE}/{state.branch}"
    server_oid = remote_mod.local_ref_oid(repo, remote_ref)
    local_oid = remote_mod.local_ref_oid(repo, "HEAD")
    if not server_oid or not local_oid or server_oid == local_oid:
        return None

    local_only = remote_mod.count_between(repo, remote_ref, "HEAD")
    server_only = remote_mod.count_between(repo, "HEAD", remote_ref)
    if local_only == 0 or server_only == 0:
        return None

    base = run(["merge-base", "HEAD", remote_ref], cwd=repo, read_only=True)
    shared = base.ok and bool(base.stdout.strip())

    ours = _subjects(repo, f"{remote_ref}..HEAD")
    theirs = set(_subjects(repo, f"HEAD..{remote_ref}"))
    matching = sum(1 for subject in ours if subject in theirs)
    add, change, remove = _files(repo, remote_ref)
    return Adoptable(
        branch=state.branch,
        remote_ref=remote_ref,
        local_oid=local_oid,
        server_oid=server_oid,
        local_only=local_only,
        server_only=server_only,
        matching=matching,
        shared=shared,
        will_add=add,
        will_change=change,
        will_remove=remove,
    )


def backup_name(branch: str, stamp: str | None = None) -> str:
    """
    Names the branch that keeps this side's old versions.

    Args:
        branch: The branch that is about to move.
        stamp: Time stamp to use, now when omitted.

    Returns:
        str: A valid branch name.
    """

    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    return f"backup/{branch}-before-server-{stamp}"


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


def adopt(repo: Path | str, credentials: dict[str, str] | None = None) -> AdoptResult:
    """
    Moves the project onto the server's newest version and keeps the old one.

    Args:
        repo: Working tree path.
        credentials: Login for the server, as built by
            ``services.git_credentials``.

    Returns:
        AdoptResult: What happened. Never raises.
    """

    state = read_state(repo)
    blocked = _blocked(state)
    if blocked:
        return AdoptResult(blocked_key=blocked)

    # Bring the server's branch up to date first, so it is what the server has
    # now and not what it had when this was last looked at.
    fetched = remote_mod.fetch(repo, REMOTE, credentials=credentials)
    if fetched.failed:
        return AdoptResult(step=fetched)
    found = detect(repo)
    if found is None:
        return AdoptResult(blocked_key=BLOCKED_NOT_ADOPTABLE)

    name = backup_name(found.branch)
    if not is_valid_branch_name(name):
        return AdoptResult(blocked_key=BLOCKED_BACKUP)
    kept = branch_mod.create_branch(repo, name, found.local_oid, checkout=False)
    if kept.failed:
        return AdoptResult(step=kept)

    moved = branch_mod.reset(repo, found.remote_ref, branch_mod.RESET_HARD)
    if moved.failed:
        # Nothing moved, so the backup is not needed and the project is as it was.
        branch_mod.delete_branch(repo, name, force=True)
        return AdoptResult(step=moved)

    tracking = run(["branch", f"--set-upstream-to={found.remote_ref}", found.branch], cwd=repo)
    branch_mod.invalidate_branch_cache(repo)
    if tracking.failed:
        return AdoptResult(backup=name, step=tracking)
    return AdoptResult(ok=True, backup=name)
