import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from app.models import (
    CallOutcome,
    LeadStatus,
    PriceUnit,
    PropertyStatus,
    PropertyType,
    SizeUnit,
    SuggestionField,
    SuggestionStatus,
    TranscriptRole,
)


class LocationSchema(BaseModel):
    area: str
    city: str


class SizeSchema(BaseModel):
    value: float
    unit: SizeUnit


class PropertyBase(BaseModel):
    type: PropertyType
    title: str
    location: LocationSchema
    price: float
    price_unit: PriceUnit
    size: SizeSchema
    bedrooms: int | None = None
    amenities: list[str] = Field(default_factory=list)
    status: PropertyStatus = PropertyStatus.available
    description: str | None = None


class PropertyCreate(PropertyBase):
    pass


class PropertyUpdate(PropertyBase):
    pass


class PropertyOut(PropertyBase):
    id: uuid.UUID
    broker_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class BrokerCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str | None = None


class BrokerLogin(BaseModel):
    email: EmailStr
    password: str


class BrokerOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    name: str | None
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LeadOut(BaseModel):
    id: uuid.UUID
    phone_number: str
    name: str | None
    property_id: uuid.UUID
    broker_id: uuid.UUID
    status: LeadStatus
    call_outcome: str | None
    is_dnd_checked: bool
    outcome: CallOutcome | None
    outcome_reason: str | None
    extracted_details: dict | None
    outcome_extracted_at: datetime | None
    # Never exposes the raw S3 key — the dashboard fetches a short-lived signed
    # URL on demand via GET .../leads/{id}/recording (see app.recording_storage).
    has_recording: bool
    attempt_count: int
    next_attempt_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class LeadUploadSkip(BaseModel):
    row: int
    reason: str


class LeadUploadResult(BaseModel):
    created: int
    skipped: list[LeadUploadSkip]


class ReasonCount(BaseModel):
    reason: str
    count: int


class PropertyLeadStats(BaseModel):
    total_calls: int
    classified_total: int
    interested_pct: float
    outcome_breakdown: dict[str, int]
    top_reasons: list[ReasonCount]


class DashboardStatsOut(BaseModel):
    calls_today: int
    daily_limit: int
    success_rate: float | None
    avg_latency_ms: dict[str, float | None]
    error_rate: dict[str, float | None]


class RecordingUrlOut(BaseModel):
    url: str


class InternalLeadStatusUpdate(BaseModel):
    status: LeadStatus
    call_outcome: str | None = None


class TranscriptTurnOut(BaseModel):
    id: uuid.UUID
    role: TranscriptRole
    text: str
    created_at: datetime

    model_config = {"from_attributes": True}


class InternalTranscriptTurnIn(BaseModel):
    role: TranscriptRole
    text: str


class PropertySuggestionOut(BaseModel):
    id: uuid.UUID
    property_id: uuid.UUID
    field: SuggestionField
    current_value: str
    suggested_value: str
    justification: str
    status: SuggestionStatus
    source_lead_ids: list[uuid.UUID]
    created_at: datetime
    reviewed_at: datetime | None

    model_config = {"from_attributes": True}


# --- Bulk property import (see app.property_import + app.routers.property_import) ---


class ImportRowData(BaseModel):
    """Flat, all-optional mirror of Property's fields — deliberately not
    PropertyCreate, since a parsed row must be able to hold partial/invalid
    data for the broker to fix in the review screen before commit."""

    type: PropertyType | None = None
    title: str | None = None
    area: str | None = None
    city: str | None = None
    price: float | None = None
    price_unit: PriceUnit = PriceUnit.total
    size_value: float | None = None
    size_unit: SizeUnit = SizeUnit.sqft
    bedrooms: int | None = None
    amenities: list[str] = Field(default_factory=list)
    status: PropertyStatus = PropertyStatus.available
    description: str | None = None


class ImportDuplicateMatch(BaseModel):
    # "existing": property_id points at a broker's already-saved listing (mergeable).
    # "batch": row_number points at another row in this same upload (advisory only —
    # there's no saved id to merge into yet).
    kind: Literal["existing", "batch"]
    property_id: uuid.UUID | None = None
    row_number: int | None = None
    title: str
    reason: str


class ImportRowOut(BaseModel):
    row_number: int
    # Original header:value pairs for this row — Excel only, lets the frontend
    # re-derive rows locally when it re-requests a parse with a changed mapping.
    raw: dict[str, str] | None = None
    data: ImportRowData
    errors: list[str]
    duplicates: list[ImportDuplicateMatch]


class ImportParseOut(BaseModel):
    source: Literal["excel", "word"]
    headers: list[str] | None = None
    column_mapping: dict[str, str] | None = None
    rows: list[ImportRowOut]


class ImportCommitItem(BaseModel):
    action: Literal["create", "merge", "skip"]
    data: ImportRowData | None = None
    target_property_id: uuid.UUID | None = None


class ImportCommitIn(BaseModel):
    items: list[ImportCommitItem]


class ImportCommitError(BaseModel):
    index: int
    detail: str


class ImportCommitOut(BaseModel):
    created: int
    merged: int
    skipped: int
    errors: list[ImportCommitError]
