import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Broker, Property


async def get_owned_property(property_id: uuid.UUID, broker: Broker, session: AsyncSession) -> Property:
    prop = await session.scalar(select(Property).where(Property.id == property_id, Property.broker_id == broker.id))
    if prop is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Property not found")
    return prop
