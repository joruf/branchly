"""
Producing and parsing diffs.

Two things live here. First, the *comparison targets*: which two states are being
held against each other — your edits against what is staged, staged against the
last commit, one commit against another, one branch against another. Second, the
parsing of unified diff output into hunks and lines, including the word-level
spans that let the view highlight what changed inside a line instead of marking
the whole line.
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from constants import DIFF_MAX_LINES
from gitops.refname import is_safe_argument, is_valid_revision
from gitops.runner import run, run_binary

TARGET_WORKTREE_INDEX = "worktree_index"
TARGET_INDEX_HEAD = "index_head"
TARGET_WORKTREE_HEAD = "worktree_head"
TARGET_COMMITS = "commits"
TARGET_BRANCHES = "branches"

VALID_TARGETS: tuple[str, ...] = (
    TARGET_WORKTREE_INDEX,
    TARGET_INDEX_HEAD,
    TARGET_WORKTREE_HEAD,
    TARGET_COMMITS,
    TARGET_BRANCHES,
)

TARGET_LABEL_KEYS: dict[str, str] = {
    TARGET_WORKTREE_INDEX: "diff.target_worktree_index",
    TARGET_INDEX_HEAD: "diff.target_index_head",
    TARGET_WORKTREE_HEAD: "diff.target_worktree_head",
    TARGET_COMMITS: "diff.target_commits",
    TARGET_BRANCHES: "diff.target_branches",
}

LINE_CONTEXT = "context"
LINE_ADDED = "added"
LINE_REMOVED = "removed"
LINE_NO_NEWLINE = "no_newline"

IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tif", ".tiff"}
)

_HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))?"
    r" \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<heading>.*)$"
)

_WORD_PATTERN = re.compile(r"\w+|\s+|[^\w\s]")


@dataclass(slots=True)
class DiffLine:
    """
    One line of a diff.

    Attributes:
        kind: ``context``, ``added``, ``removed`` or ``no_newline``.
        text: Line content without the leading diff marker.
        old_lineno: Line number on the left side, None when the line is new.
        new_lineno: Line number on the right side, None when the line is gone.
        spans: Character ranges that differ from the paired line, as
            ``(start, end)`` pairs. Empty unless word-level diffing ran.
    """

    kind: str
    text: str
    old_lineno: int | None = None
    new_lineno: int | None = None
    spans: list[tuple[int, int]] = field(default_factory=list)


@dataclass(slots=True)
class DiffHunk:
    """
    One ``@@`` block of a diff.

    Attributes:
        old_start: First line number on the left side.
        old_count: Number of lines covered on the left side.
        new_start: First line number on the right side.
        new_count: Number of lines covered on the right side.
        heading: Trailing context git puts after the ``@@`` marker.
        lines: Lines belonging to this hunk.
    """

    old_start: int = 0
    old_count: int = 0
    new_start: int = 0
    new_count: int = 0
    heading: str = ""
    lines: list[DiffLine] = field(default_factory=list)

    @property
    def header(self) -> str:
        """
        Rebuilds the ``@@`` header line.

        Returns:
            str: Header as git would print it.
        """

        return f"@@ -{self.old_start},{self.old_count} +{self.new_start},{self.new_count} @@{self.heading}"


@dataclass(slots=True)
class FileDiff:
    """
    The parsed diff of a single file.

    Attributes:
        path: Path of the file being compared.
        old_path: Previous path when the file was renamed.
        hunks: Parsed hunks.
        binary: Whether git reported the file as binary.
        truncated: Whether the diff was cut off at ``DIFF_MAX_LINES``.
        added: Number of added lines.
        removed: Number of removed lines.
        error_key: Translation key when the diff could not be produced.
    """

    path: str = ""
    old_path: str = ""
    hunks: list[DiffHunk] = field(default_factory=list)
    binary: bool = False
    truncated: bool = False
    added: int = 0
    removed: int = 0
    error_key: str = ""

    @property
    def is_empty(self) -> bool:
        """
        Reports whether there is nothing to show.

        Returns:
            bool: True when no hunks and not binary.
        """

        return not self.hunks and not self.binary

    @property
    def is_image(self) -> bool:
        """
        Reports whether the file can be previewed as a picture.

        Returns:
            bool: True for a binary file with a known image suffix.
        """

        return self.binary and Path(self.path).suffix.lower() in IMAGE_SUFFIXES

    @property
    def line_count(self) -> int:
        """
        Returns the total number of diff lines.

        Returns:
            int: Sum of all hunk lines.
        """

        return sum(len(hunk.lines) for hunk in self.hunks)


def normalize_target(target: str | None) -> str:
    """
    Maps arbitrary input onto a known comparison target.

    Args:
        target: Candidate target.

    Returns:
        str: A valid target, defaulting to working tree against the last commit.
    """

    if isinstance(target, str) and target in VALID_TARGETS:
        return target
    return TARGET_WORKTREE_HEAD


def build_diff_args(
    target: str,
    path: str | None = None,
    ignore_whitespace: bool = False,
    rev_a: str = "",
    rev_b: str = "",
    context_lines: int = 3,
) -> list[str] | None:
    """
    Builds the git arguments for one comparison.

    Args:
        target: One of the ``TARGET_*`` constants.
        path: Restrict the diff to this path, relative to the working tree root.
        ignore_whitespace: Whether whitespace-only changes are hidden.
        rev_a: Left revision, required for commit and branch comparisons.
        rev_b: Right revision, required for commit and branch comparisons.
        context_lines: Unchanged lines shown around each change.

    Returns:
        list[str] | None: Argument list, or None when a revision or path was
            refused by validation — the caller must treat that as an error rather
            than fall back to some other comparison.
    """

    chosen = normalize_target(target)
    args = ["diff", "--no-color", "--no-ext-diff", f"--unified={max(0, context_lines)}"]
    if ignore_whitespace:
        args.append("--ignore-all-space")

    if chosen == TARGET_WORKTREE_INDEX:
        pass
    elif chosen == TARGET_INDEX_HEAD:
        args.append("--cached")
    elif chosen == TARGET_WORKTREE_HEAD:
        args.append("HEAD")
    elif chosen in {TARGET_COMMITS, TARGET_BRANCHES}:
        if not is_valid_revision(rev_a) or not is_valid_revision(rev_b):
            return None
        args.extend([rev_a, rev_b])

    if path:
        if not is_safe_argument(path):
            return None
        args.extend(["--", path])
    return args


def _paths_from_header(line: str) -> tuple[str, str]:
    """
    Extracts the two paths from a ``diff --git a/x b/x`` header.

    Args:
        line: The header line.

    Returns:
        tuple[str, str]: Old and new path, each empty when it cannot be read. A
            file name containing " b/" cannot be split unambiguously, and an empty
            result is better than a wrong one — the ---/+++ lines cover that case.
    """

    remainder = line[len("diff --git "):]
    marker = remainder.find(" b/")
    if marker == -1 or not remainder.startswith("a/"):
        return "", ""
    old_path = remainder[2:marker]
    new_path = remainder[marker + 3:]
    if remainder.count(" b/") > 1:
        return "", ""
    return old_path, new_path


def _tokenize(text: str) -> list[str]:
    """
    Splits a line into words, whitespace runs and punctuation.

    Args:
        text: Line content.

    Returns:
        list[str]: Tokens in order, concatenating back to the input.
    """

    return _WORD_PATTERN.findall(text)


def intra_line_spans(old: str, new: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """
    Finds the differing character ranges between two versions of a line.

    Comparing word tokens rather than characters keeps the highlighting readable:
    a changed identifier lights up as one block instead of a scatter of letters.

    Args:
        old: Line as it was.
        new: Line as it is.

    Returns:
        tuple[list, list]: Ranges to highlight in the old line and the new line.
    """

    old_tokens = _tokenize(old)
    new_tokens = _tokenize(new)
    if not old_tokens or not new_tokens:
        return ([(0, len(old))] if old else [], [(0, len(new))] if new else [])

    old_offsets: list[int] = []
    position = 0
    for token in old_tokens:
        old_offsets.append(position)
        position += len(token)
    new_offsets: list[int] = []
    position = 0
    for token in new_tokens:
        new_offsets.append(position)
        position += len(token)

    old_spans: list[tuple[int, int]] = []
    new_spans: list[tuple[int, int]] = []
    matcher = difflib.SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if i2 > i1:
            start = old_offsets[i1]
            end = old_offsets[i2 - 1] + len(old_tokens[i2 - 1])
            old_spans.append((start, end))
        if j2 > j1:
            start = new_offsets[j1]
            end = new_offsets[j2 - 1] + len(new_tokens[j2 - 1])
            new_spans.append((start, end))
    return old_spans, new_spans


def annotate_word_diff(hunk: DiffHunk) -> None:
    """
    Fills in ``spans`` on the removed and added lines of a hunk.

    Removed and added runs are paired in order. An unequal run length means the
    surplus lines are wholly new or wholly gone, and those get no spans — marking
    every character of them would say nothing.

    Args:
        hunk: Hunk to annotate in place.

    Returns:
        None
    """

    index = 0
    lines = hunk.lines
    while index < len(lines):
        if lines[index].kind != LINE_REMOVED:
            index += 1
            continue
        removed_start = index
        while index < len(lines) and lines[index].kind == LINE_REMOVED:
            index += 1
        added_start = index
        while index < len(lines) and lines[index].kind == LINE_ADDED:
            index += 1
        removed = lines[removed_start:added_start]
        added = lines[added_start:index]
        for old_line, new_line in zip(removed, added, strict=False):
            old_spans, new_spans = intra_line_spans(old_line.text, new_line.text)
            old_line.spans = old_spans
            new_line.spans = new_spans


def parse_unified(payload: str, word_level: bool = True) -> FileDiff:
    """
    Parses unified diff output for a single file.

    Args:
        payload: Raw ``git diff`` output.
        word_level: Whether to compute intra-line highlight spans.

    Returns:
        FileDiff: Parsed diff. Output covering several files keeps only the first,
            which is all the single-file view ever asks for.
    """

    diff = FileDiff()
    hunk: DiffHunk | None = None
    old_lineno = new_lineno = 0
    seen_first_file = False

    for raw_line in payload.splitlines():
        if raw_line.startswith("diff --git "):
            if seen_first_file:
                break
            seen_first_file = True
            hunk = None
            # A binary file gets no ---/+++ lines, so this header is the only
            # place its path appears.
            old_candidate, new_candidate = _paths_from_header(raw_line)
            if old_candidate:
                diff.old_path = old_candidate
            if new_candidate:
                diff.path = new_candidate
            continue
        if raw_line.startswith("--- "):
            candidate = raw_line[4:].strip()
            if candidate.startswith("a/"):
                diff.old_path = candidate[2:]
            continue
        if raw_line.startswith("+++ "):
            candidate = raw_line[4:].strip()
            if candidate.startswith("b/"):
                diff.path = candidate[2:]
            continue
        if raw_line.startswith("rename from "):
            diff.old_path = raw_line[len("rename from "):]
            continue
        if raw_line.startswith("rename to "):
            diff.path = raw_line[len("rename to "):]
            continue
        if raw_line.startswith("Binary files ") or raw_line.startswith("GIT binary patch"):
            diff.binary = True
            continue
        if raw_line.startswith("@@"):
            match = _HUNK_HEADER.match(raw_line)
            if not match:
                continue
            hunk = DiffHunk(
                old_start=int(match.group("old_start")),
                old_count=int(match.group("old_count") or 1),
                new_start=int(match.group("new_start")),
                new_count=int(match.group("new_count") or 1),
                heading=match.group("heading") or "",
            )
            diff.hunks.append(hunk)
            old_lineno = hunk.old_start
            new_lineno = hunk.new_start
            continue
        if hunk is None:
            continue

        if diff.line_count >= DIFF_MAX_LINES:
            diff.truncated = True
            break

        marker, content = (raw_line[0], raw_line[1:]) if raw_line else (" ", "")
        if marker == "+":
            hunk.lines.append(DiffLine(kind=LINE_ADDED, text=content, new_lineno=new_lineno))
            new_lineno += 1
            diff.added += 1
        elif marker == "-":
            hunk.lines.append(DiffLine(kind=LINE_REMOVED, text=content, old_lineno=old_lineno))
            old_lineno += 1
            diff.removed += 1
        elif marker == "\\":
            hunk.lines.append(DiffLine(kind=LINE_NO_NEWLINE, text=content.strip()))
        else:
            hunk.lines.append(
                DiffLine(kind=LINE_CONTEXT, text=content, old_lineno=old_lineno, new_lineno=new_lineno)
            )
            old_lineno += 1
            new_lineno += 1

    if word_level:
        for item in diff.hunks:
            annotate_word_diff(item)
    return diff


def split_files(payload: str) -> list[str]:
    """
    Splits multi-file diff output into one chunk per file.

    Args:
        payload: Raw output covering several files.

    Returns:
        list[str]: One chunk per file, each starting at its ``diff --git`` header.
    """

    chunks: list[str] = []
    current: list[str] = []
    for line in payload.splitlines():
        if line.startswith("diff --git "):
            if current:
                chunks.append("\n".join(current))
            current = [line]
            continue
        if current:
            current.append(line)
    if current:
        chunks.append("\n".join(current))
    return chunks


def parse_multi(payload: str, word_level: bool = True) -> list[FileDiff]:
    """
    Parses diff output covering several files.

    Args:
        payload: Raw output.
        word_level: Whether to compute intra-line highlight spans.

    Returns:
        list[FileDiff]: One entry per file, in the order git printed them.
    """

    return [parse_unified(chunk, word_level=word_level) for chunk in split_files(payload)]


def commit_diff(
    repo: Path | str,
    revision: str,
    ignore_whitespace: bool = False,
    word_level: bool = True,
) -> list[FileDiff]:
    """
    Produces the full diff a single commit introduced.

    A merge commit is shown against its first parent, which is the change the
    merge actually brought onto the current line of work; the alternative,
    combined output, is much harder to read and rarely what is wanted.

    Args:
        repo: Working tree path.
        revision: Commit to show.
        ignore_whitespace: Whether whitespace-only changes are hidden.
        word_level: Whether to compute intra-line highlight spans.

    Returns:
        list[FileDiff]: One entry per changed file, empty when unreadable.
    """

    if not is_valid_revision(revision):
        return []
    args = [
        "show",
        "--no-color",
        "--no-ext-diff",
        "--first-parent",
        "--unified=3",
        "--format=",
        revision,
    ]
    if ignore_whitespace:
        args.insert(4, "--ignore-all-space")
    result = run(args, cwd=repo, read_only=True)
    if result.failed:
        return []
    return parse_multi(result.stdout, word_level=word_level)


def range_diff(
    repo: Path | str,
    rev_a: str,
    rev_b: str,
    ignore_whitespace: bool = False,
    word_level: bool = True,
) -> list[FileDiff]:
    """
    Produces the full diff between two revisions.

    Args:
        repo: Working tree path.
        rev_a: Older revision.
        rev_b: Newer revision.
        ignore_whitespace: Whether whitespace-only changes are hidden.
        word_level: Whether to compute intra-line highlight spans.

    Returns:
        list[FileDiff]: One entry per changed file, empty when unreadable.
    """

    args = build_diff_args(TARGET_COMMITS, None, ignore_whitespace, rev_a, rev_b)
    if args is None:
        return []
    result = run(args, cwd=repo, read_only=True)
    if result.failed:
        return []
    return parse_multi(result.stdout, word_level=word_level)


def pair_lines(hunk: DiffHunk) -> list[tuple[DiffLine | None, DiffLine | None]]:
    """
    Arranges hunk lines into left/right pairs for the side-by-side view.

    Args:
        hunk: Hunk to lay out.

    Returns:
        list[tuple[DiffLine | None, DiffLine | None]]: One entry per display row;
            None means that side stays blank.
    """

    rows: list[tuple[DiffLine | None, DiffLine | None]] = []
    index = 0
    lines = hunk.lines
    while index < len(lines):
        line = lines[index]
        if line.kind == LINE_CONTEXT:
            rows.append((line, line))
            index += 1
            continue
        if line.kind == LINE_NO_NEWLINE:
            rows.append((line, None))
            index += 1
            continue
        removed_start = index
        while index < len(lines) and lines[index].kind == LINE_REMOVED:
            index += 1
        added_start = index
        while index < len(lines) and lines[index].kind == LINE_ADDED:
            index += 1
        removed = lines[removed_start:added_start]
        added = lines[added_start:index]
        for position in range(max(len(removed), len(added))):
            left = removed[position] if position < len(removed) else None
            right = added[position] if position < len(added) else None
            rows.append((left, right))
    return rows


def file_diff(
    repo: Path | str,
    path: str,
    target: str = TARGET_WORKTREE_HEAD,
    ignore_whitespace: bool = False,
    word_level: bool = True,
    rev_a: str = "",
    rev_b: str = "",
) -> FileDiff:
    """
    Produces the diff of one file.

    Args:
        repo: Working tree path.
        path: File path relative to the working tree root.
        target: One of the ``TARGET_*`` constants.
        ignore_whitespace: Whether whitespace-only changes are hidden.
        word_level: Whether to compute intra-line highlight spans.
        rev_a: Left revision for commit and branch comparisons.
        rev_b: Right revision for commit and branch comparisons.

    Returns:
        FileDiff: Parsed diff, carrying ``error_key`` when it could not be read.
    """

    args = build_diff_args(target, path, ignore_whitespace, rev_a, rev_b)
    if args is None:
        return FileDiff(path=path, error_key="error.unsafe_argument")
    result = run(args, cwd=repo, read_only=True)
    if result.failed:
        return FileDiff(path=path, error_key=result.error_key())
    diff = parse_unified(result.stdout, word_level=word_level)
    if not diff.path:
        diff.path = path
    return diff


def untracked_file_diff(repo: Path | str, path: str, word_level: bool = True) -> FileDiff:
    """
    Produces a diff for a file git does not track yet.

    An untracked file has nothing to compare against, so git shows nothing at
    all. Presenting it as "every line is new" is what the user expects to see.

    Args:
        repo: Working tree path.
        path: File path relative to the working tree root.
        word_level: Accepted for signature symmetry; unused, as there is no old
            side to highlight against.

    Returns:
        FileDiff: Diff whose single hunk holds the whole file as added lines.
    """

    if not is_safe_argument(path):
        return FileDiff(path=path, error_key="error.unsafe_argument")
    args = ["diff", "--no-color", "--no-ext-diff", "--no-index", "--unified=3", "--", _null_device(), path]
    result = run(args, cwd=repo, read_only=True)
    # `--no-index` reports a difference with exit code 1, which is not a failure.
    if result.returncode not in {0, 1} or result.timed_out:
        return FileDiff(path=path, error_key=result.error_key() or "error.git_failed")
    diff = parse_unified(result.stdout, word_level=False)
    diff.path = path
    return diff


def _null_device() -> str:
    """
    Returns the platform's empty-file path for ``--no-index`` comparisons.

    Returns:
        str: ``/dev/null`` on POSIX, ``NUL`` on Windows.
    """

    return os.devnull


def blob_bytes(repo: Path | str, revision: str, path: str) -> bytes | None:
    """
    Reads a file's content at a given revision, without decoding it.

    Args:
        repo: Working tree path.
        revision: Revision to read from, or ``:`` prefixed index stage.
        path: File path relative to the working tree root.

    Returns:
        bytes | None: Raw content, or None when it could not be read.
    """

    if not is_safe_argument(path):
        return None
    if revision and not (revision.startswith(":") or is_valid_revision(revision)):
        return None
    spec = f"{revision}:{path}" if not revision.startswith(":") else f"{revision}{path}"
    ok, payload = run_binary(["show", spec], cwd=repo)
    return payload if ok else None


def numstat(
    repo: Path | str,
    target: str = TARGET_WORKTREE_HEAD,
    rev_a: str = "",
    rev_b: str = "",
) -> dict[str, tuple[int, int]]:
    """
    Reads added and removed line counts per file.

    Used for the summary numbers in the file list, which would otherwise need a
    full diff parse per file.

    Args:
        repo: Working tree path.
        target: One of the ``TARGET_*`` constants.
        rev_a: Left revision for commit and branch comparisons.
        rev_b: Right revision for commit and branch comparisons.

    Returns:
        dict[str, tuple[int, int]]: Path mapped to ``(added, removed)``. A binary
            file maps to ``(0, 0)``, which git reports as ``-``.
    """

    args = build_diff_args(target, None, False, rev_a, rev_b)
    if args is None:
        return {}
    args = [arg for arg in args if not arg.startswith("--unified=")]
    args.insert(1, "--numstat")
    args.insert(2, "-z")
    result = run(args, cwd=repo, read_only=True)
    if result.failed:
        return {}

    counts: dict[str, tuple[int, int]] = {}
    entries = result.stdout.split("\x00")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        parts = entry.split("\t")
        if len(parts) < 3:
            continue
        added_raw, removed_raw, path = parts[0], parts[1], parts[2]
        if not path:
            # Renames put the two paths in the following NUL-separated entries.
            if index + 1 < len(entries):
                path = entries[index + 1]
                index += 2
            else:
                continue
        try:
            added = int(added_raw)
            removed = int(removed_raw)
        except ValueError:
            added = removed = 0
        counts[path] = (added, removed)
    return counts
