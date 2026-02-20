"""User schemas for API validation"""

from datetime import datetime
from pydantic import BaseModel, EmailStr


class UserBase(BaseModel):
    """Base user schema"""
    github_login: str
    github_email: EmailStr | None = None
    github_avatar_url: str | None = None
    review_language: str = "de"
    notification_enabled: bool = True


class UserCreate(UserBase):
    """Schema for creating a user (from GitHub OAuth)"""
    github_id: int
    github_token: str  # Will be encrypted


class UserResponse(UserBase):
    """Schema for user response"""
    id: int
    github_id: int
    github_installation_id: int | None = None
    created_at: datetime
    last_login_at: datetime | None = None

    class Config:
        from_attributes = True
