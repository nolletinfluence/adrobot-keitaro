from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/adrobot.db"
    keitaro_base_url: str = "https://tlgk.host"
    keitaro_api_key: SecretStr = SecretStr("")
    campaign_prefix: str = "test_borkoev_"
    upstream_timeout_seconds: float = Field(default=15, ge=1, le=60)
    upstream_operation_timeout_seconds: float = Field(default=45, ge=1, le=50)

    @field_validator("keitaro_base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path.rstrip("/") not in {"", "/admin_api/v1"}
        ):
            raise ValueError("Use a Keitaro origin or /admin_api/v1 URL without credentials")
        return urlunsplit((parts.scheme, parts.netloc, "/admin_api/v1", "", ""))

    @field_validator("campaign_prefix")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        if not value.startswith("test_") or not value.endswith("_") or len(value) < 7:
            raise ValueError("Campaign prefix must look like test_surname_")
        return value
