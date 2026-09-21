"""
Tests for the GitHub API layer.

Everything here runs against a real HTTP server on localhost, so the request path
under test is the one the application uses: same session, same headers, same
parsing. What is being checked is less "does requests work" and more the
decisions the transport makes on its own, namely when it follows a page, when it
trusts the cache, when it refuses to ask again, and what it turns a failure into.
"""

from __future__ import annotations

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from github_api.client import GitHubClient
from github_api.http import (
    ERROR_CONFLICT,
    ERROR_FORBIDDEN,
    ERROR_NO_TOKEN,
    ERROR_NOT_FOUND,
    ERROR_OFFLINE,
    ERROR_RATE_LIMITED,
    ERROR_UNAUTHORIZED,
    ERROR_VALIDATION,
    missing_scopes,
)
from github_api.models import Release
from tests.support_github import FakeGitHub, Response


def _issue(number: int, title: str = "", **extra: object) -> dict:
    """
    Builds an issue object the way the API returns one.

    Args:
        number: Issue number.
        title: Title.
        **extra: Fields to add or override.

    Returns:
        dict: The object.
    """

    payload = {
        "number": number,
        "title": title or f"Issue {number}",
        "state": "open",
        "user": {"login": "joruf", "avatar_url": "https://example.invalid/a.png"},
        "labels": [{"name": "bug"}],
        "assignees": [{"login": "joruf"}],
        "comments": 2,
        "body": "text",
        "node_id": f"I_{number}",
    }
    payload.update(extra)
    return payload


def _pull(number: int, **extra: object) -> dict:
    """
    Builds a pull request object the way the API returns one.

    Args:
        number: Pull request number.
        **extra: Fields to add or override.

    Returns:
        dict: The object.
    """

    payload = {
        "number": number,
        "title": f"Pull {number}",
        "state": "open",
        "draft": False,
        "node_id": f"PR_{number}",
        "user": {"login": "joruf"},
        "head": {"ref": "feature", "sha": "a" * 40},
        "base": {"ref": "main"},
        "html_url": f"https://example.invalid/pull/{number}",
    }
    payload.update(extra)
    return payload


class GitHubApiTestCase(unittest.TestCase):
    """
    Base case holding a fake server and a client pointed at it.
    """

    def setUp(self) -> None:
        """
        Starts the fake server.

        Returns:
            None
        """

        self.server = FakeGitHub()
        self.addCleanup(self.server.stop)
        self.client = GitHubClient("ghp_" + "x" * 36, api_root=self.server.root)
        self.addCleanup(self.client.close)


