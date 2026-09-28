"""
Throwing uncommitted work away.

This is the one thing Branchly does that git cannot undo afterwards. A commit
can be found again through the reflog, a deleted branch too, a discarded change
cannot: there is no copy of it anywhere. So the user has to be told exactly what
is about to disappear, and "exactly" means per file and in the right words,
because two of the cases are not the same thing at all:

* A **tracked** file goes back to the last saved version. The file stays, its
  content is replaced.
* An **untracked** file is deleted. Git never held a copy, so there is nothing
  to go back to. That is the irreversible one and it has to be named as such.

A file that was newly added to the next commit sits between the two: dropping it
from the commit leaves an untracked file, which then has to go as well, or
"revert everything" would leave the file behind and nobody would understand why.

This module works out which case each file is and carries it out. Putting it in
front of the user is the dialog's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from gitops import stage as stage_mod
from gitops.status import CHANGE_UNTRACKED, FileChange

# What reverting one file actually does to it.
ACTION_RESTORE = "restore"
ACTION_DELETE = "delete"


@dataclass(frozen=True, slots=True)
class RevertItem:
    """
    One file and what reverting it would do.

    Attributes:
        path: Path relative to the working tree root.
        action: ``ACTION_RESTORE`` or ``ACTION_DELETE``.
        kind: The change's kind, for the wording and the colour.
        staged: Whether part of the change was already prepared for a commit.
    """

    path: str
    action: str
    kind: str
    staged: bool = False


@dataclass(frozen=True, slots=True)
class RevertPlan:
    """
    Everything a revert would do, ready to be shown.

    Attributes:
        items: One entry per file, in the order they were listed.
    """

    items: tuple[RevertItem, ...] = ()

    @property
    def restored(self) -> tuple[RevertItem, ...]:
        """
        Returns the files that go back to their last saved version.

        Returns:
            tuple[RevertItem, ...]: The recoverable half.
        """

        return tuple(item for item in self.items if item.action == ACTION_RESTORE)

    @property
    def deleted(self) -> tuple[RevertItem, ...]:
        """
        Returns the files that are deleted outright.

        Returns:
            tuple[RevertItem, ...]: The irreversible half.
        """

        return tuple(item for item in self.items if item.action == ACTION_DELETE)

    @property
    def is_empty(self) -> bool:
        """
        Reports whether there is nothing to do.

        Returns:
            bool: True when no file would be touched.
        """

        return not self.items


@dataclass(frozen=True, slots=True)
class RevertOutcome:
    """
    What actually happened.

    Attributes:
        ok: Whether every file was dealt with.
        failed: Paths that could not be reverted.
        error: Git's own words when the restore failed.
    """

    ok: bool
    failed: tuple[str, ...] = ()
    error: str = field(default="")


def plan(changes: list[FileChange], paths: list[str] | None = None) -> RevertPlan:
    """
    Works out what reverting these files would do to each of them.

    Args:
        changes: The repository's current changes, as git reported them.
        paths: Restrict the plan to these paths. None means everything.

    Returns:
        RevertPlan: One entry per file that would be touched.
    """

    wanted = set(paths) if paths is not None else None
    items: list[RevertItem] = []
    for change in changes:
        if wanted is not None and change.path not in wanted:
            continue
        # An untracked file has no older version to go back to, and neither has
        # one that is only being added in the next commit: dropping it from the
        # commit leaves exactly an untracked file behind.
        removes = change.untracked or (change.index_code == "A" and not change.conflicted)
        items.append(
            RevertItem(
                path=change.path,
                action=ACTION_DELETE if removes else ACTION_RESTORE,
                kind=CHANGE_UNTRACKED if change.untracked else change.kind,
                staged=change.staged,
            )
        )
    return RevertPlan(tuple(items))


def apply(repo: Path | str, plan_to_run: RevertPlan) -> RevertOutcome:
    """
    Carries out a plan.

    Args:
        repo: Working tree path.
        plan_to_run: What to do, from ``plan``.

    Returns:
        RevertOutcome: What happened.
    """

    if plan_to_run.is_empty:
        return RevertOutcome(ok=True)

    restore = [item.path for item in plan_to_run.restored]
    remove = [item.path for item in plan_to_run.deleted]

    if restore:
        result = stage_mod.restore_committed(repo, restore)
        if result.failed:
            return RevertOutcome(ok=False, failed=tuple(restore), error=result.message.strip())

    failed: list[str] = []
    if remove:
        # Anything prepared for the commit has to leave the index first, or the
        # file comes straight back the next time git looks.
        staged_adds = [item.path for item in plan_to_run.deleted if item.staged]
        if staged_adds:
            dropped = stage_mod.unstage_files(repo, staged_adds)
            if dropped.failed:
                return RevertOutcome(
                    ok=False, failed=tuple(remove), error=dropped.message.strip()
                )
        done, could_not = stage_mod.delete_untracked(repo, remove)
        if not done:
            failed.extend(could_not)

    return RevertOutcome(ok=not failed, failed=tuple(failed))
