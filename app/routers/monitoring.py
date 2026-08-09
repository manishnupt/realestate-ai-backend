from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_broker
from app.db import get_session
from app.models import Broker
from app.monitoring import compute_today_stats
from app.schemas import DashboardStatsOut

router = APIRouter(prefix="/metrics", tags=["monitoring"])


@router.get("/dashboard", response_model=DashboardStatsOut)
async def get_dashboard_stats(
    broker: Broker = Depends(get_current_broker),
    session: AsyncSession = Depends(get_session),
):
    stats = await compute_today_stats(session, broker_id=broker.id)
    return DashboardStatsOut(
        calls_today=stats.calls_today,
        daily_limit=stats.daily_limit,
        success_rate=stats.success_rate,
        avg_latency_ms=stats.avg_latency_ms,
        error_rate=stats.error_rate,
    )
