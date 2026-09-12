"""Runtime settings. Every value comes from the environment (prefix PBIMCP_) or a local .env file."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

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

    # Power BI / Fabric
    fabric_api_url: str = f"{FABRIC_RESOURCE}/v1"
    hosted_mcp_url: str = f"{FABRIC_RESOURCE}/v1/mcp/powerbi"

    # Foundry (Azure OpenAI) used by generate_dax
    foundry_endpoint: str | None = None
    foundry_deployment: str = "gpt-5"
    foundry_reasoning_effort: str = "low"
    foundry_api_key: str | None = None  # optional; default is Entra (managed identity / az login)

    # Behaviour
    catalog_cache_seconds: int = 300
    schema_cache_seconds: int = 600
    default_max_rows: int = 250
    skills_dir: Path = Path(__file__).resolve().parent.parent / "skills"
    host: str = "0.0.0.0"
    port: int = 8000

    @property
    def fabric_scope(self) -> str:
        return f"{FABRIC_RESOURCE}/.default"

    @property
    def identifier_uri(self) -> str:
        return f"api://{self.client_id}"


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
