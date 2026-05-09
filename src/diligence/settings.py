"""Runtime settings loaded from environment / .env file.

All environment variable access MUST go through this module.
Direct env-var access via os module is forbidden by check-constraints.py.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings sourced from environment variables or .env file."""

    minimax_api_key: str = Field(default="")
    minimax_base_url: str = Field(default="https://api.minimaxi.chat/v1")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()
