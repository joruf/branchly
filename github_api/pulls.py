"""
Pull request endpoints: the list, one pull request, its files, reviews and merge.

Two things here are not a plain REST call:

* **Mergeability is computed lazily.** The list endpoint never carries
  ``mergeable``; the single-pull-request endpoint kicks the computation off and
  answers ``null`` until it is done. So the detail view has to ask again rather
  than treat ``null`` as "no", and ``PullRequest.mergeable`` keeps ``None`` as its
  own state for exactly that reason.
* **Marking a draft as ready has no REST endpoint.** It exists only as a GraphQL
  mutation, so that one method sends GraphQL. Leaving the button out over that
  would have been the wrong trade.
"""

from __future__ import annotations

from typing import Any

from github_api.http import ApiResult, as_model, as_models
from github_api.models import ChangedFile, PullRequest, Review

STATE_OPEN = "open"
STATE_CLOSED = "closed"
STATE_ALL = "all"

MERGE_COMMIT = "merge"
MERGE_SQUASH = "squash"
MERGE_REBASE = "rebase"

REVIEW_APPROVE = "APPROVE"
REVIEW_REQUEST_CHANGES = "REQUEST_CHANGES"
REVIEW_COMMENT = "COMMENT"

# Marking a draft as ready exists only in GraphQL. Kept next to the method that
# sends it so the two never drift apart.
_READY_FOR_REVIEW = """
mutation($id: ID!) {
  markPullRequestReadyForReview(input: {pullRequestId: $id}) {
    pullRequest { number isDraft }
  }
}
"""

_CONVERT_TO_DRAFT = """
mutation($id: ID!) {
  convertPullRequestToDraft(input: {pullRequestId: $id}) {
    pullRequest { number isDraft }
  }
}
"""


