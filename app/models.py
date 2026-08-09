import enum
import uuid
from datetime import datetime

from sqlalchemy import ARRAY, ForeignKey, Numeric, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class PropertyType(str, enum.Enum):
    plot = "plot"
    flat = "flat"


class PriceUnit(str, enum.Enum):
    total = "total"
    per_sqft = "per_sqft"


class SizeUnit(str, enum.Enum):
    sqft = "sqft"
    acres = "acres"


class PropertyStatus(str, enum.Enum):
    available = "available"
    sold = "sold"
    on_hold = "on-hold"


class LeadStatus(str, enum.Enum):
    queued = "queued"
    calling = "calling"
    completed = "completed"
    failed = "failed"


class TranscriptRole(str, enum.Enum):
    assistant = "assistant"
    user = "user"


class CallOutcome(str, enum.Enum):
    """Sales outcome classified by the post-call GPT-4o extraction pass — distinct
    from Lead.call_outcome, which stores the raw Plivo telephony hangup status."""

    interested = "interested"
    not_interested = "not_interested"
    callback_requested = "callback_requested"
    no_answer = "no_answer"
    wrong_number = "wrong_number"


class SuggestionField(str, enum.Enum):
    description = "description"
    amenities = "amenities"
    price = "price"


class SuggestionStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class PipelineStage(str, enum.Enum):
    """Which voice-pipeline stage errored during a call, for monitoring/alerting
    (app.monitoring.compute_today_stats). None of these are set on a call that
    completed without an STT/LLM/TTS-layer exception."""

    stt = "stt"
    llm = "llm"
    tts = "tts"


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_cls]


class Broker(Base):
    __tablename__ = "brokers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    # Not currently read by the call pipeline (the AI persona no longer names the
    # broker — see voice-service/app/streaming/session.py). Kept for display in
    # the dashboard; nullable since it isn't collected at registration yet.
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    # Per-broker fairness controls so one broker's backlog can't starve another's
    # (worker.py._claim_batch) and so daily call volume stays within whatever
    # limit the broker's plan/compliance posture allows for.
    call_concurrency_limit: Mapped[int] = mapped_column(nullable=False, default=3, server_default="3")
    daily_call_limit: Mapped[int] = mapped_column(nullable=False, default=100, server_default="100")

    properties: Mapped[list["Property"]] = relationship(back_populates="broker", cascade="all, delete-orphan")