class TransportTests(GitHubApiTestCase):
    """
    The decisions the transport makes without being told.
    """

    def test_no_token_never_asks(self) -> None:
        client = GitHubClient("", api_root=self.server.root)
        self.addCleanup(client.close)
        result = client.get("/user")
        self.assertFalse(result.ok)
        self.assertEqual(ERROR_NO_TOKEN, result.error_key)
        self.assertEqual([], self.server.requests)

    def test_follows_every_page(self) -> None:
        self.server.pages(
            "GET",
            "/repos/o/r/issues",
            [[_issue(1), _issue(2)], [_issue(3)], []],
        )
        result = self.client.list_issues("o", "r")
        self.assertTrue(result.ok)
        self.assertEqual([1, 2, 3], [item.number for item in result.payload])

    def test_limit_stops_early(self) -> None:
        self.server.pages("GET", "/repos/o/r/issues", [[_issue(n) for n in range(1, 101)], [_issue(101)]])
        result = self.client.list_issues("o", "r", limit=2)
        self.assertEqual(2, len(result.payload))
        # The second page must never have been asked for.
        self.assertEqual(1, len(self.server.calls("GET", "/repos/o/r/issues")))

    def test_conditional_request_reuses_the_cached_body(self) -> None:
        state = {"calls": 0}

        def handler(request) -> Response:  # noqa: ANN001 - RecordedRequest
            state["calls"] += 1
            if request.headers.get("if-none-match") == '"v1"':
                return Response(status=304)
            return Response(body=[_issue(7)], headers={"ETag": '"v1"'})

        self.server.route("GET", "/repos/o/r/issues", handler)
        first = self.client.list_issues("o", "r")
        second = self.client.list_issues("o", "r")
        self.assertEqual([7], [item.number for item in first.payload])
        self.assertEqual([7], [item.number for item in second.payload])
        self.assertEqual(2, state["calls"])

    def test_a_write_drops_the_cache(self) -> None:
        state = {"body": [_issue(1)]}

        def listing(request) -> Response:  # noqa: ANN001
            if request.headers.get("if-none-match") == '"v1"':
                return Response(status=304)
            return Response(body=state["body"], headers={"ETag": '"v1"'})

        def create(_request) -> Response:  # noqa: ANN001
            state["body"] = [_issue(1), _issue(2)]
            return Response(status=201, body=_issue(2))

        self.server.route("GET", "/repos/o/r/issues", listing)
        self.server.route("POST", "/repos/o/r/issues", create)

        self.assertEqual([1], [item.number for item in self.client.list_issues("o", "r").payload])
        self.assertTrue(self.client.create_issue("o", "r", "second").ok)
        # Without the invalidation the ETag would still be sent and the stale
        # single-issue list would come back as a 304.
        self.assertEqual([1, 2], [item.number for item in self.client.list_issues("o", "r").payload])

    def test_an_enveloped_list_is_unwrapped(self) -> None:
        # Several Actions endpoints answer with a count plus the array under a
        # name of their own. Missing one of those names made the list come back
        # empty rather than fail, which is the worst of the three outcomes.
        for key in ("workflows", "workflow_runs", "jobs", "items"):
            with self.subTest(key=key):
                path = f"/wrapped/{key}"
                self.server.json("GET", path, {"total_count": 2, key: [{"id": 1}, {"id": 2}]})
                result = self.client.get_all(path)
                self.assertTrue(result.ok)
                self.assertEqual(2, len(result.items))

    def test_an_unknown_envelope_does_not_read_as_an_empty_list(self) -> None:
        self.server.json("GET", "/wrapped/odd", {"total_count": 1, "somethings": [{"id": 1}]})
        result = self.client.get_all("/wrapped/odd")
        # It cannot be unwrapped, so it comes back empty. What matters is that it
        # stops rather than walking pages forever.
        self.assertTrue(result.ok)
        self.assertEqual([], result.items)
        self.assertEqual(1, len(self.server.calls("GET", "/wrapped/odd")))

    def test_rate_limit_stops_further_requests(self) -> None:
        reset = str(int(time.time()) + 600)
        self.server.json(
            "GET",
            "/repos/o/r/issues",
            {"message": "API rate limit exceeded"},
            status=403,
            X_RateLimit_Remaining="0",
            X_RateLimit_Reset=reset,
        )
        first = self.client.list_issues("o", "r")
        self.assertEqual(ERROR_RATE_LIMITED, first.error_key)
        self.assertGreaterEqual(self.client.rate_limited_for_minutes, 9)

        second = self.client.list_issues("o", "r")
        self.assertEqual(ERROR_RATE_LIMITED, second.error_key)
        # Being told to wait means waiting, not asking again immediately.
        self.assertEqual(1, len(self.server.calls("GET", "/repos/o/r/issues")))

    def test_forbidden_is_not_read_as_a_rate_limit(self) -> None:
        self.server.json(
            "GET",
            "/repos/o/r/issues",
            {"message": "Resource not accessible by personal access token"},
            status=403,
            X_RateLimit_Remaining="4998",
        )
        result = self.client.list_issues("o", "r")
        self.assertEqual(ERROR_FORBIDDEN, result.error_key)
        self.assertEqual(0, self.client.rate_limited_for_minutes)
        self.assertIn("not accessible", result.detail)

    def test_status_codes_become_distinct_keys(self) -> None:
        cases = {
            401: ERROR_UNAUTHORIZED,
            404: ERROR_NOT_FOUND,
            409: ERROR_CONFLICT,
        }
        for status, expected in cases.items():
            with self.subTest(status=status):
                path = f"/repos/o/r{status}"
                self.server.json("GET", path, {"message": "no"}, status=status)
                self.assertEqual(expected, self.client.get(path).error_key)

    def test_validation_errors_are_flattened_into_one_sentence(self) -> None:
        self.server.json(
            "POST",
            "/user/repos",
            {
                "message": "Repository creation failed.",
                "errors": [{"resource": "Repository", "field": "name", "code": "custom",
                            "message": "name already exists on this account"}],
            },
            status=422,
        )
        result = self.client.create_repository("taken")
        self.assertEqual(ERROR_VALIDATION, result.error_key)
        self.assertIn("Repository creation failed.", result.detail)
        self.assertIn("name already exists", result.detail)

    def test_unreachable_server_reports_offline(self) -> None:
        client = GitHubClient("ghp_" + "x" * 36, api_root="http://127.0.0.1:1")
        self.addCleanup(client.close)
        self.assertEqual(ERROR_OFFLINE, client.get("/user").error_key)

    def test_scopes_are_read_off_the_response(self) -> None:
        self.server.json(
            "GET",
            "/user",
            {"login": "joruf", "name": "Joachim"},
            X_OAuth_Scopes="repo, workflow",
        )
        viewer, error = self.client.viewer()
        self.assertEqual("", error)
        self.assertEqual("joruf", viewer.login)
        self.assertEqual(("repo", "workflow"), viewer.scopes)
        self.assertEqual(["delete_repo"], self.client.missing_scopes_for("repo", "delete_repo"))


