"""
Reading history and laying out the commit graph.

Git can draw an ASCII graph itself, but parsing that back into clickable shapes
is fragile. Instead the raw commit list with its parent ids is read here and the
lanes are assigned in Python, which gives the view exact coordinates for every
dot and every line.

The lane algorithm is the usual one: each lane remembers which commit it is
waiting for. A commit takes the lane that was waiting for it, hands that lane to
its first parent, and gives any further parents a lane of their own — reusing a
freed slot before widening the graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from constants import GRAPH_PAGE_SIZE
from gitops.refname import is_safe_argument, is_valid_revision
from gitops.runner import run

_UNIT = "\x1f"
_FORMAT = _UNIT.join(["%H", "%h", "%P", "%an", "%ae", "%aI", "%ct", "%D", "%s"])


@dataclass(slots=True)
class Commit:
    """
    One commit as shown in the history and graph views.

    Attributes:
        oid: Full object id.
        short: Abbreviated object id.
        parents: Parent object ids, first parent first.
        author: Author name.
        email: Author email, used to look up an avatar.
        date: Author date in ISO 8601.
        timestamp: Committer timestamp as a Unix time.
        refs: Branch and tag names pointing here.
        subject: Subject line.
        is_head: Whether this is the checked-out commit.
        head_branch: Branch name HEAD points at, empty when detached elsewhere.
    """

    oid: str
    short: str = ""
    parents: tuple[str, ...] = ()
    author: str = ""
    email: str = ""
    date: str = ""
    timestamp: float = 0.0
    refs: tuple[str, ...] = ()
    subject: str = ""
    is_head: bool = False
    head_branch: str = ""

    @property
    def is_merge(self) -> bool:
        """
        Reports whether this commit brought two lines of work together.

        Returns:
            bool: True when it has more than one parent.
        """

        return len(self.parents) > 1


@dataclass(slots=True)
class GraphRow:
    """
    One row of the drawn graph.

    Attributes:
        commit: The commit on this row.
        lane: Lane index the dot sits in.
        passing: Lane indices whose line runs straight through this row without
            stopping, so the view knows where to draw vertical segments.
        edges: ``(parent_lane, parent_row)`` for each parent, letting the view
            draw a line from this dot to the parent's dot.
        width: Number of lanes in use at this row.
    """

    commit: Commit
    lane: int = 0
    passing: tuple[int, ...] = ()
    edges: tuple[tuple[int, int], ...] = ()
    width: int = 1


@dataclass(slots=True)
class History:
    """
    A page of history, already laid out.

    Attributes:
        rows: Graph rows, newest first.
        max_width: Widest lane count across all rows, for sizing the gutter.
        truncated: Whether more commits exist beyond this page.
        error_key: Translation key when the history could not be read.
    """

    rows: list[GraphRow] = field(default_factory=list)
    max_width: int = 1
    truncated: bool = False
    error_key: str = ""

    @property
    def ok(self) -> bool:
        """
        Reports whether the history was read successfully.

        Returns:
            bool: True when no error occurred.
        """

        return not self.error_key

    @property
    def commits(self) -> list[Commit]:
        """
        Returns just the commits, in display order.

        Returns:
            list[Commit]: Commits, newest first.
        """

        return [row.commit for row in self.rows]

    def row_of(self, oid: str) -> int:
        """
        Finds the row index of a commit.

        Args:
            oid: Full object id.

        Returns:
            int: Row index, or -1 when the commit is not on this page.
        """

        for index, row in enumerate(self.rows):
            if row.commit.oid == oid:
                return index
        return -1


def _parse_refs(decoration: str) -> tuple[tuple[str, ...], bool, str]:
    """
    Parses the ``%D`` decoration field.

    Args:
        decoration: Raw decoration text, e.g. ``HEAD -> main, origin/main, tag: v1``.

    Returns:
        tuple[tuple[str, ...], bool, str]: Ref names, whether HEAD is here, and
            the branch HEAD points at.
    """

    if not decoration.strip():
        return (), False, ""
    refs: list[str] = []
    is_head = False
    head_branch = ""
    for chunk in decoration.split(","):
        name = chunk.strip()
        if not name:
            continue
        if name.startswith("HEAD -> "):
            is_head = True
            head_branch = name[len("HEAD -> "):].strip()
            refs.append(head_branch)
            continue
        if name == "HEAD":
            is_head = True
            continue
        if name.startswith("tag: "):
            refs.append(name[len("tag: "):].strip())
            continue
        refs.append(name)
    return tuple(refs), is_head, head_branch


def parse_log(payload: str) -> list[Commit]:
    """
    Parses NUL-separated ``git log`` output.

    Args:
        payload: Raw output produced with ``_FORMAT`` and ``-z``.

    Returns:
        list[Commit]: Commits in the order git printed them.
    """

    commits: list[Commit] = []
    for entry in payload.split("\x00"):
        record = entry.strip("\n")
        if not record:
            continue
        parts = record.split(_UNIT)
        if len(parts) < 9:
            continue
        refs, is_head, head_branch = _parse_refs(parts[7])
        try:
            timestamp = float(parts[6])
        except ValueError:
            timestamp = 0.0
        commits.append(
            Commit(
                oid=parts[0],
                short=parts[1],
                parents=tuple(item for item in parts[2].split() if item),
                author=parts[3],
                email=parts[4],
                date=parts[5],
                timestamp=timestamp,
                refs=refs,
                subject=parts[8],
                is_head=is_head,
                head_branch=head_branch,
            )
        )
    return commits


def assign_lanes(commits: list[Commit]) -> History:
    """
    Places commits into lanes and records the connections between them.

    Args:
        commits: Commits in display order, newest first.

    Returns:
        History: Laid-out rows.
    """

    history = History()
    # Each slot holds the object id that lane is waiting for, or None when free.
    lanes: list[str | None] = []
    rows: list[GraphRow] = []

    for commit in commits:
        try:
            lane = lanes.index(commit.oid)
        except ValueError:
            lane = _claim_lane(lanes)
        lanes[lane] = None

        passing = tuple(index for index, waiting in enumerate(lanes) if waiting and index != lane)

        parent_lanes: list[int] = []
        for position, parent in enumerate(commit.parents):
            existing = _find_lane(lanes, parent)
            if existing is not None:
                parent_lanes.append(existing)
                continue
            target = lane if position == 0 and lanes[lane] is None else _claim_lane(lanes)
            lanes[target] = parent
            parent_lanes.append(target)

        rows.append(
            GraphRow(
                commit=commit,
                lane=lane,
                passing=passing,
                # Parent rows are filled in below, once every row index is known.
                edges=tuple((parent_lane, -1) for parent_lane in parent_lanes),
                width=max(1, len([item for item in lanes if item is not None]) + 1),
            )
        )
        _trim(lanes)
        history.max_width = max(history.max_width, len(lanes) or 1, rows[-1].lane + 1)

    index_of = {row.commit.oid: position for position, row in enumerate(rows)}
    for row in rows:
        resolved: list[tuple[int, int]] = []
        for (parent_lane, _), parent_oid in zip(row.edges, row.commit.parents, strict=False):
            resolved.append((parent_lane, index_of.get(parent_oid, -1)))
        row.edges = tuple(resolved)

    history.rows = rows
    return history


def _claim_lane(lanes: list[str | None]) -> int:
    """
    Returns a free lane index, widening the graph only when necessary.

    Args:
        lanes: Current lane occupancy, modified in place when it grows.

    Returns:
        int: Index of a lane that is free.
    """

    for index, waiting in enumerate(lanes):
        if waiting is None:
            return index
    lanes.append(None)
    return len(lanes) - 1


def _find_lane(lanes: list[str | None], oid: str) -> int | None:
    """
    Finds the lane already waiting for a commit.

    Args:
        lanes: Current lane occupancy.
        oid: Object id to look for.

    Returns:
        int | None: Lane index, or None when no lane waits for it.
    """

    try:
        return lanes.index(oid)
    except ValueError:
        return None


def _trim(lanes: list[str | None]) -> None:
    """
    Drops trailing free lanes so the graph narrows again.

    Args:
        lanes: Lane occupancy, modified in place.

    Returns:
        None
    """

    while lanes and lanes[-1] is None:
        lanes.pop()


def read_history(
    repo: Path | str,
    limit: int = GRAPH_PAGE_SIZE,
    all_branches: bool = True,
    revision: str = "",
    path: str = "",
) -> History:
    """
    Reads a page of history and lays out the graph.

    Args:
        repo: Working tree path.
        limit: Maximum number of commits to read.
        all_branches: Whether every branch is included, not just the current one.
        revision: Start from this revision instead of the branch tips.
        path: Restrict history to commits touching this path.

    Returns:
        History: Laid-out rows, carrying ``error_key`` when git failed.
    """

    if revision and not is_valid_revision(revision):
        return History(error_key="error.unsafe_argument")

    args = ["log", "--topo-order", "-z", f"--format={_FORMAT}", f"--max-count={max(1, limit) + 1}"]
    if revision:
        args.append(revision)
    elif all_branches:
        args.append("--all")
    if path:
        if not is_safe_argument(path):
            return History(error_key="error.unsafe_argument")
        args.extend(["--", path])

    result = run(args, cwd=repo, read_only=True)
    if result.failed:
        # A repository without commits is not an error worth showing.
        if "does not have any commits yet" in result.stderr.lower():
            return History()
        return History(error_key=result.error_key())

    commits = parse_log(result.stdout)
    truncated = len(commits) > limit
    history = assign_lanes(commits[:limit])
    history.truncated = truncated
    return history
