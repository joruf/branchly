"""
Shapes for the GitHub responses Branchly uses.

Every ``from_api`` here is defensive. The API is not going to hand us garbage on
purpose, but a field can be null, a nested object can be missing for a deleted
user, and none of that may take a panel down.
"""

from __future__ import annotations

from dataclasses import dataclass
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
        )

    def with_check_state(self, state: str) -> PullRequest:
        """
        Returns a copy carrying a check state.

        Args:
            state: One of the ``CHECK_*`` constants.

        Returns:
            PullRequest: Updated copy.
        """

        return PullRequest(
            number=self.number,
            title=self.title,
            author=self.author,
            avatar_url=self.avatar_url,
            head_branch=self.head_branch,
            base_branch=self.base_branch,
            html_url=self.html_url,
            draft=self.draft,
            head_sha=self.head_sha,
            check_state=state,
        )


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
    """

    number: int
    title: str = ""
    author: str = ""
    avatar_url: str = ""
    html_url: str = ""
    comments: int = 0

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
        comments = data.get("comments")
        return cls(
            number=number,
            title=_text(data, "title"),
            author=_login(data),
            avatar_url=_avatar(data),
            html_url=_text(data, "html_url"),
            comments=comments if isinstance(comments, int) else 0,
        )


@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    """
    Everything the GitHub layer fetched for one repository.

    Attributes:
        owner: Repository owner.
        repo: Repository name.
        pull_requests: Open pull requests.
        issues: Open issues.
        head_check_state: Aggregated check state of the default branch tip.
        error_key: Translation key when the fetch failed.
        retry_after_minutes: Minutes to wait when rate limited.
    """

    owner: str = ""
    repo: str = ""
    pull_requests: tuple[PullRequest, ...] = ()
    issues: tuple[Issue, ...] = ()
    head_check_state: str = CHECK_NONE
    error_key: str = ""
    retry_after_minutes: int = 0

    @property
    def ok(self) -> bool:
        """
        Reports whether the fetch succeeded.

        Returns:
            bool: True when no error occurred.
        """

        return not self.error_key