class Property(Base):
    __tablename__ = "properties"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    type: Mapped[PropertyType] = mapped_column(
        SAEnum(PropertyType, name="property_type", values_callable=_enum_values), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    # location
    area: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(255), nullable=False)

    price: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    price_unit: Mapped[PriceUnit] = mapped_column(
        SAEnum(PriceUnit, name="price_unit", values_callable=_enum_values), nullable=False
    )

    # size
    size_value: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    size_unit: Mapped[SizeUnit] = mapped_column(
        SAEnum(SizeUnit, name="size_unit", values_callable=_enum_values), nullable=False
    )

    bedrooms: Mapped[int | None] = mapped_column(nullable=True)
    amenities: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)

    status: Mapped[PropertyStatus] = mapped_column(
        SAEnum(PropertyStatus, name="property_status", values_callable=_enum_values),
        nullable=False,
        default=PropertyStatus.available,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now(), nullable=False)

    # Bookkeeping for the suggestion-generation batch job: null until the first
    # run, then used both to bound "calls since last run" and to decide the
    # daily-cadence trigger. See backend/app/worker.py.
    last_suggestion_run_at: Mapped[datetime | None] = mapped_column(nullable=True)

    broker_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brokers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    broker: Mapped["Broker"] = relationship(back_populates="properties")

    leads: Mapped[list["Lead"]] = relationship(back_populates="property", cascade="all, delete-orphan")
    suggestions: Mapped[list["PropertySuggestion"]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True
    )
    property: Mapped["Property"] = relationship(back_populates="leads")

    status: Mapped[LeadStatus] = mapped_column(
        SAEnum(LeadStatus, name="lead_status", values_callable=_enum_values),
        nullable=False,
        default=LeadStatus.queued,
    )
    call_outcome: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Structured sales outcome extracted by GPT-4o from the transcript at call end.
    # Distinct from call_outcome above (raw Plivo hangup status).
    outcome: Mapped[CallOutcome | None] = mapped_column(
        SAEnum(CallOutcome, name="lead_outcome", values_callable=_enum_values), nullable=True, index=True
    )
    outcome_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Free-form facts the lead mentioned (budget_range, preferred_location, timeline).
    extracted_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    outcome_extracted_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # S3 object key (not a playable URL) for the call recording, populated once
    # voice-service has downloaded Plivo's recording and uploaded it to S3 (see
    # voice-service/app/recording_storage.py). Null until then, if recording failed,
    # or after the retention job (worker.py.recording_retention_poll_forever) has
    # deleted it. Never exposed directly to the frontend — see
    # app.recording_storage.presigned_url() / GET .../leads/{id}/recording.
    recording_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Named hybrid_property, not @property: this class already has an attribute
    # named `property` (the relationship to Property below), which shadows the
    # builtin property() decorator for the rest of this class body.
    @hybrid_property
    def has_recording(self) -> bool:
        return self.recording_url is not None

    # TRAI DND/NDNC compliance check — see app.dnd.check_dnd().
    is_dnd_checked: Mapped[bool] = mapped_column(nullable=False, default=False)

    # Retry bookkeeping for no-answer/busy leads (see app.calling_hours and
    # worker.py._claim_batch / routers/internal.py.update_lead_status). attempt_count
    # is incremented each time the lead is claimed for dialing; next_attempt_at gates
    # re-claiming until the scheduled retry time (clamped into calling hours).
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(nullable=True)

    broker_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brokers.id", ondelete="CASCADE"), nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[TranscriptRole] = mapped_column(
        SAEnum(TranscriptRole, name="transcript_role", values_callable=_enum_values), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class PropertySuggestion(Base):
    """A GPT-4o-proposed edit to a Property field, generated by the batch job in
    app.worker from recurring objections/themes across recent calls. Never
    applied automatically — only approve_suggestion() (broker-triggered) writes
    it into the Property record."""

    __tablename__ = "property_suggestions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False, index=True
    )
    property: Mapped["Property"] = relationship(back_populates="suggestions")

    field: Mapped[SuggestionField] = mapped_column(
        SAEnum(SuggestionField, name="suggestion_field", values_callable=_enum_values), nullable=False
    )
    current_value: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_value: Mapped[str] = mapped_column(Text, nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[SuggestionStatus] = mapped_column(
        SAEnum(SuggestionStatus, name="suggestion_status", values_callable=_enum_values),
        nullable=False,
        default=SuggestionStatus.pending,
        index=True,
    )

    # The leads/calls whose transcripts+outcomes motivated this suggestion —
    # i.e. every call included in the generation batch that produced it.
    source_lead_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class DNDNumber(Base):
    """Local cache of numbers known to be TRAI DND/NDNC-registered, scrubbed against
    before every dial (app.dnd.check_dnd). Populated either by POST /internal/dnd/sync
    (loading an NCPR bulk CSV export once registered as a telemarketer, or a
    third-party compliance vendor's export) or automatically on a positive live
    vendor lookup — see app.dnd for the full compliance write-up."""

    __tablename__ = "dnd_numbers"

    phone_number: Mapped[str] = mapped_column(String(32), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    added_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class CallAttempt(Base):
    """One row per dial attempt. Backs both per-broker daily rate limiting
    (worker.py._claim_batch) and the monitoring/alerting dashboard
    (app.monitoring.compute_today_stats) — latency/error columns are filled in later
    via POST /internal/leads/{id}/metrics once the call ends."""

    __tablename__ = "call_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    broker_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("brokers.id", ondelete="CASCADE"), nullable=False, index=True
    )

    dialed_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False, index=True)

    # Raw Plivo telephony outcome (CallStatus/HangupCause), filled in when the
    # hangup webhook reports back — null while the call is still in flight.
    outcome: Mapped[str | None] = mapped_column(String(255), nullable=True)

    stt_latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    llm_latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    tts_latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    error_stage: Mapped[PipelineStage | None] = mapped_column(
        SAEnum(PipelineStage, name="pipeline_stage", values_callable=_enum_values), nullable=True
    )
