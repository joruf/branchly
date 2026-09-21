"""
Shapes for the GitHub responses Branchly uses.

Every ``from_api`` here is defensive. The API is not going to hand us garbage on
purpose, but a field can be null, a nested object can be missing for a deleted
user, and none of that may take a panel down.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

CHECK_SUCCESS = "success"
CHECK_FAILURE = "failure"
CHECK_PENDING = "pending"
CHECK_NEUTRAL = "neutral"
CHECK_NONE = "none"

# GitHub reports combined states and per-run conclusions with different
# vocabularies; both are mapped onto the four states the UI knows about.
_STATE_MAP: dict[str, str] = {
    "success": CHECK_SUCCESS,
    "completed": CHECK_SUCCESS,
    "failure": CHECK_FAILURE,
    "error": CHECK_FAILURE,
    "timed_out": CHECK_FAILURE,
    "cancelled": CHECK_FAILURE,
    "action_required": CHECK_FAILURE,
    "startup_failure": CHECK_FAILURE,
    "pending": CHECK_PENDING,
    "queued": CHECK_PENDING,
    "in_progress": CHECK_PENDING,
    "waiting": CHECK_PENDING,
    "requested": CHECK_PENDING,
    "neutral": CHECK_NEUTRAL,
    "skipped": CHECK_NEUTRAL,
    "stale": CHECK_NEUTRAL,
}

CHECK_LABEL_KEYS: dict[str, str] = {
    CHECK_SUCCESS: "github.checks_passed",
    CHECK_FAILURE: "github.checks_failed",
    CHECK_PENDING: "github.checks_pending",
    CHECK_NEUTRAL: "github.checks_none",
    CHECK_NONE: "github.checks_none",
}

CHECK_COLOR_TOKENS: dict[str, str] = {
    CHECK_SUCCESS: "check_pass",
    CHECK_FAILURE: "check_fail",
    CHECK_PENDING: "check_pending",
    CHECK_NEUTRAL: "check_neutral",
    CHECK_NONE: "check_neutral",
}


def normalize_check_state(value: Any) -> str:
    """
    Maps a GitHub state or conclusion onto one of Branchly's four states.

    Args:
        value: Raw ``state`` or ``conclusion`` field.

    Returns:
        str: One of the ``CHECK_*`` constants.
    """

    if not isinstance(value, str):
        return CHECK_NONE
    return _STATE_MAP.get(value.strip().lower(), CHECK_NEUTRAL)


def combine_check_states(states: list[str]) -> str:
    """
    Reduces several check states to the one worth showing.

    A failure is what the user needs to know about, so it wins; a run still going
    comes next.

    Args:
        states: Individual states.

    Returns:
        str: The state to display.
    """

    if not states:
        return CHECK_NONE
    if CHECK_FAILURE in states:
        return CHECK_FAILURE
    if CHECK_PENDING in states:
        return CHECK_PENDING
    if CHECK_SUCCESS in states:
        return CHECK_SUCCESS
    return CHECK_NEUTRAL


def _text(data: dict[str, Any], key: str) -> str:
    """
    Reads a string field, tolerating null and wrong types.

    Args:
        data: Response object.
        key: Field name.

    Returns:
        str: The value, or an empty string.
    """

    value = data.get(key)
    return value if isinstance(value, str) else ""


def _login(data: dict[str, Any], key: str = "user") -> str:
    """
    Reads a nested user login.

    Args:
        data: Response object.
        key: Field holding the user object.

    Returns:
        str: The login, or an empty string when the user is gone.
    """

    nested = data.get(key)
    if isinstance(nested, dict):
        return _text(nested, "login")
    return ""


def _avatar(data: dict[str, Any], key: str = "user") -> str:
    """
    Reads a nested avatar URL.

    Args:
        data: Response object.
        key: Field holding the user object.

    Returns:
        str: The URL, or an empty string.
    """

    nested = data.get(key)
    if isinstance(nested, dict):
        return _text(nested, "avatar_url")
    return ""


def _number(data: dict[str, Any], key: str, fallback: int = 0) -> int:
    """
    Reads an integer field, tolerating null and wrong types.

    Args:
        data: Response object.
        key: Field name.
        fallback: Value to use when the field is missing or not a number.

    Returns:
        int: The value.
    """

    value = data.get(key)
    # bool is a subclass of int and would silently become 0 or 1.
    if isinstance(value, bool) or not isinstance(value, int):
        return fallback
    return value


def _label_names(data: dict[str, Any]) -> tuple[str, ...]:
    """
    Reads the label names off an issue or pull request.

    Args:
        data: Response object.

    Returns:
        tuple[str, ...]: Label names in the order the API listed them.
    """

    raw = data.get("labels")
    if not isinstance(raw, list):
        return ()
    names: list[str] = []
    for entry in raw:
        if isinstance(entry, str):
            names.append(entry)
        elif isinstance(entry, dict):
            name = _text(entry, "name")
            if name:
                names.append(name)
    return tuple(names)


def _assignee_logins(data: dict[str, Any]) -> tuple[str, ...]:
    """
    Reads the assignee logins off an issue or pull request.

    Args:
        data: Response object.

    Returns:
        tuple[str, ...]: Logins, empty when nobody is assigned.
    """

    raw = data.get("assignees")
    if not isinstance(raw, list):
        # Older responses carry a single "assignee" instead of the list.
        single = _login(data, "assignee")
        return (single,) if single else ()
    logins = [_text(entry, "login") for entry in raw if isinstance(entry, dict)]
    return tuple(name for name in logins if name)


def _milestone_title(data: dict[str, Any]) -> str:
    """
    Reads the milestone title off an issue or pull request.

    Args:
        data: Response object.

    Returns:
        str: The title, empty when no milestone is set.
    """

    nested = data.get("milestone")
    return _text(nested, "title") if isinstance(nested, dict) else ""


@dataclass(frozen=True, slots=True)
class Viewer:
    """
    The account a token belongs to.

    Attributes:
        login: Account name.
        name: Display name.
        scopes: Permissions the token carries, as reported by the API.
    """

    login: str
    name: str = ""
    scopes: tuple[str, ...] = ()

    @property
    def scope_summary(self) -> str:
        """
        Returns the scopes as one readable string.

        Returns:
            str: Comma-separated scopes, or a dash when the API named none. A
                fine-grained token reports no classic scopes at all, which is not
                a problem worth alarming the user about.
        """

        return ", ".join(self.scopes) if self.scopes else "—"


@dataclass(frozen=True, slots=True)
class PullRequest:
    """
    One open pull request.

    Attributes:
        number: Pull request number.
        title: Title.
        author: Author login.
        avatar_url: Author avatar URL.
        head_branch: Branch the changes are on.
        base_branch: Branch they are meant to go into.
        html_url: Web page for the pull request.
        draft: Whether it is still a draft.
        head_sha: Tip commit, used to look up check state.
        check_state: Aggregated check state, filled in separately.
        node_id: Global id, needed for the few actions REST cannot perform.
        state: ``open`` or ``closed``.
        merged: Whether a closed pull request was merged rather than dropped.
        body: Description text.
        labels: Label names.
        assignees: Logins the pull request is assigned to.
        comments: Number of conversation comments.
        created_at: ISO timestamp of creation.
        updated_at: ISO timestamp of the last change.
        mergeable: Whether GitHub thinks it can be merged. ``None`` means the
            answer is still being computed, which is its own state and must not
            be flattened into "no".
        mergeable_state: GitHub's finer verdict, for example ``blocked`` or
            ``behind``, which is what explains a refused merge.
        milestone: Milestone title, empty when none is set.
    """

    number: int
    title: str = ""
    author: str = ""
    avatar_url: str = ""
    head_branch: str = ""
    base_branch: str = ""
    html_url: str = ""
    draft: bool = False
    head_sha: str = ""
    check_state: str = CHECK_NONE
    node_id: str = ""
    state: str = "open"
    merged: bool = False
    body: str = ""
    labels: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()
    comments: int = 0
    created_at: str = ""
    updated_at: str = ""
    mergeable: bool | None = None
    mergeable_state: str = ""
    milestone: str = ""

    @classmethod
    def from_api(cls, data: Any) -> PullRequest | None:
        """
        Builds a pull request from an API object.

        Args:
            data: One element of the pulls response.

        Returns:
            PullRequest | None: The pull request, or None when unusable.
        """

        if not isinstance(data, dict):
            return None
        number = data.get("number")
        if not isinstance(number, int):
            return None
        head = data.get("head") if isinstance(data.get("head"), dict) else {}
        base = data.get("base") if isinstance(data.get("base"), dict) else {}
        mergeable = data.get("mergeable")
        return cls(
            number=number,
            title=_text(data, "title"),
            author=_login(data),
            avatar_url=_avatar(data),
            head_branch=_text(head, "ref"),
            base_branch=_text(base, "ref"),
            html_url=_text(data, "html_url"),
            draft=bool(data.get("draft")),
            head_sha=_text(head, "sha"),
            node_id=_text(data, "node_id"),
            state=_text(data, "state") or "open",
            merged=bool(data.get("merged") or data.get("merged_at")),
            body=_text(data, "body"),
            labels=_label_names(data),
            assignees=_assignee_logins(data),
            comments=_number(data, "comments"),
            created_at=_text(data, "created_at"),
            updated_at=_text(data, "updated_at"),
            mergeable=mergeable if isinstance(mergeable, bool) else None,
            mergeable_state=_text(data, "mergeable_state"),
            milestone=_milestone_title(data),
        )

    def with_check_state(self, state: str) -> PullRequest:
        """
        Returns a copy carrying a check state.

        Args:
            state: One of the ``CHECK_*`` constants.

        Returns:
            PullRequest: Updated copy.
        """

        return replace(self, check_state=state)


@dataclass(frozen=True, slots=True)
class Issue:
    """
    One open issue.

    Attributes:
        number: Issue number.
        title: Title.
        author: Author login.
        avatar_url: Author avatar URL.
        html_url: Web page for the issue.
        comments: Number of comments.
        node_id: Global id.
        state: ``open`` or ``closed``.
        state_reason: Why it was closed, for example ``completed`` or
            ``not_planned``. GitHub shows those with different icons, and so
            should anything reading this.
        body: Description text.
        labels: Label names.
        assignees: Logins the issue is assigned to.
        milestone: Milestone title, empty when none is set.
        locked: Whether the conversation was locked.
        created_at: ISO timestamp of creation.
        updated_at: ISO timestamp of the last change.
    """

    number: int
    title: str = ""
    author: str = ""
    avatar_url: str = ""
    html_url: str = ""
    comments: int = 0
    node_id: str = ""
    state: str = "open"
    state_reason: str = ""
    body: str = ""
    labels: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()
    milestone: str = ""
    locked: bool = False
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_api(cls, data: Any) -> Issue | None:
        """
        Builds an issue from an API object.

        The issues endpoint also returns pull requests. Those are filtered out
        here, because a pull request listed as an issue would be confusing and is
        already shown in its own panel.

        Args:
            data: One element of the issues response.

        Returns:
            Issue | None: The issue, or None when it is a pull request or unusable.
        """

        if not isinstance(data, dict) or "pull_request" in data:
            return None
        number = data.get("number")
        if not isinstance(number, int):
            return None
        return cls(
            number=number,
            title=_text(data, "title"),
            author=_login(data),
            avatar_url=_avatar(data),
            html_url=_text(data, "html_url"),
            comments=_number(data, "comments"),
            node_id=_text(data, "node_id"),
            state=_text(data, "state") or "open",
            state_reason=_text(data, "state_reason"),
            body=_text(data, "body"),
            labels=_label_names(data),
            assignees=_assignee_logins(data),
            milestone=_milestone_title(data),
            locked=bool(data.get("locked")),
            created_at=_text(data, "created_at"),
            updated_at=_text(data, "updated_at"),
        )


@dataclass(frozen=True, slots=True)
class Repository:
    """
    One repository on the server.

    Attributes:
        owner: Account the repository belongs to.
        name: Repository name.
        full_name: ``owner/name``.
        description: Short description.
        homepage: Project website.
        private: Whether it is private.
        fork: Whether it was forked from somewhere.
        archived: Whether it is archived, which makes every write fail.
        default_branch: Branch the server treats as the main one.
        html_url: Web page.
        clone_url: HTTPS clone address.
        ssh_url: SSH clone address.
        topics: Topic names.
        stars: Stargazer count.
        forks: Fork count.
        open_issues: Open issues *and* pull requests, which is what the API
            counts. Named so nobody reads it as issues alone.
        language: Main language as GitHub detected it.
        pushed_at: ISO timestamp of the last push.
        updated_at: ISO timestamp of the last change of any kind.
        permissions: What the token may do here, as ``{"push": True, ...}``.
        has_issues: Whether the issue tracker is switched on.
        has_wiki: Whether the wiki is switched on.
        has_projects: Whether project boards are switched on.
        has_discussions: Whether discussions are switched on.
        visibility: ``public``, ``private`` or ``internal``.
        allow_merge_commit: Whether a merge commit is permitted.
        allow_squash_merge: Whether squashing is permitted.
        allow_rebase_merge: Whether rebasing is permitted.
        delete_branch_on_merge: Whether the server removes the branch itself.
    """

    owner: str = ""
    name: str = ""
    full_name: str = ""
    description: str = ""
    homepage: str = ""
    private: bool = False
    fork: bool = False
    archived: bool = False
    default_branch: str = ""
    html_url: str = ""
    clone_url: str = ""
    ssh_url: str = ""
    topics: tuple[str, ...] = ()
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    language: str = ""
    pushed_at: str = ""
    updated_at: str = ""
    permissions: dict[str, bool] | None = None
    has_issues: bool = True
    has_wiki: bool = True
    has_projects: bool = True
    has_discussions: bool = False
    visibility: str = "public"
    # The list endpoint leaves these out entirely, so "not stated" has to mean
    # "allowed". Showing a method as forbidden because the answer was thin would
    # hide a button that works.
    allow_merge_commit: bool = True
    allow_squash_merge: bool = True
    allow_rebase_merge: bool = True
    delete_branch_on_merge: bool = False

    @classmethod
    def from_api(cls, data: Any) -> Repository | None:
        """
        Builds a repository from an API object.

        Args:
            data: One element of a repositories response.

        Returns:
            Repository | None: The repository, or None when unusable.
        """

        if not isinstance(data, dict):
            return None
        name = _text(data, "name")
        if not name:
            return None
        topics = data.get("topics")
        permissions = data.get("permissions")
        return cls(
            owner=_login(data, "owner"),
            name=name,
            full_name=_text(data, "full_name"),
            description=_text(data, "description"),
            homepage=_text(data, "homepage"),
            private=bool(data.get("private")),
            fork=bool(data.get("fork")),
            archived=bool(data.get("archived")),
            default_branch=_text(data, "default_branch"),
            html_url=_text(data, "html_url"),
            clone_url=_text(data, "clone_url"),
            ssh_url=_text(data, "ssh_url"),
            topics=(
                tuple(item for item in topics if isinstance(item, str))
                if isinstance(topics, list)
                else ()
            ),
            stars=_number(data, "stargazers_count"),
            forks=_number(data, "forks_count"),
            open_issues=_number(data, "open_issues_count"),
            language=_text(data, "language"),
            pushed_at=_text(data, "pushed_at"),
            updated_at=_text(data, "updated_at"),
            permissions={
                key: bool(value)
                for key, value in permissions.items()
                if isinstance(key, str)
            }
            if isinstance(permissions, dict)
            else None,
            has_issues=bool(data.get("has_issues", True)),
            has_wiki=bool(data.get("has_wiki", True)),
            has_projects=bool(data.get("has_projects", True)),
            has_discussions=bool(data.get("has_discussions", False)),
            visibility=_text(data, "visibility") or ("private" if data.get("private") else "public"),
            allow_merge_commit=bool(data.get("allow_merge_commit", True)),
            allow_squash_merge=bool(data.get("allow_squash_merge", True)),
            allow_rebase_merge=bool(data.get("allow_rebase_merge", True)),
            delete_branch_on_merge=bool(data.get("delete_branch_on_merge", False)),
        )

    @property
    def can_push(self) -> bool:
        """
        Reports whether the token may write to this repository.

        An archived repository refuses every write regardless of permissions, so
        it counts as read-only here rather than making every caller remember the
        second condition.

        Returns:
            bool: True when writes are possible.
        """

        if self.archived:
            return False
        if self.permissions is None:
            return True
        return bool(self.permissions.get("push") or self.permissions.get("admin"))

    @property
    def can_administer(self) -> bool:
        """
        Reports whether the token may change settings or delete the repository.

        Returns:
            bool: True when administrative actions are possible.
        """

        if self.permissions is None:
            return True
        return bool(self.permissions.get("admin"))

    @property
    def merge_methods(self) -> dict[str, bool]:
        """
        Reports which merge methods this repository permits.

        Returns:
            dict[str, bool]: Keyed by ``merge``, ``squash`` and ``rebase``, in the
                shape the merge dialog expects.
        """

        return {
            "merge": self.allow_merge_commit,
            "squash": self.allow_squash_merge,
            "rebase": self.allow_rebase_merge,
        }


@dataclass(frozen=True, slots=True)
class Branch:
    """
    One branch on the server.

    Attributes:
        name: Branch name.
        sha: Commit it points at.
        protected: Whether a protection rule applies.
        is_default: Whether this is the repository's default branch.
    """

    name: str
    sha: str = ""
    protected: bool = False
    is_default: bool = False

    @classmethod
    def from_api(cls, data: Any, default_branch: str = "") -> Branch | None:
        """
        Builds a branch from an API object.

        Args:
            data: One element of the branches response.
            default_branch: Name of the repository's default branch.

        Returns:
            Branch | None: The branch, or None when unusable.
        """

        if not isinstance(data, dict):
            return None
        name = _text(data, "name")
        if not name:
            return None
        commit = data.get("commit")
        return cls(
            name=name,
            sha=_text(commit, "sha") if isinstance(commit, dict) else "",
            protected=bool(data.get("protected")),
            is_default=bool(default_branch) and name == default_branch,
        )


@dataclass(frozen=True, slots=True)
class Label:
    """
    One label.

    Attributes:
        name: Label name, which is also its identifier in the API.
        color: Six hex digits without a leading hash, as GitHub stores it.
        description: What the label means.
    """

    name: str
    color: str = ""
    description: str = ""

    @classmethod
    def from_api(cls, data: Any) -> Label | None:
        """
        Builds a label from an API object.

        Args:
            data: One element of the labels response.

        Returns:
            Label | None: The label, or None when unusable.
        """

        if not isinstance(data, dict):
            return None
        name = _text(data, "name")
        if not name:
            return None
        return cls(name=name, color=_text(data, "color"), description=_text(data, "description"))


@dataclass(frozen=True, slots=True)
class Milestone:
    """
    One milestone.

    Attributes:
        number: Milestone number, which is what write calls take.
        title: Title.
        state: ``open`` or ``closed``.
        description: Description text.
        due_on: ISO timestamp of the due date, empty when none is set.
        open_issues: Issues still open in it.
        closed_issues: Issues already closed in it.
    """

    number: int
    title: str = ""
    state: str = "open"
    description: str = ""
    due_on: str = ""
    open_issues: int = 0
    closed_issues: int = 0

    @classmethod
    def from_api(cls, data: Any) -> Milestone | None:
        """
        Builds a milestone from an API object.

        Args:
            data: One element of the milestones response.

        Returns:
            Milestone | None: The milestone, or None when unusable.
        """

        if not isinstance(data, dict):
            return None
        number = data.get("number")
        if not isinstance(number, int):
            return None
        return cls(
            number=number,
            title=_text(data, "title"),
            state=_text(data, "state") or "open",
            description=_text(data, "description"),
            due_on=_text(data, "due_on"),
            open_issues=_number(data, "open_issues"),
            closed_issues=_number(data, "closed_issues"),
        )


@dataclass(frozen=True, slots=True)
class Comment:
    """
    One comment on an issue or pull request.

    Attributes:
        comment_id: Identifier, needed to edit or delete it.
        author: Author login.
        avatar_url: Author avatar URL.
        body: Comment text.
        created_at: ISO timestamp of creation.
        updated_at: ISO timestamp of the last edit.
        html_url: Web page for the comment.
        editable: Whether the token's account may change it.
    """

    comment_id: int
    author: str = ""
    avatar_url: str = ""
    body: str = ""
    created_at: str = ""
    updated_at: str = ""
    html_url: str = ""
    editable: bool = False

    @classmethod
    def from_api(cls, data: Any, viewer_login: str = "") -> Comment | None:
        """
        Builds a comment from an API object.

        Args:
            data: One element of the comments response.
            viewer_login: Login of the account the token belongs to, used to work
                out whether the comment may be edited.

        Returns:
            Comment | None: The comment, or None when unusable.
        """

        if not isinstance(data, dict):
            return None
        comment_id = data.get("id")
        if not isinstance(comment_id, int):
            return None
        author = _login(data)
        permissions = data.get("author_association")
        owns_it = bool(viewer_login) and author == viewer_login
        return cls(
            comment_id=comment_id,
            author=author,
            avatar_url=_avatar(data),
            body=_text(data, "body"),
            created_at=_text(data, "created_at"),
            updated_at=_text(data, "updated_at"),
            html_url=_text(data, "html_url"),
            # Owning the comment is the reliable half. Being an owner or
            # collaborator also allows deleting, which the API enforces anyway.
            editable=owns_it or permissions in {"OWNER", "COLLABORATOR"},
        )


REVIEW_APPROVED = "APPROVED"
REVIEW_CHANGES_REQUESTED = "CHANGES_REQUESTED"
REVIEW_COMMENTED = "COMMENTED"
REVIEW_PENDING = "PENDING"
REVIEW_DISMISSED = "DISMISSED"


@dataclass(frozen=True, slots=True)
class Review:
    """
    One review on a pull request.

    Attributes:
        review_id: Identifier.
        author: Reviewer login.
        avatar_url: Reviewer avatar URL.
        state: One of the ``REVIEW_*`` constants.
        body: Review text.
        submitted_at: ISO timestamp.
        html_url: Web page for the review.
    """

    review_id: int
    author: str = ""
    avatar_url: str = ""
    state: str = REVIEW_COMMENTED
    body: str = ""
    submitted_at: str = ""
    html_url: str = ""

    @classmethod
    def from_api(cls, data: Any) -> Review | None:
        """
        Builds a review from an API object.

        Args:
            data: One element of the reviews response.

        Returns:
            Review | None: The review, or None when unusable.
        """

        if not isinstance(data, dict):
            return None
        review_id = data.get("id")
        if not isinstance(review_id, int):
            return None
        return cls(
            review_id=review_id,
            author=_login(data),
            avatar_url=_avatar(data),
            state=(_text(data, "state") or REVIEW_COMMENTED).upper(),
            body=_text(data, "body"),
            submitted_at=_text(data, "submitted_at"),
            html_url=_text(data, "html_url"),
        )


@dataclass(frozen=True, slots=True)
class ChangedFile:
    """
    One file a pull request touches.

    Attributes:
        filename: Path in the repository.
        status: ``added``, ``modified``, ``removed``, ``renamed`` or ``copied``.
        additions: Lines added.
        deletions: Lines removed.
        previous_filename: Former path for a rename.
        patch: Unified diff of this file, empty for a binary or oversized one.
    """

    filename: str
    status: str = "modified"
    additions: int = 0
    deletions: int = 0
    previous_filename: str = ""
    patch: str = ""

    @classmethod
    def from_api(cls, data: Any) -> ChangedFile | None:
        """
        Builds a changed file from an API object.

        Args:
            data: One element of the files response.

        Returns:
            ChangedFile | None: The file, or None when unusable.
        """

        if not isinstance(data, dict):
            return None
        filename = _text(data, "filename")
        if not filename:
            return None
        return cls(
            filename=filename,
            status=_text(data, "status") or "modified",
            additions=_number(data, "additions"),
            deletions=_number(data, "deletions"),
            previous_filename=_text(data, "previous_filename"),
            patch=_text(data, "patch"),
        )


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    """
    One file attached to a release.

    Attributes:
        asset_id: Identifier, needed to delete it.
        name: File name.
        size: Size in bytes.
        download_count: How often it was downloaded.
        browser_download_url: Public download address.
        content_type: MIME type.
    """

    asset_id: int
    name: str = ""
    size: int = 0
    download_count: int = 0
    browser_download_url: str = ""
    content_type: str = ""

    @classmethod
    def from_api(cls, data: Any) -> ReleaseAsset | None:
        """
        Builds an asset from an API object.

        Args:
            data: One element of the assets list.

        Returns:
            ReleaseAsset | None: The asset, or None when unusable.
        """

        if not isinstance(data, dict):
            return None
        asset_id = data.get("id")
        if not isinstance(asset_id, int):
            return None
        return cls(
            asset_id=asset_id,
            name=_text(data, "name"),
            size=_number(data, "size"),
            download_count=_number(data, "download_count"),
            browser_download_url=_text(data, "browser_download_url"),
            content_type=_text(data, "content_type"),
        )


@dataclass(frozen=True, slots=True)
class Release:
    """
    One release.

    Attributes:
        release_id: Identifier, needed to change or delete it.
        tag_name: Tag the release points at.
        name: Release title.
        body: Release notes.
        draft: Whether it is still a draft.
        prerelease: Whether it is marked as a pre-release.
        target_commitish: Branch or commit the tag is created from, relevant only
            while the tag does not exist yet.
        html_url: Web page.
        upload_url: Where assets are uploaded, with the URI template removed.
        published_at: ISO timestamp, empty for a draft.
        author: Login of whoever published it.
        assets: Attached files.
    """

    release_id: int
    tag_name: str = ""
    name: str = ""
    body: str = ""
    draft: bool = False
    prerelease: bool = False
    target_commitish: str = ""
    html_url: str = ""
    upload_url: str = ""
    published_at: str = ""
    author: str = ""
    assets: tuple[ReleaseAsset, ...] = ()

    @classmethod
    def from_api(cls, data: Any) -> Release | None:
        """
        Builds a release from an API object.

        Args:
            data: One element of the releases response.

        Returns:
            Release | None: The release, or None when unusable.
        """

        if not isinstance(data, dict):
            return None
        release_id = data.get("id")
        if not isinstance(release_id, int):
            return None
        raw_assets = data.get("assets")
        assets = (
            [ReleaseAsset.from_api(entry) for entry in raw_assets] if isinstance(raw_assets, list) else []
        )
        upload_url = _text(data, "upload_url")
        return cls(
            release_id=release_id,
            tag_name=_text(data, "tag_name"),
            name=_text(data, "name"),
            body=_text(data, "body"),
            draft=bool(data.get("draft")),
            prerelease=bool(data.get("prerelease")),
            target_commitish=_text(data, "target_commitish"),
            html_url=_text(data, "html_url"),
            # The API hands this over as an RFC 6570 template ending in
            # "{?name,label}", which requests would send as a literal path.
            upload_url=upload_url.split("{", 1)[0],
            published_at=_text(data, "published_at"),
            author=_login(data, "author"),
            assets=tuple(asset for asset in assets if asset is not None),
        )


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    """
    One run of a GitHub Actions workflow.

    Attributes:
        run_id: Identifier, needed to re-run or cancel it.
        name: Workflow name.
        display_title: Title of the commit or event that triggered it.
        run_number: Sequential number within its workflow.
        event: What triggered it, for example ``push`` or ``pull_request``.
        status: ``queued``, ``in_progress`` or ``completed``.
        conclusion: Outcome once completed, empty while it is still running.
        check_state: ``status``/``conclusion`` mapped onto the four states the UI
            already knows, so the run list uses the same dots as everything else.
        branch: Branch it ran on.
        head_sha: Commit it ran on.
        html_url: Web page for the run.
        created_at: ISO timestamp.
        updated_at: ISO timestamp.
        actor: Login of whoever triggered it.
    """

    run_id: int
    name: str = ""
    display_title: str = ""
    run_number: int = 0
    event: str = ""
    status: str = ""
    conclusion: str = ""
    check_state: str = CHECK_NONE
    branch: str = ""
    head_sha: str = ""
    html_url: str = ""
    created_at: str = ""
    updated_at: str = ""
    actor: str = ""

    @classmethod
    def from_api(cls, data: Any) -> WorkflowRun | None:
        """
        Builds a workflow run from an API object.

        Args:
            data: One element of the runs response.

        Returns:
            WorkflowRun | None: The run, or None when unusable.
        """

        if not isinstance(data, dict):
            return None
        run_id = data.get("id")
        if not isinstance(run_id, int):
            return None
        status = _text(data, "status")
        conclusion = _text(data, "conclusion")
        return cls(
            run_id=run_id,
            name=_text(data, "name"),
            display_title=_text(data, "display_title"),
            run_number=_number(data, "run_number"),
            event=_text(data, "event"),
            status=status,
            conclusion=conclusion,
            # While a run is going, "status" is the informative half; once it is
            # done, "status" only says "completed" and the conclusion is what
            # counts.
            check_state=normalize_check_state(conclusion or status),
            branch=_text(data, "head_branch"),
            head_sha=_text(data, "head_sha"),
            html_url=_text(data, "html_url"),
            created_at=_text(data, "created_at"),
            updated_at=_text(data, "updated_at"),
            actor=_login(data, "actor"),
        )

    @property
    def can_cancel(self) -> bool:
        """
        Reports whether cancelling this run still makes sense.

        Returns:
            bool: True while the run has not finished.
        """

        return self.status in {"queued", "in_progress", "waiting", "requested", "pending"}