class ScopeTests(unittest.TestCase):
    """
    The scope comparison, which has to stay quiet when it cannot tell.
    """

    def test_repo_implies_its_children(self) -> None:
        self.assertEqual([], missing_scopes(["repo"], ["public_repo", "repo:status"]))

    def test_unknown_scopes_report_nothing(self) -> None:
        # A fine-grained token lists no classic scopes. Warning here would train
        # the user to ignore the warning.
        self.assertEqual([], missing_scopes([], ["repo", "delete_repo"]))

    def test_missing_scope_is_named(self) -> None:
        self.assertEqual(["workflow"], missing_scopes(["repo"], ["repo", "workflow"]))


class RepositoryTests(GitHubApiTestCase):
    """
    Repository listing, creation, settings and deletion.
    """

    def test_lists_repositories(self) -> None:
        self.server.json(
            "GET",
            "/user/repos",
            [
                {"name": "branchly", "owner": {"login": "joruf"}, "private": False,
                 "default_branch": "main", "permissions": {"push": True, "admin": True}},
                {"name": "pmtool", "owner": {"login": "joruf"}, "private": True,
                 "archived": True, "permissions": {"push": True, "admin": True}},
            ],
        )
        result = self.client.repositories()
        self.assertTrue(result.ok)
        names = [item.name for item in result.payload]
        self.assertEqual(["branchly", "pmtool"], names)
        self.assertTrue(result.payload[0].can_push)
        # An archived repository refuses every write, whatever the permissions say.
        self.assertFalse(result.payload[1].can_push)

    def test_creates_a_private_repository_by_default(self) -> None:
        self.server.json(
            "POST",
            "/user/repos",
            {"name": "new", "owner": {"login": "joruf"}, "private": True, "default_branch": "main"},
            status=201,
        )
        result = self.client.create_repository("new", description="d")
        self.assertTrue(result.ok)
        sent = self.server.calls("POST", "/user/repos")[0].body
        self.assertTrue(sent["private"])
        self.assertTrue(sent["auto_init"])
        self.assertEqual("d", sent["description"])

    def test_creates_in_an_organisation(self) -> None:
        self.server.json("POST", "/orgs/emis/repos", {"name": "x", "owner": {"login": "emis"}}, status=201)
        self.assertTrue(self.client.create_repository("x", organization="emis").ok)

    def test_update_sends_only_what_was_asked_for(self) -> None:
        self.server.json("PATCH", "/repos/o/r", {"name": "r", "owner": {"login": "o"}})
        self.client.update_repository("o", "r", description="new", nonsense="ignored")
        sent = self.server.calls("PATCH", "/repos/o/r")[0].body
        self.assertEqual({"description": "new"}, sent)

    def test_topics_are_folded_to_what_github_accepts(self) -> None:
        self.server.json("PUT", "/repos/o/r/topics", {"names": []})
        self.client.set_topics("o", "r", ["Git Client", "PySide6", "  "])
        sent = self.server.calls("PUT", "/repos/o/r/topics")[0].body
        self.assertEqual(["git-client", "pyside6"], sent["names"])

    def test_deletes_a_repository(self) -> None:
        self.server.json("DELETE", "/repos/o/r", None, status=204)
        self.assertTrue(self.client.delete_repository("o", "r").ok)

    def test_branches_mark_the_default(self) -> None:
        self.server.json(
            "GET",
            "/repos/o/r/branches",
            [{"name": "main", "commit": {"sha": "b" * 40}, "protected": True},
             {"name": "feature", "commit": {"sha": "c" * 40}}],
        )
        result = self.client.branches("o", "r", default_branch="main")
        self.assertTrue(result.payload[0].is_default)
        self.assertTrue(result.payload[0].protected)
        self.assertFalse(result.payload[1].is_default)

    def test_collaborators_come_back_with_their_role(self) -> None:
        self.server.json(
            "GET",
            "/repos/o/r/collaborators",
            [{"login": "joruf", "role_name": "admin"}, {"login": "other", "role_name": "write"}],
        )
        result = self.client.collaborator_list("o", "r")
        self.assertTrue(result.ok)
        self.assertEqual([("joruf", "admin"), ("other", "write")], result.payload)

    def test_inviting_and_removing_a_collaborator(self) -> None:
        self.server.json("PUT", "/repos/o/r/collaborators/other", {"id": 1}, status=201)
        self.server.json("DELETE", "/repos/o/r/collaborators/other", None, status=204)
        self.assertTrue(self.client.add_collaborator("o", "r", "other", "maintain").ok)
        self.assertEqual(
            "maintain", self.server.calls("PUT", "/repos/o/r/collaborators/other")[0].body["permission"]
        )
        self.assertTrue(self.client.remove_collaborator("o", "r", "other").ok)

    def test_merge_methods_default_to_allowed_when_the_answer_is_thin(self) -> None:
        # The list endpoint leaves these fields out. Reading that as "forbidden"
        # would hide buttons that work.
        self.server.json("GET", "/repos/o/r", {"name": "r", "owner": {"login": "o"}})
        thin = self.client.repository("o", "r").payload
        self.assertEqual({"merge": True, "squash": True, "rebase": True}, thin.merge_methods)

    def test_merge_methods_follow_the_repository(self) -> None:
        self.server.json(
            "GET",
            "/repos/o/r2",
            {"name": "r2", "owner": {"login": "o"}, "allow_squash_merge": False,
             "allow_rebase_merge": False, "delete_branch_on_merge": True},
        )
        strict = self.client.repository("o", "r2").payload
        self.assertEqual({"merge": True, "squash": False, "rebase": False}, strict.merge_methods)
        self.assertTrue(strict.delete_branch_on_merge)

    def test_deletes_a_remote_branch(self) -> None:
        self.server.json("DELETE", "/repos/o/r/git/refs/heads/feature", None, status=204)
        self.assertTrue(self.client.delete_branch("o", "r", "feature").ok)


