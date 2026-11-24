from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    database_url: str = "postgresql+psycopg://compass:compass_local_only@127.0.0.1:55432/compass"
    compass_demo_enabled: bool = False
    # Trusted BFF service identity; model routes are unavailable when unset.
    compass_agent_service_token: str = ""
    # Protects provider credentials and tool-continuation payloads (Fernet envelope).
    compass_model_encryption_key: str = ""
    # Exact HTTPS origins allowed for provider endpoints; comma separated.
    compass_model_allowed_origins: str = "https://api.deepseek.com,https://api.typesafe.ai"
    compass_model_allow_local_http: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
