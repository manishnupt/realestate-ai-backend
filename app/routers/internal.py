import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.calling_hours import clamp_to_window
from app.config import settings
from app.db import get_session
from app.dnd import sync_dnd_numbers
from app.models import (
    CallAttempt,
    CallOutcome,
    Lead,
    LeadStatus,
    PipelineStage,
    PriceUnit,
    Property,
    PropertyStatus,
    PropertySuggestion,
    PropertyType,
    SizeUnit,
    SuggestionField,
    Transcript,
)
from app.schemas import InternalLeadStatusUpdate, InternalTranscriptTurnIn, TranscriptTurnOut

router = APIRouter(prefix="/internal", tags=["internal"])


class InternalPropertyDetails(BaseModel):
    id: uuid.UUID
    type: PropertyType
    title: str
    area: str
    city: str
    price: float
    price_unit: PriceUnit
    size_value: float
    size_unit: SizeUnit
    bedrooms: int | None
    amenities: list[str]
    status: PropertyStatus
    description: str | None


class InternalLeadContext(BaseModel):
    lead_id: uuid.UUID
    phone_number: str
    lead_name: str | None
    property_id: uuid.UUID
    property_title: str
    broker_id: uuid.UUID
    broker_name: str | None
    property: InternalPropertyDetails


class InternalOutcomeUpdate(BaseModel):
    outcome: CallOutcome
    reason: str
    details: dict[str, str | None] = {}


class InternalRecordingUpdate(BaseModel):
    recording_url: str


class InternalDNDSyncIn(BaseModel):
    phone_numbers: list[str]
    source: str = "manual_sync"


class InternalDNDSyncOut(BaseModel):
    added: int


class InternalCallMetricsIn(BaseModel):
    stt_latency_ms: int | None = None
    llm_latency_ms: int | None = None
    tts_latency_ms: int | None = None
    error_stage: PipelineStage | None = None


class InternalCallSummary(BaseModel):
    lead_id: uuid.UUID
    outcome: CallOutcome
    reason: str | None
    extracted_details: dict | None
    transcript: list[TranscriptTurnOut]


class InternalSuggestionContext(BaseModel):
    property: InternalPropertyDetails
    calls: list[InternalCallSummary]


class InternalSuggestionItemIn(BaseModel):
    field: SuggestionField
    current_value: str
    suggested_value: str
    justification: str


class InternalSuggestionBatchIn(BaseModel):
    suggestions: list[InternalSuggestionItemIn]
    source_lead_ids: list[uuid.UUID]


def _verify_internal_secret(x_internal_secret: str | None = Header(default=None)) -> None:
    if x_internal_secret != settings.internal_api_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal secret")


@router.post("/dnd/sync", response_model=InternalDNDSyncOut)
async def sync_dnd(
    payload: InternalDNDSyncIn,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(_verify_internal_secret),
):
    """Load a batch of DND-registered numbers into the local scrubbing cache —
    this is how an NCPR bulk CSV export (once registered as a telemarketer) or
    a third-party compliance vendor's export gets applied. See app.dnd."""
    added = await sync_dnd_numbers(payload.phone_numbers, payload.source, session)
    return InternalDNDSyncOut(added=added)


# Plivo's telephony-level CallStatus/HangupCause values that mean the callee's
# line was reachable but the call didn't connect — worth a retry, unlike e.g. an
# invalid number. Distinct from CallOutcome.no_answer, which is a later,
# transcript-based signal (the call connected but nobody spoke, e.g. voicemail)
# that arrives via /leads/{id}/outcome and isn't part of this immediate retry path.
_RETRYABLE_CALL_STATUSES = {"busy", "no-answer", "no_answer"}


async def _latest_call_attempt(lead_id: uuid.UUID, session: AsyncSession) -> CallAttempt | None:
    return await session.scalar(
        select(CallAttempt).where(CallAttempt.lead_id == lead_id).order_by(CallAttempt.dialed_at.desc()).limit(1)
    )


