"""Repository schemas for API validation"""

from datetime import datetime
from typing import List
from pydantic import BaseModel, Field


class RepositoryBase(BaseModel):
    """Base repository schema"""
    repo_name: str
    repo_full_name: str
    repo_owner: str
    repo_description: str | None = None
    repo_private: bool = False
    repo_url: str | None = None


class RepositoryCreate(RepositoryBase):
    """Schema for creating a repository"""
    github_repo_id: int
    branches_to_monitor: List[str] = ["main", "master"]
    exclude_branches: List[str] = []
    ai_review_enabled: bool = True
    review_language: str | None = None


class RepositoryUpdate(BaseModel):
    """Schema for updating a repository"""
    is_monitored: bool | None = None
    branches_to_monitor: List[str] | None = None
    exclude_branches: List[str] | None = None
    ai_review_enabled: bool | None = None
    review_language: str | None = None


class RepositoryResponse(RepositoryBase):
    """Schema for repository response"""
    id: int
    user_id: int
    github_repo_id: int
    is_monitored: bool
    branches_to_monitor: List[str] = []
    exclude_branches: List[str] = []
    ai_review_enabled: bool
    review_language: str | None = None
    created_at: datetime
    updated_at: datetime
    last_webhook_at: datetime | None = None

    class Config:
        from_attributes = True
