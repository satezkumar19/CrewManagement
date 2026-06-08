"""
Pattern Detection API route (L4 #4).

Exposes the aggregate view over the decision history: the failure-category breakdown
and the single flagged recurring gap.
"""
from fastapi import APIRouter

from services.pattern_service import pattern_service

router = APIRouter(prefix="/patterns", tags=["patterns"])


@router.get("/", response_model=dict)
async def get_patterns(limit: int = 200):
    """Aggregate the decision history and flag the recurring compliance gap, if any."""
    return await pattern_service.detect_patterns(limit=limit)
