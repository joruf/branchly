"""
Branches, merges and the operations the graph view offers.

Every name and revision that reaches git is validated first. The destructive
operations are here too, but nothing in this module asks for confirmation — that
is the UI's job, and it must happen before these functions are called.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gitops.refname import is_valid_branch_name, is_valid_revision
from gitops.runner import GitResult, run, run_lines

RESET_SOFT = "soft"
RESET_MIXED = "mixed"
RESET_HARD = "hard"
VALID_RESET_MODES: tuple[str, ...] = (RESET_SOFT, RESET_MIXED, RESET_HARD)


def _refused(command: str, reason: str = "unsafe argument") -> GitResult:
    """
    Builds a failed result for input that never reached git.

    Args:
        command: Command that was refused, for the result's args.
        reason: Text placed in stderr.

    Returns:
        GitResult: Failed result.
    """

    return GitResult(returncode=-1, stdout="", stderr=reason, args=(command,))


@dataclass(frozen=True, slots=True)
class BranchInfo:
    """
    One branch as listed for the switcher.

    Attributes:
        name: Short name, e.g. ``main`` or ``origin/main``.
        is_remote: Whether this is a remote-tracking branch.
        is_current: Whether it is checked out.
        upstream: Tracking branch, empty when there is none.
        tip: Commit id the branch points at.
        subject: Subject line of the tip commit.
    """

    name: str
    is_remote: bool = False
    is_current: bool = False
    upstream: str = ""
    tip: str = ""
    subject: str = ""


def list_branches(repo: Path | str, include_remote: bool = True) -> list[BranchInfo]:
    """
    Lists branches, local first.

    Args:
        repo: Working tree path.
        include_remote: Whether remote-tracking branches are included.

    Returns:
        list[BranchInfo]: Branches, empty when none could be read.
    """

    separator = "\x1f"
    fmt = separator.join(
        ["%(refname:short)", "%(HEAD)", "%(upstream:short)", "%(objectname)", "%(contents:subject)"]
    )
    args = ["for-each-ref", f"--format={fmt}", "refs/heads"]
    if include_remote:
        args.append("refs/remotes")
    branches: list[BranchInfo] = []
    for line in run_lines(args, cwd=repo):
        parts = line.split(separator)
        if len(parts) < 5:
            continue
        name = parts[0]
        # git lists the symbolic origin/HEAD too; it is not a branch to check out.
        if name.endswith("/HEAD"):
            continue
        branches.append(
            BranchInfo(
                name=name,
                is_remote=name.count("/") > 0 and not _is_local(repo, name),
                is_current=parts[1].strip() == "*",
                upstream=parts[2],
                tip=parts[3],
                subject=parts[4],
            )
        )
    branches.sort(key=lambda item: (item.is_remote, item.name.lower()))
    return branches


_local_cache: dict[str, set[str]] = {}


def _is_local(repo: Path | str, name: str) -> bool:
    """
    Reports whether a branch name refers to a local branch.

    A local branch may contain slashes (``feature/login``), so the name alone
    does not say which side it belongs to.

    Args:
        repo: Working tree path.
        name: Short branch name.

    Returns:
        bool: True when a local branch of that name exists.
    """

    key = str(repo)
    if key not in _local_cache:
        _local_cache[key] = set(
            run_lines(["for-each-ref", "--format=%(refname:short)", "refs/heads"], cwd=repo)
        )
    return name in _local_cache[key]


def invalidate_branch_cache(repo: Path | str | None = None) -> None:
    """
    Drops the cached local-branch names.

    Args:
        repo: Repository to forget, or None to clear everything.

    Returns:
        None
    """

    if repo is None:
        _local_cache.clear()
    else:
        _local_cache.pop(str(repo), None)


def branch_exists(repo: Path | str, name: str) -> bool:
    """
    Reports whether a local branch already exists.

    Args:
        repo: Working tree path.
        name: Branch name.

    Returns:
        bool: True when the branch exists.
    """

    if not is_valid_branch_name(name):
        return False
    result = run(["show-ref", "--verify", "--quiet", f"refs/heads/{name}"], cwd=repo, read_only=True)
    return result.ok


def create_branch(repo: Path | str, name: str, start_point: str = "", checkout: bool = True) -> GitResult:
    """
    Creates a branch, optionally switching to it.

    Args:
        repo: Working tree path.
        name: New branch name.
        start_point: Revision to branch from. Defaults to the current HEAD.
        checkout: Whether to switch to the new branch.

    Returns:
        GitResult: Outcome.
    """

    if not is_valid_branch_name(name):
        return _refused("branch", "invalid branch name")
    if start_point and not is_valid_revision(start_point):
        return _refused("branch", "invalid start point")
    invalidate_branch_cache(repo)
    args = ["switch", "--create", name] if checkout else ["branch", name]
    if start_point:
        args.append(start_point)
    return run(args, cwd=repo)


def checkout_branch(repo: Path | str, name: str) -> GitResult:
    """
    Switches to an existing branch.

    A remote-tracking name like ``origin/feature`` is turned into a local branch
    that tracks it, which is what the user means by "check out this branch" and
    avoids landing them in a detached HEAD by accident.

    Args:
        repo: Working tree path.
        name: Branch name, local or remote-tracking.

    Returns:
        GitResult: Outcome.
    """

    if not is_valid_branch_name(name):
        return _refused("switch", "invalid branch name")
    invalidate_branch_cache(repo)
    if _is_local(repo, name):
        return run(["switch", name], cwd=repo)
    local_name = name.split("/", 1)[1] if "/" in name else name
    if not is_valid_branch_name(local_name):
        return _refused("switch", "invalid branch name")
    if branch_exists(repo, local_name):
        return run(["switch", local_name], cwd=repo)
    return run(["switch", "--create", local_name, "--track", name], cwd=repo)


def checkout_revision(repo: Path | str, revision: str) -> GitResult:
    """
    Checks out a commit directly, leaving HEAD detached.

    Args:
        repo: Working tree path.
        revision: Revision to check out.

    Returns:
        GitResult: Outcome.
    """

    if not is_valid_revision(revision):
        return _refused("switch", "invalid revision")
    return run(["switch", "--detach", revision], cwd=repo)


def delete_branch(repo: Path | str, name: str, force: bool = False) -> GitResult:
    """
    Deletes a local branch.

    Args:
        repo: Working tree path.
        name: Branch name.
        force: Whether to delete even when commits would be lost.

    Returns:
        GitResult: Outcome.
    """

    if not is_valid_branch_name(name):
        return _refused("branch", "invalid branch name")
    invalidate_branch_cache(repo)
    args = ["branch", "--delete"]
    if force:
        args.append("--force")
    args.append(name)
    return run(args, cwd=repo)


def rename_branch(repo: Path | str, old_name: str, new_name: str) -> GitResult:
    """
    Renames a local branch.

    Args:
        repo: Working tree path.
        old_name: Current name.
        new_name: New name.

    Returns:
        GitResult: Outcome.
    """

    if not is_valid_branch_name(old_name) or not is_valid_branch_name(new_name):
        return _refused("branch", "invalid branch name")
    invalidate_branch_cache(repo)
    return run(["branch", "--move", old_name, new_name], cwd=repo)


def merge(repo: Path | str, revision: str, no_commit: bool = False) -> GitResult:
    """
    Merges a revision into the current branch.

    Args:
        repo: Working tree path.
        revision: Branch or commit to merge in.
        no_commit: Whether to stop before creating the merge commit.

    Returns:
        GitResult: Outcome. A merge that stops on conflicts is a failure here; the
            caller inspects the state and opens the conflict assistant.
    """

    if not is_valid_revision(revision):
        return _refused("merge", "invalid revision")
    args = ["merge", "--no-edit"]
    if no_commit:
        args.append("--no-commit")
    args.append(revision)
    return run(args, cwd=repo)


def merge_abort(repo: Path | str) -> GitResult:
    """
    Undoes an in-progress merge.

    Args:
        repo: Working tree path.

    Returns:
        GitResult: Outcome.
    """

    return run(["merge", "--abort"], cwd=repo)


def cherry_pick(repo: Path | str, revision: str) -> GitResult:
    """
    Copies one commit onto the current branch.

    Args:
        repo: Working tree path.
        revision: Commit to copy.

    Returns:
        GitResult: Outcome.
    """

    if not is_valid_revision(revision):
        return _refused("cherry-pick", "invalid revision")
    return run(["cherry-pick", revision], cwd=repo)


def cherry_pick_abort(repo: Path | str) -> GitResult:
    """
    Undoes an in-progress cherry-pick.

    Args:
        repo: Working tree path.

    Returns:
        GitResult: Outcome.
    """

    return run(["cherry-pick", "--abort"], cwd=repo)


def revert(repo: Path | str, revision: str) -> GitResult:
    """
    Adds a commit that undoes an earlier one.

    Args:
        repo: Working tree path.
        revision: Commit to undo.

    Returns:
        GitResult: Outcome.
    """

    if not is_valid_revision(revision):
        return _refused("revert", "invalid revision")
    return run(["revert", "--no-edit", revision], cwd=repo)


def revert_abort(repo: Path | str) -> GitResult:
    """
    Undoes an in-progress revert.

    Args:
        repo: Working tree path.

    Returns:
        GitResult: Outcome.
    """

    return run(["revert", "--abort"], cwd=repo)


def reset(repo: Path | str, revision: str, mode: str = RESET_MIXED) -> GitResult:
    """
    Moves the current branch to another commit.

    ``hard`` destroys uncommitted work irrecoverably. The UI must have said so in
    plain words and got a confirmation before calling this.

    Args:
        repo: Working tree path.
        revision: Commit to move to.
        mode: One of ``soft``, ``mixed`` or ``hard``.

    Returns:
        GitResult: Outcome.
    """

    if not is_valid_revision(revision):
        return _refused("reset", "invalid revision")
    if mode not in VALID_RESET_MODES:
        return _refused("reset", "invalid reset mode")
    return run(["reset", f"--{mode}", revision], cwd=repo)


def create_tag(repo: Path | str, name: str, revision: str = "", message: str = "") -> GitResult:
    """
    Gives a commit a name.

    Args:
        repo: Working tree path.
        name: Tag name.
        revision: Commit to tag. Defaults to HEAD.
        message: Annotation text. An empty message creates a lightweight tag.

    Returns:
        GitResult: Outcome.
    """

    if not is_valid_branch_name(name):
        return _refused("tag", "invalid tag name")
    if revision and not is_valid_revision(revision):
        return _refused("tag", "invalid revision")
    args = ["tag"]
    if message:
        args.extend(["--annotate", "--file", "-", name])
    else:
        args.append(name)
    if revision:
        args.append(revision)
    return run(args, cwd=repo, input_text=message if message else None)


def delete_tag(repo: Path | str, name: str) -> GitResult:
    """
    Removes a tag.

    Args:
        repo: Working tree path.
        name: Tag name.

    Returns:
        GitResult: Outcome.
    """

    if not is_valid_branch_name(name):
        return _refused("tag", "invalid tag name")
    return run(["tag", "--delete", name], cwd=repo)


def list_tags(repo: Path | str) -> list[str]:
    """
    Lists tag names, newest first.

    Args:
        repo: Working tree path.

    Returns:
        list[str]: Tag names.
    """

    return run_lines(["tag", "--sort=-creatordate"], cwd=repo)


def stash_push(repo: Path | str, message: str = "", include_untracked: bool = True) -> GitResult:
    """
    Puts the current changes aside.

    Args:
        repo: Working tree path.
        message: Optional label.
        include_untracked: Whether untracked files are stashed too.

    Returns:
        GitResult: Outcome.
    """

    args = ["stash", "push"]
    if include_untracked:
        args.append("--include-untracked")
    if message:
        args.extend(["--message", message])
    return run(args, cwd=repo)


def stash_pop(repo: Path | str) -> GitResult:
    """
    Brings the most recently stashed changes back.

    Args:
        repo: Working tree path.

    Returns:
        GitResult: Outcome.
    """

    return run(["stash", "pop"], cwd=repo)


def stash_list(repo: Path | str) -> list[str]:
    """
    Lists stash entries.

    Args:
        repo: Working tree path.

    Returns:
        list[str]: One description per entry.
    """

    return run_lines(["stash", "list", "--format=%gd: %s"], cwd=repo)
