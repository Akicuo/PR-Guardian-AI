"""GitHub OAuth authentication routes"""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import create_access_token, decrypt_token, encrypt_token
from app.models import User
from app.config import get_settings

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def github_login():
    """Redirect user to GitHub OAuth page"""
    if not settings.github_app_client_id:
        raise HTTPException(status_code=500, detail="GitHub App not configured")

    redirect_uri = f"{settings.app_url}/auth/callback"
    scope = "read:org,repo,user:email"

    github_auth_url = (
        f"https://github.com/login/oauth/authorize"
        f"?client_id={settings.github_app_client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={scope}"
    )
    return RedirectResponse(github_auth_url)


@router.get("/callback")
async def github_callback(
    code: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Handle GitHub OAuth callback"""
    # Exchange code for access token
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": settings.github_app_client_id,
                "client_secret": settings.github_app_client_secret,
                "code": code,
            },
            headers={"Accept": "application/json"}
        )
        token_data = token_response.json()

    if "error" in token_data:
        raise HTTPException(status_code=400, detail=f"OAuth failed: {token_data.get('error_description', 'Unknown error')}")

    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="No access token received")

    # Get user info from GitHub
    async with httpx.AsyncClient() as client:
        user_response = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json"
            }
        )
        github_user = user_response.json()

    # Get user emails
    async with httpx.AsyncClient() as client:
        email_response = await client.get(
            "https://api.github.com/user/emails",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json"
            }
        )
        emails = email_response.json()
        # Find primary email
        primary_email = next((e["email"] for e in emails if e.get("primary")), github_user.get("email"))

    # Find or create user
    result = await db.execute(select(User).where(User.github_id == github_user["id"]))
    user = result.scalar_one_or_none()

    if not user:
        # Create new user
        user = User(
            github_id=github_user["id"],
            github_login=github_user["login"],
            github_email=primary_email,
            github_avatar_url=github_user.get("avatar_url"),
            github_token=encrypt_token(access_token),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    else:
        # Update existing user
        user.github_token = encrypt_token(access_token)
        user.github_login = github_user["login"]
        user.github_email = primary_email
        user.github_avatar_url = github_user.get("avatar_url")
        await db.commit()

    # Create session token
    session_token = create_access_token({"user_id": user.id})

    # Set cookie and redirect to dashboard
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        secure=False,  # Set to True in production with HTTPS
        samesite="lax",
        max_age=3600 * 24 * 7  # 7 days
    )
    return response


@router.get("/logout")
async def logout():
    """Clear session and redirect to home"""
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("session_token")
    return response


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> User:
    """Dependency to get authenticated user from session cookie"""
    session_token = request.cookies.get("session_token")

    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    from app.core.security import decode_access_token
    payload = decode_access_token(session_token)

    if not payload:
        raise HTTPException(status_code=401, detail="Invalid session")

    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid session")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user