class PullRequestEndpoints:
    """
    Reads and changes pull requests.
    """

    def list_pull_requests(
        self,
        owner: str,
        repo: str,
        state: str = STATE_OPEN,
        limit: int = 0,
        base: str = "",
        head: str = "",
    ) -> ApiResult:
        """
        Lists pull requests.

        Args:
            owner: Repository owner.
            repo: Repository name.
            state: ``open``, ``closed`` or ``all``.
            limit: Stop after this many, zero for all of them.
            base: Only pull requests going into this branch.
            head: Only pull requests coming from this branch, as ``owner:branch``.

        Returns:
            ApiResult: Payload is a ``list[PullRequest]``.
        """

        params: dict[str, Any] = {"state": state, "sort": "updated", "direction": "desc"}
        if base:
            params["base"] = base
        if head:
            params["head"] = head
        return as_models(
            self.get_all(f"/repos/{owner}/{repo}/pulls", params, limit=limit),
            PullRequest.from_api,
        )

    def pull_request(self, owner: str, repo: str, number: int) -> ApiResult:
        """
        Reads one pull request, including whether it can be merged.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: Pull request number.

        Returns:
            ApiResult: Payload is a ``PullRequest``. ``mergeable`` is ``None``
                while GitHub is still working the answer out; asking again a
                moment later is the documented way to get it.
        """

        return as_model(self.get(f"/repos/{owner}/{repo}/pulls/{number}"), PullRequest.from_api)

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str = "",
        draft: bool = False,
        maintainer_can_modify: bool = True,
    ) -> ApiResult:
        """
        Opens a pull request.

        Args:
            owner: Repository owner.
            repo: Repository name.
            title: Title.
            head: Branch the changes are on. A branch in a fork is named
                ``forkowner:branch``.
            base: Branch they should go into.
            body: Description text.
            draft: Whether to open it as a draft.
            maintainer_can_modify: Whether maintainers may push to the branch.

        Returns:
            ApiResult: Payload is the created ``PullRequest``.
        """

        payload: dict[str, Any] = {
            "title": title,
            "head": head,
            "base": base,
            "draft": bool(draft),
            "maintainer_can_modify": bool(maintainer_can_modify),
        }
        if body:
            payload["body"] = body
        return as_model(
            self.send("POST", f"/repos/{owner}/{repo}/pulls", payload), PullRequest.from_api
        )

    def update_pull_request(self, owner: str, repo: str, number: int, **changes: Any) -> ApiResult:
        """
        Changes a pull request.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: Pull request number.
            **changes: Any of ``title``, ``body``, ``state``, ``base``,
                ``maintainer_can_modify``. Labels and assignees are not here:
                those go through the issues endpoint, which is where GitHub keeps
                them for both kinds.

        Returns:
            ApiResult: Payload is the updated ``PullRequest``.
        """

        allowed = {"title", "body", "state", "base", "maintainer_can_modify"}
        body = {key: value for key, value in changes.items() if key in allowed}
        if not body:
            return self.pull_request(owner, repo, number)
        return as_model(
            self.send("PATCH", f"/repos/{owner}/{repo}/pulls/{number}", body), PullRequest.from_api
        )

    def close_pull_request(self, owner: str, repo: str, number: int) -> ApiResult:
        """
        Closes a pull request without merging it.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: Pull request number.

        Returns:
            ApiResult: Payload is the updated ``PullRequest``.
        """

        return self.update_pull_request(owner, repo, number, state=STATE_CLOSED)

    def reopen_pull_request(self, owner: str, repo: str, number: int) -> ApiResult:
        """
        Reopens a closed pull request.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: Pull request number.

        Returns:
            ApiResult: Payload is the updated ``PullRequest``.
        """

        return self.update_pull_request(owner, repo, number, state=STATE_OPEN)

    def merge_pull_request(
        self,
        owner: str,
        repo: str,
        number: int,
        method: str = MERGE_COMMIT,
        title: str = "",
        message: str = "",
        sha: str = "",
    ) -> ApiResult:
        """
        Merges a pull request.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: Pull request number.
            method: ``merge``, ``squash`` or ``rebase``.
            title: Commit title, empty for GitHub's default.
            message: Commit body.
            sha: The head commit the caller believes it is merging. Passing it
                makes GitHub refuse the merge when someone pushed in the
                meantime, which is the same protection ``--force-with-lease``
                gives a push and is worth having here too.

        Returns:
            ApiResult: Payload carries ``merged``, ``sha`` and a message.
        """

        payload: dict[str, Any] = {"merge_method": method}
        if title:
            payload["commit_title"] = title
        if message:
            payload["commit_message"] = message
        if sha:
            payload["sha"] = sha
        return self.send("PUT", f"/repos/{owner}/{repo}/pulls/{number}/merge", payload)

    def mark_ready_for_review(self, owner: str, repo: str, node_id: str) -> ApiResult:
        """
        Turns a draft pull request into a normal one.

        Args:
            owner: Repository owner, kept for a uniform signature.
            repo: Repository name, kept for a uniform signature.
            node_id: The pull request's global id, which GraphQL needs and REST
                supplies as ``node_id``.

        Returns:
            ApiResult: Payload is the mutation's data object.
        """

        del owner, repo
        if not node_id:
            return ApiResult(ok=False, error_key="github.validation_failed")
        return self.graphql(_READY_FOR_REVIEW, {"id": node_id})

    def convert_to_draft(self, owner: str, repo: str, node_id: str) -> ApiResult:
        """
        Turns a pull request back into a draft.

        Args:
            owner: Repository owner, kept for a uniform signature.
            repo: Repository name, kept for a uniform signature.
            node_id: The pull request's global id.

        Returns:
            ApiResult: Payload is the mutation's data object.
        """

        del owner, repo
        if not node_id:
            return ApiResult(ok=False, error_key="github.validation_failed")
        return self.graphql(_CONVERT_TO_DRAFT, {"id": node_id})

    # --------------------------------------------------------- files and commits

    def files(self, owner: str, repo: str, number: int, limit: int = 0) -> ApiResult:
        """
        Lists the files a pull request touches.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: Pull request number.
            limit: Stop after this many, zero for all of them.

        Returns:
            ApiResult: Payload is a ``list[ChangedFile]``.
        """

        return as_models(
            self.get_all(f"/repos/{owner}/{repo}/pulls/{number}/files", limit=limit),
            ChangedFile.from_api,
        )

    def commits(self, owner: str, repo: str, number: int, limit: int = 0) -> ApiResult:
        """
        Lists the commits on a pull request.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: Pull request number.
            limit: Stop after this many, zero for all of them.

        Returns:
            ApiResult: Payload is the raw commit list, which the caller reads for
                ``sha`` and ``commit.message``.
        """

        return self.get_all(f"/repos/{owner}/{repo}/pulls/{number}/commits", limit=limit)

    # ----------------------------------------------------------------- reviews

    def reviews(self, owner: str, repo: str, number: int) -> ApiResult:
        """
        Lists the reviews on a pull request.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: Pull request number.

        Returns:
            ApiResult: Payload is a ``list[Review]``.
        """

        return as_models(self.get_all(f"/repos/{owner}/{repo}/pulls/{number}/reviews"), Review.from_api)

    def create_review(
        self, owner: str, repo: str, number: int, event: str = REVIEW_COMMENT, body: str = ""
    ) -> ApiResult:
        """
        Submits a review.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: Pull request number.
            event: ``APPROVE``, ``REQUEST_CHANGES`` or ``COMMENT``.
            body: Review text. GitHub requires one for everything but an
                approval, so a missing text is caught here rather than as a 422.

        Returns:
            ApiResult: Payload is the created ``Review``.
        """

        if event != REVIEW_APPROVE and not body.strip():
            return ApiResult(ok=False, error_key="github.review_body_required")
        payload: dict[str, Any] = {"event": event}
        if body:
            payload["body"] = body
        return as_model(
            self.send("POST", f"/repos/{owner}/{repo}/pulls/{number}/reviews", payload),
            Review.from_api,
        )

    def request_reviewers(
        self, owner: str, repo: str, number: int, reviewers: list[str]
    ) -> ApiResult:
        """
        Asks people for a review.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: Pull request number.
            reviewers: Logins to ask.

        Returns:
            ApiResult: Payload is the updated pull request.
        """

        return self.send(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{number}/requested_reviewers",
            {"reviewers": reviewers},
        )
