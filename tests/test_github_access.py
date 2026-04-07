import importlib
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

os.environ.setdefault("GITHUB_TOKEN", "test-github-token")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

from app.github_app import get_repository_permission, has_write_permission, is_trusted_repository_user

main_module = importlib.import_module("app.main")


class GitHubAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_member_association_is_trusted_without_permission_lookup(self) -> None:
        with patch("app.github_app.get_repository_permission", new_callable=AsyncMock) as permission_mock:
            trusted = await is_trusted_repository_user(
                repo_owner="acme",
                repo_name="repo",
                username="alice",
                author_association="MEMBER",
            )

        self.assertTrue(trusted)
        permission_mock.assert_not_awaited()

    async def test_write_permission_is_trusted(self) -> None:
        with patch(
            "app.github_app.get_repository_permission",
            new=AsyncMock(return_value="write"),
        ):
            trusted = await is_trusted_repository_user(
                repo_owner="acme",
                repo_name="repo",
                username="bob",
                author_association="CONTRIBUTOR",
            )

        self.assertTrue(trusted)

    async def test_read_permission_is_not_trusted(self) -> None:
        with patch(
            "app.github_app.get_repository_permission",
            new=AsyncMock(return_value="read"),
        ):
            trusted = await is_trusted_repository_user(
                repo_owner="acme",
                repo_name="repo",
                username="carol",
                author_association="CONTRIBUTOR",
            )

        self.assertFalse(trusted)

    async def test_missing_collaborator_permission_is_treated_as_untrusted(self) -> None:
        request = httpx.Request("GET", "https://api.github.com/repos/acme/repo/collaborators/dave/permission")
        response = httpx.Response(404, request=request)
        error = httpx.HTTPStatusError("not found", request=request, response=response)

        with patch(
            "app.github_app.github_request",
            new=AsyncMock(side_effect=error),
        ):
            permission = await get_repository_permission("acme", "repo", "dave")

        self.assertIsNone(permission)

    def test_has_write_permission_matches_repo_write_roles(self) -> None:
        self.assertTrue(has_write_permission("write"))
        self.assertTrue(has_write_permission("maintain"))
        self.assertTrue(has_write_permission("admin"))
        self.assertFalse(has_write_permission("triage"))
        self.assertFalse(has_write_permission("read"))
        self.assertFalse(has_write_permission(None))


class WebhookTrustPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main_module.app)

    def test_untrusted_pr_author_is_ignored(self) -> None:
        payload = {
            "action": "opened",
            "repository": {
                "full_name": "acme/repo",
                "name": "repo",
                "owner": {"login": "acme"},
            },
            "pull_request": {
                "number": 7,
                "title": "Example PR",
                "body": "Please review",
                "comments_url": "https://api.github.com/repos/acme/repo/issues/7/comments",
                "diff_url": "https://github.com/acme/repo/pull/7.diff",
                "author_association": "CONTRIBUTOR",
                "head": {"sha": "abc123"},
                "user": {"login": "external-user"},
            },
        }

        with patch(
            "app.main.is_trusted_repository_user",
            new=AsyncMock(return_value=False),
        ) as trusted_mock:
            response = self.client.post(
                "/webhook",
                json=payload,
                headers={"X-GitHub-Event": "pull_request"},
            )

        self.assertEqual(200, response.status_code)
        self.assertEqual({"msg": "ignored untrusted PR author"}, response.json())
        trusted_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
