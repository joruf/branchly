"""
Turning a merge conflict into a list of decisions.

The three sides of a conflict are read from the index stages git already
prepared: stage 1 is the common ancestor, stage 2 is your version, stage 3 is
theirs. Those three go through ``git merge-file --diff3``, so git's own merge
algorithm decides where the conflicts are — writing that algorithm again here
would be both harder and less trustworthy.

The marked-up result is then split into plain regions and conflict regions. The
user only ever sees the regions; the ``<<<<<<<`` markers never reach the UI.

Whole-file cases are kept separate: when one side deleted the file there is
nothing to merge line by line, so the question becomes "keep it or drop it".
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from gitops.refname import is_safe_argument
from gitops.runner import GitResult, run, run_binary, run_lines
from gitops.status import git_dir

STAGE_BASE = 1
STAGE_OURS = 2
STAGE_THEIRS = 3

CHOICE_PENDING = "pending"
CHOICE_OURS = "ours"
CHOICE_THEIRS = "theirs"
CHOICE_BOTH = "both"
CHOICE_CUSTOM = "custom"

REASON_BOTH_CHANGED = "conflict.reason_both_changed"
REASON_BOTH_ADDED = "conflict.reason_both_added"
REASON_DELETED_BY_THEM = "conflict.reason_deleted_by_them"
REASON_DELETED_BY_US = "conflict.reason_deleted_by_us"

KIND_TEXT = "text"
KIND_BINARY = "binary"
KIND_DELETED_BY_THEM = "deleted_by_them"
KIND_DELETED_BY_US = "deleted_by_us"

_MARKER_OURS = "<<<<<<<"
_MARKER_BASE = "|||||||"
_MARKER_SPLIT = "======="
_MARKER_THEIRS = ">>>>>>>"

CHOICE_RESULT_KEYS: dict[str, str] = {
    CHOICE_PENDING: "conflict.result_pending",
    CHOICE_OURS: "conflict.result_chosen_ours",
    CHOICE_THEIRS: "conflict.result_chosen_theirs",
    CHOICE_BOTH: "conflict.result_chosen_both",
    CHOICE_CUSTOM: "conflict.result_chosen_custom",
}


@dataclass(slots=True)
class ConflictRegion:
    """
    One place in a file where the two sides disagree.

    Attributes:
        ours: Lines from your version.
        theirs: Lines from the other version.
        base: Lines from the common ancestor, empty when both sides added anew.
        choice: What the user picked, one of the ``CHOICE_*`` constants.
        custom: Lines the user typed, used when ``choice`` is ``custom``.
    """

    ours: list[str] = field(default_factory=list)
    theirs: list[str] = field(default_factory=list)
    base: list[str] = field(default_factory=list)
    choice: str = CHOICE_PENDING
    custom: list[str] = field(default_factory=list)

    @property
    def reason_key(self) -> str:
        """
        Returns the plain-language explanation of this conflict.

        Returns:
            str: Translation key.
        """

        if not self.base:
            return REASON_BOTH_ADDED
        if not self.theirs:
            return REASON_DELETED_BY_THEM
        if not self.ours:
            return REASON_DELETED_BY_US
        return REASON_BOTH_CHANGED

    @property
    def resolved(self) -> bool:
        """
        Reports whether the user has decided this region.

        Returns:
            bool: True when a choice was made.
        """

        return self.choice != CHOICE_PENDING

    def resolution_lines(self) -> list[str]:
        """
        Returns the lines this region contributes to the finished file.

        Returns:
            list[str]: Chosen lines, empty while the region is undecided.
        """

        if self.choice == CHOICE_OURS:
            return list(self.ours)
        if self.choice == CHOICE_THEIRS:
            return list(self.theirs)
        if self.choice == CHOICE_BOTH:
            return [*self.ours, *self.theirs]
        if self.choice == CHOICE_CUSTOM:
            return list(self.custom)
        return []


@dataclass(slots=True)
class ConflictedFile:
    """
    One file that needs decisions.

    Attributes:
        path: Path relative to the working tree root.
        kind: One of the ``KIND_*`` constants.
        segments: Alternating plain text (``list[str]``) and ``ConflictRegion``
            entries, in file order.
        whole_file_choice: Choice for the binary and deleted cases.
        error_key: Translation key when the file could not be read.
    """

    path: str
    kind: str = KIND_TEXT
    segments: list[object] = field(default_factory=list)
    whole_file_choice: str = CHOICE_PENDING
    error_key: str = ""

    @property
    def regions(self) -> list[ConflictRegion]:
        """
        Returns just the conflict regions, in file order.

        Returns:
            list[ConflictRegion]: Regions needing a decision.
        """

        return [item for item in self.segments if isinstance(item, ConflictRegion)]

    @property
    def needs_whole_file_choice(self) -> bool:
        """
        Reports whether this file is decided as a whole rather than per region.

        Returns:
            bool: True for binary files and for delete/modify conflicts.
        """

        return self.kind in {KIND_BINARY, KIND_DELETED_BY_THEM, KIND_DELETED_BY_US}

    @property
    def resolved(self) -> bool:
        """
        Reports whether every decision in this file has been made.

        Returns:
            bool: True when the file can be written out.
        """

        if self.error_key:
            return False
        if self.needs_whole_file_choice:
            return self.whole_file_choice != CHOICE_PENDING
        regions = self.regions
        return bool(regions) and all(region.resolved for region in regions)

    @property
    def pending_count(self) -> int:
        """
        Returns how many decisions are still open in this file.

        Returns:
            int: Number of undecided regions, or 1 for an undecided whole file.
        """

        if self.needs_whole_file_choice:
            return 0 if self.whole_file_choice != CHOICE_PENDING else 1
        return sum(1 for region in self.regions if not region.resolved)

    def assemble(self) -> str:
        """
        Builds the finished file content from the decisions.

        Returns:
            str: File content. Undecided regions contribute nothing, so callers
                must check ``resolved`` first.
        """

        lines: list[str] = []
        for segment in self.segments:
            if isinstance(segment, ConflictRegion):
                lines.extend(segment.resolution_lines())
            elif isinstance(segment, list):
                lines.extend(segment)
        if not lines:
            return ""
        return "\n".join(lines) + "\n"


def conflicted_paths(repo: Path | str) -> list[str]:
    """
    Lists files with unresolved conflicts.

    Args:
        repo: Working tree path.

    Returns:
        list[str]: Paths relative to the working tree root.
    """

    lines = run_lines(["diff", "--name-only", "--diff-filter=U"], cwd=repo)
    return [line for line in lines if line]


def stage_content(repo: Path | str, path: str, stage: int) -> bytes | None:
    """
    Reads one side of a conflicted file out of the index.

    Args:
        repo: Working tree path.
        path: Path relative to the working tree root.
        stage: 1 for the common ancestor, 2 for ours, 3 for theirs.

    Returns:
        bytes | None: Raw content, or None when that stage does not exist — which
            is the normal case when one side added or deleted the file.
    """

    if not is_safe_argument(path) or stage not in {STAGE_BASE, STAGE_OURS, STAGE_THEIRS}:
        return None
    ok, payload = run_binary(["show", f":{stage}:{path}"], cwd=repo)
    return payload if ok else None


def _looks_binary(payload: bytes) -> bool:
    """
    Reports whether content should be treated as binary.

    A NUL byte in the first few kilobytes is the same heuristic git uses.

    Args:
        payload: Raw content.

    Returns:
        bool: True when the content is not safe to show as text.
    """

    return b"\x00" in payload[:8000]


def _decode_lines(payload: bytes) -> list[str]:
    """
    Splits raw content into lines for display.

    Args:
        payload: Raw content.

    Returns:
        list[str]: Lines without their terminators.
    """

    return payload.decode("utf-8", errors="replace").split("\n")


def parse_diff3(text: str) -> list[object]:
    """
    Splits ``git merge-file --diff3`` output into plain and conflicting parts.

    Args:
        text: Marked-up merge result.

    Returns:
        list[object]: Alternating ``list[str]`` of unchanged lines and
            ``ConflictRegion`` entries, in file order.
    """

    segments: list[object] = []
    plain: list[str] = []
    lines = text.split("\n")
    # Drop the trailing empty element produced by a final newline.
    if lines and lines[-1] == "":
        lines.pop()

    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(_MARKER_OURS):
            if plain:
                segments.append(plain)
                plain = []
            region = ConflictRegion()
            index += 1
            while index < len(lines) and not lines[index].startswith(
                (_MARKER_BASE, _MARKER_SPLIT, _MARKER_THEIRS)
            ):
                region.ours.append(lines[index])
                index += 1
            if index < len(lines) and lines[index].startswith(_MARKER_BASE):
                index += 1
                while index < len(lines) and not lines[index].startswith((_MARKER_SPLIT, _MARKER_THEIRS)):
                    region.base.append(lines[index])
                    index += 1
            if index < len(lines) and lines[index].startswith(_MARKER_SPLIT):
                index += 1
                while index < len(lines) and not lines[index].startswith(_MARKER_THEIRS):
                    region.theirs.append(lines[index])
                    index += 1
            if index < len(lines) and lines[index].startswith(_MARKER_THEIRS):
                index += 1
            segments.append(region)
            continue
        plain.append(line)
        index += 1

    if plain:
        segments.append(plain)
    return segments


def merge_sides(base: bytes, ours: bytes, theirs: bytes) -> str | None:
    """
    Runs git's merge algorithm over three versions of a file.

    Args:
        base: Common ancestor content.
        ours: Our content.
        theirs: Their content.

    Returns:
        str | None: Marked-up merge result, or None when git could not run.
    """

    with tempfile.TemporaryDirectory(prefix="branchly-merge-") as tmp:
        directory = Path(tmp)
        ours_file = directory / "ours"
        base_file = directory / "base"
        theirs_file = directory / "theirs"
        ours_file.write_bytes(ours)
        base_file.write_bytes(base)
        theirs_file.write_bytes(theirs)
        result = run(
            [
                "merge-file",
                "--diff3",
                "--marker-size=7",
                "-p",
                str(ours_file),
                str(base_file),
                str(theirs_file),
            ],
            cwd=directory,
        )
        # Exit code is the number of conflicts, or negative on real failure.
        if result.timed_out or result.returncode < 0:
            return None
        return result.stdout


def load_file(repo: Path | str, path: str) -> ConflictedFile:
    """
    Loads one conflicted file and works out what has to be decided.

    Args:
        repo: Working tree path.
        path: Path relative to the working tree root.

    Returns:
        ConflictedFile: The file with its regions, or with ``error_key`` set.
    """

    if not is_safe_argument(path):
        return ConflictedFile(path=path, error_key="error.unsafe_argument")

    base = stage_content(repo, path, STAGE_BASE)
    ours = stage_content(repo, path, STAGE_OURS)
    theirs = stage_content(repo, path, STAGE_THEIRS)

    if ours is None and theirs is None:
        return ConflictedFile(path=path, error_key="error.git_failed")
    if theirs is None:
        return ConflictedFile(path=path, kind=KIND_DELETED_BY_THEM)
    if ours is None:
        return ConflictedFile(path=path, kind=KIND_DELETED_BY_US)

    if _looks_binary(ours) or _looks_binary(theirs) or (base is not None and _looks_binary(base)):
        return ConflictedFile(path=path, kind=KIND_BINARY)

    merged = merge_sides(base or b"", ours, theirs)
    if merged is None:
        return ConflictedFile(path=path, error_key="error.git_failed")

    # A result without regions means git merged everything by itself; the file
    # then has nothing to ask about and `resolved` stays False, which keeps the
    # assistant from claiming it is done.
    return ConflictedFile(path=path, kind=KIND_TEXT, segments=parse_diff3(merged))


def load_all(repo: Path | str) -> list[ConflictedFile]:
    """
    Loads every conflicted file in the repository.

    Args:
        repo: Working tree path.

    Returns:
        list[ConflictedFile]: One entry per conflicted path.
    """

    return [load_file(repo, path) for path in conflicted_paths(repo)]


def write_resolution(repo: Path | str, item: ConflictedFile) -> GitResult:
    """
    Writes a decided file to disk and stages it.

    Args:
        repo: Working tree path.
        item: A file whose ``resolved`` is True.

    Returns:
        GitResult: Outcome of staging the file.
    """

    if not is_safe_argument(item.path):
        return GitResult(returncode=-1, stdout="", stderr="unsafe path", args=("add",))
    if not item.resolved:
        return GitResult(returncode=-1, stdout="", stderr="file not fully resolved", args=("add",))

    root = Path(repo)
    target = root / item.path

    if item.needs_whole_file_choice:
        return _write_whole_file(repo, item, target)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item.assemble(), encoding="utf-8")
    except OSError as error:
        return GitResult(returncode=-1, stdout="", stderr=str(error), args=("add",))
    return run(["add", "--", item.path], cwd=repo)


def _write_whole_file(repo: Path | str, item: ConflictedFile, target: Path) -> GitResult:
    """
    Applies a whole-file decision for a binary or deleted file.

    Args:
        repo: Working tree path.
        item: The file being resolved.
        target: Absolute path of the file.

    Returns:
        GitResult: Outcome of the staging command.
    """

    keep_ours = item.whole_file_choice in {CHOICE_OURS, CHOICE_BOTH}
    if item.kind == KIND_DELETED_BY_THEM:
        if keep_ours:
            return run(["add", "--", item.path], cwd=repo)
        return run(["rm", "--force", "--", item.path], cwd=repo)
    if item.kind == KIND_DELETED_BY_US:
        if keep_ours:
            return run(["rm", "--force", "--", item.path], cwd=repo)
        content = stage_content(repo, item.path, STAGE_THEIRS)
        if content is None:
            return GitResult(returncode=-1, stdout="", stderr="missing stage", args=("add",))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        except OSError as error:
            return GitResult(returncode=-1, stdout="", stderr=str(error), args=("add",))
        return run(["add", "--", item.path], cwd=repo)

    stage = STAGE_OURS if keep_ours else STAGE_THEIRS
    content = stage_content(repo, item.path, stage)
    if content is None:
        return GitResult(returncode=-1, stdout="", stderr="missing stage", args=("add",))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    except OSError as error:
        return GitResult(returncode=-1, stdout="", stderr=str(error), args=("add",))
    return run(["add", "--", item.path], cwd=repo)


def finish_merge(repo: Path | str, message: str = "") -> GitResult:
    """
    Creates the commit that completes the merge.

    Args:
        repo: Working tree path.
        message: Commit message. Empty uses the message git prepared.

    Returns:
        GitResult: Outcome.
    """

    if message.strip():
        return run(["commit", "-F", "-"], cwd=repo, input_text=message)
    return run(["commit", "--no-edit"], cwd=repo)


def abort(repo: Path | str) -> GitResult:
    """
    Undoes the in-progress operation, whichever one it is.

    Args:
        repo: Working tree path.

    Returns:
        GitResult: Outcome of the matching abort command.
    """

    directory = git_dir(repo)
    if directory is not None:
        if (directory / "CHERRY_PICK_HEAD").exists():
            return run(["cherry-pick", "--abort"], cwd=repo)
        if (directory / "REVERT_HEAD").exists():
            return run(["revert", "--abort"], cwd=repo)
        if (directory / "rebase-merge").is_dir() or (directory / "rebase-apply").is_dir():
            return run(["rebase", "--abort"], cwd=repo)
    return run(["merge", "--abort"], cwd=repo)
