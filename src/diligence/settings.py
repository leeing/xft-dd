"""Runtime settings loaded from environment / .env file.

All environment variable access MUST go through this module.
Direct env-var access via os module is forbidden by check-constraints.py.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings sourced from environment variables or .env file."""

    # MiniMax Search — 搜索层，不可替换
    minimax_api_key: str = Field(default="")
    minimax_base_url: str = Field(default="https://api.minimaxi.chat/v1")

    # 秘塔 AI 搜索 (metaso.cn) — 带联网搜索能力的 AI 问答
    metaso_api_key: str = Field(default="")
    metaso_enabled: bool = Field(default=False)

    # 推理层（摘要 + 合并报告）— 支持任意 OpenAI 兼容接口
    # llm_api_key 为空时自动 fallback 到 minimax_api_key（向后兼容）
    llm_api_key: str = Field(default="")
    llm_base_url: str = Field(default="https://api.minimaxi.chat/v1")
    llm_model: str = Field(default="MiniMax-M2.7-Highspeed")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()
