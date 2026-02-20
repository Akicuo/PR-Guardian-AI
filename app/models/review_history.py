"""ReviewHistory model for tracking AI code reviews"""

from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.core.database import Base


class ReviewHistory(Base):
    """ReviewHistory model representing an AI-generated code review"""

    __tablename__ = "review_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    repository_id = Column(Integer, ForeignKey("repositories.id"), nullable=False)

    # PR info
    pr_number = Column(Integer, nullable=False)
    pr_title = Column(String(500))
    pr_author = Column(String(255))
    pr_url = Column(String(500))

    # Review data
    review_content = Column(Text, nullable=False)
    ai_model = Column(String(100))  # gpt-4o-mini, GLM-4.7, etc.
    tokens_used = Column(Integer)

    # Status
    status = Column(String(50))  # posted, failed, pending

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    user = relationship("User", back_populates="review_history")
    repository = relationship("Repository", back_populates="review_history")

    def __repr__(self):
        return f"<ReviewHistory(id={self.id}, pr_number={self.pr_number}, status={self.status})>"
