"""Webhook schemas for API validation"""

from datetime import datetime
from typing import List
from pydantic import BaseModel


class WebhookConfigBase(BaseModel):
    """Base webhook config schema"""
    events: List[str] = ["pull_request"]


class WebhookConfigResponse(WebhookConfigBase):
    """Schema for webhook config response"""
    id: int
    user_id: int
    repository_id: int
    webhook_id: int | None = None
    webhook_url: str | None = None
    is_active: bool
    delivery_status: str | None = None
    created_at: datetime
    updated_at: datetime
    last_delivery_at: datetime | None = None

    class Config:
        from_attributes = True
