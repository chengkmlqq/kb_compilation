"""Operation audit logging — write modo_oper_log rows.

Ported from data-synth src/lib/operate-log.ts: field-length limiting, action
classification (mutating vs readonly via prefix), and a single writer used by
API handlers to record user operations (login/logout/CRUD/...).
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from api.models.framework import OperLog

logger = logging.getLogger(__name__)

MUTATING_PREFIX = re.compile(
    r"^(save|delete|update|create|upload|import|export|send|switch|set|toggle|start|stop|"
    r"retry|cancel|run|submit|execute|add|remove|mark|publish|trigger|download)",
    re.IGNORECASE,
)
READONLY_PREFIX = re.compile(
    r"^(get|list|search|query|check|preview|resolve|find|load)",
    re.IGNORECASE,
)

MAX_FIELDS = {
    "user_id": 64,
    "user_name": 64,
    "team_name": 64,
    "oper_type": 32,
    "client_id": 64,
    "server_id": 64,
    "oper_url": 512,
    "menu_id": 64,
}


def _limit(value: Any, max_len: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_len]


def classify_action(name: str) -> str:
    """Classify an action as mutating or readonly by its prefix."""
    if MUTATING_PREFIX.match(name):
        return "mutating"
    if READONLY_PREFIX.match(name):
        return "readonly"
    return "other"


def format_oper_time(value: Any = None) -> str:
    if value is None:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)[:19]


def write_oper_log(
    db: Session,
    *,
    oper_type: str,
    oper_content: str | None = None,
    user_id: str | None = None,
    user_name: str | None = None,
    team_name: str | None = None,
    oper_url: str | None = None,
    menu_id: str | None = None,
    client_id: str | None = None,
    server_id: str | None = None,
    server_port: int | None = None,
    oper_time: Any = None,
) -> OperLog:
    """Insert one operation-audit row (field lengths clamped like the TS)."""
    row = OperLog(
        id=uuid.uuid4().hex,
        oper_type=_limit(oper_type, MAX_FIELDS["oper_type"]) or "OTHER",
        oper_content=_limit(oper_content, 65535),
        user_id=_limit(user_id, MAX_FIELDS["user_id"]),
        user_name=_limit(user_name, MAX_FIELDS["user_name"]),
        team_name=_limit(team_name, MAX_FIELDS["team_name"]),
        oper_url=_limit(oper_url, MAX_FIELDS["oper_url"]),
        menu_id=_limit(menu_id, MAX_FIELDS["menu_id"]),
        client_id=_limit(client_id, MAX_FIELDS["client_id"]),
        server_id=_limit(server_id, MAX_FIELDS["server_id"]),
        server_port=server_port,
        oper_time=format_oper_time(oper_time),
    )
    db.add(row)
    db.commit()
    return row
