import httpx

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
