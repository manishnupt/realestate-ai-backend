import asyncio
import logging
import uuid
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, select

from app.calling_hours import is_within_window, today_start_utc
from app.config import settings
from app.db import async_session
from app.dnd import check_dnd
from app.models import Broker, CallAttempt, Lead, LeadStatus, Property
from app.monitoring import DashboardStats, compute_today_stats
from app.recording_storage import delete_recording

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("worker")

# Keep strong references to in-flight dial tasks so asyncio doesn't garbage
# collect them mid-flight (create_task only holds a weak ref internally).
_background_tasks: set[asyncio.Task] = set()


async def _claim_batch() -> list[uuid.UUID]:
    """Mark up to the remaining call-concurrency capacity of queued leads as
    calling. Enforced in this order: legally-permitted calling hours (queue-wide
    — see app.calling_hours), the global concurrency ceiling
    (settings.call_concurrency_limit), and per-broker concurrency/daily caps so
    one broker's backlog can't starve another's."""
    if not is_within_window():
        return []

    async with async_session() as session:
        global_in_flight = await session.scalar(
            select(func.count()).select_from(Lead).where(Lead.status == LeadStatus.calling)
        )
        global_capacity = settings.call_concurrency_limit - (global_in_flight or 0)
        if global_capacity <= 0:
            return []

        now = datetime.utcnow()
        leads = (
            await session.scalars(
                select(Lead)
                .where(
                    Lead.status == LeadStatus.queued,
                    (Lead.next_attempt_at.is_(None)) | (Lead.next_attempt_at <= now),
                )
                .order_by(Lead.created_at)
            )
        ).all()
        if not leads:
            return []

        broker_ids = {lead.broker_id for lead in leads}

        in_flight_by_broker = dict(
            (
                await session.execute(
                    select(Lead.broker_id, func.count())
                    .where(Lead.status == LeadStatus.calling, Lead.broker_id.in_(broker_ids))
                    .group_by(Lead.broker_id)
                )
            ).all()
        )
        today_start = today_start_utc()
        dialed_today_by_broker = dict(
            (
                await session.execute(
                    select(CallAttempt.broker_id, func.count())
                    .where(CallAttempt.dialed_at >= today_start, CallAttempt.broker_id.in_(broker_ids))
                    .group_by(CallAttempt.broker_id)
                )
            ).all()
        )
        brokers = {
            broker.id: broker
            for broker in (await session.scalars(select(Broker).where(Broker.id.in_(broker_ids)))).all()
        }

        claimed_ids: list[uuid.UUID] = []
        claimed_this_cycle: dict[uuid.UUID, int] = {}

        for lead in leads:
            if len(claimed_ids) >= global_capacity:
                break  # global ceiling reached — no further leads claimable this cycle regardless of broker

            broker = brokers.get(lead.broker_id)
            concurrency_limit = broker.call_concurrency_limit if broker else settings.call_concurrency_limit
            daily_limit = broker.daily_call_limit if broker else None

            already_claimed = claimed_this_cycle.get(lead.broker_id, 0)
            in_flight = in_flight_by_broker.get(lead.broker_id, 0) + already_claimed
            if in_flight >= concurrency_limit:
                continue  # this broker's concurrency cap is full — skip, leave queued, keep scanning

            dialed_today = dialed_today_by_broker.get(lead.broker_id, 0) + already_claimed
            if daily_limit is not None and dialed_today >= daily_limit:
                continue  # this broker's daily cap is hit for today — skip, leave queued

            lead.is_dnd_checked = True
            dnd_result = await check_dnd(lead.phone_number, session)
            if dnd_result.blocked:
                lead.status = LeadStatus.failed
                lead.call_outcome = "blocked_dnd" if dnd_result.reason == "dnd_listed" else "blocked_dnd_unverified"
                continue

            lead.status = LeadStatus.calling
            lead.attempt_count += 1
            session.add(CallAttempt(lead_id=lead.id, broker_id=lead.broker_id))
            claimed_ids.append(lead.id)
            claimed_this_cycle[lead.broker_id] = already_claimed + 1

        await session.commit()
        return claimed_ids


async def _dial(lead_id: uuid.UUID) -> None:
    async with async_session() as session:
        lead = await session.get(Lead, lead_id)
        if lead is None:
            return
        phone_number = lead.phone_number
        property_id = lead.property_id

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{settings.voice_service_url.rstrip('/')}/trigger-call",
                json={
                    "phone_number": phone_number,
                    "lead_id": str(lead_id),
                    "property_id": str(property_id),
                },
            )
            resp.raise_for_status()
        logger.info("Dialed lead=%s phone=%s", lead_id, phone_number)
    except Exception:
        logger.exception("Failed to trigger call for lead=%s phone=%s", lead_id, phone_number)
        async with async_session() as session:
            lead = await session.get(Lead, lead_id)
            if lead is not None:
                lead.status = LeadStatus.failed
                lead.call_outcome = "dial_failed"
                await session.commit()


async def poll_forever() -> None:
    logger.info(
        "Lead-calling worker started (concurrency=%s, poll_interval=%ss, calling_window=%s-%s %s)",
        settings.call_concurrency_limit,
        settings.worker_poll_interval_seconds,
        settings.calling_window_start_hour,
        settings.calling_window_end_hour,
        settings.calling_timezone,
    )
    while True:
        try:
            claimed_ids = await _claim_batch()
            for lead_id in claimed_ids:
                task = asyncio.create_task(_dial(lead_id))
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
        except Exception:
            logger.exception("Worker poll cycle failed")
        await asyncio.sleep(settings.worker_poll_interval_seconds)


