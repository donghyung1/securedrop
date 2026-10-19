from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    aws_region: str = "ap-northeast-2"
    aws_profile: str | None = None
    s3_bucket: str = ""
    max_file_mb: int = Field(default=20, ge=1, le=20)
    scan_file_max_mb: int = Field(default=5, ge=1, le=5)
    default_expires_sec: int = 600
    max_expires_sec: int = Field(default=3600, ge=300, le=3600)
    sanitize_ttl_sec: int = Field(default=600, ge=1, le=3600)
    sanitize_memory_mb: int = Field(default=100, ge=20, le=1024)
    plugin_api_key: SecretStr = SecretStr("")
    allowed_origins: list[str] = [
        "http://localhost:5500",
        "http://localhost:8000",
        "http://127.0.0.1:5500",
        "http://127.0.0.1:8000",
    ]
    database_path: Path = Path("data/securedrop.db")

    @model_validator(mode="after")
    def validate_limits(self):
        if self.default_expires_sec not in (300, 600, 3600):
            raise ValueError("DEFAULT_EXPIRES_SEC must be 300, 600 or 3600")
        if self.default_expires_sec > self.max_expires_sec:
            raise ValueError("Default expiry exceeds maximum")
        if "*" in self.allowed_origins:
            raise ValueError("Explicit CORS origins are required")
        return self
