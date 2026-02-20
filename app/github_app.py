import base64
import httpx
from typing import Dict, List, Optional

from .config import get_settings

GITHUB_API_BASE = "https://api.github.com"


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


async def get_pr_details(repo_owner: str, repo_name: str, pr_number: int) -> Dict:
    """
    Get PR details from GitHub API.
    """
    url = f"{GITHUB_API_BASE}/repos/{repo_owner}/{repo_name}/pulls/{pr_number}"
    response = await github_request("GET", url)
    return response.json()


async def get_pr_files(repo_owner: str, repo_name: str, pr_number: int) -> List[Dict]:
    """
    Get list of files changed in a PR with metadata.

    Returns:
        List of files with: filename, status, additions, deletions, patch, sha, blob_url
    """
    url = f"{GITHUB_API_BASE}/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/files"
    response = await github_request("GET", url)
    return response.json()


async def get_file_content(
    repo_owner: str,
    repo_name: str,
    file_path: str,
    ref: Optional[str] = None
) -> Dict:
    """
    Get file content from a specific branch/ref.

    Args:
        ref: Branch name, commit SHA, or PR head SHA
             (e.g., "refs/pull/123/head" or "feature-branch")

    Returns:
        Dict with: content (base64 encoded), encoding, sha, size
    """
    import urllib.parse
    encoded_path = urllib.parse.quote(file_path, safe='')
    url = f"{GITHUB_API_BASE}/repos/{repo_owner}/{repo_name}/contents/{encoded_path}"
    params = {"ref": ref} if ref else {}
    response = await github_request("GET", url, params=params)
    return response.json()


async def get_file_lines(
    repo_owner: str,
    repo_name: str,
    file_path: str,
    start_line: int,
    end_line: int,
    ref: Optional[str] = None
) -> str:
    """
    Get specific lines from a file.

    Returns:
        String containing the requested line range
    """
    file_data = await get_file_content(repo_owner, repo_name, file_path, ref)

    # Decode base64 content
    content_bytes = base64.b64decode(file_data["content"])
    content = content_bytes.decode("utf-8")

    # Split into lines and extract range (GitHub line numbers are 1-indexed)
    lines = content.split("\n")
    requested_lines = lines[start_line - 1:end_line]

    return "\n".join(requested_lines)


async def get_pr_head_ref(
    repo_owner: str,
    repo_name: str,
    pr_number: int
) -> tuple[str, str]:
    """
    Get the head reference and SHA for a PR.

    Returns:
        (head_ref, head_sha) - e.g., ("feature-branch", "abc123...")
    """
    pr_data = await get_pr_details(repo_owner, repo_name, pr_number)
    head_ref = pr_data.get("head", {}).get("ref", "")
    head_sha = pr_data.get("head", {}).get("sha", "")
    return head_ref, head_sha


async def update_pr_comment(
    repo_owner: str,
    repo_name: str,
    comment_id: int,
    body: str
) -> None:
    """
    Update an existing PR comment.
    """
    url = f"{GITHUB_API_BASE}/repos/{repo_owner}/{repo_name}/issues/comments/{comment_id}"
    payload = {"body": body}
    await github_request("PATCH", url, json=payload)


async def find_bot_comment(
    repo_owner: str,
    repo_name: str,
    pr_number: int,
    bot_name: str
) -> Optional[Dict]:
    """
    Find the most recent comment by this bot in a PR.

    Returns:
        Comment dict if found, None otherwise
    """
    url = f"{GITHUB_API_BASE}/repos/{repo_owner}/{repo_name}/issues/{pr_number}/comments"
    response = await github_request("GET", url)
    comments = response.json()

    # Find most recent comment by bot
    for comment in reversed(comments):
        if bot_name in comment.get("body", ""):
            return comment
    return None
