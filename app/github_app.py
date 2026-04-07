import logging

import httpx
from typing import Any

from .config import get_settings

GITHUB_API_BASE = "https://api.github.com"
TRUSTED_AUTHOR_ASSOCIATIONS = {"OWNER", "MEMBER"}
WRITE_PERMISSIONS = {"admin", "maintain", "write"}

logger = logging.getLogger("pr-guardian.github")


def get_github_token() -> str:
    """Get the GitHub Personal Access Token from settings."""
    settings = get_settings()
    return settings.github_token


async def github_request(method: str, url: str, **kwargs):
    """
    Make an authenticated request to GitHub API using PAT.
    """
    token = get_github_token()
    headers = kwargs.pop("headers", {})
    headers.update({
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "PR-Guardian-AI/1.0",
    })
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        return response


async def get_pr_details(repo_owner: str, repo_name: str, pr_number: int) -> dict[str, Any]:
    """
    Get PR details from GitHub API.
    """
    url = f"{GITHUB_API_BASE}/repos/{repo_owner}/{repo_name}/pulls/{pr_number}"
    response = await github_request("GET", url)
    return response.json()


async def get_repository_permission(repo_owner: str, repo_name: str, username: str) -> str | None:
    """
    Get a user's explicit repository permission level, if GitHub exposes it.
    """
    if not username:
        return None

    url = f"{GITHUB_API_BASE}/repos/{repo_owner}/{repo_name}/collaborators/{username}/permission"
    try:
        response = await github_request("GET", url)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {403, 404}:
            logger.info(
                "Permission lookup unavailable for %s/%s user %s (status=%s)",
                repo_owner,
                repo_name,
                username,
                exc.response.status_code,
            )
            return None
        raise
    payload = response.json()
    permission = str(payload.get("permission", "")).strip().lower()
    return permission or None


def has_write_permission(permission: str | None) -> bool:
    """
    Return True when the permission grants write-level or higher access.
    """
    if not permission:
        return False

    return permission.strip().lower() in WRITE_PERMISSIONS


async def is_trusted_repository_user(
    repo_owner: str,
    repo_name: str,
    username: str,
    author_association: str | None = None,
) -> bool:
    """
    Allow organization members, repository owners, and users with write access.
    """
    association = (author_association or "").strip().upper()
    if association in TRUSTED_AUTHOR_ASSOCIATIONS:
        return True

    permission = await get_repository_permission(repo_owner, repo_name, username)
    return has_write_permission(permission)
