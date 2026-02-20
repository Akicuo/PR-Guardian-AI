"""User model for GitHub OAuth authentication"""

from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import relationship

from app.core.database import Base


class User(Base):
    """User model representing a GitHub user"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    github_id = Column(Integer, unique=True, nullable=False, index=True)
    github_login = Column(String(255), nullable=False)
    github_email = Column(String(255))
    github_avatar_url = Column(String(500))
    github_token = Column(Text, nullable=False)  # Encrypted OAuth token
    github_refresh_token = Column(Text)  # For token refresh (if available)

    # GitHub App Installation
    github_installation_id = Column(Integer)  # Links to GitHub App installation

    # Preferences
    review_language = Column(String(50), default="de")  # de, en, etc.
    notification_enabled = Column(Boolean, default=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login_at = Column(DateTime)

    # Relationships
    repositories = relationship("Repository", back_populates="user", cascade="all, delete-orphan")
    webhook_configs = relationship("WebhookConfig", back_populates="user", cascade="all, delete-orphan")
    review_history = relationship("ReviewHistory", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(id={self.id}, github_login={self.github_login})>"
