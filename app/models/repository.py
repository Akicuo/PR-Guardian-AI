"""Repository model for monitoring GitHub repositories"""

from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.core.database import Base


class Repository(Base):
    """Repository model representing a GitHub repository being monitored"""

    __tablename__ = "repositories"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # GitHub repository info
    github_repo_id = Column(Integer, unique=True, nullable=False, index=True)
    repo_name = Column(String(255), nullable=False)  # owner/repo format
    repo_full_name = Column(String(255), nullable=False)  # owner/repo
    repo_owner = Column(String(255), nullable=False)
    repo_description = Column(Text)
    repo_private = Column(Boolean, default=False)
    repo_url = Column(String(500))

    # Monitoring settings
    is_monitored = Column(Boolean, default=True)
    branches_to_monitor = Column(Text)  # JSON array: ["main", "develop"]
    exclude_branches = Column(Text)  # JSON array: ["staging", "release/*"]

    # AI Review settings (per repo override)
    ai_review_enabled = Column(Boolean, default=True)
    review_language = Column(String(50))  # Override user default

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_webhook_at = Column(DateTime)

    # Relationships
    user = relationship("User", back_populates="repositories")
    webhook_configs = relationship("WebhookConfig", back_populates="repository", cascade="all, delete-orphan")
    review_history = relationship("ReviewHistory", back_populates="repository", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Repository(id={self.id}, repo_full_name={self.repo_full_name}, is_monitored={self.is_monitored})>"
