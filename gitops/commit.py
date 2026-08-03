"""
Creating commits.

The message always travels through standard input (``git commit -F -``), never as
a command-line argument. That keeps a message of any length working and removes
the whole question of what a quote, a newline or a semicolon in the text might do.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gitops.refname import is_valid_revision
from gitops.runner import GitResult, run
from gitops.stage import checked_paths

MAX_SUMMARY_LENGTH = 72


@dataclass(frozen=True, slots=True)
class CommitDraft:
    """
    What the user typed into the commit box.

    Attributes:
        summary: First line of the message.
        description: Optional body, separated from the summary by a blank line.
        amend: Whether to replace the previous commit instead of adding one.
    """

    summary: str
    description: str = ""
    amend: bool = False

    @property
    def is_valid(self) -> bool:
        """
        Reports whether the draft can be committed.

        Returns:
            bool: True when the summary carries something other than whitespace.
        """

        return bool(self.summary.strip())

    @property
    def message(self) -> str:
        """
        Assembles the full commit message.

        Returns:
            str: Summary, then a blank line, then the body when present.
        """

        summary = " ".join(self.summary.split())
        body = self.description.strip()
        if not body:
            return summary + "\n"
        return f"{summary}\n\n{body}\n"


def commit(repo: Path | str, draft: CommitDraft) -> GitResult:
    """
    Commits whatever is currently staged.

    Args:
        repo: Working tree path.
        draft: Message and options.

    Returns:
        GitResult: Outcome. A draft without a summary is refused here rather than
            letting git create a commit with an empty message.
    """

    if not draft.is_valid:
        return GitResult(returncode=-1, stdout="", stderr="empty commit summary", args=("commit",))
    args = ["commit", "-F", "-"]
    if draft.amend:
        args.append("--amend")
    return run(args, cwd=repo, input_text=draft.message)


def commit_staged_paths(repo: Path | str, draft: CommitDraft, paths: list[str]) -> GitResult:
    """
    Commits only the given paths, leaving other staged changes staged.

    Args:
        repo: Working tree path.
        draft: Message and options.
        paths: Paths relative to the working tree root.

    Returns:
        GitResult: Outcome.
    """

    if not draft.is_valid:
        return GitResult(returncode=-1, stdout="", stderr="empty commit summary", args=("commit",))
    checked = checked_paths(paths)
    if checked is None:
        return GitResult(returncode=-1, stdout="", stderr="unsafe path", args=("commit",))
    args = ["commit", "-F", "-"]
    if draft.amend:
        args.append("--amend")
    args.extend(["--", *checked])
    return run(args, cwd=repo, input_text=draft.message)


def has_staged_changes(repo: Path | str) -> bool:
    """
    Reports whether anything is staged.

    Args:
        repo: Working tree path.

    Returns:
        bool: True when the index differs from HEAD.
    """

    head = run(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=repo, read_only=True)
    if head.failed or not head.stdout.strip():
        # No commits yet: anything in the index counts as staged.
        listed = run(["diff", "--cached", "--name-only"], cwd=repo, read_only=True)
        return bool(listed.stdout.strip())
    result = run(["diff", "--cached", "--quiet"], cwd=repo, read_only=True)
    return result.returncode == 1


def previous_message(repo: Path | str) -> CommitDraft | None:
    """
    Reads the last commit message, for pre-filling the amend box.

    Args:
        repo: Working tree path.

    Returns:
        CommitDraft | None: Draft holding the previous summary and body, or None
            when there is no commit yet.
    """

    result = run(["log", "-1", "--format=%B"], cwd=repo, read_only=True)
    if result.failed:
        return None
    text = result.stdout.strip("\n")
    if not text:
        return None
    summary, _, body = text.partition("\n")
    return CommitDraft(summary=summary, description=body.strip("\n"), amend=True)


def commit_details(repo: Path | str, revision: str) -> dict[str, str] | None:
    """
    Reads the metadata of one commit.

    Args:
        repo: Working tree path.
        revision: Revision to describe.

    Returns:
        dict[str, str] | None: Keys ``oid``, ``short``, ``author``, ``email``,
            ``date``, ``subject`` and ``body``, or None when unreadable.
    """

    if not is_valid_revision(revision):
        return None
    separator = "\x1f"
    fmt = separator.join(["%H", "%h", "%an", "%ae", "%aI", "%s", "%b"])
    result = run(["show", "--no-patch", f"--format={fmt}", revision], cwd=repo, read_only=True)
    if result.failed:
        return None
    parts = result.stdout.rstrip("\n").split(separator)
    if len(parts) < 7:
        return None
    return {
        "oid": parts[0],
        "short": parts[1],
        "author": parts[2],
        "email": parts[3],
        "date": parts[4],
        "subject": parts[5],
        "body": parts[6].strip("\n"),
    }


def changed_paths(repo: Path | str, revision: str) -> list[str]:
    """
    Lists the files one commit touched.

    Args:
        repo: Working tree path.
        revision: Revision to inspect.

    Returns:
        list[str]: Paths, empty when the commit cannot be read.
    """

    if not is_valid_revision(revision):
        return []
    result = run(
        ["show", "--no-color", "--name-only", "--format=", "-z", revision],
        cwd=repo,
        read_only=True,
    )
    if result.failed:
        return []
    return [path for path in result.stdout.split("\x00") if path]
