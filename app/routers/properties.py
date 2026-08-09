import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_broker
from app.db import get_session
from app.deps import get_owned_property
from app.models import Broker, Property
from app.schemas import LocationSchema, PropertyCreate, PropertyOut, PropertyUpdate, SizeSchema

router = APIRouter(prefix="/properties", tags=["properties"])


def _to_out(prop: Property) -> PropertyOut:
    return PropertyOut(
        id=prop.id,
        broker_id=prop.broker_id,
        type=prop.type,
        title=prop.title,
        location=LocationSchema(area=prop.area, city=prop.city),
        price=float(prop.price),
        price_unit=prop.price_unit,
        size=SizeSchema(value=float(prop.size_value), unit=prop.size_unit),
        bedrooms=prop.bedrooms,
        amenities=list(prop.amenities or []),
        status=prop.status,
        description=prop.description,
        created_at=prop.created_at,
        updated_at=prop.updated_at,
    )


def _apply_fields(prop: Property, payload: PropertyCreate | PropertyUpdate) -> None:
    prop.type = payload.type
    prop.title = payload.title
    prop.area = payload.location.area
    prop.city = payload.location.city
    prop.price = payload.price
    prop.price_unit = payload.price_unit
    prop.size_value = payload.size.value
    prop.size_unit = payload.size.unit
    prop.bedrooms = payload.bedrooms
    prop.amenities = payload.amenities
    prop.status = payload.status
    prop.description = payload.description


@router.get("", response_model=list[PropertyOut])
async def list_properties(
    broker: Broker = Depends(get_current_broker),
    session: AsyncSession = Depends(get_session),
):
    result = await session.scalars(
        select(Property).where(Property.broker_id == broker.id).order_by(Property.created_at.desc())
    )
    return [_to_out(p) for p in result.all()]


@router.post("", response_model=PropertyOut, status_code=status.HTTP_201_CREATED)
async def create_property(
    payload: PropertyCreate,
    broker: Broker = Depends(get_current_broker),
    session: AsyncSession = Depends(get_session),
):
    prop = Property(broker_id=broker.id)
    _apply_fields(prop, payload)
    session.add(prop)
    await session.commit()
    await session.refresh(prop)
    return _to_out(prop)


@router.get("/{property_id}", response_model=PropertyOut)
async def get_property(
    property_id: uuid.UUID,
    broker: Broker = Depends(get_current_broker),
    session: AsyncSession = Depends(get_session),
):
    prop = await get_owned_property(property_id, broker, session)
    return _to_out(prop)


@router.put("/{property_id}", response_model=PropertyOut)
async def update_property(
    property_id: uuid.UUID,
    payload: PropertyUpdate,
    broker: Broker = Depends(get_current_broker),
    session: AsyncSession = Depends(get_session),
):
    prop = await get_owned_property(property_id, broker, session)
    _apply_fields(prop, payload)
    await session.commit()
    await session.refresh(prop)
    return _to_out(prop)


@router.delete("/{property_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_property(
    property_id: uuid.UUID,
    broker: Broker = Depends(get_current_broker),
    session: AsyncSession = Depends(get_session),
):
    prop = await get_owned_property(property_id, broker, session)
    await session.delete(prop)
    await session.commit()
