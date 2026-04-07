import importlib
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

os.environ.setdefault("GITHUB_TOKEN", "test-github-token")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")

from app.merge_conflicts import (
    MERGE_CONFLICT_OFFER_MARKER,
    MergeConflictResult,
    build_merge_conflict_offer_comment,
    is_merge_conflict_command,
    merge_conflict_offer_already_posted,
)

main_module = importlib.import_module("app.main")


class MergeConflictHelperTests(unittest.TestCase):
    def test_command_requires_bot_addressing_and_conflict_phrase(self) -> None:
        self.assertTrue(is_merge_conflict_command("/pr-guardian resolve conflicts", "PR Guardian AI"))
        self.assertTrue(is_merge_conflict_command("PR Guardian AI please solve merge conflicts", "PR Guardian AI"))
        self.assertFalse(is_merge_conflict_command("please resolve conflicts", "PR Guardian AI"))
        self.assertFalse(is_merge_conflict_command("/pr-guardian review this", "PR Guardian AI"))

    def test_offer_marker_detection_prevents_duplicate_prompts(self) -> None:
        comments = [{"body": build_merge_conflict_offer_comment("PR Guardian AI")}]
        self.assertTrue(merge_conflict_offer_already_posted(comments))
        self.assertIn(MERGE_CONFLICT_OFFER_MARKER, comments[0]["body"])


class MergeConflictWebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main_module.app)

    def test_issue_comment_merge_request_runs_for_authorized_user(self) -> None:
        payload = {
            "action": "created",
            "repository": {
                "full_name": "acme/repo",
                "name": "repo",
                "owner": {"login": "acme"},
            },
            "issue": {
                "number": 8,
                "pull_request": {"url": "https://api.github.com/repos/acme/repo/pulls/8"},
                "comments_url": "https://api.github.com/repos/acme/repo/issues/8/comments",
            },
            "comment": {
                "body": "/pr-guardian resolve conflicts",
                "author_association": "MEMBER",
                "user": {"login": "maintainer"},
            },
        }
        pr_details = {
            "number": 8,
            "user": {"login": "contributor"},
            "mergeable": False,
            "mergeable_state": "dirty",
            "comments_url": "https://api.github.com/repos/acme/repo/issues/8/comments",
            "head": {
                "sha": "headsha",
                "ref": "feature/conflicts",
                "repo": {"full_name": "acme/repo"},
            },
            "base": {
                "sha": "basesha",
                "ref": "main",
                "repo": {"full_name": "acme/repo"},
            },
        }

        with patch("app.main.get_pr_details", new=AsyncMock(return_value=pr_details)), patch(
            "app.main.is_authorized_conflict_requester",
            new=AsyncMock(return_value=True),
        ), patch(
            "app.main.try_resolve_pull_request_conflicts",
            new=AsyncMock(
                return_value=MergeConflictResult(
                    success=True,
                    message="I pushed a merge conflict resolution commit.",
                    resolved_files=["app/main.py"],
                    skipped_files=[],
                    commit_sha="abc123",
                )
            ),
        ), patch("app.main.post_pr_comment", new=AsyncMock()) as post_comment_mock:
            response = self.client.post(
                "/webhook",
                json=payload,
                headers={"X-GitHub-Event": "issue_comment"},
            )

        self.assertEqual(200, response.status_code)
        self.assertEqual({"msg": "merge conflict request processed"}, response.json())
        post_comment_mock.assert_awaited_once()

    def test_unauthorized_issue_comment_request_is_ignored(self) -> None:
        payload = {
            "action": "created",
            "repository": {
                "full_name": "acme/repo",
                "name": "repo",
                "owner": {"login": "acme"},
            },
            "issue": {
                "number": 8,
                "pull_request": {"url": "https://api.github.com/repos/acme/repo/pulls/8"},
                "comments_url": "https://api.github.com/repos/acme/repo/issues/8/comments",
            },
            "comment": {
                "body": "/pr-guardian resolve conflicts",
                "author_association": "NONE",
                "user": {"login": "random-user"},
            },
        }
        pr_details = {
            "number": 8,
            "user": {"login": "contributor"},
            "mergeable": False,
            "mergeable_state": "dirty",
            "comments_url": "https://api.github.com/repos/acme/repo/issues/8/comments",
        }

        with patch("app.main.get_pr_details", new=AsyncMock(return_value=pr_details)), patch(
            "app.main.is_authorized_conflict_requester",
            new=AsyncMock(return_value=False),
        ), patch("app.main.post_pr_comment", new=AsyncMock()) as post_comment_mock:
            response = self.client.post(
                "/webhook",
                json=payload,
                headers={"X-GitHub-Event": "issue_comment"},
            )

        self.assertEqual(200, response.status_code)
        self.assertEqual({"msg": "ignored unauthorized merge conflict request"}, response.json())
        post_comment_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
