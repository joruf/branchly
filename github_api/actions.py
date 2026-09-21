"""
GitHub Actions endpoints: workflow runs and what can be done to them.

Only the three things a desktop client can usefully offer are here: see what ran,
start it again, stop it. Everything else about Actions happens in a YAML file in
the repository, which is edited as a file rather than through an API.

All of these need the ``workflow`` scope. A token without it gets a 403, which
the transport turns into ``github.forbidden`` rather than a bare failure, so the
UI can say which permission is missing instead of "something went wrong".
"""

from __future__ import annotations

from typing import Any

from github_api.http import ApiResult, as_model, as_models
from github_api.models import WorkflowRun


class ActionEndpoints:
    """
    Reads and controls workflow runs.
    """

    def workflow_runs(
        self,
        owner: str,
        repo: str,
        branch: str = "",
        status: str = "",
        limit: int = 50,
    ) -> ApiResult:
        """
        Lists workflow runs, newest first.

        Args:
            owner: Repository owner.
            repo: Repository name.
            branch: Only runs on this branch.
            status: Only runs in this state, for example ``failure`` or
                ``in_progress``.
            limit: Stop after this many. Defaults to fifty rather than to
                everything: a busy repository has tens of thousands of runs and
                nobody scrolls through them.

        Returns:
            ApiResult: Payload is a ``list[WorkflowRun]``.
        """

        params: dict[str, Any] = {}
        if branch:
            params["branch"] = branch
        if status:
            params["status"] = status
        return as_models(
            self.get_all(f"/repos/{owner}/{repo}/actions/runs", params, limit=limit),
            WorkflowRun.from_api,
        )

    def workflow_run(self, owner: str, repo: str, run_id: int) -> ApiResult:
        """
        Reads one workflow run.

        Args:
            owner: Repository owner.
            repo: Repository name.
            run_id: Run identifier.

        Returns:
            ApiResult: Payload is a ``WorkflowRun``.
        """

        return as_model(
            self.get(f"/repos/{owner}/{repo}/actions/runs/{run_id}"), WorkflowRun.from_api
        )

    def run_jobs(self, owner: str, repo: str, run_id: int) -> ApiResult:
        """
        Lists the jobs of one run.

        Args:
            owner: Repository owner.
            repo: Repository name.
            run_id: Run identifier.

        Returns:
            ApiResult: Payload is the raw job list, read for ``name``,
                ``conclusion`` and ``html_url``.
        """

        return self.get_all(f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs")

    def rerun(self, owner: str, repo: str, run_id: int, failed_only: bool = False) -> ApiResult:
        """
        Starts a finished run again.

        Args:
            owner: Repository owner.
            repo: Repository name.
            run_id: Run identifier.
            failed_only: Whether to repeat only the jobs that failed, which is
                usually what someone wants after a flaky step.

        Returns:
            ApiResult: Success carries no useful payload.
        """

        suffix = "rerun-failed-jobs" if failed_only else "rerun"
        return self.send("POST", f"/repos/{owner}/{repo}/actions/runs/{run_id}/{suffix}")

    def cancel(self, owner: str, repo: str, run_id: int) -> ApiResult:
        """
        Stops a run that is still going.

        Args:
            owner: Repository owner.
            repo: Repository name.
            run_id: Run identifier.

        Returns:
            ApiResult: Success carries no useful payload.
        """

        return self.send("POST", f"/repos/{owner}/{repo}/actions/runs/{run_id}/cancel")

    def delete_run(self, owner: str, repo: str, run_id: int) -> ApiResult:
        """
        Removes a run and its logs from the history.

        Args:
            owner: Repository owner.
            repo: Repository name.
            run_id: Run identifier.

        Returns:
            ApiResult: Success carries no payload.
        """

        return self.send("DELETE", f"/repos/{owner}/{repo}/actions/runs/{run_id}")

    def workflows(self, owner: str, repo: str) -> ApiResult:
        """
        Lists the workflows a repository defines.

        Args:
            owner: Repository owner.
            repo: Repository name.

        Returns:
            ApiResult: Payload is the raw workflow list, read for ``id``,
                ``name``, ``state`` and ``path``.
        """

        return self.get_all(f"/repos/{owner}/{repo}/actions/workflows")

    def dispatch_workflow(
        self,
        owner: str,
        repo: str,
        workflow_id: int | str,
        ref: str,
        inputs: dict[str, Any] | None = None,
    ) -> ApiResult:
        """
        Starts a workflow by hand.

        Only works for a workflow that declares ``workflow_dispatch``; anything
        else is refused by GitHub with a message saying so.

        Args:
            owner: Repository owner.
            repo: Repository name.
            workflow_id: Numeric id or file name, for example ``tests.yml``.
            ref: Branch or tag to run it on.
            inputs: Values for the workflow's declared inputs.

        Returns:
            ApiResult: Success carries no payload.
        """

        body: dict[str, Any] = {"ref": ref}
        if inputs:
            body["inputs"] = inputs
        return self.send(
            "POST", f"/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches", body
        )
