from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token, get_current_broker, hash_password, verify_password
from app.db import get_session
from app.models import Broker
from app.schemas import BrokerCreate, BrokerLogin, BrokerOut, Token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(payload: BrokerCreate, session: AsyncSession = Depends(get_session)):
    existing = await session.scalar(select(Broker).where(Broker.email == payload.email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    broker = Broker(email=payload.email, hashed_password=hash_password(payload.password), name=payload.name)
    session.add(broker)
    await session.commit()
    await session.refresh(broker)

    return Token(access_token=create_access_token(broker.id))


@router.post("/login", response_model=Token)
async def login(payload: BrokerLogin, session: AsyncSession = Depends(get_session)):
    broker = await session.scalar(select(Broker).where(Broker.email == payload.email))
    if broker is None or not verify_password(payload.password, broker.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    return Token(access_token=create_access_token(broker.id))


@router.get("/me", response_model=BrokerOut)
async def me(broker: Broker = Depends(get_current_broker)):
    return broker