@router.post("/leads/{lead_id}/status", status_code=status.HTTP_204_NO_CONTENT)
async def update_lead_status(
    lead_id: uuid.UUID,
    payload: InternalLeadStatusUpdate,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(_verify_internal_secret),
):
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    attempt = await _latest_call_attempt(lead_id, session)
    if attempt is not None:
        attempt.outcome = payload.call_outcome

    original_outcome = (payload.call_outcome or "").lower()
    if (
        payload.status == LeadStatus.failed
        and original_outcome in _RETRYABLE_CALL_STATUSES
        and lead.attempt_count < settings.max_call_attempts
    ):
        lead.status = LeadStatus.queued
        lead.next_attempt_at = clamp_to_window(datetime.utcnow() + timedelta(hours=settings.retry_delay_hours))
        lead.call_outcome = f"retry_scheduled:{payload.call_outcome}"
    else:
        lead.status = payload.status
        if payload.call_outcome is not None:
            lead.call_outcome = payload.call_outcome

    await session.commit()


@router.post("/leads/{lead_id}/metrics", status_code=status.HTTP_204_NO_CONTENT)
async def update_call_metrics(
    lead_id: uuid.UUID,
    payload: InternalCallMetricsIn,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(_verify_internal_secret),
):
    """Voice-service reports voice-pipeline latency/error data here once a call
    ends (see streaming/session.py). Written onto the most recent CallAttempt
    row for this lead — the data backbone for app.monitoring's dashboard stats
    and the alerting loop in worker.py."""
    attempt = await _latest_call_attempt(lead_id, session)
    if attempt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No call attempt found for lead")

    attempt.stt_latency_ms = payload.stt_latency_ms
    attempt.llm_latency_ms = payload.llm_latency_ms
    attempt.tts_latency_ms = payload.tts_latency_ms
    attempt.error_stage = payload.error_stage
    await session.commit()


@router.get("/leads/{lead_id}/context", response_model=InternalLeadContext)
async def get_lead_context(
    lead_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(_verify_internal_secret),
):
    lead = await session.get(Lead, lead_id, options=[joinedload(Lead.property).joinedload(Property.broker)])
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    prop = lead.property
    return InternalLeadContext(
        lead_id=lead.id,
        phone_number=lead.phone_number,
        lead_name=lead.name,
        property_id=lead.property_id,
        property_title=prop.title,
        broker_id=lead.broker_id,
        broker_name=prop.broker.name,
        property=InternalPropertyDetails(
            id=prop.id,
            type=prop.type,
            title=prop.title,
            area=prop.area,
            city=prop.city,
            price=float(prop.price),
            price_unit=prop.price_unit,
            size_value=float(prop.size_value),
            size_unit=prop.size_unit,
            bedrooms=prop.bedrooms,
            amenities=list(prop.amenities or []),
            status=prop.status,
            description=prop.description,
        ),
    )


@router.post("/leads/{lead_id}/transcript", status_code=status.HTTP_204_NO_CONTENT)
async def append_transcript_turn(
    lead_id: uuid.UUID,
    payload: InternalTranscriptTurnIn,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(_verify_internal_secret),
):
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    session.add(Transcript(lead_id=lead_id, role=payload.role, text=payload.text))
    await session.commit()


@router.get("/leads/{lead_id}/transcript", response_model=list[TranscriptTurnOut])
async def get_transcript(
    lead_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(_verify_internal_secret),
):
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    result = await session.scalars(
        select(Transcript).where(Transcript.lead_id == lead_id).order_by(Transcript.created_at)
    )
    return result.all()


@router.post("/leads/{lead_id}/outcome", status_code=status.HTTP_204_NO_CONTENT)
async def update_lead_outcome(
    lead_id: uuid.UUID,
    payload: InternalOutcomeUpdate,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(_verify_internal_secret),
):
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    lead.outcome = payload.outcome
    lead.outcome_reason = payload.reason
    lead.extracted_details = payload.details
    lead.outcome_extracted_at = datetime.utcnow()
    await session.commit()


