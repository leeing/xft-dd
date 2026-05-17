"""Runtime settings loaded from environment / .env file.

All environment variable access MUST go through this module.
Direct env-var access via os module is forbidden by check-constraints.py.

API keys are expected to be SM4-encrypted in .env (prefix SM4:).
To encrypt a key:
    python -m xft.keys encode <plaintext_key>

Plaintext keys are still accepted for backward compatibility.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# SM4 = 128-bit block cipher (国密标准); fixed 16-byte obfuscation key.
# SECURITY: This is local obfuscation only — prevents accidental plaintext exposure
# in .env files (e.g. copy-paste, screen sharing). It does NOT protect against
# local attackers with filesystem access. For production, use keyring or a system
# keychain instead.
_SM4_KEY: bytes = b"xft" + b"\x00" * 13
_SM4_PREFIX = "SM4:"
_KEY_FIELDS = ("minimax_api_key", "metaso_api_key", "llm_api_key")


def _sm4_encrypt(plaintext: str) -> str:
    """PKCS7-pad, SM4-ECB encrypt, Base64-encode."""
    data = plaintext.encode()
    pad_len = 16 - (len(data) % 16)
    data += bytes([pad_len] * pad_len)
    enc = Cipher(algorithms.SM4(_SM4_KEY), modes.ECB()).encryptor()  # noqa: S305
    return base64.b64encode(enc.update(data) + enc.finalize()).decode()


def _sm4_decrypt(ciphertext_b64: str) -> str:
    """Base64-decode, SM4-ECB decrypt, strip PKCS7 padding."""
    ct = base64.b64decode(ciphertext_b64)
    dec = Cipher(algorithms.SM4(_SM4_KEY), modes.ECB()).decryptor()  # noqa: S305
    raw = dec.update(ct) + dec.finalize()
    return raw[: -raw[-1]].decode()


def _decode_key(v: str) -> str:
    """Decrypt SM4-prefixed value; return plaintext as-is for backward compat."""
    return _sm4_decrypt(v[len(_SM4_PREFIX) :]) if v.startswith(_SM4_PREFIX) else v


class Settings(BaseSettings):
    """Application settings sourced from environment variables or .env file."""

    # MiniMax Search — 搜索层，不可替换
    minimax_api_key: str = Field(default="")
    minimax_base_url: str = Field(default="https://api.minimax.io/v1")

    # 秘塔 AI 搜索 (metaso.cn) — 带联网搜索能力的 AI 问答
    metaso_api_key: str = Field(default="")
    metaso_enabled: bool = Field(default=False)
    metaso_verify_tls: bool = Field(default=True)

    # 推理层（摘要 + 合并报告）— 支持任意 OpenAI 兼容接口
    # llm_api_key 为空时自动 fallback 到 minimax_api_key（向后兼容）
    llm_api_key: str = Field(default="")
    llm_base_url: str = Field(default="https://api.minimax.io/v1")
    llm_model: str = Field(default="MiniMax-M2.7-Highspeed")

    # SQL cache — optional L1 search cache + L2 fetch cache.
    # Supports sqlite+aiosqlite:///... for local development and
    # postgresql+asyncpg://... for shared remote caches.
    cache_enabled: bool = Field(default=False)
    cache_database_url: str = Field(default="sqlite+aiosqlite:///cache/diligence_cache.db")
    cache_create_tables: bool = Field(default=True)
    cache_policy_version: str = Field(default="v1-202605")
    cache_worker_id: str = Field(default="local-dev")

    search_cache_enabled: bool = Field(default=True)
    search_cache_ttl_days: int = Field(default=14)

    fetch_cache_enabled: bool = Field(default=True)
    fetch_cache_ttl_days: int = Field(default=30)
    fetch_failed_retry_hours: int = Field(default=24)
    fetch_cache_lock_minutes: int = Field(default=10)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @model_validator(mode="after")
    def _decode_keys(self) -> Settings:
        """Auto-decrypt SM4-prefixed key fields after loading from .env."""
        for field in _KEY_FIELDS:
            if raw := getattr(self, field):
                object.__setattr__(self, field, _decode_key(raw))
        return self


settings = Settings()
