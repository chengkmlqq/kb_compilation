"""Runtime config resolution — env-first, DB fallback, sensitive-value masking.

Ported from data-synth src/lib/runtime-config.ts:
- resolve_runtime_config: DB dim codes (SYSTEM_CONFIG) take priority, then env,
  then fallback. Sensitive keys are masked in output (never logged raw).
- mask_value / is_sensitive_key helpers used by logging and audit paths.
"""

from __future__ import annotations

import os

from api.services.config_cache import get_system_config_rows
from sqlalchemy.orm import Session

SENSITIVE_KEYS = {
    "AI_CHAT_API_KEY",
    "OPEN_API_CLIENT_SECRET",
    "OPEN_API_JWT_SECRET",
    "WATERMARK_API_KEY",
    "WATERMARK_SALT",
    "PY_SCHEDULER_API_TOKEN",
    "JOB_DISPATCH_API_TOKEN",
    "SYNTH_WEAVER_API_KEY",
    "SYNTH_WEAVER_API_TOKEN",
    "SYNC_SECRET_KEY",
    "SSO_SYNC_SECRET_KEY",
    "AES_SECRET_KEY",
    "AES_IV_KEY",
}


def normalize_value(value: object) -> str:
    return str(value or "").strip()


def mask_sensitive_value(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}****{value[-4:]}"


def is_sensitive_key(env_key: str) -> bool:
    upper = env_key.upper()
    return (
        upper in SENSITIVE_KEYS
        or "SECRET" in upper
        or "TOKEN" in upper
        or "KEY" in upper
    )


def resolve_runtime_config(
    db: Session,
    env_key: str,
    db_codes: list[str] | None = None,
    sensitive: bool | None = None,
    fallback: str = "",
) -> dict:
    """Resolve a runtime config value (mirrors TS resolveRuntimeConfig)."""
    env_value = normalize_value(os.getenv(env_key))
    sens = sensitive if sensitive is not None else is_sensitive_key(env_key)

    if not db_codes:
        return {
            "value": env_value,
            "source": "env" if env_value else "db",
            "masked": mask_sensitive_value(env_value) if sens else env_value,
        }

    unique_codes = list(dict.fromkeys(normalize_value(c) for c in db_codes if normalize_value(c)))
    if not unique_codes:
        return {
            "value": env_value,
            "source": "env" if env_value else "db",
            "masked": mask_sensitive_value(env_value) if sens else env_value,
        }

    config_rows = get_system_config_rows(db)
    config_map = {code: config_rows[code] for code in unique_codes if code in config_rows}

    resolved = ""
    for code in unique_codes:
        if code in config_map:
            resolved = config_map[code]
            break
    if not resolved:
        resolved = env_value or fallback

    source = "db" if any(code in config_map for code in unique_codes) else ("env" if env_value else "db")
    return {
        "value": resolved,
        "source": source,
        "masked": mask_sensitive_value(resolved) if sens else resolved,
    }


def get_env_config(env_key: str, fallback: str = "") -> str:
    return normalize_value(os.getenv(env_key)) or fallback


def mask_value(value: str) -> str:
    return mask_sensitive_value(normalize_value(value))
