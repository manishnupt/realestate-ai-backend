import csv
import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_broker
from app.config import settings
from app.db import get_session
from app.deps import get_owned_property
from app.models import Broker, Lead, LeadStatus, Transcript
from app.recording_storage import get_presigned_url
from app.schemas import (
    LeadOut,
    LeadUploadResult,
    LeadUploadSkip,
    PropertyLeadStats,
    ReasonCount,
    RecordingUrlOut,
    TranscriptTurnOut,
)

router = APIRouter(prefix="/properties/{property_id}/leads", tags=["leads"])

MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MB is plenty for a lead list CSV


def _normalize_phone(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None
    if not value.startswith("+"):
        value = "+" + value
    digits = value[1:]
    if not digits.isdigit() or len(digits) < 7:
        return None
    return value


@router.get("", response_model=list[LeadOut])
async def list_leads(
    property_id: uuid.UUID,
    broker: Broker = Depends(get_current_broker),
    session: AsyncSession = Depends(get_session),
):
    await get_owned_property(property_id, broker, session)
    result = await session.scalars(
        select(Lead).where(Lead.property_id == property_id).order_by(Lead.created_at.desc())
    )
    return result.all()


@router.get("/stats", response_model=PropertyLeadStats)
async def get_lead_stats(
    property_id: uuid.UUID,
    broker: Broker = Depends(get_current_broker),
    session: AsyncSession = Depends(get_session),
):
    await get_owned_property(property_id, broker, session)

    total_calls = await session.scalar(
        select(func.count())
        .select_from(Lead)
        .where(Lead.property_id == property_id, Lead.status.in_([LeadStatus.completed, LeadStatus.failed]))
    )

    outcome_rows = (
        await session.execute(
            select(Lead.outcome, func.count())
            .where(Lead.property_id == property_id, Lead.outcome.is_not(None))
            .group_by(Lead.outcome)
        )
    ).all()
    outcome_breakdown = {outcome.value: count for outcome, count in outcome_rows}
    classified_total = sum(outcome_breakdown.values())
    interested_pct = (
        round((outcome_breakdown.get("interested", 0) / classified_total) * 100, 1) if classified_total else 0.0
    )

    reason_rows = (
        await session.execute(
            select(Lead.outcome_reason, func.count())
            .where(Lead.property_id == property_id, Lead.outcome_reason.is_not(None))
            .group_by(Lead.outcome_reason)
            .order_by(func.count().desc())
            .limit(5)
        )
    ).all()
    top_reasons = [ReasonCount(reason=reason, count=count) for reason, count in reason_rows]

    return PropertyLeadStats(
        total_calls=total_calls or 0,
        classified_total=classified_total,
        interested_pct=interested_pct,
        outcome_breakdown=outcome_breakdown,
        top_reasons=top_reasons,
    )


@router.post("/upload", response_model=LeadUploadResult, status_code=status.HTTP_201_CREATED)
async def upload_leads(
    property_id: uuid.UUID,
    file: UploadFile,
    broker: Broker = Depends(get_current_broker),
    session: AsyncSession = Depends(get_session),
):
    await get_owned_property(property_id, broker, session)

    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="CSV file too large")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be UTF-8 encoded CSV") from exc

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV file is empty")

    fieldnames = {name.strip().lower(): name for name in reader.fieldnames}
    if "phone_number" not in fieldnames:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV must have a 'phone_number' column")

    phone_col = fieldnames["phone_number"]
    name_col = fieldnames.get("name")

    skipped: list[LeadUploadSkip] = []
    leads: list[Lead] = []

    for row_number, row in enumerate(reader, start=2):  # row 1 is the header
        phone = _normalize_phone(row.get(phone_col) or "")
        if phone is None:
            skipped.append(LeadUploadSkip(row=row_number, reason="missing or invalid phone_number"))
            continue

        name = row.get(name_col, "").strip() if name_col else ""
        leads.append(
            Lead(
                phone_number=phone,
                name=name or None,
                property_id=property_id,
                broker_id=broker.id,
            )
        )

    session.add_all(leads)
    await session.commit()

    return LeadUploadResult(created=len(leads), skipped=skipped)


@router.get("/{lead_id}/recording", response_model=RecordingUrlOut)
async def get_lead_recording(
    property_id: uuid.UUID,
    lead_id: uuid.UUID,
    broker: Broker = Depends(get_current_broker),
    session: AsyncSession = Depends(get_session),
):
    if not settings.recording_s3_bucket:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Recording storage is not configured"
        )

    await get_owned_property(property_id, broker, session)

    lead = await session.scalar(
        select(Lead).where(Lead.id == lead_id, Lead.property_id == property_id, Lead.broker_id == broker.id)
    )
    if lead is None or lead.recording_url is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No recording available for this lead")

    return RecordingUrlOut(url=await get_presigned_url(lead.recording_url))


@router.get("/{lead_id}/transcript", response_model=list[TranscriptTurnOut])
async def get_lead_transcript(
    property_id: uuid.UUID,
    lead_id: uuid.UUID,
    broker: Broker = Depends(get_current_broker),
    session: AsyncSession = Depends(get_session),
):
    await get_owned_property(property_id, broker, session)

    lead = await session.scalar(
        select(Lead).where(Lead.id == lead_id, Lead.property_id == property_id, Lead.broker_id == broker.id)
    )
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    result = await session.scalars(
        select(Transcript).where(Transcript.lead_id == lead_id).order_by(Transcript.created_at)
    )
    return result.all()