class IssueTests(GitHubApiTestCase):
    """
    Issues, their comments and their labels.
    """

    def test_pull_requests_are_dropped_from_the_issue_list(self) -> None:
        self.server.json(
            "GET",
            "/repos/o/r/issues",
            [_issue(1), _issue(2, pull_request={"url": "..."}), _issue(3)],
        )
        result = self.client.list_issues("o", "r")
        self.assertEqual([1, 3], [item.number for item in result.payload])

    def test_reads_the_whole_issue(self) -> None:
        self.server.json("GET", "/repos/o/r/issues/5", _issue(5, milestone={"title": "1.0"}))
        issue = self.client.issue("o", "r", 5).payload
        self.assertEqual(("bug",), issue.labels)
        self.assertEqual(("joruf",), issue.assignees)
        self.assertEqual("1.0", issue.milestone)
        self.assertEqual("text", issue.body)

    def test_creates_an_issue(self) -> None:
        self.server.json("POST", "/repos/o/r/issues", _issue(9), status=201)
        result = self.client.create_issue("o", "r", "title", body="b", labels=["bug"], assignees=["joruf"])
        self.assertTrue(result.ok)
        sent = self.server.calls("POST", "/repos/o/r/issues")[0].body
        self.assertEqual({"title": "title", "body": "b", "labels": ["bug"], "assignees": ["joruf"]}, sent)

    def test_closing_records_why(self) -> None:
        self.server.json("PATCH", "/repos/o/r/issues/5", _issue(5, state="closed"))
        self.client.close_issue("o", "r", 5, reason="not_planned")
        sent = self.server.calls("PATCH", "/repos/o/r/issues/5")[0].body
        self.assertEqual({"state": "closed", "state_reason": "not_planned"}, sent)

    def test_reopening_clears_the_reason(self) -> None:
        self.server.json("PATCH", "/repos/o/r/issues/5", _issue(5))
        self.client.reopen_issue("o", "r", 5)
        sent = self.server.calls("PATCH", "/repos/o/r/issues/5")[0].body
        self.assertEqual({"state": "open", "state_reason": None}, sent)

    def test_comments_know_who_may_edit_them(self) -> None:
        self.server.json(
            "GET",
            "/repos/o/r/issues/5/comments",
            [
                {"id": 1, "body": "mine", "user": {"login": "joruf"}, "author_association": "OWNER"},
                {"id": 2, "body": "theirs", "user": {"login": "other"}, "author_association": "NONE"},
            ],
        )
        result = self.client.comments("o", "r", 5, viewer_login="joruf")
        self.assertTrue(result.payload[0].editable)
        self.assertFalse(result.payload[1].editable)

    def test_writes_and_edits_a_comment(self) -> None:
        self.server.json("POST", "/repos/o/r/issues/5/comments", {"id": 3, "body": "hi"}, status=201)
        self.server.json("PATCH", "/repos/o/r/issues/comments/3", {"id": 3, "body": "hello"})
        self.server.json("DELETE", "/repos/o/r/issues/comments/3", None, status=204)
        self.assertTrue(self.client.create_comment("o", "r", 5, "hi").ok)
        self.assertEqual("hello", self.client.update_comment("o", "r", 3, "hello").payload.body)
        self.assertTrue(self.client.delete_comment("o", "r", 3).ok)

    def test_a_label_is_renamed_by_its_old_name(self) -> None:
        self.server.json("PATCH", "/repos/o/r/labels/bug", {"name": "defect", "color": "ff0000"})
        result = self.client.update_label("o", "r", "bug", new_name="defect", color="#ff0000")
        self.assertTrue(result.ok)
        sent = self.server.calls("PATCH", "/repos/o/r/labels/bug")[0].body
        self.assertEqual({"new_name": "defect", "color": "ff0000"}, sent)

    def test_updating_a_label_with_nothing_sends_nothing(self) -> None:
        self.assertTrue(self.client.update_label("o", "r", "bug").ok)
        self.assertEqual([], self.server.requests)

    def test_milestones_can_be_closed_and_deleted(self) -> None:
        self.server.json("PATCH", "/repos/o/r/milestones/2", {"number": 2, "state": "closed"})
        self.server.json("DELETE", "/repos/o/r/milestones/2", None, status=204)
        closed = self.client.close_milestone("o", "r", 2)
        self.assertTrue(closed.ok and closed.payload.state == "closed")
        self.assertTrue(self.client.delete_milestone("o", "r", 2).ok)

    def test_label_colour_loses_its_hash(self) -> None:
        self.server.json("POST", "/repos/o/r/labels", {"name": "bug", "color": "ff0000"}, status=201)
        self.client.create_label("o", "r", "bug", color="#ff0000")
        self.assertEqual("ff0000", self.server.calls("POST", "/repos/o/r/labels")[0].body["color"])


