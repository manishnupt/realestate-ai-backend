import logging

from sqlalchemy import select

from app.auth import hash_password
from app.config import settings
from app.db import async_session
from app.models import Broker

logger = logging.getLogger("seed")


async def seed_demo_broker() -> None:
    if not settings.show_demo_login:
        return

    async with async_session() as session:
        existing = await session.scalar(select(Broker).where(Broker.email == settings.demo_broker_email))
        if existing is not None:
            return

        session.add(
            Broker(
                email=settings.demo_broker_email,
                hashed_password=hash_password(settings.demo_broker_password),
                name="Demo Account",
            )
        )
        await session.commit()
        logger.info("Seeded demo broker account email=%s", settings.demo_broker_email)
