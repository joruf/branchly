"""
Reading the working tree state.

Uses ``git status --porcelain=v2 -z``, not the human-readable output: v2 is a
documented, stable format, and ``-z`` means a file name containing a newline or a
quote parses correctly instead of silently splitting into two entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from gitops.runner import GitResult, run

CHANGE_MODIFIED = "modified"
CHANGE_ADDED = "added"
CHANGE_DELETED = "deleted"
CHANGE_RENAMED = "renamed"
CHANGE_COPIED = "copied"
CHANGE_TYPE_CHANGED = "type_changed"
CHANGE_UNTRACKED = "untracked"
CHANGE_CONFLICTED = "conflicted"

# Translation keys for each kind, so the UI never maps status letters itself.
CHANGE_LABEL_KEYS: dict[str, str] = {
    CHANGE_MODIFIED: "status.modified",
    CHANGE_ADDED: "status.added",
    CHANGE_DELETED: "status.deleted",
    CHANGE_RENAMED: "status.renamed",
    CHANGE_COPIED: "status.renamed",
    CHANGE_TYPE_CHANGED: "status.modified",
    CHANGE_UNTRACKED: "status.untracked",
    CHANGE_CONFLICTED: "status.conflicted",
}

# Theme token names for each kind, resolved by the UI against the active theme.
CHANGE_COLOR_TOKENS: dict[str, str] = {
    CHANGE_MODIFIED: "status_modified",
    CHANGE_ADDED: "status_added",
    CHANGE_DELETED: "status_deleted",
    CHANGE_RENAMED: "status_renamed",
    CHANGE_COPIED: "status_renamed",
    CHANGE_TYPE_CHANGED: "status_modified",
    CHANGE_UNTRACKED: "status_untracked",
    CHANGE_CONFLICTED: "status_conflict",
}

# One character per kind, the way GitHub Desktop marks its rows: a plus for
# something that was not there before, a dot for something that was replaced, a
# minus for something that is gone, an arrow for a move. Kept next to the kinds
# themselves so the UI never maps status letters on its own.
CHANGE_GLYPHS: dict[str, str] = {
    CHANGE_MODIFIED: "\u2022",
    CHANGE_ADDED: "+",
    CHANGE_UNTRACKED: "+",
    CHANGE_DELETED: "\u2212",
    CHANGE_RENAMED: "\u2192",
    CHANGE_COPIED: "\u2192",
    CHANGE_TYPE_CHANGED: "\u2022",
    CHANGE_CONFLICTED: "!",
}

_XY_TO_KIND = {
    "M": CHANGE_MODIFIED,
    "A": CHANGE_ADDED,
    "D": CHANGE_DELETED,
    "R": CHANGE_RENAMED,
    "C": CHANGE_COPIED,
    "T": CHANGE_TYPE_CHANGED,
}


@dataclass(slots=True)
class FileChange:
    """
    One changed path as reported by git status.

    Attributes:
        path: Path relative to the working tree root.
        orig_path: Previous path for renames and copies, otherwise empty.
        index_code: Status letter for the staged side, ``.`` when unchanged.
        worktree_code: Status letter for the unstaged side, ``.`` when unchanged.
        conflicted: Whether the file has an unresolved merge conflict.
        untracked: Whether git does not track the file yet.
    """

    path: str
    orig_path: str = ""
    index_code: str = "."
    worktree_code: str = "."
    conflicted: bool = False
    untracked: bool = False

    @property
    def staged(self) -> bool:
        """
        Reports whether part of this change is staged.

        Returns:
            bool: True when the index differs from HEAD.
        """

        return self.index_code not in {".", "?"} and not self.conflicted

    @property
    def unstaged(self) -> bool:
        """
        Reports whether part of this change is not staged.

        Returns:
            bool: True when the working tree differs from the index.
        """

        return self.untracked or (self.worktree_code not in {".", "?"})

    @property
    def kind(self) -> str:
        """
        Returns the semantic kind of the change.

        The unstaged side wins when both sides changed: that is what the user is
        looking at in the working tree.

        Returns:
            str: One of the ``CHANGE_*`` constants.
        """

        if self.conflicted:
            return CHANGE_CONFLICTED
        if self.untracked:
            return CHANGE_UNTRACKED
        for code in (self.worktree_code, self.index_code):
            if code in _XY_TO_KIND:
                return _XY_TO_KIND[code]
        return CHANGE_MODIFIED

    @property
    def label_key(self) -> str:
        """
        Returns the translation key describing this change.

        Returns:
            str: Translation key.
        """

        return CHANGE_LABEL_KEYS.get(self.kind, "status.modified")

    @property
    def glyph(self) -> str:
        """
        Returns the one-character marker for this change.

        Returns:
            str: The marker, or a dot for a kind that has none of its own.
        """

        return CHANGE_GLYPHS.get(self.kind, CHANGE_GLYPHS[CHANGE_MODIFIED])

    @property
    def color_token(self) -> str:
        """
        Returns the theme token name to color this change with.

        Returns:
            str: Attribute name on ``ThemeColors``.
        """

        return CHANGE_COLOR_TOKENS.get(self.kind, "status_modified")

    @property
    def display_path(self) -> str:
        """
        Returns the path as it should appear in the file list.

        Returns:
            str: ``old → new`` for renames, otherwise just the path.
        """

        if self.orig_path and self.orig_path != self.path:
            return f"{self.orig_path} → {self.path}"
        return self.path


@dataclass(slots=True)
class RepositoryState:
    """
    Everything the changes panel and the sidebar badge need about one repository.

    Attributes:
        branch: Current branch name, empty when detached.
        oid: Current commit id, empty in a repository without commits.
        upstream: Tracking branch, e.g. ``origin/main``, empty when there is none.
        ahead: Commits the local branch has and the upstream does not.
        behind: Commits the upstream has and the local branch does not.
        detached: Whether HEAD points straight at a commit.
        initial: Whether the repository has no commits at all.
        files: Every changed, untracked and conflicted path.
        merging: Whether a merge is in progress.
        rebasing: Whether a rebase is in progress.
        cherry_picking: Whether a cherry-pick is in progress.
        reverting: Whether a revert is in progress.
        error_key: Translation key when the state could not be read.
    """

    branch: str = ""
    oid: str = ""
    upstream: str = ""
    ahead: int = 0
    behind: int = 0
    detached: bool = False
    initial: bool = False
    files: list[FileChange] = field(default_factory=list)
    merging: bool = False
    rebasing: bool = False
    cherry_picking: bool = False
    reverting: bool = False
    error_key: str = ""

    @property
    def ok(self) -> bool:
        """
        Reports whether the state was read successfully.

        Returns:
            bool: True when no error occurred.
        """

        return not self.error_key

    @property
    def conflicted_files(self) -> list[FileChange]:
        """
        Returns only the files with unresolved conflicts.

        Returns:
            list[FileChange]: Conflicted files.
        """

        return [item for item in self.files if item.conflicted]

    @property
    def staged_files(self) -> list[FileChange]:
        """
        Returns only the files with staged changes.

        Returns:
            list[FileChange]: Staged files.
        """

        return [item for item in self.files if item.staged]

    @property
    def unstaged_files(self) -> list[FileChange]:
        """
        Returns only the files with unstaged changes.

        Returns:
            list[FileChange]: Unstaged and untracked files.
        """

        return [item for item in self.files if item.unstaged and not item.conflicted]

    @property
    def has_conflicts(self) -> bool:
        """
        Reports whether a merge needs decisions.

        Returns:
            bool: True when at least one file is conflicted.
        """

        return any(item.conflicted for item in self.files)

    @property
    def is_clean(self) -> bool:
        """
        Reports whether the working tree holds no changes at all.

        Returns:
            bool: True when nothing is changed, staged or untracked.
        """

        return not self.files

    @property
    def operation_in_progress(self) -> bool:
        """
        Reports whether git is in the middle of a multi-step operation.

        Returns:
            bool: True during a merge, rebase, cherry-pick or revert.
        """

        return self.merging or self.rebasing or self.cherry_picking or self.reverting

    @property
    def display_branch(self) -> str:
        """
        Returns what to show as the current position.

        Returns:
            str: Branch name, or a short commit id when detached.
        """

        if self.branch:
            return self.branch
        if self.oid:
            return self.oid[:7]
        return ""


def _parse_ab(value: str) -> tuple[int, int]:
    """
    Parses the ``+ahead -behind`` header of porcelain v2.

    Args:
        value: Header value, e.g. ``+2 -1``.

    Returns:
        tuple[int, int]: Ahead and behind counts, zero when unparseable.
    """

    ahead = behind = 0
    for token in value.split():
        try:
            if token.startswith("+"):
                ahead = int(token[1:])
            elif token.startswith("-"):
                behind = int(token[1:])
        except ValueError:
            continue
    return max(ahead, 0), max(behind, 0)


def parse_porcelain_v2(payload: str) -> RepositoryState:
    """
    Parses ``git status --porcelain=v2 --branch -z`` output.

    Args:
        payload: Raw NUL-separated output.

    Returns:
        RepositoryState: Parsed state, without the in-progress-operation flags,
            which are read from the git directory rather than from status.
    """

    state = RepositoryState()
    entries = payload.split("\x00")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue

        if entry.startswith("# "):
            header, _, value = entry[2:].partition(" ")
            if header == "branch.oid":
                state.oid = "" if value == "(initial)" else value
                state.initial = value == "(initial)"
            elif header == "branch.head":
                if value == "(detached)":
                    state.detached = True
                else:
                    state.branch = value
            elif header == "branch.upstream":
                state.upstream = value
            elif header == "branch.ab":
                state.ahead, state.behind = _parse_ab(value)
            continue

        marker = entry[0]
        if marker == "1":
            fields = entry.split(" ", 8)
            if len(fields) < 9:
                continue
            xy = fields[1]
            state.files.append(
                FileChange(
                    path=fields[8],
                    index_code=xy[0],
                    worktree_code=xy[1] if len(xy) > 1 else ".",
                )
            )
        elif marker == "2":
            fields = entry.split(" ", 9)
            if len(fields) < 10:
                continue
            xy = fields[1]
            # With -z the original path is the next NUL-separated entry.
            orig_path = entries[index] if index < len(entries) else ""
            index += 1
            state.files.append(
                FileChange(
                    path=fields[9],
                    orig_path=orig_path,
                    index_code=xy[0],
                    worktree_code=xy[1] if len(xy) > 1 else ".",
                )
            )
        elif marker == "u":
            fields = entry.split(" ", 10)
            if len(fields) < 11:
                continue
            xy = fields[1]
            state.files.append(
                FileChange(
                    path=fields[10],
                    index_code=xy[0],
                    worktree_code=xy[1] if len(xy) > 1 else ".",
                    conflicted=True,
                )
            )
        elif marker == "?":
            state.files.append(FileChange(path=entry[2:], untracked=True, worktree_code="?", index_code="?"))
        # "!" is an ignored file; status is called without --ignored, but a
        # future caller adding it should not crash here.

    return state


def git_dir(repo: Path | str) -> Path | None:
    """
    Resolves the git directory of a working tree.

    Not always ``.git``: worktrees and submodules point elsewhere, and the
    in-progress-operation markers live in the real directory.

    Args:
        repo: Working tree path.

    Returns:
        Path | None: Absolute git directory, or None when it cannot be resolved.
    """

    result = run(["rev-parse", "--absolute-git-dir"], cwd=repo, read_only=True)
    if result.failed:
        return None
    raw = result.stdout.strip()
    return Path(raw) if raw else None


def _detect_operations(repo: Path | str, state: RepositoryState) -> None:
    """
    Fills in the in-progress-operation flags.

    Args:
        repo: Working tree path.
        state: State to update in place.

    Returns:
        None
    """

    directory = git_dir(repo)
    if directory is None:
        return
    state.merging = (directory / "MERGE_HEAD").exists()
    state.cherry_picking = (directory / "CHERRY_PICK_HEAD").exists()
    state.reverting = (directory / "REVERT_HEAD").exists()
    state.rebasing = (directory / "rebase-merge").is_dir() or (directory / "rebase-apply").is_dir()


def read_state(repo: Path | str) -> RepositoryState:
    """
    Reads the full working tree state of a repository.

    Args:
        repo: Working tree path.

    Returns:
        RepositoryState: Parsed state, carrying ``error_key`` when git failed.
    """

    result: GitResult = run(
        ["status", "--porcelain=v2", "--branch", "-z"],
        cwd=repo,
        read_only=True,
    )
    if result.failed:
        return RepositoryState(error_key=result.error_key())
    state = parse_porcelain_v2(result.stdout)
    _detect_operations(repo, state)
    return state


def last_commit_timestamp(repo: Path | str) -> float | None:
    """
    Reads the committer timestamp of the current tip.

    Args:
        repo: Working tree path.

    Returns:
        float | None: Unix timestamp, or None in a repository without commits.
    """

    result = run(["log", "-1", "--format=%ct"], cwd=repo, read_only=True)
    if result.failed:
        return None
    raw = result.stdout.strip()
    try:
        return float(raw)
    except ValueError:
        return None


def current_branch(repo: Path | str) -> str:
    """
    Returns the checked-out branch name.

    Args:
        repo: Working tree path.

    Returns:
        str: Branch name, empty when HEAD is detached or there are no commits.
    """

    result = run(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=repo, read_only=True)
    if result.failed:
        return ""
    return result.stdout.strip()
