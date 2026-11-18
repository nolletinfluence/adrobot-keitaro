from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/adrobot.db"
    keitaro_mode: Literal["live", "demo"] = "live"
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
        return urlunsplit((parts.scheme, parts.netloc.lower(), "/admin_api/v1", "", ""))

    @model_validator(mode="after")
    def isolate_demo_database(self):
        parsed = make_url(self.database_url)
        if self.keitaro_mode == "demo" and parsed.database and parsed.database != ":memory:":
            path = Path(parsed.database)
            if not path.stem.endswith("-demo"):
                path = path.with_name(path.stem + "-demo" + path.suffix)
                self.database_url = str(parsed.set(database=str(path)))
        return self

    @field_validator("campaign_prefix")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        if not value.startswith("test_") or not value.endswith("_") or len(value) < 7:
            raise ValueError("Campaign prefix must look like test_surname_")
        return value
