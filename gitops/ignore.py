"""
Adding entries to ``.gitignore``.

Right-clicking a file and saying "never mention this again" is one of the few
things people reach for constantly, and hand-editing ``.gitignore`` for it is
the kind of small friction that adds up.

Two things here are less obvious than they look:

* **A file name is not a pattern.** ``.gitignore`` treats ``*``, ``?``, ``[``,
  a leading ``#`` and a leading ``!`` as syntax, so a file actually called
  ``report[2].txt`` has to be written with those characters escaped, or the
  entry silently matches something else, or nothing.
* **A pattern without a slash matches at every depth.** Ignoring a top-level
  ``notes.txt`` by writing ``notes.txt`` would also ignore ``docs/notes.txt``.
  Entries for one specific file are therefore anchored with a leading slash,
  which is what the user pointing at that one row meant.

The file itself is written here rather than through git, because git has no
command for it. Whether something is *already* ignored is a question only git
can answer, so that one goes through ``git check-ignore``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from constants import GIT_TIMEOUT_LOCAL
from gitops.runner import UnsafeGitArgument, run

GITIGNORE_NAME = ".gitignore"

KIND_FILE = "file"
KIND_EXTENSION = "extension"
KIND_FOLDER = "folder"

# Characters gitignore reads as syntax. A literal file name containing one of
# them has to escape it.
_SPECIAL = "*?[]\\"


@dataclass(frozen=True, slots=True)
class IgnoreSuggestion:
    """
    One entry the context menu can offer.

    Attributes:
        pattern: The line that would be written to ``.gitignore``.
        kind: One of the ``KIND_*`` constants, so the menu can pick a wording.
        subject: What the entry is about, for the menu label: the file name, the
            extension, or the folder.
    """

    pattern: str
    kind: str
    subject: str


@dataclass(frozen=True, slots=True)
class IgnoreOutcome:
    """
    What happened when an entry was added.

    Attributes:
        ok: Whether ``.gitignore`` now contains the pattern.
        pattern: The pattern in question.
        already_present: Whether it was already there, in which case nothing was
            written. Not a failure, but worth saying rather than pretending
            something changed.
        created: Whether the file had to be created.
        error_key: Translation key when the write failed.
    """

    ok: bool
    pattern: str = ""
    already_present: bool = False
    created: bool = False
    error_key: str = ""


def escape_literal(text: str) -> str:
    """
    Turns a literal name into a pattern matching exactly that name.

    Args:
        text: File or folder name as it is on disk.

    Returns:
        str: The name with gitignore's syntax characters escaped.
    """

    escaped = "".join(f"\\{char}" if char in _SPECIAL else char for char in text)
    # A leading '#' would be a comment and a leading '!' would be a negation.
    if escaped.startswith(("#", "!")):
        escaped = f"\\{escaped}"
    # Trailing spaces are stripped by git unless they are escaped.
    if escaped.endswith(" "):
        escaped = f"{escaped[:-1]}\\ "
    return escaped


def suggestions(relative_path: str) -> list[IgnoreSuggestion]:
    """
    Works out what could sensibly be ignored for one path.

    Args:
        relative_path: Path of the file inside the repository, with forward
            slashes, as git reports it.

    Returns:
        list[IgnoreSuggestion]: The offers, most specific first. Empty when the
            path is unusable.
    """

    cleaned = relative_path.strip().replace("\\", "/").strip("/")
    if not cleaned or cleaned == "." or ".." in cleaned.split("/"):
        return []

    parts = cleaned.split("/")
    name = parts[-1]
    found: list[IgnoreSuggestion] = [
        IgnoreSuggestion(
            pattern="/" + "/".join(escape_literal(part) for part in parts),
            kind=KIND_FILE,
            subject=name,
        )
    ]

    suffix = Path(name).suffix
    # A dotfile such as ".env" is all suffix and no stem; ignoring "*.env" there
    # would be a different thing from ignoring that one file.
    if suffix and len(suffix) > 1 and Path(name).stem:
        found.append(
            IgnoreSuggestion(
                pattern=f"*{escape_literal(suffix)}",
                kind=KIND_EXTENSION,
                subject=f"*{suffix}",
            )
        )

    if len(parts) > 1:
        folder = "/".join(escape_literal(part) for part in parts[:-1])
        found.append(
            IgnoreSuggestion(
                pattern=f"/{folder}/",
                kind=KIND_FOLDER,
                subject=f"{'/'.join(parts[:-1])}/",
            )
        )

    return found


def existing_patterns(repository: Path | str) -> list[str]:
    """
    Reads the patterns already in the repository's ``.gitignore``.

    Args:
        repository: Working tree root.

    Returns:
        list[str]: One entry per non-empty, non-comment line, stripped. An
            unreadable or missing file reads as an empty list.
    """

    target = Path(repository) / GITIGNORE_NAME
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = []
    for raw in content.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def add_pattern(repository: Path | str, pattern: str) -> IgnoreOutcome:
    """
    Appends one pattern to the repository's ``.gitignore``.

    Args:
        repository: Working tree root.
        pattern: The line to add.

    Returns:
        IgnoreOutcome: What happened. A pattern that was already there is
            reported as such rather than written twice.
    """

    cleaned = pattern.strip()
    if not cleaned:
        return IgnoreOutcome(ok=False, error_key="ignore.pattern_empty")

    root = Path(repository)
    if not root.is_dir():
        return IgnoreOutcome(ok=False, pattern=cleaned, error_key="repo.missing")

    target = root / GITIGNORE_NAME
    existed = target.is_file()
    try:
        if existed:
            # newline="" keeps the line endings as they are on disk. Reading
            # normally translates CRLF to LF, and the file would then look like
            # an LF file that is about to be appended to with the wrong ending.
            with target.open("r", encoding="utf-8", newline="") as handle:
                content = handle.read()
        else:
            content = ""
    except (OSError, UnicodeDecodeError):
        return IgnoreOutcome(ok=False, pattern=cleaned, error_key="ignore.unreadable")

    if cleaned in [line.strip() for line in content.splitlines()]:
        return IgnoreOutcome(ok=True, pattern=cleaned, already_present=True)

    # Appending an LF to a file written with CRLF would leave it with mixed line
    # endings, which shows up as a whole-file change in the next diff.
    newline = "\r\n" if "\r\n" in content else "\n"
    prefix = "" if not content or content.endswith(("\n", "\r")) else newline
    try:
        with target.open("a", encoding="utf-8", newline="") as handle:
            handle.write(f"{prefix}{cleaned}{newline}")
    except OSError:
        return IgnoreOutcome(ok=False, pattern=cleaned, error_key="ignore.write_failed")

    return IgnoreOutcome(ok=True, pattern=cleaned, created=not existed)


def is_ignored(repository: Path | str, relative_path: str) -> bool:
    """
    Asks git whether a path is already ignored.

    Only git knows: the answer depends on every ``.gitignore`` up the tree, on
    ``.git/info/exclude`` and on the global excludes file.

    Args:
        repository: Working tree root.
        relative_path: Path inside the repository.

    Returns:
        bool: True when git would ignore it. A failure to ask reads as False,
            because offering an entry that turns out to be redundant is a much
            smaller problem than hiding one that was needed.
    """

    if not relative_path.strip():
        return False
    try:
        result = run(
            ["check-ignore", "--quiet", "--", relative_path],
            cwd=repository,
            timeout=GIT_TIMEOUT_LOCAL,
            read_only=True,
        )
    except UnsafeGitArgument:
        return False
    # git answers 0 for ignored, 1 for not ignored, and something else for a
    # real problem.
    return result.returncode == 0
