"""Legally-permitted calling window enforcement (TRAI/TCCCPR: commercial calls
only 9am-9pm local time by default). Used at the queue level in worker.py, and
to clamp retry scheduling in routers/internal.py so a 2am retry can't slip
outside the window.

All datetimes in this codebase are naive and treated as UTC (matching the
existing datetime.utcnow() convention elsewhere) — functions here accept either
naive-UTC or timezone-aware datetimes but always return naive UTC, so results
stay directly comparable to values like datetime.utcnow().
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import settings


def configured_tz() -> ZoneInfo:
    return ZoneInfo(settings.calling_timezone)


def _to_local(dt: datetime) -> datetime:
    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return aware.astimezone(configured_tz())


def is_within_window(dt: datetime | None = None) -> bool:
    local = _to_local(dt or datetime.now(timezone.utc))
    return settings.calling_window_start_hour <= local.hour < settings.calling_window_end_hour


def clamp_to_window(dt: datetime) -> datetime:
    """If dt falls outside the calling window, push it forward to the start of
    the next window (same day if dt is before the window opens, next day if
    dt is at/after the window closes)."""
    local = _to_local(dt)
    window_start = local.replace(hour=settings.calling_window_start_hour, minute=0, second=0, microsecond=0)
    window_end = local.replace(hour=settings.calling_window_end_hour, minute=0, second=0, microsecond=0)

    if local < window_start:
        result = window_start
    elif local >= window_end:
        result = window_start + timedelta(days=1)
    else:
        result = local

    return result.astimezone(timezone.utc).replace(tzinfo=None)


def today_start_utc() -> datetime:
    """Midnight in the configured calling timezone, as naive UTC — the "today"
    boundary used for per-broker daily call caps (worker.py) and the monitoring
    dashboard (app.monitoring)."""
    now_local = datetime.now(configured_tz())
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(timezone.utc).replace(tzinfo=None)
