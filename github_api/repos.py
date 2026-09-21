"""
Repository endpoints: listing, details, settings, branches and tags.

Two decisions worth stating, because they are not obvious from the endpoints:

* Creating a repository goes to ``/user/repos`` for a personal one and to
  ``/orgs/{org}/repos`` for an organisation. Both are wrapped in one method, so a
  caller picks an owner rather than picking an endpoint.
* Deleting a repository is the one call here that cannot be undone, so it is
  spelled out on its own rather than hidden behind a generic ``send``. The
  confirmation belongs in the UI, but the method name has to make it impossible
  to reach by accident.
"""

from __future__ import annotations

from typing import Any

from github_api.http import ApiResult, as_model, as_models
from github_api.models import Branch, Repository

# Sort orders the repository list offers. ``pushed`` is the default because "what
# did I touch recently" is the question the list is usually opened with.
SORT_PUSHED = "pushed"
SORT_UPDATED = "updated"
SORT_CREATED = "created"
SORT_NAME = "full_name"


class RepositoryEndpoints:
    """
    Reads and changes repositories.
    """

    def repositories(
        self,
        affiliation: str = "owner,collaborator,organization_member",
        sort: str = SORT_PUSHED,
        limit: int = 0,
    ) -> ApiResult:
        """
        Lists every repository the token can see.

        Args:
            affiliation: Which relationships to include, as a comma-separated
                list of ``owner``, ``collaborator`` and ``organization_member``.
            sort: One of the ``SORT_*`` constants.
            limit: Stop after this many, zero for all of them.

        Returns:
            ApiResult: Payload is a ``list[Repository]``.
        """

        return as_models(
            self.get_all(
                "/user/repos",
                {"affiliation": affiliation, "sort": sort, "direction": "desc"},
                limit=limit,
            ),
            Repository.from_api,
        )

    def organizations(self) -> ApiResult:
        """
        Lists the organisations the token's account belongs to.

        Needs the ``read:org`` scope. Without it GitHub answers with an empty
        list rather than an error, which is why a caller must not read "no
        organisations" as "the account has none".

        Returns:
            ApiResult: Payload is a ``list[str]`` of organisation logins.
        """

        result = self.get_all("/user/orgs")
        if not result.ok:
            return result
        logins = [
            entry.get("login")
            for entry in result.items
            if isinstance(entry, dict) and isinstance(entry.get("login"), str)
        ]
        return ApiResult(ok=True, payload=logins, status=result.status)

    def repository(self, owner: str, repo: str) -> ApiResult:
        """
        Reads one repository.

        Args:
            owner: Repository owner.
            repo: Repository name.

        Returns:
            ApiResult: Payload is a ``Repository``.
        """

        return as_model(self.get(f"/repos/{owner}/{repo}"), Repository.from_api)

    def create_repository(
        self,
        name: str,
        description: str = "",
        private: bool = True,
        organization: str = "",
        auto_init: bool = True,
        gitignore_template: str = "",
        license_template: str = "",
        homepage: str = "",
    ) -> ApiResult:
        """
        Creates a repository.

        Args:
            name: Repository name.
            description: Short description.
            private: Whether it starts out private. Defaulting to private is
                deliberate: a repository made public by mistake cannot be made
                unseen, while the opposite mistake costs one click.
            organization: Organisation to create it in, empty for the personal
                account.
            auto_init: Whether to create an initial commit with a README, which is
                what makes the repository cloneable straight away.
            gitignore_template: Name of a ``.gitignore`` template, for example
                ``Python``.
            license_template: License key, for example ``mit``.
            homepage: Project website.

        Returns:
            ApiResult: Payload is the created ``Repository``.
        """

        body: dict[str, Any] = {
            "name": name,
            "private": bool(private),
            "auto_init": bool(auto_init),
        }
        if description:
            body["description"] = description
        if homepage:
            body["homepage"] = homepage
        if gitignore_template:
            body["gitignore_template"] = gitignore_template
        if license_template:
            body["license_template"] = license_template

        path = f"/orgs/{organization}/repos" if organization else "/user/repos"
        return as_model(self.send("POST", path, body), Repository.from_api)

    def update_repository(self, owner: str, repo: str, **changes: Any) -> ApiResult:
        """
        Changes repository settings.

        Only the keys that were passed are sent, so a caller changing the
        description cannot accidentally reset the visibility to a default.

        Args:
            owner: Repository owner.
            repo: Repository name.
            **changes: Any of ``name``, ``description``, ``homepage``,
                ``private``, ``visibility``, ``default_branch``, ``has_issues``,
                ``has_wiki``, ``has_projects``, ``has_discussions``,
                ``archived``, ``allow_squash_merge``, ``allow_merge_commit``,
                ``allow_rebase_merge``, ``delete_branch_on_merge``.

        Returns:
            ApiResult: Payload is the updated ``Repository``.
        """

        allowed = {
            "name",
            "description",
            "homepage",
            "private",
            "visibility",
            "default_branch",
            "has_issues",
            "has_wiki",
            "has_projects",
            "has_discussions",
            "archived",
            "allow_squash_merge",
            "allow_merge_commit",
            "allow_rebase_merge",
            "delete_branch_on_merge",
            "is_template",
        }
        body = {key: value for key, value in changes.items() if key in allowed}
        if not body:
            return self.repository(owner, repo)
        return as_model(self.send("PATCH", f"/repos/{owner}/{repo}", body), Repository.from_api)

    def set_topics(self, owner: str, repo: str, topics: list[str]) -> ApiResult:
        """
        Replaces the topic list.

        Topics live behind their own endpoint rather than the settings patch, and
        the call replaces the whole list rather than adding to it.

        Args:
            owner: Repository owner.
            repo: Repository name.
            topics: The complete new list. GitHub only accepts lowercase names
                made of letters, digits and hyphens, so the values are folded
                here rather than letting the server refuse the whole call over
                one capital letter.

        Returns:
            ApiResult: Payload is the stored topic list.
        """

        cleaned = [item.strip().lower().replace(" ", "-") for item in topics]
        return self.send(
            "PUT",
            f"/repos/{owner}/{repo}/topics",
            {"names": [item for item in cleaned if item]},
        )

    def delete_repository(self, owner: str, repo: str) -> ApiResult:
        """
        Deletes a repository, permanently.

        Needs the ``delete_repo`` scope. There is no undo and no trash: whoever
        calls this has to have asked first.

        Args:
            owner: Repository owner.
            repo: Repository name.

        Returns:
            ApiResult: Success carries no payload.
        """

        return self.send("DELETE", f"/repos/{owner}/{repo}")

    # ----------------------------------------------------------- refs and tags

    def branches(self, owner: str, repo: str, default_branch: str = "", limit: int = 0) -> ApiResult:
        """
        Lists the branches on the server.

        Args:
            owner: Repository owner.
            repo: Repository name.
            default_branch: Name of the default branch, so the result can mark it.
            limit: Stop after this many, zero for all of them.

        Returns:
            ApiResult: Payload is a ``list[Branch]``.
        """

        return as_models(
            self.get_all(f"/repos/{owner}/{repo}/branches", limit=limit),
            lambda entry: Branch.from_api(entry, default_branch),
        )

    def tags(self, owner: str, repo: str, limit: int = 0) -> ApiResult:
        """
        Lists the tags on the server.

        Args:
            owner: Repository owner.
            repo: Repository name.
            limit: Stop after this many, zero for all of them.

        Returns:
            ApiResult: Payload is a ``list[Branch]``, reusing that shape because a
                tag carries the same two fields a branch does.
        """

        return as_models(self.get_all(f"/repos/{owner}/{repo}/tags", limit=limit), Branch.from_api)

    def delete_branch(self, owner: str, repo: str, branch: str) -> ApiResult:
        """
        Deletes a branch on the server.

        Args:
            owner: Repository owner.
            repo: Repository name.
            branch: Branch name, without the ``refs/heads/`` prefix.

        Returns:
            ApiResult: Success carries no payload.
        """

        return self.send("DELETE", f"/repos/{owner}/{repo}/git/refs/heads/{branch}")

    def delete_tag(self, owner: str, repo: str, tag: str) -> ApiResult:
        """
        Deletes a tag on the server.

        Args:
            owner: Repository owner.
            repo: Repository name.
            tag: Tag name, without the ``refs/tags/`` prefix.

        Returns:
            ApiResult: Success carries no payload.
        """

        return self.send("DELETE", f"/repos/{owner}/{repo}/git/refs/tags/{tag}")

    def collaborator_list(self, owner: str, repo: str) -> ApiResult:
        """
        Lists the people who have access, with the access they have.

        Needs administrative permission on the repository, unlike ``collaborators``
        below, which only answers who can be assigned to an issue.

        Args:
            owner: Repository owner.
            repo: Repository name.

        Returns:
            ApiResult: Payload is a ``list[tuple[str, str]]`` of login and role.
        """

        result = self.get_all(f"/repos/{owner}/{repo}/collaborators")
        if not result.ok:
            return result
        people: list[tuple[str, str]] = []
        for entry in result.items:
            if not isinstance(entry, dict):
                continue
            login = entry.get("login")
            if not isinstance(login, str):
                continue
            role = entry.get("role_name")
            people.append((login, role if isinstance(role, str) else ""))
        return ApiResult(ok=True, payload=people, status=result.status)

    def add_collaborator(self, owner: str, repo: str, login: str, permission: str = "push") -> ApiResult:
        """
        Invites someone, or changes what an existing collaborator may do.

        The person has to accept the invitation before the access takes effect,
        which is why a successful call does not mean they are in the list yet.

        Args:
            owner: Repository owner.
            repo: Repository name.
            login: Account to invite.
            permission: ``pull``, ``triage``, ``push``, ``maintain`` or ``admin``.

        Returns:
            ApiResult: Payload is the invitation, or empty when the person
                already had access.
        """

        return self.send(
            "PUT", f"/repos/{owner}/{repo}/collaborators/{login}", {"permission": permission}
        )

    def remove_collaborator(self, owner: str, repo: str, login: str) -> ApiResult:
        """
        Takes someone's access away.

        Args:
            owner: Repository owner.
            repo: Repository name.
            login: Account to remove.

        Returns:
            ApiResult: Success carries no payload.
        """

        return self.send("DELETE", f"/repos/{owner}/{repo}/collaborators/{login}")

    def collaborators(self, owner: str, repo: str) -> ApiResult:
        """
        Lists the logins that can be assigned to issues and pull requests.

        The assignees endpoint is used rather than the collaborators one because
        it needs no administrative permission and answers exactly the question
        the assignee picker asks.

        Args:
            owner: Repository owner.
            repo: Repository name.

        Returns:
            ApiResult: Payload is a ``list[str]`` of logins.
        """

        result = self.get_all(f"/repos/{owner}/{repo}/assignees")
        if not result.ok:
            return result
        logins = [
            entry.get("login")
            for entry in result.items
            if isinstance(entry, dict) and isinstance(entry.get("login"), str)
        ]
        return ApiResult(ok=True, payload=logins, status=result.status)
