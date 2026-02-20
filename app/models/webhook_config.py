"""WebhookConfig model for managing GitHub webhooks"""

from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.core.database import Base


class WebhookConfig(Base):
    """WebhookConfig model representing a GitHub webhook configuration"""

    __tablename__ = "webhook_configs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    repository_id = Column(Integer, ForeignKey("repositories.id"), nullable=False)

    # Webhook settings
    webhook_id = Column(Integer)  # GitHub webhook ID
    webhook_url = Column(String(500))
    webhook_secret = Column(String(255))  # Encrypted
    events = Column(Text)  # JSON array: ["pull_request", "pull_request_review"]

    # Status
    is_active = Column(Boolean, default=True)
    delivery_status = Column(String(50))  # active, failing, disabled

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_delivery_at = Column(DateTime)

    # Relationships
    user = relationship("User", back_populates="webhook_configs")
    repository = relationship("Repository", back_populates="webhook_configs")

    def __repr__(self):
        return f"<WebhookConfig(id={self.id}, webhook_id={self.webhook_id}, is_active={self.is_active})>"
