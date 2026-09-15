"""Runtime settings. Every value comes from the environment (prefix PBIMCP_) or a local .env file."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FABRIC_RESOURCE = "https://api.fabric.microsoft.com"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PBIMCP_", env_file=".env", extra="ignore")

    # Entra app registration (confidential client) used by the OAuth proxy and for the OBO exchange
    tenant_id: str
    client_id: str
    client_secret: str
    base_url: str = "http://localhost:8000"
    jwt_signing_key: str | None = None
    api_scope_name: str = "access_as_user"
    server_name: str = "Power BI MCP Gateway"
    oauth_storage_dir: Path | None = None

    # Power BI / Fabric
    fabric_api_url: str = f"{FABRIC_RESOURCE}/v1"
    hosted_mcp_url: str = f"{FABRIC_RESOURCE}/v1/mcp/powerbi"

    # Foundry (Azure OpenAI) used by generate_dax
    foundry_endpoint: str | None = None
    foundry_deployment: str = "gpt-5"
    foundry_reasoning_effort: str = "low"
    foundry_api_key: str | None = None  # optional; default is Entra (managed identity / az login)

    # Behaviour
    catalog_cache_seconds: int = Field(default=300, ge=0)
    schema_cache_seconds: int = Field(default=600, ge=0)
    default_max_rows: int = Field(default=250, ge=1, le=5000)
    max_rows_limit: int = Field(default=5000, ge=1, le=5000)
    cache_max_entries: int = Field(default=256, ge=1)
    timezone: str = "UTC"
    skills_dir: Path = Path(__file__).resolve().parent.parent / "skills"
    host: str = "0.0.0.0"
    port: int = 8000

    @model_validator(mode="after")
    def validate_behavior(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        if self.default_max_rows > self.max_rows_limit:
            raise ValueError("default_max_rows exceeds max_rows_limit")
        if self.oauth_storage_dir and (not self.jwt_signing_key or len(self.jwt_signing_key) < 32):
            raise ValueError("Persistent OAuth storage requires PBIMCP_JWT_SIGNING_KEY of at least 32 characters.")
        return self

    @property
    def fabric_scope(self) -> str:
        return f"{FABRIC_RESOURCE}/.default"

    @property
    def identifier_uri(self) -> str:
        return f"api://{self.client_id}"


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
