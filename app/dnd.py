"""TRAI DND/NDNC (National Customer Preference Register) compliance check.

FLAGGED FINDING: there is no free-standing government REST API a business can
call per phone number. Real access to NCPR data comes one of two ways:

  1. Register as a Principal Entity + Registered Telemarketer on a DLT platform
     (Airtel/Jio/Vi/BSNL/Tata) to receive bulk NCPR CSV dumps (an initial full
     dump, then daily deltas) — a multi-day/week registration process, or
  2. Subscribe to a third-party compliance/CPaaS vendor (e.g. Gupshup, Kaleyra,
     MSG91, ValueFirst) that exposes a scrubbing API/feed on your behalf — the
     realistic path for a business at this scale that doesn't want to become a
     registered telemarketer itself.

Either way the data is a *list to scrub against*, not a live single-number
government lookup. This module implements that: a locally synced table
(populated from an NCPR CSV export or a vendor's export via POST
/internal/dnd/sync) checked on every dial, plus an optional live HTTP call-out
to a configured vendor endpoint for numbers not yet in the local cache.

Fail-closed by default: if a number's DND status genuinely cannot be
determined (no local match, no vendor configured, or the vendor call fails),
the call is treated as blocked ("unverified") rather than risking a
non-compliant call — see settings.dnd_allow_unverified for the local-dev-only
override. Until a vendor is wired up or the local table is synced, this means
*all* calls will be blocked as unverified; that's intentional, not a bug.
"""
import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import DNDNumber

logger = logging.getLogger("dnd")


@dataclass
class DNDResult:
    blocked: bool
    reason: str  # "dnd_listed" | "unverified" | "clear"


async def _vendor_lookup(phone_number: str) -> bool | None:
    """Returns True/False if the vendor gave a definitive answer, None if the
    call itself failed (network error, non-2xx, bad payload) — callers must
    treat None as "couldn't verify", not as "clear"."""
    if not settings.dnd_provider_api_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                settings.dnd_provider_api_url,
                params={"phone_number": phone_number},
                headers={"Authorization": f"Bearer {settings.dnd_provider_api_key}"}
                if settings.dnd_provider_api_key
                else {},
            )
            resp.raise_for_status()
            data = resp.json()
            return bool(data.get("is_dnd"))
    except Exception:
        logger.exception("DND vendor lookup failed for phone_number=%s", phone_number)
        return None


async def check_dnd(phone_number: str, session: AsyncSession) -> DNDResult:
    local_hit = await session.get(DNDNumber, phone_number)
    if local_hit is not None:
        return DNDResult(blocked=True, reason="dnd_listed")

    vendor_result = await _vendor_lookup(phone_number)
    if vendor_result is True:
        session.add(DNDNumber(phone_number=phone_number, source="vendor"))
        return DNDResult(blocked=True, reason="dnd_listed")
    if vendor_result is False:
        return DNDResult(blocked=False, reason="clear")

    # vendor_result is None: either no vendor configured, or the lookup failed.
    if settings.dnd_allow_unverified:
        logger.warning(
            "DND status unverified for phone_number=%s — allowing call because "
            "dnd_allow_unverified=True (local-dev only, never for production)",
            phone_number,
        )
        return DNDResult(blocked=False, reason="unverified")

    logger.warning(
        "DND status unverified for phone_number=%s — blocking (fail-closed). "
        "Sync the local dnd_numbers table via POST /internal/dnd/sync or configure "
        "DND_PROVIDER_API_URL to a compliance vendor to lift this.",
        phone_number,
    )
    return DNDResult(blocked=True, reason="unverified")


async def sync_dnd_numbers(phone_numbers: list[str], source: str, session: AsyncSession) -> int:
    """Upsert a batch of numbers into the local DND cache — this is how an NCPR
    CSV export or a vendor's bulk feed gets loaded. Returns the count inserted
    (numbers already present are left untouched, not double-counted)."""
    existing = set(
        await session.scalars(select(DNDNumber.phone_number).where(DNDNumber.phone_number.in_(phone_numbers)))
    )
    new_numbers = [number for number in phone_numbers if number not in existing]
    session.add_all([DNDNumber(phone_number=number, source=source) for number in new_numbers])
    await session.commit()
    return len(new_numbers)
