import json

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_broker
from app.config import settings
from app.db import get_session
from app.models import Broker, Property
from app.property_import import (
    BatchRowRef,
    DocxParseError,
    ExcelParseError,
    apply_import_fields,
    cell_to_display_str,
    extract_docx_text,
    find_duplicates,
    guess_column_mapping,
    normalize_excel_row,
    normalize_extracted_row,
    parse_excel,
    validate_row,
)
from app.schemas import (
    ImportCommitError,
    ImportCommitIn,
    ImportCommitOut,
    ImportParseOut,
    ImportRowData,
    ImportRowOut,
)

router = APIRouter(prefix="/properties/import", tags=["property-import"])

MAX_IMPORT_UPLOAD_BYTES = 10 * 1024 * 1024  # richer documents than the plain lead CSV, so a higher cap


@router.post("/parse", response_model=ImportParseOut)
async def parse_import_file(
    file: UploadFile,
    mapping: str | None = Form(default=None),
    broker: Broker = Depends(get_current_broker),
    session: AsyncSession = Depends(get_session),
):
    filename = (file.filename or "").lower()
    if filename.endswith(".xlsx"):
        source = "excel"
    elif filename.endswith(".docx"):
        source = "word"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="File must be an Excel (.xlsx) or Word (.docx) document"
        )

    raw_bytes = await file.read(MAX_IMPORT_UPLOAD_BYTES + 1)
    if len(raw_bytes) > MAX_IMPORT_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large")

    explicit_mapping: dict[str, str] | None = None
    if mapping:
        try:
            explicit_mapping = json.loads(mapping)
            if not isinstance(explicit_mapping, dict):
                raise ValueError
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="mapping must be a JSON object of field -> header"
            ) from exc

    existing_properties = list(
        (await session.scalars(select(Property).where(Property.broker_id == broker.id))).all()
    )
    batch_so_far: list[BatchRowRef] = []

    if source == "excel":
        try:
            headers, raw_rows = parse_excel(raw_bytes)
        except ExcelParseError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        column_mapping = explicit_mapping if explicit_mapping is not None else guess_column_mapping(headers)

        rows: list[ImportRowOut] = []
        for offset, raw in enumerate(raw_rows):
            row_number = offset + 2  # row 1 is the header, matching leads.py's CSV row numbering
            data = normalize_excel_row(raw, column_mapping)
            errors = validate_row(data)
            duplicates = find_duplicates(data, existing_properties, batch_so_far)
            batch_so_far.append(BatchRowRef(row_number=row_number, data=data))
            rows.append(
                ImportRowOut(
                    row_number=row_number,
                    raw={header: cell_to_display_str(value) for header, value in raw.items()},
                    data=data,
                    errors=errors,
                    duplicates=duplicates,
                )
            )

        return ImportParseOut(source="excel", headers=headers, column_mapping=column_mapping, rows=rows)

    # Word doc: extract raw text, then hand it to voice-service's GPT-4o
    # extraction pass — backend owns persistence/dedup, voice-service owns the
    # LLM call, same split as the suggestion-generation feature.
    try:
        text = extract_docx_text(raw_bytes)
    except DocxParseError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if not text.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No readable text found in this document")

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{settings.voice_service_url.rstrip('/')}/extract-properties", json={"text": text}
            )
            resp.raise_for_status()
            extracted = resp.json().get("properties", [])
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Property extraction failed") from exc

    rows = []
    for offset, item in enumerate(extracted):
        row_number = offset + 1
        data = normalize_extracted_row(item)
        errors = validate_row(data)
        duplicates = find_duplicates(data, existing_properties, batch_so_far)
        batch_so_far.append(BatchRowRef(row_number=row_number, data=data))
        rows.append(ImportRowOut(row_number=row_number, raw=None, data=data, errors=errors, duplicates=duplicates))

    return ImportParseOut(source="word", headers=None, column_mapping=None, rows=rows)


@router.post("/commit", response_model=ImportCommitOut)
async def commit_import(
    payload: ImportCommitIn,
    broker: Broker = Depends(get_current_broker),
    session: AsyncSession = Depends(get_session),
):
    existing_by_id = {
        p.id: p for p in (await session.scalars(select(Property).where(Property.broker_id == broker.id))).all()
    }

    errors: list[ImportCommitError] = []
    to_create: list[ImportRowData] = []
    to_merge: list[tuple[ImportRowData, Property]] = []
    skipped = 0

    for index, item in enumerate(payload.items):
        if item.action == "skip":
            skipped += 1
            continue

        if item.data is None:
            errors.append(ImportCommitError(index=index, detail="Row is missing data"))
            continue

        row_errors = validate_row(item.data)
        if row_errors:
            errors.append(ImportCommitError(index=index, detail="; ".join(row_errors)))
            continue

        if item.action == "create":
            to_create.append(item.data)
        elif item.action == "merge":
            if item.target_property_id is None:
                errors.append(ImportCommitError(index=index, detail="Merge action requires a target property"))
                continue
            target = existing_by_id.get(item.target_property_id)
            if target is None:
                errors.append(ImportCommitError(index=index, detail="Target property not found"))
                continue
            to_merge.append((item.data, target))

    for data in to_create:
        prop = Property(broker_id=broker.id)
        apply_import_fields(prop, data)
        session.add(prop)

    for data, target in to_merge:
        apply_import_fields(target, data)

    await session.commit()

    return ImportCommitOut(created=len(to_create), merged=len(to_merge), skipped=skipped, errors=errors)
