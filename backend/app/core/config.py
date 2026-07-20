from functools import lru_cache

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = "PVE Master"
    environment: str = "development"
    log_level: str = "INFO"
    database_url: SecretStr
    redis_url: SecretStr
    app_secret_key: SecretStr = Field(min_length=32)
    cors_origins: list[AnyHttpUrl] = Field(default_factory=list)
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    refresh_token_ttl_days: int = Field(default=30, ge=1, le=90)
    login_failure_limit: int = Field(default=5, ge=2, le=20)
    login_failure_window_seconds: int = Field(default=900, ge=60, le=86400)
    login_lockout_seconds: int = Field(default=900, ge=60, le=86400)
    jwt_issuer: str = "pve-master"
    jwt_audience: str = "pve-master-api"
    pve_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    pve_read_timeout_seconds: float = Field(default=15.0, gt=0, le=300)
    pve_max_connections: int = Field(default=20, gt=0, le=200)
    pve_max_keepalive_connections: int = Field(default=10, gt=0, le=100)
    pve_allowed_hosts: list[str] = Field(default_factory=list)
    pve_allowed_networks: list[str] = Field(default_factory=list)
    pve_task_poll_interval_seconds: float = Field(default=2.0, gt=0, le=30)
    pve_task_timeout_seconds: int = Field(default=300, ge=10, le=3600)
    pve_task_max_poll_attempts: int = Field(default=150, ge=1, le=1000)
    pve_action_max_attempts: int = Field(default=3, ge=1, le=5)
    console_session_ttl_seconds: int = Field(default=30, ge=10, le=120)
    console_max_duration_seconds: int = Field(default=3600, ge=60, le=14400)
    console_connect_timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    console_sessions_per_minute: int = Field(default=10, ge=1, le=60)
    operation_lease_seconds: int = Field(default=60, ge=10, le=600)
    worker_heartbeat_ttl_seconds: int = Field(default=60, ge=15, le=600)
    queue_backlog_alert_threshold: int = Field(default=100, ge=1, le=1000000)
    ip_pool_low_available_threshold: int = Field(default=5, ge=0, le=1000000)
    provisioning_failure_alert_count: int = Field(default=3, ge=1, le=1000)
    provisioning_failure_window_minutes: int = Field(default=30, ge=1, le=10080)
    audit_retention_days: int = Field(default=365, ge=30, le=3650)


@lru_cache
def get_settings() -> Settings:
    return Settings()