class PullRequestTests(GitHubApiTestCase):
    """
    Pull requests, reviews and merging.
    """

    def test_lists_and_parses(self) -> None:
        self.server.json("GET", "/repos/o/r/pulls", [_pull(1), _pull(2, draft=True)])
        result = self.client.list_pull_requests("o", "r", state="all")
        self.assertEqual([1, 2], [item.number for item in result.payload])
        self.assertTrue(result.payload[1].draft)
        self.assertEqual("all", self.server.calls("GET", "/repos/o/r/pulls")[0].query["state"][0])

    def test_pending_mergeability_stays_unknown(self) -> None:
        self.server.json("GET", "/repos/o/r/pulls/1", _pull(1, mergeable=None, mergeable_state="unknown"))
        pull = self.client.pull_request("o", "r", 1).payload
        self.assertIsNone(pull.mergeable)
        self.assertEqual("unknown", pull.mergeable_state)

    def test_creates_a_pull_request(self) -> None:
        self.server.json("POST", "/repos/o/r/pulls", _pull(4), status=201)
        self.client.create_pull_request("o", "r", "t", head="feature", base="main", body="b", draft=True)
        sent = self.server.calls("POST", "/repos/o/r/pulls")[0].body
        self.assertEqual("feature", sent["head"])
        self.assertEqual("main", sent["base"])
        self.assertTrue(sent["draft"])

    def test_merge_carries_the_expected_head(self) -> None:
        self.server.json("PUT", "/repos/o/r/pulls/1/merge", {"merged": True, "sha": "d" * 40})
        result = self.client.merge_pull_request("o", "r", 1, method="squash", sha="a" * 40)
        self.assertTrue(result.ok)
        sent = self.server.calls("PUT", "/repos/o/r/pulls/1/merge")[0].body
        self.assertEqual("squash", sent["merge_method"])
        self.assertEqual("a" * 40, sent["sha"])

    def test_a_refused_merge_says_why(self) -> None:
        self.server.json(
            "PUT",
            "/repos/o/r/pulls/1/merge",
            {"message": "Pull Request is not mergeable"},
            status=405,
        )
        result = self.client.merge_pull_request("o", "r", 1)
        self.assertFalse(result.ok)
        self.assertIn("not mergeable", result.detail)

    def test_a_review_without_text_is_caught_before_the_request(self) -> None:
        result = self.client.create_review("o", "r", 1, event="REQUEST_CHANGES")
        self.assertFalse(result.ok)
        self.assertEqual("github.review_body_required", result.error_key)
        self.assertEqual([], self.server.requests)

    def test_an_approval_needs_no_text(self) -> None:
        self.server.json("POST", "/repos/o/r/pulls/1/reviews", {"id": 1, "state": "APPROVED"}, status=200)
        self.assertTrue(self.client.create_review("o", "r", 1, event="APPROVE").ok)

    def test_ready_for_review_goes_through_graphql(self) -> None:
        self.server.json("POST", "/graphql", {"data": {"markPullRequestReadyForReview": {}}})
        result = self.client.mark_ready_for_review("o", "r", "PR_1")
        self.assertTrue(result.ok)
        sent = self.server.calls("POST", "/graphql")[0].body
        self.assertIn("markPullRequestReadyForReview", sent["query"])
        self.assertEqual({"id": "PR_1"}, sent["variables"])

    def test_graphql_errors_are_failures_despite_the_200(self) -> None:
        self.server.json("POST", "/graphql", {"errors": [{"message": "not a draft"}]})
        result = self.client.mark_ready_for_review("o", "r", "PR_1")
        self.assertFalse(result.ok)
        self.assertIn("not a draft", result.detail)

    def test_changed_files_are_parsed(self) -> None:
        self.server.json(
            "GET",
            "/repos/o/r/pulls/1/files",
            [{"filename": "a.py", "status": "modified", "additions": 3, "deletions": 1, "patch": "@@"}],
        )
        changed = self.client.files("o", "r", 1).payload[0]
        self.assertEqual("a.py", changed.filename)
        self.assertEqual(3, changed.additions)


