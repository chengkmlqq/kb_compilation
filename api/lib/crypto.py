"""Cryptographic helpers — byte-compatible with the source platform's TypeScript.

The source platform uses CryptoJS (AES-128-CBC / DES-ECB, PKCS7, key padded with '0')
and must interoperate because BOTH services read/write the SAME tables
(`modo_user.user_pwd` is stored AES-encrypted) and share cookies.

Keep these functions bit-exact with src/lib/crypto/{aes,des,utils}.ts.
"""

from __future__ import annotations

import base64
import os

from Crypto.Cipher import AES, DES


def fill_char(key: str, target_length: int) -> str:
    """Pad-or-truncate key with '0' — same as the source platform's fillChar()."""
    if len(key) >= target_length:
        return key[:target_length]
    return key.ljust(target_length, "0")


def _default_aes_key() -> str:
    return os.getenv("AES_SECRET_KEY", "b6fa92796c6431c5")


def _default_iv() -> str:
    return os.getenv("AES_IV_KEY", "7b51fd7053196308")


def _default_des_key() -> str:
    return os.getenv("LEGACY_DES_KEY", "b6fa92796c6431c5")


def aes_encrypt(plaintext: str, key: str | None = None) -> str:
    """AES-128-CBC / PKCS7, base64 output (CryptoJS-compatible)."""
    k = fill_char(key or _default_aes_key(), 16).encode("utf-8")
    iv = _default_iv().encode("utf-8")
    cipher = AES.new(k, AES.MODE_CBC, iv)
    data = plaintext.encode("utf-8")
    # PKCS7 padding
    pad_len = 16 - (len(data) % 16)
    data += bytes([pad_len] * pad_len)
    return base64.b64encode(cipher.encrypt(data)).decode("ascii")


def aes_decrypt(ciphertext: str, key: str | None = None) -> str:
    """Inverse of aes_encrypt. Returns '' on failure (mirrors TS behaviour)."""
    try:
        k = fill_char(key or _default_aes_key(), 16).encode("utf-8")
        iv = _default_iv().encode("utf-8")
        raw = base64.b64decode(ciphertext)
        cipher = AES.new(k, AES.MODE_CBC, iv)
        data = cipher.decrypt(raw)
        # strip PKCS7
        pad_len = data[-1]
        if 1 <= pad_len <= 16:
            data = data[:-pad_len]
        return data.decode("utf-8")
    except Exception:
        return ""


def des_encrypt(plaintext: str, key: str | None = None) -> str:
    """DES-ECB / PKCS7, base64 output — legacy identity cookie format."""
    k = fill_char(key or _default_des_key(), 8).encode("utf-8")
    cipher = DES.new(k, DES.MODE_ECB)
    data = plaintext.encode("utf-8")
    pad_len = 8 - (len(data) % 8)
    data += bytes([pad_len] * pad_len)
    return base64.b64encode(cipher.encrypt(data)).decode("ascii")


def des_decrypt(ciphertext: str, key: str | None = None) -> str:
    """Inverse of des_encrypt. Returns '' on failure."""
    try:
        k = fill_char(key or _default_des_key(), 8).encode("utf-8")
        raw = base64.b64decode(ciphertext)
        cipher = DES.new(k, DES.MODE_ECB)
        data = cipher.decrypt(raw)
        pad_len = data[-1]
        if 1 <= pad_len <= 8:
            data = data[:-pad_len]
        return data.decode("utf-8")
    except Exception:
        return ""