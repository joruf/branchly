"""
Saving the ticked files as one version, and uploading it if asked.

This used to be a method of the main window, written out among its widgets. It is
here because it is slow and has to run on a thread, and a thread must not reach
into the interface: everything it needs, the ticked files and the blocks taken out
of them, comes in as arguments.

The steps are the same as ever. The index is emptied so that it holds the last
saved version, whole files are staged as they are, files from which single blocks
were taken out are staged block by block, and what is staged is committed. With an
upload asked for, the version is then sent, in the same run, so that the hourglass
shows one action from the click to the end instead of two with a gap between them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from gitops import diff as diff_mod
from gitops import remote as remote_mod
from gitops import stage as stage_mod
from gitops.commit import CommitDraft
from gitops.commit import commit as do_commit
from gitops.runner import GitResult


@dataclass(frozen=True, slots=True)
class SaveOutcome:
    """
    How saving, and uploading when asked, ended.

    Attributes:
        saved: Whether the version was made.
        failure: The git step that failed while saving, None when it worked.
        push: The upload's result, None when none was asked for or the save
            failed before it.
        fetched: Whether the server's branch was fetched after a refused upload,
            which is how a project whose history shares nothing with the server is
            told from one that is merely behind.
    """

    saved: bool = False
    failure: GitResult | None = None
    push: GitResult | None = None
    fetched: bool = False


def partial_paths(
    repo: Path | str, hunk_selection: Mapping[str, set[str]]
) -> dict[str, bool]:
    """
    Finds the files of which only some blocks are to be saved.

    Args:
        repo: Working tree path.
        hunk_selection: For each file, the blocks that were taken out.

    Returns:
        dict[str, bool]: Path mapped to whether anything of it is left in.
    """

    found: dict[str, bool] = {}
    for path, deselected in hunk_selection.items():
        if not deselected:
            continue
        parsed = diff_mod.file_diff(repo, path, diff_mod.TARGET_WORKTREE_HEAD)
        keys = {hunk.key for hunk in parsed.hunks}
        live = keys & deselected
        if not live:
            continue
        found[path] = bool(keys - live)
    return found


def save(
    repo: Path | str,
    draft: CommitDraft,
    selected: list[str],
    hunk_selection: Mapping[str, set[str]],
) -> SaveOutcome:
    """
    Stages the ticked files and saves them as one version.

    Args:
        repo: Working tree path.
        draft: The message.
        selected: The ticked files.
        hunk_selection: For each file, the blocks that were taken out.

    Returns:
        SaveOutcome: ``saved`` when it worked, otherwise the step that failed.
    """

    unstaged = stage_mod.unstage_all(repo)
    if unstaged.failed and "did not match" not in unstaged.message:
        return SaveOutcome(failure=unstaged)

    # Files the user took single blocks out of are staged block by block, the
    # rest wholesale. The index has to start at the last saved version for that,
    # which is exactly what the reset above leaves behind.
    partial = partial_paths(repo, hunk_selection)
    whole = [path for path in selected if path not in partial]
    if whole:
        added = stage_mod.stage_files(repo, whole)
        if added.failed:
            return SaveOutcome(failure=added)
    for path in selected:
        if path not in partial:
            continue
        parsed = diff_mod.file_diff(repo, path, diff_mod.TARGET_WORKTREE_HEAD)
        parsed.selectable = True
        deselected = hunk_selection.get(parsed.path or path, set())
        for hunk in parsed.hunks:
            hunk.selected = hunk.key not in deselected
        applied = stage_mod.stage_selected_hunks(repo, parsed)
        if applied.failed:
            return SaveOutcome(failure=applied)

    made = do_commit(repo, draft)
    if made.failed:
        return SaveOutcome(failure=made)
    return SaveOutcome(saved=True)


def save_and_upload(
    repo: Path | str,
    draft: CommitDraft,
    selected: list[str],
    hunk_selection: Mapping[str, set[str]],
    branch: str = "",
    set_upstream: bool = False,
    credentials: dict[str, str] | None = None,
) -> SaveOutcome:
    """
    Saves the ticked files and sends the result to the server.

    The upload starts only once the version is made. If it then fails, for
    instance because somebody else sent something first, the version stays.

    Args:
        repo: Working tree path.
        draft: The message.
        selected: The ticked files.
        hunk_selection: For each file, the blocks that were taken out.
        branch: The branch to name in the upload, needed when it is the first of
            that branch.
        set_upstream: Whether the upload records which server branch it follows.
        credentials: Login for the server.

    Returns:
        SaveOutcome: What happened, with the upload's result when it ran.
    """

    outcome = save(repo, draft, selected, hunk_selection)
    if not outcome.saved:
        return outcome
    return upload(repo, branch, set_upstream, credentials, saved=True)


def upload(
    repo: Path | str,
    branch: str = "",
    set_upstream: bool = False,
    credentials: dict[str, str] | None = None,
    saved: bool = False,
    force_with_lease: bool = False,
) -> SaveOutcome:
    """
    Sends the saved versions to the server.

    When the server refuses, its branch is fetched afterwards: only the branch as
    it is now can tell whether the two merely moved apart or share no history at
    all, and that is a question for the reader of this result, not something to
    find out on the window's thread.

    Args:
        repo: Working tree path.
        branch: The branch to name, needed when it is the first of that branch.
        set_upstream: Whether to record which server branch it follows.
        credentials: Login for the server.
        saved: Whether a version was just made, carried into the outcome.
        force_with_lease: Whether to overwrite the server's branch, but only if it
            still is where it was last seen.

    Returns:
        SaveOutcome: ``push`` holds the result.
    """

    pushed = remote_mod.push(
        repo,
        branch=branch,
        set_upstream=set_upstream,
        force_with_lease=force_with_lease,
        credentials=credentials,
    )
    fetched = False
    if pushed.failed and pushed.error_key() in ("sync.push_rejected", "sync.no_upstream"):
        fetched = not remote_mod.fetch(repo, credentials=credentials).failed
    return SaveOutcome(saved=saved, push=pushed, fetched=fetched)
