from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql://coldcall:coldcall@localhost:5432/coldcall"

    jwt_secret_key: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7

    cors_origins: str = "http://localhost:5173"

    voice_service_url: str = "http://localhost:8001"
    internal_api_secret: str = "dev-internal-secret-change-me"

    call_concurrency_limit: int = 5
    worker_poll_interval_seconds: int = 5

    # TRAI DND/NDNC compliance (see app.dnd). No local match and no vendor
    # configured means the number's DND status literally cannot be determined —
    # dnd_allow_unverified controls whether that fails the call closed (safe
    # default) or open (local-dev convenience only, never for production).
    dnd_provider_api_url: str = ""
    dnd_provider_api_key: str = ""
    dnd_allow_unverified: bool = False

    # Calling-hour restriction, enforced at the queue level (worker.py._claim_batch).
    # TRAI/TCCCPR permits commercial calls only 9am-9pm local time by default.
    calling_window_start_hour: int = 9
    calling_window_end_hour: int = 21
    calling_timezone: str = "Asia/Kolkata"

    # Retry logic for no-answer/busy leads (see routers/internal.py.update_lead_status).
    max_call_attempts: int = 2
    retry_delay_hours: int = 2

    # Monitoring/alerting thresholds (see app.monitoring, worker.py.alerting_poll_forever).
    # Alerts only fire once at least this many calls have landed today, so a couple
    # of early failures doesn't trip a false alarm.
    alert_min_sample_size: int = 5
    alert_success_rate_threshold: float = 0.5
    alert_error_rate_threshold: float = 0.2
    alert_webhook_url: str = ""
    alerting_poll_interval_seconds: int = 900

    # Call recording storage (see app.recording_storage) — S3 bucket/region only;
    # AWS credentials come from boto3's standard env vars (AWS_ACCESS_KEY_ID etc.)
    # rather than being re-plumbed through settings.
    recording_s3_bucket: str = ""
    aws_region: str = "ap-south-1"
    recording_presigned_url_expiry_seconds: int = 900
    recording_retention_days: int = 90
    recording_retention_poll_interval_seconds: int = 86400

    # Property-suggestion batch job (see app.worker.suggestions_poll_forever).
    # How often the worker checks whether any property is due for a run.
    suggestion_poll_interval_seconds: int = 3600
    # Trigger condition 1: re-run once at least this many newly-classified calls
    # have landed since the last run (also gates the very first run).
    suggestion_trigger_call_count: int = 5
    # Trigger condition 2: re-run at least this often regardless of call count,
    # as long as there's been at least one new call since the last run.
    suggestion_trigger_interval_hours: int = 24
    # Cap on how many recent calls' transcripts are sent to GPT-4o in one batch,
    # to bound prompt size.
    suggestion_context_max_calls: int = 30

    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
