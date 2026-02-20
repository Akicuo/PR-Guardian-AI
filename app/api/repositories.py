"""Repository management API routes"""

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.core.database import get_db
from app.core.security import decrypt_token
from app.api.auth import get_current_user
from app.models import User, Repository
from app.schemas.repository import RepositoryResponse

router = APIRouter(prefix="/api/repositories", tags=["repositories"])


@router.get("/", response_model=List[RepositoryResponse])
async def list_repositories(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List user's repositories (merge GitHub API + database)"""
    # Fetch from GitHub
    github_token = decrypt_token(current_user.github_token)

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.github.com/user/repos",
            headers={
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github+json",
            },
            params={"per_page": 100, "sort": "updated"}
        )
        response.raise_for_status()
        github_repos = response.json()

    # Get monitored repos from database
    result = await db.execute(
        select(Repository).where(Repository.user_id == current_user.id)
    )
    monitored_repos = result.scalars().all()

    # Merge data
    monitored_ids = {repo.github_repo_id for repo in monitored_repos}
    for gh_repo in github_repos:
        gh_repo["monitored"] = gh_repo["id"] in monitored_ids

    return github_repos


@router.post("/{repo_id}/monitor")
async def start_monitoring(
    repo_id: int,
    branches: List[str],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Enable monitoring for a repository"""
    github_token = decrypt_token(current_user.github_token)

    # Get repo info from GitHub
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repositories/{repo_id}",
            headers={
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github+json",
            }
        )
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Repository not found")
        response.raise_for_status()
        gh_repo = response.json()

    # Create or update repository record
    result = await db.execute(
        select(Repository).where(
            Repository.github_repo_id == repo_id,
            Repository.user_id == current_user.id
        )
    )
    repo = result.scalar_one_or_none()

    if repo:
        repo.is_monitored = True
        repo.branches_to_monitor = str(branches)
    else:
        repo = Repository(
            user_id=current_user.id,
            github_repo_id=gh_repo["id"],
            repo_name=gh_repo["name"],
            repo_full_name=gh_repo["full_name"],
            repo_owner=gh_repo["owner"]["login"],
            repo_description=gh_repo.get("description"),
            repo_private=gh_repo["private"],
            repo_url=gh_repo["html_url"],
            is_monitored=True,
            branches_to_monitor=str(branches),
        )
        db.add(repo)

    await db.commit()
    await db.refresh(repo)

    return {"status": "monitoring", "repo_id": repo.id, "branches": branches}


@router.delete("/{repo_id}/monitor")
async def stop_monitoring(
    repo_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Disable monitoring for a repository"""
    result = await db.execute(
        select(Repository).where(
            Repository.github_repo_id == repo_id,
            Repository.user_id == current_user.id
        )
    )
    repo = result.scalar_one_or_none()

    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")

    repo.is_monitored = False
    await db.commit()

    return {"status": "stopped"}


@router.get("/{repo_id}/branches")
async def list_branches(
    repo_id: int,
    current_user: User = Depends(get_current_user)
):
    """List branches for a repository"""
    github_token = decrypt_token(current_user.github_token)

    # Get repo owner/name from database
    result = await db.execute(
        select(Repository).where(
            Repository.github_repo_id == repo_id,
            Repository.user_id == current_user.id
        )
    )
    repo = result.scalar_one_or_none()

    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")

    # Fetch branches from GitHub
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{repo.repo_full_name}/branches",
            headers={
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github+json",
            },
            params={"per_page": 100}
        )
        response.raise_for_status()
        branches = response.json()

    return {"branches": [b["name"] for b in branches]}