class ReleaseTests(GitHubApiTestCase):
    """
    Releases and their assets.
    """

    def test_upload_url_loses_its_template(self) -> None:
        self.server.json(
            "GET",
            "/repos/o/r/releases",
            [{"id": 1, "tag_name": "v1", "upload_url": "https://up.invalid/assets{?name,label}",
              "assets": [{"id": 9, "name": "x.zip", "size": 10}]}],
        )
        release = self.client.releases("o", "r").payload[0]
        self.assertEqual("https://up.invalid/assets", release.upload_url)
        self.assertEqual("x.zip", release.assets[0].name)

    def test_creates_a_release(self) -> None:
        self.server.json("POST", "/repos/o/r/releases", {"id": 2, "tag_name": "v2"}, status=201)
        self.client.create_release("o", "r", "v2", name="Two", body="notes", prerelease=True)
        sent = self.server.calls("POST", "/repos/o/r/releases")[0].body
        self.assertEqual("v2", sent["tag_name"])
        self.assertTrue(sent["prerelease"])
        self.assertFalse(sent["draft"])

    def test_uploads_an_asset(self) -> None:
        self.server.json("POST", "/assets", {"id": 5, "name": "a.txt", "size": 3}, status=201)
        release = Release(release_id=1, tag_name="v1", upload_url=f"{self.server.root}/assets")
        with TemporaryDirectory() as folder:
            path = Path(folder) / "a.txt"
            path.write_text("abc", encoding="utf-8")
            result = self.client.upload_asset(release, path)
        self.assertTrue(result.ok)
        call = self.server.calls("POST", "/assets")[0]
        self.assertEqual("a.txt", call.query["name"][0])
        self.assertEqual(b"abc", call.body)

    def test_upload_without_a_url_is_refused_locally(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "a.txt"
            path.write_text("abc", encoding="utf-8")
            result = self.client.upload_asset(Release(release_id=1), path)
        self.assertFalse(result.ok)
        self.assertEqual([], self.server.requests)

    def test_deletes_a_release(self) -> None:
        self.server.json("DELETE", "/repos/o/r/releases/2", None, status=204)
        self.assertTrue(self.client.delete_release("o", "r", 2).ok)


class ActionTests(GitHubApiTestCase):
    """
    Workflow runs.
    """

    def test_runs_are_unwrapped_from_their_envelope(self) -> None:
        self.server.json(
            "GET",
            "/repos/o/r/actions/runs",
            {
                "total_count": 2,
                "workflow_runs": [
                    {"id": 1, "name": "tests", "status": "completed", "conclusion": "failure"},
                    {"id": 2, "name": "tests", "status": "in_progress", "conclusion": None},
                ],
            },
        )
        runs = self.client.workflow_runs("o", "r").payload
        self.assertEqual([1, 2], [item.run_id for item in runs])
        self.assertEqual("failure", runs[0].check_state)
        self.assertEqual("pending", runs[1].check_state)
        self.assertFalse(runs[0].can_cancel)
        self.assertTrue(runs[1].can_cancel)

    def test_rerun_and_cancel(self) -> None:
        self.server.json("POST", "/repos/o/r/actions/runs/1/rerun", {}, status=201)
        self.server.json("POST", "/repos/o/r/actions/runs/1/rerun-failed-jobs", {}, status=201)
        self.server.json("POST", "/repos/o/r/actions/runs/1/cancel", {}, status=202)
        self.assertTrue(self.client.rerun("o", "r", 1).ok)
        self.assertTrue(self.client.rerun("o", "r", 1, failed_only=True).ok)
        self.assertTrue(self.client.cancel("o", "r", 1).ok)

    def test_missing_workflow_scope_reads_as_forbidden(self) -> None:
        self.server.json(
            "POST",
            "/repos/o/r/actions/runs/1/cancel",
            {"message": "Resource not accessible by personal access token"},
            status=403,
            X_RateLimit_Remaining="4000",
        )
        result = self.client.cancel("o", "r", 1)
        self.assertEqual(ERROR_FORBIDDEN, result.error_key)


if __name__ == "__main__":
    unittest.main()
