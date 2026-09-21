"""
Reading the two versions of a file, and what can be said about them.

A text file gets a diff. A file git calls binary gets this instead: both
versions side by side, as pictures when they are pictures, and otherwise as the
two facts that are true of any file at all, its size and when it was last
written.

Where those facts come from is the whole difficulty, because the two sides of a
comparison are rarely the same kind of thing:

* The **working tree** has a real file with a real size and a real modification
  time from the filesystem.
* A **commit** has a blob, whose size git knows, and no timestamp of its own. The
  meaningful date there is when the version came to be, which is the date of the
  last commit that touched the path up to that revision.
* The **index** has a blob plus the modification time git recorded when the file
  was staged. That is the closest thing to "when this staged version was
  written", and it is what ``git ls-files --debug`` reports.

Saying "unknown" where a date genuinely cannot be established is part of the
job: an invented timestamp in a comparison is worse than a dash.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from constants import GIT_TIMEOUT_LOCAL
from gitops.diff import (
    IMAGE_SUFFIXES,
    TARGET_BRANCHES,
    TARGET_COMMITS,
    TARGET_INDEX_HEAD,
    TARGET_WORKTREE_HEAD,
    TARGET_WORKTREE_INDEX,
    normalize_target,
)
from gitops.refname import is_safe_argument, is_valid_revision
from gitops.runner import UnsafeGitArgument, run, run_binary

# Where one side of a comparison comes from.
SOURCE_WORKTREE = "worktree"
SOURCE_INDEX = "index"
SOURCE_REVISION = "revision"
SOURCE_NONE = "none"

# Beyond this a preview is not loaded. The picture would have to be scaled down
# to a few hundred pixels anyway, and reading a hundred megabytes into memory to
# do it is not a trade worth making.
MAX_PREVIEW_BYTES = 32 * 1024 * 1024

_MTIME_LINE = re.compile(r"^\s*mtime:\s*(\d+)", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class BlobFacts:
    """
    What is known about one version of a file.

    Attributes:
        exists: Whether this side has the file at all. A file that was just added
            has no "before", and a deleted one has no "after".
        size: Size in bytes.
        modified: Unix timestamp of when this version came to be, or None when
            that cannot be established.
        source: One of the ``SOURCE_*`` constants, so the view can name where the
            side comes from.
        revision: The revision this side refers to, for display.
    """

    exists: bool = False
    size: int = 0
    modified: float | None = None
    source: str = SOURCE_NONE
    revision: str = ""


@dataclass(frozen=True, slots=True)
class BinaryComparison:
    """
    Both versions of one file that has no readable diff.

    Attributes:
        path: Repository-relative path.
        before: The older version.
        after: The newer version.
        is_image: Whether both sides can be shown as pictures.
        before_bytes: Content of the older version, loaded only for an image
            small enough to preview.
        after_bytes: Content of the newer version, same condition.
        preview_skipped: Whether a preview was left out because the file is too
            large. Said out loud rather than shown as a blank panel.
    """

    path: str = ""
    before: BlobFacts = BlobFacts()
    after: BlobFacts = BlobFacts()
    is_image: bool = False
    before_bytes: bytes | None = None
    after_bytes: bytes | None = None
    preview_skipped: bool = False

    @property
    def size_delta(self) -> int:
        """
        Returns how much the file grew.

        Returns:
            int: Difference in bytes, negative when it shrank. Zero when one of
                the sides does not exist, because "grew by its whole size" is not
                a useful way to describe an added file.
        """

        if not self.before.exists or not self.after.exists:
            return 0
        return self.after.size - self.before.size


def is_image_path(path: str) -> bool:
    """
    Reports whether a path looks like a picture.

    Args:
        path: File path.

    Returns:
        bool: True when the suffix is one Qt can be expected to render.
    """

    return bool(path) and Path(path).suffix.lower() in IMAGE_SUFFIXES


def comparison_sides(
    target: str, rev_a: str = "", rev_b: str = ""
) -> tuple[tuple[str, str], tuple[str, str]]:
    """
    Works out which two versions a comparison target refers to.

    Args:
        target: One of the ``TARGET_*`` constants.
        rev_a: Left revision, for commit and branch comparisons.
        rev_b: Right revision, for commit and branch comparisons.

    Returns:
        tuple: ``((source, revision), (source, revision))`` for the older and the
            newer side.
    """

    chosen = normalize_target(target)
    if chosen == TARGET_WORKTREE_INDEX:
        return (SOURCE_INDEX, ""), (SOURCE_WORKTREE, "")
    if chosen == TARGET_INDEX_HEAD:
        return (SOURCE_REVISION, "HEAD"), (SOURCE_INDEX, "")
    if chosen in {TARGET_COMMITS, TARGET_BRANCHES}:
        return (SOURCE_REVISION, rev_a), (SOURCE_REVISION, rev_b)
    # TARGET_WORKTREE_HEAD and anything unrecognised.
    return (SOURCE_REVISION, "HEAD"), (SOURCE_WORKTREE, "")


def worktree_facts(repo: Path | str, path: str) -> BlobFacts:
    """
    Reads size and modification time from the filesystem.

    Args:
        repo: Working tree path.
        path: File path relative to the working tree root.

    Returns:
        BlobFacts: What the file system reports, or a non-existent side.
    """

    target = Path(repo) / path
    try:
        stat = target.stat()
    except OSError:
        return BlobFacts(source=SOURCE_WORKTREE)
    if not target.is_file():
        return BlobFacts(source=SOURCE_WORKTREE)
    return BlobFacts(
        exists=True, size=stat.st_size, modified=stat.st_mtime, source=SOURCE_WORKTREE
    )


def blob_size(repo: Path | str, spec: str) -> int | None:
    """
    Asks git how large a stored version is.

    Args:
        repo: Working tree path.
        spec: Object spec such as ``HEAD:path`` or ``:path``.

    Returns:
        int | None: Size in bytes, or None when the object does not exist.
    """

    try:
        result = run(["cat-file", "-s", spec], cwd=repo, timeout=GIT_TIMEOUT_LOCAL, read_only=True)
    except UnsafeGitArgument:
        return None
    if result.failed:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def revision_time(repo: Path | str, revision: str, path: str) -> float | None:
    """
    Finds when a path last changed, up to a revision.

    Args:
        repo: Working tree path.
        revision: Revision to look back from.
        path: File path relative to the working tree root.

    Returns:
        float | None: Commit timestamp, or None when nothing is recorded.
    """

    if not is_valid_revision(revision) or not is_safe_argument(path):
        return None
    try:
        result = run(
            ["log", "-1", "--format=%ct", revision, "--", path],
            cwd=repo,
            timeout=GIT_TIMEOUT_LOCAL,
            read_only=True,
        )
    except UnsafeGitArgument:
        return None
    if result.failed:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def index_time(repo: Path | str, path: str) -> float | None:
    """
    Reads the modification time git recorded when the file was staged.

    The index is the one place where git keeps a filesystem timestamp, and it is
    the closest thing there is to "when the staged version was written".

    Args:
        repo: Working tree path.
        path: File path relative to the working tree root.

    Returns:
        float | None: Timestamp, or None when the path is not staged.
    """

    if not is_safe_argument(path):
        return None
    try:
        result = run(
            ["ls-files", "--debug", "--", path],
            cwd=repo,
            timeout=GIT_TIMEOUT_LOCAL,
            read_only=True,
        )
    except UnsafeGitArgument:
        return None
    if result.failed:
        return None
    found = _MTIME_LINE.search(result.stdout)
    if not found:
        return None
    stamp = int(found.group(1))
    # A freshly written index entry can carry a zero mtime, which means "not
    # recorded" rather than "1 January 1970".
    return float(stamp) if stamp > 0 else None


def side_facts(repo: Path | str, path: str, source: str, revision: str) -> BlobFacts:
    """
    Collects what is known about one side of a comparison.

    Args:
        repo: Working tree path.
        path: File path relative to the working tree root.
        source: One of the ``SOURCE_*`` constants.
        revision: Revision, when the source is one.

    Returns:
        BlobFacts: Size, date and where they came from.
    """

    if source == SOURCE_WORKTREE:
        return worktree_facts(repo, path)

    if source == SOURCE_INDEX:
        size = blob_size(repo, f":{path}") if is_safe_argument(path) else None
        if size is None:
            return BlobFacts(source=SOURCE_INDEX)
        return BlobFacts(
            exists=True, size=size, modified=index_time(repo, path), source=SOURCE_INDEX
        )

    if source == SOURCE_REVISION and revision:
        if not is_valid_revision(revision) or not is_safe_argument(path):
            return BlobFacts(source=SOURCE_REVISION, revision=revision)
        size = blob_size(repo, f"{revision}:{path}")
        if size is None:
            return BlobFacts(source=SOURCE_REVISION, revision=revision)
        return BlobFacts(
            exists=True,
            size=size,
            modified=revision_time(repo, revision, path),
            source=SOURCE_REVISION,
            revision=revision,
        )

    return BlobFacts(source=SOURCE_NONE)


def side_bytes(repo: Path | str, path: str, source: str, revision: str) -> bytes | None:
    """
    Reads the content of one side.

    Args:
        repo: Working tree path.
        path: File path relative to the working tree root.
        source: One of the ``SOURCE_*`` constants.
        revision: Revision, when the source is one.

    Returns:
        bytes | None: Raw content, or None when it is not there or unreadable.
    """

    if source == SOURCE_WORKTREE:
        target = Path(repo) / path
        try:
            return target.read_bytes() if target.is_file() else None
        except OSError:
            return None

    if not is_safe_argument(path):
        return None
    if source == SOURCE_INDEX:
        spec = f":{path}"
    elif source == SOURCE_REVISION and revision and is_valid_revision(revision):
        spec = f"{revision}:{path}"
    else:
        return None

    try:
        ok, payload = run_binary(["show", spec], cwd=repo)
    except UnsafeGitArgument:
        return None
    return payload if ok else None


def compare(
    repo: Path | str,
    path: str,
    target: str = TARGET_WORKTREE_HEAD,
    rev_a: str = "",
    rev_b: str = "",
    untracked: bool = False,
    load_previews: bool = True,
) -> BinaryComparison:
    """
    Builds the comparison of a file that has no readable diff.

    Args:
        repo: Working tree path.
        path: File path relative to the working tree root.
        target: One of the ``TARGET_*`` constants.
        rev_a: Left revision, for commit and branch comparisons.
        rev_b: Right revision, for commit and branch comparisons.
        untracked: Whether git does not track the file yet, in which case there
            is no older side to look for.
        load_previews: Whether to read the content of an image for previewing.

    Returns:
        BinaryComparison: Both sides, with pictures when the file is one.
    """

    if untracked:
        before_source: tuple[str, str] = (SOURCE_NONE, "")
        after_source: tuple[str, str] = (SOURCE_WORKTREE, "")
    else:
        before_source, after_source = comparison_sides(target, rev_a, rev_b)

    before = side_facts(repo, path, *before_source)
    after = side_facts(repo, path, *after_source)
    image = is_image_path(path)

    before_payload: bytes | None = None
    after_payload: bytes | None = None
    skipped = False
    if image and load_previews:
        for facts, source in ((before, before_source), (after, after_source)):
            if not facts.exists:
                continue
            if facts.size > MAX_PREVIEW_BYTES:
                skipped = True
                continue
            payload = side_bytes(repo, path, *source)
            if facts is before:
                before_payload = payload
            else:
                after_payload = payload

    return BinaryComparison(
        path=path,
        before=before,
        after=after,
        is_image=image,
        before_bytes=before_payload,
        after_bytes=after_payload,
        preview_skipped=skipped,
    )


def format_size(size: int) -> str:
    """
    Renders a byte count the way a file manager would.

    Args:
        size: Number of bytes.

    Returns:
        str: A short, readable size.
    """

    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024:
            # One decimal below ten, none above: "9.4 MB" but "312 MB".
            return f"{value:.1f} {unit}" if value < 10 else f"{value:.0f} {unit}"
    return f"{value:.0f} PB"
