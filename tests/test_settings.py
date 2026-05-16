"""Tests for SM4 key encryption/decryption in settings.py."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from diligence.settings import Settings, _SM4_PREFIX, _decode_key, _sm4_decrypt, _sm4_encrypt


# ── Core crypto roundtrip ──────────────────────────────────────────────────


def test_sm4_roundtrip_ascii() -> None:
    """Encrypt then decrypt an ASCII key returns the original value."""
    original = "sk-abcdef1234567890"
    assert _sm4_decrypt(_sm4_encrypt(original)) == original


def test_sm4_roundtrip_short() -> None:
    """Keys shorter than one block (16 bytes) roundtrip correctly."""
    original = "short"
    assert _sm4_decrypt(_sm4_encrypt(original)) == original


def test_sm4_roundtrip_exactly_16_bytes() -> None:
    """Keys of exactly 16 bytes (edge case: PKCS7 adds a full extra block)."""
    original = "a" * 16
    assert _sm4_decrypt(_sm4_encrypt(original)) == original


def test_sm4_roundtrip_long() -> None:
    """Keys longer than one block roundtrip correctly."""
    original = "sk-" + "x" * 60
    assert _sm4_decrypt(_sm4_encrypt(original)) == original


def test_sm4_roundtrip_chinese() -> None:
    """Non-ASCII (UTF-8) values roundtrip correctly."""
    original = "密钥-测试值"
    assert _sm4_decrypt(_sm4_encrypt(original)) == original


def test_sm4_ciphertext_does_not_contain_plaintext() -> None:
    """Encrypted output must not expose the original plaintext."""
    original = "supersecretkey"
    ciphertext = _sm4_encrypt(original)
    assert original not in ciphertext


def test_sm4_encrypt_returns_no_prefix() -> None:
    """_sm4_encrypt returns raw Base64, not SM4:-prefixed string."""
    ciphertext = _sm4_encrypt("key")
    assert not ciphertext.startswith(_SM4_PREFIX)


# ── _decode_key dispatch ───────────────────────────────────────────────────


def test_decode_key_with_sm4_prefix() -> None:
    """SM4:-prefixed values are decrypted to plaintext."""
    original = "my-api-key-xyz"
    encrypted = _SM4_PREFIX + _sm4_encrypt(original)
    assert _decode_key(encrypted) == original


def test_decode_key_plaintext_passthrough() -> None:
    """Values without SM4: prefix are returned as-is (backward compat)."""
    plaintext = "plain-value-no-prefix"
    assert _decode_key(plaintext) == plaintext


def test_decode_key_empty_string() -> None:
    """Empty string passes through without error."""
    assert _decode_key("") == ""


def test_decode_key_prefix_only_raises() -> None:
    """'SM4:' with no ciphertext raises an exception (signals corrupt .env)."""
    with pytest.raises(Exception):  # noqa: B017
        _decode_key(_SM4_PREFIX)


# ── Settings model_validator integration ──────────────────────────────────


def test_settings_validator_decodes_minimax_key() -> None:
    """Settings.minimax_api_key is transparently decrypted on instantiation."""
    original = "minimax-key-abc"
    encrypted = _SM4_PREFIX + _sm4_encrypt(original)
    s = Settings(
        minimax_api_key=encrypted,
        minimax_base_url="https://example.com/v1",
        llm_base_url="https://example.com/v1",
    )
    assert s.minimax_api_key == original


def test_settings_validator_decodes_metaso_key() -> None:
    """Settings.metaso_api_key is transparently decrypted on instantiation."""
    original = "metaso-key-xyz"
    encrypted = _SM4_PREFIX + _sm4_encrypt(original)
    s = Settings(metaso_api_key=encrypted)
    assert s.metaso_api_key == original


def test_settings_validator_decodes_llm_key() -> None:
    """Settings.llm_api_key is transparently decrypted on instantiation."""
    original = "llm-key-999"
    encrypted = _SM4_PREFIX + _sm4_encrypt(original)
    s = Settings(llm_api_key=encrypted)
    assert s.llm_api_key == original


def test_settings_validator_plaintext_unchanged() -> None:
    """Plain-text key values pass through the validator without modification."""
    s = Settings(minimax_api_key="plain-value")
    assert s.minimax_api_key == "plain-value"


def test_settings_validator_empty_keys_unchanged() -> None:
    """Empty key values remain empty strings after validation."""
    s = Settings(minimax_api_key="", metaso_api_key="", llm_api_key="")
    assert s.minimax_api_key == ""
    assert s.metaso_api_key == ""
    assert s.llm_api_key == ""


def test_settings_all_three_keys_decoded_independently() -> None:
    """All three key fields are decoded independently in one Settings instance."""
    k1, k2, k3 = "key-one", "key-two", "key-three"
    s = Settings(
        minimax_api_key=_SM4_PREFIX + _sm4_encrypt(k1),
        metaso_api_key=_SM4_PREFIX + _sm4_encrypt(k2),
        llm_api_key=_SM4_PREFIX + _sm4_encrypt(k3),
    )
    assert s.minimax_api_key == k1
    assert s.metaso_api_key == k2
    assert s.llm_api_key == k3


def test_settings_sql_cache_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """SQL cache is opt-in and defaults to local SQLite when enabled later."""
    for name in (
        "CACHE_ENABLED",
        "CACHE_DATABASE_URL",
        "CACHE_CREATE_TABLES",
        "CACHE_POLICY_VERSION",
        "CACHE_WORKER_ID",
        "SEARCH_CACHE_ENABLED",
        "SEARCH_CACHE_TTL_DAYS",
        "FETCH_CACHE_ENABLED",
        "FETCH_CACHE_TTL_DAYS",
        "FETCH_FAILED_RETRY_HOURS",
        "FETCH_CACHE_LOCK_MINUTES",
    ):
        monkeypatch.delenv(name, raising=False)
    s = Settings(_env_file=None)
    assert s.cache_enabled is False
    assert s.cache_database_url.startswith("sqlite+aiosqlite:///")
    assert s.search_cache_enabled is True
    assert s.fetch_cache_enabled is True
    assert s.fetch_cache_lock_minutes == 10


# ── keys.py CLI helpers ────────────────────────────────────────────────────


def test_cmd_encode_output_has_prefix(capsys: pytest.CaptureFixture) -> None:
    """keys encode writes SM4:-prefixed ciphertext to stdout."""
    from diligence.keys import _cmd_encode

    _cmd_encode("my-secret")
    out = capsys.readouterr().out.strip()
    assert out.startswith(_SM4_PREFIX)


def test_cmd_check_encrypted(tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture) -> None:
    """keys check reports 'encrypted' for SM4:-prefixed keys."""
    from diligence.keys import _cmd_check

    env_file = tmp_path / ".env"  # type: ignore[operator]
    env_file.write_text(f"MINIMAX_API_KEY={_SM4_PREFIX}abc123\n", encoding="utf-8")

    with patch("diligence.keys.Path") as mock_path_cls:
        mock_path_cls.return_value = env_file
        _cmd_check()

    out = capsys.readouterr().out
    assert "encrypted" in out


def test_cmd_check_plaintext_warns(tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture) -> None:
    """keys check flags plaintext keys with a warning."""
    from diligence.keys import _cmd_check

    env_file = tmp_path / ".env"  # type: ignore[operator]
    env_file.write_text("MINIMAX_API_KEY=some-value\n", encoding="utf-8")

    with patch("diligence.keys.Path") as mock_path_cls:
        mock_path_cls.return_value = env_file
        _cmd_check()

    out = capsys.readouterr().out
    assert "PLAINTEXT" in out.upper()