@router.post("/leads/{lead_id}/recording", status_code=status.HTTP_204_NO_CONTENT)
async def update_lead_recording(
    lead_id: uuid.UUID,
    payload: InternalRecordingUpdate,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(_verify_internal_secret),
):
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")

    lead.recording_url = payload.recording_url
    await session.commit()


@router.get("/properties/{property_id}/suggestion-context", response_model=InternalSuggestionContext)
async def get_suggestion_context(
    property_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(_verify_internal_secret),
):
    """Everything the suggestion-generation batch job needs for one property:
    current listing facts, plus every classified call since last_suggestion_run_at
    (capped at suggestion_context_max_calls, most recent first) with its full
    transcript."""
    prop = await session.get(Property, property_id)
    if prop is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")

    since = prop.last_suggestion_run_at or datetime.min
    # Oldest-first: if there's more backlog than the cap, we must process it in
    # order and only advance the watermark past what we actually included (see
    # create_suggestions) — newest-first would silently strand older calls.
    leads = (
        await session.scalars(
            select(Lead)
            .where(Lead.property_id == property_id, Lead.outcome.is_not(None), Lead.outcome_extracted_at > since)
            .order_by(Lead.outcome_extracted_at.asc())
            .limit(settings.suggestion_context_max_calls)
        )
    ).all()

    lead_ids = [lead.id for lead in leads]
    transcripts_by_lead: dict[uuid.UUID, list] = {lead_id: [] for lead_id in lead_ids}
    if lead_ids:
        turns = (
            await session.scalars(
                select(Transcript).where(Transcript.lead_id.in_(lead_ids)).order_by(Transcript.created_at)
            )
        ).all()
        for turn in turns:
            transcripts_by_lead[turn.lead_id].append(turn)

    return InternalSuggestionContext(
        property=InternalPropertyDetails(
            id=prop.id,
            type=prop.type,
            title=prop.title,
            area=prop.area,
            city=prop.city,
            price=float(prop.price),
            price_unit=prop.price_unit,
            size_value=float(prop.size_value),
            size_unit=prop.size_unit,
            bedrooms=prop.bedrooms,
            amenities=list(prop.amenities or []),
            status=prop.status,
            description=prop.description,
        ),
        calls=[
            InternalCallSummary(
                lead_id=lead.id,
                outcome=lead.outcome,
                reason=lead.outcome_reason,
                extracted_details=lead.extracted_details,
                transcript=transcripts_by_lead[lead.id],
            )
            for lead in leads
        ],
    )


@router.post("/properties/{property_id}/suggestions", status_code=status.HTTP_204_NO_CONTENT)
async def create_suggestions(
    property_id: uuid.UUID,
    payload: InternalSuggestionBatchIn,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(_verify_internal_secret),
):
    prop = await session.get(Property, property_id)
    if prop is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")

    for item in payload.suggestions:
        session.add(
            PropertySuggestion(
                property_id=property_id,
                field=item.field,
                current_value=item.current_value,
                suggested_value=item.suggested_value,
                justification=item.justification,
                source_lead_ids=payload.source_lead_ids,
            )
        )

    # Advance the watermark to the latest call actually included in this batch —
    # not wall-clock now — so a backlog larger than suggestion_context_max_calls
    # can't strand older, still-unprocessed calls behind it. Falls back to now()
    # only if the batch was empty (nothing to advance past).
    if payload.source_lead_ids:
        max_processed = await session.scalar(
            select(func.max(Lead.outcome_extracted_at)).where(Lead.id.in_(payload.source_lead_ids))
        )
        prop.last_suggestion_run_at = max_processed or datetime.utcnow()
    else:
        prop.last_suggestion_run_at = datetime.utcnow()
    await session.commit()
