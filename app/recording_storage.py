"""Backend side of call-recording storage: on-demand presigned GET URLs for the
dashboard, and deletion once a recording ages past the retention window (see
worker.py.recording_retention_poll_forever). Uploads happen on the voice-service
side (voice-service/app/recording_storage.py) right after Plivo's recording
webhook fires — this module never writes objects, only reads/deletes.

AWS credentials come from boto3's standard env vars (AWS_ACCESS_KEY_ID /
AWS_SECRET_ACCESS_KEY) or an attached IAM role — only the bucket/region are
pydantic-settings-managed (app.config.settings).
"""
import asyncio
import logging

import boto3

from app.config import settings

logger = logging.getLogger("recording_storage")

_client = None


def _s3_client():
    global _client
    if _client is None:
        _client = boto3.client("s3", region_name=settings.aws_region)
    return _client


def presigned_url(key: str) -> str:
    return _s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.recording_s3_bucket, "Key": key},
        ExpiresIn=settings.recording_presigned_url_expiry_seconds,
    )


async def get_presigned_url(key: str) -> str:
    return await asyncio.to_thread(presigned_url, key)


async def delete_recording(key: str) -> None:
    def _delete() -> None:
        _s3_client().delete_object(Bucket=settings.recording_s3_bucket, Key=key)

    try:
        await asyncio.to_thread(_delete)
    except Exception:
        logger.exception("Failed to delete recording key=%s from S3", key)
        raise
