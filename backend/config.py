import json
from typing import Annotated

from pydantic import AliasChoices, AnyHttpUrl, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)
    app_env: str = "local"
    root_path: str = ""
    aws_region: str = "ap-northeast-2"
    aws_profile: str | None = None
    s3_bucket: str = Field(default="", validation_alias=AliasChoices("FILES_BUCKET", "s3_bucket"))
    public_base_url: AnyHttpUrl | None = None
    table_transfers: str = "sd_transfers"
    table_sessions: str = "sd_sessions"
    table_events: str = "sd_events"
    table_admins: str = "sd_admins"
    table_admin_sessions: str = "sd_admin_sessions"
    max_file_mb: int = Field(default=20, ge=1, le=20)
    scan_file_max_mb: int = Field(default=4, ge=1, le=4)
    max_files_per_transfer: int = Field(default=5, ge=1, le=5)
    default_expires_sec: int = 600
    max_expires_sec: int = Field(default=3600, ge=300, le=3600)
    session_ttl_sec: int = Field(default=600, ge=1, le=600)
    upload_url_ttl_sec: int = Field(default=300, ge=1, le=300)
    download_url_ttl_sec: int = Field(default=300, ge=1, le=300)
    admin_session_ttl_sec: int = Field(default=28800, ge=1, le=28800)
    admin_login_max_fails: int = Field(default=5, ge=1, le=5)
    admin_login_lock_sec: int = Field(default=900, ge=1)
    allowed_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:5500",
        "http://localhost:8000",
        "http://127.0.0.1:5500",
        "http://127.0.0.1:8000",
    ]

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, value):
        if isinstance(value, str):
            return json.loads(value) if value.lstrip().startswith("[") else value.split(",")
        return value

    @model_validator(mode="after")
    def validate_limits(self):
        self.allowed_origins = [origin.strip() for origin in self.allowed_origins]
        if self.default_expires_sec not in (300, 600, 3600):
            raise ValueError("DEFAULT_EXPIRES_SEC must be 300, 600 or 3600")
        if self.default_expires_sec > self.max_expires_sec:
            raise ValueError("Default expiry exceeds maximum")
        if "*" in self.allowed_origins:
            raise ValueError("Explicit CORS origins are required")
        if self.public_base_url and (
            self.public_base_url.path not in (None, "/")
            or self.public_base_url.query
            or self.public_base_url.fragment
            or self.public_base_url.username
            or self.public_base_url.password
        ):
            raise ValueError("PUBLIC_BASE_URL must be an HTTP(S) origin without credentials")
        if self.app_env == "prod" and not self.public_base_url:
            raise ValueError("PUBLIC_BASE_URL is required in production")
        return self
