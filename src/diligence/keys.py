"""SM4 API key encryption helper.

Encrypt a plaintext API key before writing it to .env:
    python -m diligence.keys encode <plaintext_key>

The output (SM4:Base64) can be pasted directly into .env.
Settings loads and decrypts it automatically at runtime.

Commands:
    encode <plaintext>   Encrypt and print SM4:Base64 string
    check                Show encryption status of all API_KEY entries in .env
"""

from __future__ import annotations

import sys
from pathlib import Path

from diligence.settings import _SM4_PREFIX, _sm4_encrypt


def _cmd_encode(plaintext: str) -> None:
    """Print SM4-encrypted form of *plaintext*."""
    sys.stdout.write(_SM4_PREFIX + _sm4_encrypt(plaintext) + "\n")


def _cmd_check() -> None:
    """Print encryption status of all *_API_KEY entries in .env."""
    env_file = Path(".env")
    if not env_file.exists():
        sys.stderr.write(".env not found\n")
        sys.exit(1)

    found = False
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, val = line.partition("=")
        if not name.strip().upper().endswith("API_KEY"):
            continue
        found = True
        if val.startswith(_SM4_PREFIX):
            status = "✓ encrypted"
        elif not val:
            status = "(empty)"
        else:
            status = "⚠ PLAINTEXT — run: python -m diligence.keys encode <key>"
        sys.stdout.write(f"  {name.strip()}: {status}\n")

    if not found:
        sys.stdout.write("  (no *_API_KEY entries found in .env)\n")


def main() -> None:
    """Entry point for `python -m diligence.keys`."""
    args = sys.argv[1:]
    match args:
        case ["encode", key]:
            _cmd_encode(key)
        case ["check"]:
            _cmd_check()
        case _:
            sys.stderr.write(
                "Usage:\n"
                "  python -m diligence.keys encode <plaintext_key>   # encrypt\n"
                "  python -m diligence.keys check                    # .env status\n"
            )
            sys.exit(2)


if __name__ == "__main__":
    main()
