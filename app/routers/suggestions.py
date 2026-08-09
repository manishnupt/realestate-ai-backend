import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_broker
from app.db import get_session
from app.deps import get_owned_property
from app.models import Broker, Property, PropertySuggestion, SuggestionField, SuggestionStatus
from app.schemas import PropertySuggestionOut

router = APIRouter(prefix="/properties/{property_id}/suggestions", tags=["suggestions"])


async def _get_owned_suggestion(
    property_id: uuid.UUID, suggestion_id: uuid.UUID, broker: Broker, session: AsyncSession
) -> PropertySuggestion:
    await get_owned_property(property_id, broker, session)
    suggestion = await session.scalar(
        select(PropertySuggestion).where(
            PropertySuggestion.id == suggestion_id, PropertySuggestion.property_id == property_id
        )
    )
    if suggestion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")
    return suggestion


def _apply_suggestion(prop: Property, suggestion: PropertySuggestion) -> None:
    """Writes suggestion.suggested_value into the live Property record. Only ever
    called from approve_suggestion (broker-triggered) — suggestions never apply
    themselves. Raises 422 rather than guessing if the stored value can't be
    parsed for its field type, so a malformed suggestion can't corrupt listing data."""
    if suggestion.field == SuggestionField.description:
        prop.description = suggestion.suggested_value
        return

    if suggestion.field == SuggestionField.amenities:
        try:
            amenities = json.loads(suggestion.suggested_value)
        except json.JSONDecodeError:
            amenities = None
        if not isinstance(amenities, list) or not all(isinstance(item, str) for item in amenities):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Suggested amenities value is not a valid JSON array of strings",
            )
        prop.amenities = amenities
        return

    if suggestion.field == SuggestionField.price:
        try:
            new_price = float(suggestion.suggested_value)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Suggested price value is not a valid number",
            )
        if new_price <= 0:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Suggested price must be positive")
        prop.price = new_price
        return

    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown suggestion field: {suggestion.field}")


@router.get("", response_model=list[PropertySuggestionOut])
async def list_suggestions(
    property_id: uuid.UUID,
    status_filter: SuggestionStatus | None = Query(default=None, alias="status"),
    broker: Broker = Depends(get_current_broker),
    session: AsyncSession = Depends(get_session),
):
    await get_owned_property(property_id, broker, session)

    stmt = select(PropertySuggestion).where(PropertySuggestion.property_id == property_id)
    if status_filter is not None:
        stmt = stmt.where(PropertySuggestion.status == status_filter)
    stmt = stmt.order_by(PropertySuggestion.created_at.desc())

    result = await session.scalars(stmt)
    return result.all()


@router.post("/{suggestion_id}/approve", response_model=PropertySuggestionOut)
async def approve_suggestion(
    property_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    broker: Broker = Depends(get_current_broker),
    session: AsyncSession = Depends(get_session),
):
    suggestion = await _get_owned_suggestion(property_id, suggestion_id, broker, session)
    if suggestion.status != SuggestionStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Suggestion already {suggestion.status.value}"
        )

    prop = await session.get(Property, property_id)
    _apply_suggestion(prop, suggestion)

    suggestion.status = SuggestionStatus.approved
    suggestion.reviewed_at = datetime.utcnow()
    await session.commit()
    await session.refresh(suggestion)
    return suggestion


@router.post("/{suggestion_id}/reject", response_model=PropertySuggestionOut)
async def reject_suggestion(
    property_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    broker: Broker = Depends(get_current_broker),
    session: AsyncSession = Depends(get_session),
):
    suggestion = await _get_owned_suggestion(property_id, suggestion_id, broker, session)
    if suggestion.status != SuggestionStatus.pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Suggestion already {suggestion.status.value}"
        )

    # Rejections are never deleted — the row (with its justification and
    # source calls) stays around as a dismissed record for future tuning.
    suggestion.status = SuggestionStatus.rejected
    suggestion.reviewed_at = datetime.utcnow()
    await session.commit()
    await session.refresh(suggestion)
    return suggestion
