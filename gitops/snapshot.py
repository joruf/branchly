"""
The files of one commit, and getting them back out.

Three things, all about one saved state of a project:

* **Which files a commit touched**, with what happened to each. Read from
  ``git show --raw`` rather than ``--name-status`` because the raw form carries
  the file mode, and the mode is what tells a file from a link or a submodule.
* **Downloading** a file as it was in that commit, into a folder of the user's
  choosing. The content goes through ``git cat-file --filters``, which applies
  the same line-ending and smudge rules a checkout would, so what lands on disk
  is the file a checkout would have written, not git's internal copy of it.
* **Putting that version back** into the working tree. Done with ``git restore
  --worktree``, which leaves the index alone. The result is an ordinary,
  uncommitted change: visible in the changes list, comparable, and committed or
  thrown away like any other.

"The version in this commit" means the file after the commit. A file the commit
deleted has no such version, so for it the version just before the commit is
used, which is the last one that existed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from gitops.refname import is_safe_argument, is_valid_revision
from gitops.runner import run, run_binary
from gitops.status import (
    CHANGE_ADDED,
    CHANGE_COPIED,
    CHANGE_DELETED,
    CHANGE_MODIFIED,
    CHANGE_RENAMED,
    CHANGE_TYPE_CHANGED,
)

MODE_FILE = "100644"
MODE_EXECUTABLE = "100755"
MODE_LINK = "120000"
MODE_SUBMODULE = "160000"

ERROR_READ = "snapshot.read_failed"
ERROR_WRITE = "snapshot.write_failed"
ERROR_UNSAFE = "snapshot.unsafe_path"
ERROR_NOT_A_FILE = "snapshot.not_a_file"
ERROR_RESTORE = "snapshot.restore_failed"

# Paths handed to one ``git restore`` call. Well below any platform's command
# line limit even for long paths, and a failed batch is retried file by file.
_RESTORE_BATCH = 100

_RAW_KINDS = {
    "A": CHANGE_ADDED,
    "M": CHANGE_MODIFIED,
    "D": CHANGE_DELETED,
    "R": CHANGE_RENAMED,
    "C": CHANGE_COPIED,
    "T": CHANGE_TYPE_CHANGED,
}

# Characters no file or folder name may contain on at least one of the systems
# Branchly runs on. Only used for the folder Branchly names itself.
_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True, slots=True)
class CommitFile:
    """
    One file a commit touched.

    Attributes:
        path: Path after the commit, or the removed path for a deletion.
        old_path: Path before the commit when the file was renamed or copied.
        kind: One of the ``CHANGE_*`` constants from ``gitops.status``.
        mode: Git file mode of the version this module would fetch.
        added: Lines added, 0 for a binary file.
        removed: Lines removed, 0 for a binary file.
        binary: Whether git counted no lines because the content is not text.
    """

    path: str
    old_path: str = ""
    kind: str = CHANGE_MODIFIED
    mode: str = MODE_FILE
    added: int = 0
    removed: int = 0
    binary: bool = False

    @property
    def is_submodule(self) -> bool:
        """
        Reports whether this entry is another repository rather than a file.

        Returns:
            bool: True for a submodule pointer.
        """

        return self.mode == MODE_SUBMODULE

    @property
    def is_link(self) -> bool:
        """
        Reports whether this entry is a symbolic link.

        Returns:
            bool: True for a link.
        """

        return self.mode == MODE_LINK

    @property
    def downloadable(self) -> bool:
        """
        Reports whether the entry can be saved as a file somewhere else.

        A link only makes sense where it points, and a submodule is a pointer to
        a whole other project, so neither is written into a download folder.

        Returns:
            bool: True for an ordinary or executable file.
        """

        return self.mode in (MODE_FILE, MODE_EXECUTABLE)

    @property
    def restorable(self) -> bool:
        """
        Reports whether the entry can be put back into the working tree.

        Returns:
            bool: True for anything but a submodule.
        """

        return not self.is_submodule


@dataclass(frozen=True, slots=True)
class CommitFiles:
    """
    Everything one commit touched.

    Attributes:
        revision: The commit, as passed in.
        files: The files, in git's order.
        error_key: Translation key when the commit could not be read.
    """

    revision: str
    files: list[CommitFile] = field(default_factory=list)
    error_key: str = ""

    @property
    def ok(self) -> bool:
        """
        Reports whether the list could be read.

        Returns:
            bool: True without an error.
        """

        return not self.error_key


@dataclass(slots=True)
class ExportOutcome:
    """
    What a download wrote.

    Attributes:
        folder: The folder the files went into.
        written: Paths written, relative to the folder.
        skipped: ``(path, translation key)`` for every file left out.
    """

    folder: Path
    written: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class RestoreOutcome:
    """
    What putting an old version back did.

    Attributes:
        restored: Paths now holding the chosen version.
        failed: ``(path, translation key)`` for every file that could not be.
        detail: Git's own words for the last failure, for the error notice.
    """

    restored: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    detail: str = ""

    @property
    def ok(self) -> bool:
        """
        Reports whether every file was restored.

        Returns:
            bool: True without failures.
        """

        return not self.failed


# --------------------------------------------------------------------- listing


def parse_raw(payload: str) -> list[CommitFile]:
    """
    Reads the output of ``git show --raw -z``.

    Each entry is a header ``:old_mode new_mode old_oid new_oid status``
    followed by one path, or two for a rename or copy, all NUL separated.

    Args:
        payload: Standard output of the command.

    Returns:
        list[CommitFile]: One entry per file, without line counts.
    """

    parts = payload.split("\x00")
    files: list[CommitFile] = []
    index = 0
    while index < len(parts):
        header = parts[index].strip()
        index += 1
        if not header.startswith(":"):
            continue
        fields_ = header[1:].split()
        if len(fields_) < 5 or index >= len(parts):
            continue
        old_mode, new_mode, status = fields_[0], fields_[1], fields_[4]
        kind = _RAW_KINDS.get(status[:1], CHANGE_MODIFIED)
        first = parts[index]
        index += 1
        if kind in (CHANGE_RENAMED, CHANGE_COPIED) and index < len(parts):
            files.append(CommitFile(path=parts[index], old_path=first, kind=kind, mode=new_mode))
            index += 1
            continue
        mode = old_mode if kind == CHANGE_DELETED else new_mode
        files.append(CommitFile(path=first, kind=kind, mode=mode))
    return files


def parse_numstat(payload: str) -> dict[str, tuple[int, int, bool]]:
    """
    Reads the output of ``git show --numstat -z``.

    Args:
        payload: Standard output of the command.

    Returns:
        dict[str, tuple[int, int, bool]]: Path after the commit mapped to
            ``(added, removed, binary)``.
    """

    counts: dict[str, tuple[int, int, bool]] = {}
    entries = payload.split("\x00")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        parts = entry.split("\t")
        if len(parts) < 3:
            continue
        added_raw, removed_raw, path = parts[0], parts[1], parts[2]
        if not path:
            # A rename puts both paths in the next two entries.
            if index + 1 >= len(entries):
                break
            path = entries[index + 1]
            index += 2
        binary = added_raw == "-" or removed_raw == "-"
        try:
            counts[path] = (int(added_raw), int(removed_raw), binary)
        except ValueError:
            counts[path] = (0, 0, binary)
    return counts


def list_files(repo: Path | str, revision: str) -> CommitFiles:
    """
    Lists every file a commit touched.

    A merge is read against its first parent, the same way the diff of a merge
    is shown: what the merge brought onto the line of work it was made on.

    Args:
        repo: Working tree path.
        revision: Commit to read.

    Returns:
        CommitFiles: The files, or an error key.
    """

    if not is_valid_revision(revision):
        return CommitFiles(revision=revision, error_key=ERROR_READ)
    common = ["show", "--no-color", "--no-ext-diff", "--first-parent", "-M", "-z", "--format="]
    raw = run([*common, "--raw", revision], cwd=repo, read_only=True)
    if raw.failed:
        return CommitFiles(revision=revision, error_key=ERROR_READ)
    files = parse_raw(raw.stdout)

    stats = run([*common, "--numstat", revision], cwd=repo, read_only=True)
    counts = parse_numstat(stats.stdout) if not stats.failed else {}
    counted: list[CommitFile] = []
    for item in files:
        added, removed, binary = counts.get(item.path, (0, 0, False))
        counted.append(
            CommitFile(
                path=item.path,
                old_path=item.old_path,
                kind=item.kind,
                mode=item.mode,
                added=added,
                removed=removed,
                binary=binary,
            )
        )
    return CommitFiles(revision=revision, files=counted)


def source_revision(revision: str, item: CommitFile) -> str:
    """
    Names the commit a file's version is taken from.

    Args:
        revision: The commit the user picked.
        item: One of its files.

    Returns:
        str: The commit itself, or its first parent for a deleted file.
    """

    return f"{revision}^" if item.kind == CHANGE_DELETED else revision


def is_safe_path(path: str) -> bool:
    """
    Reports whether a path from git may be joined to a local folder.

    Git never records an absolute path or a ``..`` step, but a download writes
    wherever the path points, so that is checked here rather than assumed.

    Args:
        path: Path relative to the working tree root, with ``/`` separators.

    Returns:
        bool: True when the path stays inside whatever folder it is joined to.
    """

    if not path or "\x00" in path or "\\" in path:
        return False
    pure = PurePosixPath(path)
    if pure.is_absolute() or re.match(r"^[A-Za-z]:", path):
        return False
    return all(part not in ("", ".", "..") for part in pure.parts)


# ------------------------------------------------------------------- download


def folder_name(project: str, revision: str) -> str:
    """
    Builds the name of the folder a download goes into.

    Project name and short commit id, so two downloads from different commits
    never end up mixed in one folder and the folder says where it came from.

    Args:
        project: Name of the project.
        revision: Commit the files come from.

    Returns:
        str: A name that is valid on every supported system.
    """

    name = _UNSAFE_NAME.sub("_", project).strip(" .") or "branchly"
    return f"{name}-{revision[:7]}"


def unique_folder(parent: Path, name: str) -> Path:
    """
    Picks a folder that does not exist yet.

    A second download of the same commit gets ``name-2`` rather than writing
    over the first, which may have been edited since.

    Args:
        parent: Folder the user chose.
        name: Preferred name.

    Returns:
        Path: A path inside ``parent`` that is free.
    """

    candidate = parent / name
    number = 2
    while candidate.exists():
        candidate = parent / f"{name}-{number}"
        number += 1
    return candidate


def file_bytes(repo: Path | str, revision: str, path: str) -> bytes | None:
    """
    Reads one file's content the way a checkout would write it.

    ``--filters`` applies line-ending conversion and smudge filters such as Git
    LFS. When a filter is configured but missing on this machine that fails, and
    the stored content is the better answer than none.

    Args:
        repo: Working tree path.
        revision: Commit to read from.
        path: Path inside the commit.

    Returns:
        bytes | None: The content, or None when it could not be read.
    """

    spec = f"{revision}:{path}"
    if not is_safe_argument(spec):
        return None
    ok, payload = run_binary(["cat-file", "--filters", spec], cwd=repo)
    if ok:
        return payload
    ok, payload = run_binary(["cat-file", "blob", spec], cwd=repo)
    return payload if ok else None


def export(
    repo: Path | str,
    revision: str,
    files: list[CommitFile],
    destination: Path | str,
    project: str,
) -> ExportOutcome:
    """
    Saves files as they were in a commit.

    They go into a new folder inside ``destination``, keeping the paths they
    have in the project, so two files of the same name never collide.

    Args:
        repo: Working tree path.
        revision: Commit the user picked.
        files: Files to save.
        destination: Folder the user chose.
        project: Project name, used for the new folder.

    Returns:
        ExportOutcome: The folder and what went into it.
    """

    folder = unique_folder(Path(destination), folder_name(project, revision))
    outcome = ExportOutcome(folder=folder)
    if not is_valid_revision(revision):
        outcome.skipped = [(item.path, ERROR_READ) for item in files]
        return outcome

    for item in files:
        if not item.downloadable:
            outcome.skipped.append((item.path, ERROR_NOT_A_FILE))
            continue
        if not is_safe_path(item.path):
            outcome.skipped.append((item.path, ERROR_UNSAFE))
            continue
        content = file_bytes(repo, source_revision(revision, item), item.path)
        if content is None:
            outcome.skipped.append((item.path, ERROR_READ))
            continue
        target = folder.joinpath(*PurePosixPath(item.path).parts)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            if item.mode == MODE_EXECUTABLE and os.name != "nt":
                target.chmod(target.stat().st_mode | 0o111)
        except OSError:
            outcome.skipped.append((item.path, ERROR_WRITE))
            continue
        outcome.written.append(item.path)
    return outcome


# -------------------------------------------------------------------- restore


def _status_paths(repo: Path | str) -> set[str]:
    """
    Collects every path with a change that is not committed.

    Args:
        repo: Working tree path.

    Returns:
        set[str]: Paths changed, staged, untracked or in conflict. Both sides of
            a staged rename are included.
    """

    result = run(
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=repo, read_only=True
    )
    if result.failed:
        return set()
    paths: set[str] = set()
    entries = result.stdout.split("\x00")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        paths.add(entry[3:])
        if entry[0] in "RC" and index < len(entries):
            # The path before the rename follows as its own entry.
            paths.add(entries[index])
            index += 1
    return paths


def _tracked_paths(repo: Path | str) -> set[str]:
    """
    Collects every path git tracks right now.

    Args:
        repo: Working tree path.

    Returns:
        set[str]: Paths in the index.
    """

    result = run(["ls-files", "-z"], cwd=repo, read_only=True)
    if result.failed:
        return set()
    return {path for path in result.stdout.split("\x00") if path}


def at_risk(repo: Path | str, paths: list[str]) -> list[str]:
    """
    Finds the files whose current content nothing else has a copy of.

    Those are the ones replacing would lose for good: a change not committed
    yet, or a file on disk git does not track at all (new, or ignored).

    Args:
        repo: Working tree path.
        paths: Files about to be replaced.

    Returns:
        list[str]: The subset of ``paths`` at risk, in the given order.
    """

    changed = _status_paths(repo)
    tracked = _tracked_paths(repo)
    root = Path(repo)
    risky: list[str] = []
    for path in paths:
        untracked_on_disk = (
            path not in tracked and is_safe_path(path) and (root / path).exists()
        )
        if path in changed or untracked_on_disk:
            risky.append(path)
    return risky


def _restore_batch(repo: Path | str, source: str, paths: list[str]) -> tuple[bool, str]:
    """
    Runs one ``git restore`` for a group of paths from the same commit.

    Args:
        repo: Working tree path.
        source: Commit to take the content from.
        paths: Paths to restore.

    Returns:
        tuple[bool, str]: Success, and git's message when it failed.
    """

    # ``:(literal)`` keeps a name with ``*`` or ``?`` from being read as a
    # pattern and replacing more than the file that was ticked.
    specs = [f":(literal){path}" for path in paths]
    result = run(["restore", f"--source={source}", "--worktree", "--", *specs], cwd=repo)
    return not result.failed, result.message


def restore(repo: Path | str, revision: str, files: list[CommitFile]) -> RestoreOutcome:
    """
    Puts the versions of a commit into the working tree.

    Only the files on disk change. Nothing is staged and nothing committed, so
    the result shows up as ordinary changes that can be looked at, committed or
    thrown away.

    Args:
        repo: Working tree path.
        revision: Commit the user picked.
        files: Files to put back.

    Returns:
        RestoreOutcome: What was restored and what was not.
    """

    outcome = RestoreOutcome()
    if not is_valid_revision(revision):
        outcome.failed = [(item.path, ERROR_READ) for item in files]
        return outcome

    groups: dict[str, list[str]] = {}
    for item in files:
        if not item.restorable:
            outcome.failed.append((item.path, ERROR_NOT_A_FILE))
        elif not is_safe_path(item.path) or not is_safe_argument(item.path):
            outcome.failed.append((item.path, ERROR_UNSAFE))
        else:
            groups.setdefault(source_revision(revision, item), []).append(item.path)

    for source, paths in groups.items():
        for start in range(0, len(paths), _RESTORE_BATCH):
            batch = paths[start : start + _RESTORE_BATCH]
            ok, message = _restore_batch(repo, source, batch)
            if ok:
                outcome.restored.extend(batch)
                continue
            # One bad path fails the whole call, so find out which it was
            # rather than reporting a hundred files as failed.
            for path in batch:
                single_ok, single_message = _restore_batch(repo, source, [path])
                if single_ok:
                    outcome.restored.append(path)
                else:
                    outcome.failed.append((path, ERROR_RESTORE))
                    outcome.detail = single_message or message
    return outcome
