"""Dashboard data API routes"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.api.auth import get_current_user
from app.models import User, Repository, ReviewHistory

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get statistics for user dashboard"""

    # Count repositories
    repo_result = await db.execute(
        select(func.count(Repository.id)).where(
            Repository.user_id == current_user.id,
            Repository.is_monitored == True
        )
    )
    total_repos = repo_result.scalar() or 0

    # Count active webhooks (monitored repos with webhooks)
    webhook_result = await db.execute(
        select(func.count(Repository.id)).where(
            Repository.user_id == current_user.id,
            Repository.is_monitored == True
        )
    )
    active_webhooks = webhook_result.scalar() or 0

    # Count total reviews
    review_result = await db.execute(
        select(func.count(ReviewHistory.id)).where(
            ReviewHistory.user_id == current_user.id
        )
    )
    total_reviews = review_result.scalar() or 0

    # Get recent reviews
    recent_result = await db.execute(
        select(ReviewHistory)
        .where(ReviewHistory.user_id == current_user.id)
        .order_by(ReviewHistory.created_at.desc())
        .limit(10)
    )
    recent_reviews = recent_result.scalars().all()

    return {
        "total_repositories": total_repos,
        "active_webhooks": active_webhooks,
        "total_reviews": total_reviews,
        "recent_reviews": [
            {
                "id": r.id,
                "pr_number": r.pr_number,
                "pr_title": r.pr_title,
                "repo_full_name": r.repository.repo_full_name if r.repository else "Unknown",
                "created_at": r.created_at.isoformat(),
                "status": r.status,
            }
            for r in recent_reviews
        ]
    }
