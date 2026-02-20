"""GitHub App JWT generation for API authentication"""

import time
import jwt

from app.config import get_settings

settings = get_settings()


def create_github_app_jwt() -> str:
    """
    Create a JWT for GitHub App authentication.

    This JWT is used to authenticate as the GitHub App when:
    - Creating installation access tokens
    - Managing webhooks
    - Accessing App APIs

    Returns:
        Encoded JWT token

    Raises:
        ValueError: If GITHUB_APP_ID or GITHUB_APP_PRIVATE_KEY is not set
    """
    if not settings.github_app_id:
        raise ValueError("GITHUB_APP_ID is not set")

    if not settings.github_app_private_key:
        raise ValueError("GITHUB_APP_PRIVATE_KEY is not set")

    # GitHub App JWT must use RS256 algorithm
    # The private key should be in PEM format
    try:
        private_key = settings.github_app_private_key
        if private_key.startswith("-----BEGIN"):
            # It's already in PEM format
            pem_key = private_key
        else:
            # Try to parse as a single-line base64 key
            import base64
            pem_key = base64.b64decode(private_key).decode("utf-8")
    except Exception:
        raise ValueError("Invalid GITHUB_APP_PRIVATE_KEY format")

    # JWT payload
    now = int(time.time())
    payload = {
        "iat": now - 60,  # Issued at (60 seconds ago to allow for clock skew)
        "exp": now + (10 * 60),  # Expires in 10 minutes
        "iss": settings.github_app_id,  # Issuer (App ID)
    }

    # Encode JWT
    token = jwt.encode(payload, pem_key, algorithm="RS256")
    return token


async def create_installation_token(installation_id: int) -> dict:
    """
    Create an installation access token for a GitHub App installation.

    Installation tokens are used to make API requests on behalf of
    a specific installation of the GitHub App.

    Args:
        installation_id: The GitHub installation ID

    Returns:
        Dictionary with token and metadata:
        {
            "token": "ghs_xxx",
            "expires_at": "2026-02-19T13:00:00Z",
            "permissions": {...},
            "repositories": [...]
        }

    Raises:
        httpx.HTTPError: If the API request fails
    """
    import httpx

    # Create GitHub App JWT
    app_jwt = create_github_app_jwt()

    # Create installation token via GitHub API
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers)
        response.raise_for_status()
        return response.json()
