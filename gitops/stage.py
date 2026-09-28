"""
Staging, unstaging and discarding changes.

Whole files go through ``git add`` and ``git restore``. Single hunks go through
``git apply --cached`` with a patch rebuilt from one hunk, which is how a
partial stage is done without rewriting the file.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from gitops.diff import LINE_ADDED, LINE_NO_NEWLINE, LINE_REMOVED, DiffHunk, FileDiff
from gitops.refname import is_safe_argument
from gitops.runner import GitResult, run


def checked_paths(paths: list[str] | tuple[str, ...]) -> list[str] | None:
    """
    Validates a list of repository-relative paths.

    Args:
        paths: Paths to check.

    Returns:
        list[str] | None: The paths, or None when any one of them is unsafe. All
            or nothing on purpose: a partially applied batch is worse than a
            refused one, because the user cannot tell what happened.
    """

    cleaned: list[str] = []
    for path in paths:
        if not is_safe_argument(path):
            return None
        cleaned.append(path)
    return cleaned or None


def stage_files(repo: Path | str, paths: list[str]) -> GitResult:
    """
    Stages whole files, including deletions and untracked files.

    Args:
        repo: Working tree path.
        paths: Paths relative to the working tree root.

    Returns:
        GitResult: Outcome of ``git add``.
    """

    checked = checked_paths(paths)
    if checked is None:
        return GitResult(returncode=-1, stdout="", stderr="unsafe path", args=("add",))
    return run(["add", "--all", "--", *checked], cwd=repo)


def unstage_files(repo: Path | str, paths: list[str]) -> GitResult:
    """
    Removes files from the staging area, keeping the working tree untouched.

    Args:
        repo: Working tree path.
        paths: Paths relative to the working tree root.

    Returns:
        GitResult: Outcome of ``git restore --staged``, or of ``git rm --cached``
            in a repository that has no commits yet, where there is no HEAD to
            restore from.
    """

    checked = checked_paths(paths)
    if checked is None:
        return GitResult(returncode=-1, stdout="", stderr="unsafe path", args=("restore",))
    head = run(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=repo, read_only=True)
    if head.failed or not head.stdout.strip():
        return run(["rm", "--cached", "-r", "--", *checked], cwd=repo)
    return run(["restore", "--staged", "--", *checked], cwd=repo)


def discard_changes(repo: Path | str, paths: list[str]) -> GitResult:
    """
    Throws away unstaged changes to tracked files.

    Destructive and unrecoverable — the caller must have confirmed with the user
    first. Untracked files are not touched here; deleting a file git never knew
    about is handled separately so the two cases stay distinguishable.

    Args:
        repo: Working tree path.
        paths: Paths relative to the working tree root.

    Returns:
        GitResult: Outcome of ``git restore``.
    """

    checked = checked_paths(paths)
    if checked is None:
        return GitResult(returncode=-1, stdout="", stderr="unsafe path", args=("restore",))
    return run(["restore", "--worktree", "--", *checked], cwd=repo)


def delete_untracked(repo: Path | str, paths: list[str]) -> tuple[bool, list[str]]:
    """
    Deletes untracked files from disk.

    Destructive and unrecoverable — git holds no copy of an untracked file.

    Args:
        repo: Working tree path.
        paths: Paths relative to the working tree root.

    Returns:
        tuple[bool, list[str]]: Whether everything was removed, and the paths
            that could not be.
    """

    root = Path(repo)
    failed: list[str] = []
    for path in paths:
        if not is_safe_argument(path):
            failed.append(path)
            continue
        target = root / path
        try:
            resolved = target.resolve()
            # Refuse anything that escapes the working tree, however it got here.
            resolved.relative_to(root.resolve())
        except (OSError, ValueError):
            failed.append(path)
            continue
        try:
            if resolved.is_dir():
                shutil.rmtree(resolved)
            else:
                resolved.unlink(missing_ok=True)
        except OSError:
            failed.append(path)
    return not failed, failed


def build_patch(diff: FileDiff, hunks: list[DiffHunk]) -> str:
    """
    Builds a patch containing the given blocks of one file.

    Several blocks go into **one** patch rather than one patch each. Every
    block's line numbers are counted against the same original, so applying them
    one after another would make each patch land against a file the previous one
    had already moved. Git resolves that by searching for the context and usually
    gets it right, and "usually" is not a good enough property for the thing that
    decides what a commit contains.

    Unstaging uses the same forward patch with ``git apply --reverse``: reversing
    it by hand would mean recomputing every line number, and getting that subtly
    wrong corrupts the index.

    Args:
        diff: The parsed diff the blocks belong to, for the file headers.
        hunks: The blocks to include, in the order they appear in the file.

    Returns:
        str: Patch text ready for ``git apply``, empty when there is nothing to
            apply.
    """

    usable = [hunk for hunk in hunks if hunk.lines]
    if not usable:
        return ""
    old_path = diff.old_path or diff.path
    new_path = diff.path or diff.old_path
    if not old_path or not new_path:
        return ""

    parts = [
        f"diff --git a/{old_path} b/{new_path}",
        f"--- a/{old_path}",
        f"+++ b/{new_path}",
    ]
    for hunk in usable:
        parts.append(hunk.header)
        for line in hunk.lines:
            if line.kind == LINE_ADDED:
                parts.append(f"+{line.text}")
            elif line.kind == LINE_REMOVED:
                parts.append(f"-{line.text}")
            elif line.kind == LINE_NO_NEWLINE:
                parts.append("\\ No newline at end of file")
            else:
                parts.append(f" {line.text}")
    return "\n".join(parts) + "\n"


def build_hunk_patch(diff: FileDiff, hunk: DiffHunk) -> str:
    """
    Builds a patch containing exactly one block.

    Args:
        diff: The parsed diff the block belongs to, for the file headers.
        hunk: The block to include.

    Returns:
        str: Patch text ready for ``git apply``, empty when it holds no lines.
    """

    return build_patch(diff, [hunk])


def apply_hunk(repo: Path | str, patch: str, stage: bool = True, reverse: bool = False) -> GitResult:
    """
    Applies a single-hunk patch to the staging area.

    Args:
        repo: Working tree path.
        patch: Patch text from ``build_hunk_patch``.
        stage: Whether to apply to the index (True) or the working tree (False).
        reverse: Whether to apply the patch backwards, which unstages it.

    Returns:
        GitResult: Outcome of ``git apply``.
    """

    if not patch.strip():
        return GitResult(returncode=-1, stdout="", stderr="empty patch", args=("apply",))
    args = ["apply", "--unidiff-zero", "--whitespace=nowarn"]
    if stage:
        args.append("--cached")
    if reverse:
        args.append("--reverse")
    args.append("-")
    return run(args, cwd=repo, input_text=patch)


def stage_hunk(repo: Path | str, diff: FileDiff, hunk: DiffHunk) -> GitResult:
    """
    Stages one hunk of a file.

    Args:
        repo: Working tree path.
        diff: Parsed diff the hunk belongs to.
        hunk: Hunk to stage.

    Returns:
        GitResult: Outcome.
    """

    return apply_hunk(repo, build_hunk_patch(diff, hunk), stage=True, reverse=False)


def unstage_hunk(repo: Path | str, diff: FileDiff, hunk: DiffHunk) -> GitResult:
    """
    Removes one hunk from the staging area.

    Args:
        repo: Working tree path.
        diff: Parsed diff the hunk belongs to, produced with the staged target.
        hunk: Hunk to unstage.

    Returns:
        GitResult: Outcome.
    """

    return apply_hunk(repo, build_hunk_patch(diff, hunk), stage=True, reverse=True)


def stage_selected_hunks(repo: Path | str, diff: FileDiff) -> GitResult:
    """
    Stages the blocks of a file the user left ticked.

    The index has to hold the last saved version of the file before this runs,
    because the patch's line numbers are counted against exactly that. The commit
    path clears the index first for precisely this reason.

    Args:
        repo: Working tree path.
        diff: Working-tree diff of the file, with ``selected`` set per block.

    Returns:
        GitResult: Outcome. A diff with nothing ticked stages nothing and reports
            success, because "none of this file" is a valid thing to mean.
    """

    chosen = [hunk for hunk in diff.hunks if hunk.selected]
    if not chosen:
        return GitResult(returncode=0, stdout="", stderr="", args=("apply",))
    return apply_hunk(repo, build_patch(diff, chosen), stage=True, reverse=False)


def stage_all(repo: Path | str) -> GitResult:
    """
    Stages every change in the working tree.

    Args:
        repo: Working tree path.

    Returns:
        GitResult: Outcome of ``git add --all``.
    """

    return run(["add", "--all"], cwd=repo)


def unstage_all(repo: Path | str) -> GitResult:
    """
    Empties the staging area, keeping the working tree untouched.

    Args:
        repo: Working tree path.

    Returns:
        GitResult: Outcome.
    """

    head = run(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=repo, read_only=True)
    if head.failed or not head.stdout.strip():
        return run(["rm", "--cached", "-r", "."], cwd=repo)
    return run(["restore", "--staged", "."], cwd=repo)