async def _properties_due_for_suggestions() -> list[uuid.UUID]:
    """A property is due when it has at least one newly-classified call since
    its last run, and either enough of them have piled up (count trigger) or
    enough time has passed (daily-cadence trigger)."""
    async with async_session() as session:
        properties = (await session.scalars(select(Property))).all()
        now = datetime.utcnow()
        due: list[uuid.UUID] = []

        for prop in properties:
            since = prop.last_suggestion_run_at or datetime.min
            calls_since = await session.scalar(
                select(func.count())
                .select_from(Lead)
                .where(Lead.property_id == prop.id, Lead.outcome.is_not(None), Lead.outcome_extracted_at > since)
            )
            if not calls_since:
                continue  # nothing new to learn from — don't burn an LLM call for no reason

            due_by_count = calls_since >= settings.suggestion_trigger_call_count
            due_by_time = prop.last_suggestion_run_at is None or (
                now - prop.last_suggestion_run_at >= timedelta(hours=settings.suggestion_trigger_interval_hours)
            )
            if due_by_count or due_by_time:
                due.append(prop.id)

        return due


async def _generate_suggestions(property_id: uuid.UUID) -> None:
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{settings.voice_service_url.rstrip('/')}/generate-suggestions",
                json={"property_id": str(property_id)},
            )
            resp.raise_for_status()
        logger.info("Generated suggestions for property=%s", property_id)
    except Exception:
        logger.exception("Failed to generate suggestions for property=%s", property_id)


async def suggestions_poll_forever() -> None:
    logger.info(
        "Suggestion-generation worker started (poll_interval=%ss, trigger_call_count=%s, trigger_interval_hours=%s)",
        settings.suggestion_poll_interval_seconds,
        settings.suggestion_trigger_call_count,
        settings.suggestion_trigger_interval_hours,
    )
    while True:
        try:
            due_property_ids = await _properties_due_for_suggestions()
            # Sequential, not concurrent tasks like the dial loop above: this is a
            # low-frequency batch job, and going one at a time naturally avoids
            # bursting OpenAI with a pile of simultaneous requests.
            for property_id in due_property_ids:
                await _generate_suggestions(property_id)
        except Exception:
            logger.exception("Suggestion poll cycle failed")
        await asyncio.sleep(settings.suggestion_poll_interval_seconds)


def _format_alerts(label: str, stats: DashboardStats) -> str | None:
    problems: list[str] = []
    if stats.success_rate is not None and stats.success_rate < settings.alert_success_rate_threshold:
        problems.append(
            f"success rate {stats.success_rate:.0%} is below the {settings.alert_success_rate_threshold:.0%} threshold"
        )
    for stage, rate in stats.error_rate.items():
        if rate is not None and rate > settings.alert_error_rate_threshold:
            problems.append(
                f"{stage} error rate {rate:.0%} is above the {settings.alert_error_rate_threshold:.0%} threshold"
            )
    if not problems:
        return None
    return f"[{label}] {'; '.join(problems)} (calls_today={stats.calls_today})"


async def _send_alert(message: str) -> None:
    logger.warning("ALERT: %s", message)
    if not settings.alert_webhook_url:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # "text" (Slack incoming-webhook format) and "content" (Discord webhook
            # format) both included so this works with either without extra config;
            # unrecognized extra fields are harmless on both platforms.
            await client.post(settings.alert_webhook_url, json={"text": message, "content": message})
    except Exception:
        logger.exception("Failed to POST alert to ALERT_WEBHOOK_URL")


async def _check_alerts() -> None:
    async with async_session() as session:
        global_stats = await compute_today_stats(session)
        if global_stats.calls_today >= settings.alert_min_sample_size:
            message = _format_alerts("global", global_stats)
            if message:
                await _send_alert(message)

        brokers = (await session.scalars(select(Broker))).all()
        for broker in brokers:
            stats = await compute_today_stats(session, broker_id=broker.id)
            if stats.calls_today < settings.alert_min_sample_size:
                continue
            message = _format_alerts(f"broker={broker.email}", stats)
            if message:
                await _send_alert(message)


async def alerting_poll_forever() -> None:
    logger.info("Alerting worker started (poll_interval=%ss)", settings.alerting_poll_interval_seconds)
    while True:
        try:
            await _check_alerts()
        except Exception:
            logger.exception("Alerting poll cycle failed")
        await asyncio.sleep(settings.alerting_poll_interval_seconds)


async def _sweep_expired_recordings() -> None:
    if not settings.recording_s3_bucket:
        return  # nothing was ever uploaded without a bucket configured — nothing to sweep
    cutoff = datetime.utcnow() - timedelta(days=settings.recording_retention_days)
    async with async_session() as session:
        leads = (
            await session.scalars(select(Lead).where(Lead.recording_url.is_not(None), Lead.created_at < cutoff))
        ).all()
        for lead in leads:
            try:
                await delete_recording(lead.recording_url)
            except Exception:
                continue  # leave recording_url set so this retries next sweep
            lead.recording_url = None
        await session.commit()


async def recording_retention_poll_forever() -> None:
    logger.info(
        "Recording-retention worker started (poll_interval=%ss, retention_days=%s)",
        settings.recording_retention_poll_interval_seconds,
        settings.recording_retention_days,
    )
    while True:
        try:
            await _sweep_expired_recordings()
        except Exception:
            logger.exception("Recording retention sweep failed")
        await asyncio.sleep(settings.recording_retention_poll_interval_seconds)


async def _run_all() -> None:
    await asyncio.gather(
        poll_forever(),
        suggestions_poll_forever(),
        alerting_poll_forever(),
        recording_retention_poll_forever(),
    )


if __name__ == "__main__":
    asyncio.run(_run_all())
