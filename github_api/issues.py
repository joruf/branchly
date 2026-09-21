"""
Issue endpoints: the list, one issue, its comments, labels and milestones.

The issues endpoint answers with pull requests as well, because GitHub models a
pull request as an issue with a branch attached. ``Issue.from_api`` refuses those
and the list drops them, so a caller asking for issues gets issues. The write
calls go to the same ``/issues/{number}`` path for both kinds, which is why
commenting on a pull request lives here rather than in the pull request module.
"""

from __future__ import annotations

from typing import Any

from github_api.http import ApiResult, as_model, as_models
from github_api.models import Comment, Issue, Label, Milestone

STATE_OPEN = "open"
STATE_CLOSED = "closed"
STATE_ALL = "all"

# Why an issue was closed. GitHub shows the two with different icons, and a
# "not planned" issue closed as "completed" reads as a lie in the history.
REASON_COMPLETED = "completed"
REASON_NOT_PLANNED = "not_planned"


class IssueEndpoints:
    """
    Reads and changes issues, their comments, labels and milestones.
    """

    def list_issues(
        self,
        owner: str,
        repo: str,
        state: str = STATE_OPEN,
        limit: int = 0,
        labels: str = "",
        assignee: str = "",
        milestone: str = "",
    ) -> ApiResult:
        """
        Lists issues.

        Args:
            owner: Repository owner.
            repo: Repository name.
            state: ``open``, ``closed`` or ``all``.
            limit: Stop after this many, zero for all of them.
            labels: Comma-separated label names to filter by.
            assignee: Login to filter by, ``none`` for unassigned ones.
            milestone: Milestone number as text, or ``none`` / ``*``.

        Returns:
            ApiResult: Payload is a ``list[Issue]``, pull requests removed.
        """

        params: dict[str, Any] = {"state": state, "sort": "updated", "direction": "desc"}
        if labels:
            params["labels"] = labels
        if assignee:
            params["assignee"] = assignee
        if milestone:
            params["milestone"] = milestone
        return as_models(
            self.get_all(f"/repos/{owner}/{repo}/issues", params, limit=limit),
            Issue.from_api,
        )

    def issue(self, owner: str, repo: str, number: int) -> ApiResult:
        """
        Reads one issue.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: Issue number.

        Returns:
            ApiResult: Payload is an ``Issue``.
        """

        return as_model(self.get(f"/repos/{owner}/{repo}/issues/{number}"), Issue.from_api)

    def create_issue(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str = "",
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
        milestone: int | None = None,
    ) -> ApiResult:
        """
        Creates an issue.

        Args:
            owner: Repository owner.
            repo: Repository name.
            title: Title, the one field GitHub insists on.
            body: Description text.
            labels: Label names.
            assignees: Logins to assign.
            milestone: Milestone number.

        Returns:
            ApiResult: Payload is the created ``Issue``.
        """

        payload: dict[str, Any] = {"title": title}
        if body:
            payload["body"] = body
        if labels:
            payload["labels"] = labels
        if assignees:
            payload["assignees"] = assignees
        if milestone is not None:
            payload["milestone"] = milestone
        return as_model(self.send("POST", f"/repos/{owner}/{repo}/issues", payload), Issue.from_api)

    def update_issue(self, owner: str, repo: str, number: int, **changes: Any) -> ApiResult:
        """
        Changes an issue.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: Issue number.
            **changes: Any of ``title``, ``body``, ``state``, ``state_reason``,
                ``labels``, ``assignees``, ``milestone``. A ``milestone`` of
                ``None`` removes the milestone, which is why it is passed through
                rather than dropped like the other empty values.

        Returns:
            ApiResult: Payload is the updated ``Issue``.
        """

        allowed = {"title", "body", "state", "state_reason", "labels", "assignees", "milestone"}
        body = {key: value for key, value in changes.items() if key in allowed}
        if not body:
            return self.issue(owner, repo, number)
        return as_model(
            self.send("PATCH", f"/repos/{owner}/{repo}/issues/{number}", body),
            Issue.from_api,
        )

    def close_issue(
        self, owner: str, repo: str, number: int, reason: str = REASON_COMPLETED
    ) -> ApiResult:
        """
        Closes an issue.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: Issue number.
            reason: ``completed`` or ``not_planned``.

        Returns:
            ApiResult: Payload is the updated ``Issue``.
        """

        return self.update_issue(owner, repo, number, state=STATE_CLOSED, state_reason=reason)

    def reopen_issue(self, owner: str, repo: str, number: int) -> ApiResult:
        """
        Reopens a closed issue.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: Issue number.

        Returns:
            ApiResult: Payload is the updated ``Issue``.
        """

        return self.update_issue(owner, repo, number, state=STATE_OPEN, state_reason=None)

    # ------------------------------------------------------------------ comments

    def comments(self, owner: str, repo: str, number: int, viewer_login: str = "") -> ApiResult:
        """
        Lists the comments on an issue or pull request.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: Issue or pull request number.
            viewer_login: Login of the token's account, used to mark which
                comments can be edited.

        Returns:
            ApiResult: Payload is a ``list[Comment]``, oldest first.
        """

        return as_models(
            self.get_all(f"/repos/{owner}/{repo}/issues/{number}/comments"),
            lambda entry: Comment.from_api(entry, viewer_login),
        )

    def create_comment(self, owner: str, repo: str, number: int, body: str) -> ApiResult:
        """
        Writes a comment.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: Issue or pull request number.
            body: Comment text.

        Returns:
            ApiResult: Payload is the created ``Comment``.
        """

        return as_model(
            self.send("POST", f"/repos/{owner}/{repo}/issues/{number}/comments", {"body": body}),
            Comment.from_api,
        )

    def update_comment(self, owner: str, repo: str, comment_id: int, body: str) -> ApiResult:
        """
        Changes a comment's text.

        Args:
            owner: Repository owner.
            repo: Repository name.
            comment_id: Comment identifier.
            body: New text.

        Returns:
            ApiResult: Payload is the updated ``Comment``.
        """

        return as_model(
            self.send("PATCH", f"/repos/{owner}/{repo}/issues/comments/{comment_id}", {"body": body}),
            Comment.from_api,
        )

    def delete_comment(self, owner: str, repo: str, comment_id: int) -> ApiResult:
        """
        Deletes a comment.

        Args:
            owner: Repository owner.
            repo: Repository name.
            comment_id: Comment identifier.

        Returns:
            ApiResult: Success carries no payload.
        """

        return self.send("DELETE", f"/repos/{owner}/{repo}/issues/comments/{comment_id}")

    # -------------------------------------------------------- labels, milestones

    def labels(self, owner: str, repo: str) -> ApiResult:
        """
        Lists the labels a repository defines.

        Args:
            owner: Repository owner.
            repo: Repository name.

        Returns:
            ApiResult: Payload is a ``list[Label]``.
        """

        return as_models(self.get_all(f"/repos/{owner}/{repo}/labels"), Label.from_api)

    def create_label(
        self, owner: str, repo: str, name: str, color: str = "ededed", description: str = ""
    ) -> ApiResult:
        """
        Creates a label.

        Args:
            owner: Repository owner.
            repo: Repository name.
            name: Label name.
            color: Six hex digits. A leading hash is stripped, because that is how
                every colour picker hands one over and GitHub refuses it.
            description: What the label means.

        Returns:
            ApiResult: Payload is the created ``Label``.
        """

        body: dict[str, Any] = {"name": name, "color": color.lstrip("#")}
        if description:
            body["description"] = description
        return as_model(self.send("POST", f"/repos/{owner}/{repo}/labels", body), Label.from_api)

    def update_label(
        self, owner: str, repo: str, name: str, new_name: str = "", color: str = "", description: str = ""
    ) -> ApiResult:
        """
        Changes a label.

        Args:
            owner: Repository owner.
            repo: Repository name.
            name: Current label name, which is how the API addresses it.
            new_name: New name, empty to keep the current one.
            color: Six hex digits, empty to keep the current colour.
            description: New description, empty to keep the current one.

        Returns:
            ApiResult: Payload is the updated ``Label``.
        """

        body: dict[str, Any] = {}
        if new_name:
            body["new_name"] = new_name
        if color:
            body["color"] = color.lstrip("#")
        if description:
            body["description"] = description
        if not body:
            return ApiResult(ok=True, payload=None)
        return as_model(self.send("PATCH", f"/repos/{owner}/{repo}/labels/{name}", body), Label.from_api)

    def delete_label(self, owner: str, repo: str, name: str) -> ApiResult:
        """
        Deletes a label from the repository and from every issue carrying it.

        Args:
            owner: Repository owner.
            repo: Repository name.
            name: Label name.

        Returns:
            ApiResult: Success carries no payload.
        """

        return self.send("DELETE", f"/repos/{owner}/{repo}/labels/{name}")

    def milestones(self, owner: str, repo: str, state: str = STATE_OPEN) -> ApiResult:
        """
        Lists milestones.

        Args:
            owner: Repository owner.
            repo: Repository name.
            state: ``open``, ``closed`` or ``all``.

        Returns:
            ApiResult: Payload is a ``list[Milestone]``.
        """

        return as_models(
            self.get_all(f"/repos/{owner}/{repo}/milestones", {"state": state}),
            Milestone.from_api,
        )

    def create_milestone(
        self, owner: str, repo: str, title: str, description: str = "", due_on: str = ""
    ) -> ApiResult:
        """
        Creates a milestone.

        Args:
            owner: Repository owner.
            repo: Repository name.
            title: Title.
            description: Description text.
            due_on: Due date as an ISO timestamp.

        Returns:
            ApiResult: Payload is the created ``Milestone``.
        """

        body: dict[str, Any] = {"title": title}
        if description:
            body["description"] = description
        if due_on:
            body["due_on"] = due_on
        return as_model(self.send("POST", f"/repos/{owner}/{repo}/milestones", body), Milestone.from_api)

    def close_milestone(self, owner: str, repo: str, number: int) -> ApiResult:
        """
        Closes a milestone without deleting it.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: Milestone number.

        Returns:
            ApiResult: Payload is the updated ``Milestone``.
        """

        return as_model(
            self.send("PATCH", f"/repos/{owner}/{repo}/milestones/{number}", {"state": STATE_CLOSED}),
            Milestone.from_api,
        )

    def delete_milestone(self, owner: str, repo: str, number: int) -> ApiResult:
        """
        Deletes a milestone.

        Issues that were in it keep existing and simply lose the milestone.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: Milestone number.

        Returns:
            ApiResult: Success carries no payload.
        """

        return self.send("DELETE", f"/repos/{owner}/{repo}/milestones/{number}")
