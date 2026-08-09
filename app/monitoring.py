"""Basic monitoring aggregates over today's CallAttempt rows (app.models.CallAttempt) —
backs both the broker-facing dashboard widget (routers/monitoring.py) and the
alerting loop (worker.py.alerting_poll_forever).
"""
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.calling_hours import today_start_utc
from app.models import Broker, CallAttempt

_STAGES = ("stt", "llm", "tts")
_STAGE_LATENCY_ATTR = {"stt": "stt_latency_ms", "llm": "llm_latency_ms", "tts": "tts_latency_ms"}

# Plivo CallStatus values that count as a successful, completed connection —
# anything else (busy, no-answer, failed, blocked_dnd*, retry_scheduled:*) counts
# against the success rate.
_SUCCESS_OUTCOMES = {"completed"}


@dataclass
class DashboardStats:
    calls_today: int
    daily_limit: int
    success_rate: float | None
    avg_latency_ms: dict[str, float | None]
    error_rate: dict[str, float | None]


async def compute_today_stats(session: AsyncSession, broker_id: uuid.UUID | None = None) -> DashboardStats:
    """Pass broker_id for a single broker's dashboard view; omit it for the
    global rollup the alerting loop checks."""
    since = today_start_utc()

    query = select(CallAttempt).where(CallAttempt.dialed_at >= since)
    if broker_id is not None:
        query = query.where(CallAttempt.broker_id == broker_id)
    attempts = (await session.scalars(query)).all()

    calls_today = len(attempts)
    finished = [a for a in attempts if a.outcome is not None]
    successes = [a for a in finished if a.outcome in _SUCCESS_OUTCOMES]
    success_rate = (len(successes) / len(finished)) if finished else None

    avg_latency_ms: dict[str, float | None] = {}
    error_rate: dict[str, float | None] = {}
    for stage in _STAGES:
        values = [getattr(a, _STAGE_LATENCY_ATTR[stage]) for a in attempts]
        values = [v for v in values if v is not None]
        avg_latency_ms[stage] = (sum(values) / len(values)) if values else None
        errors = sum(1 for a in attempts if a.error_stage == stage)
        error_rate[stage] = (errors / calls_today) if calls_today else None

    daily_limit = 0
    if broker_id is not None:
        broker = await session.get(Broker, broker_id)
        daily_limit = broker.daily_call_limit if broker else 0

    return DashboardStats(
        calls_today=calls_today,
        daily_limit=daily_limit,
        success_rate=success_rate,
        avg_latency_ms=avg_latency_ms,
        error_rate=error_rate,
    )
