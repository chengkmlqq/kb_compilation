#!/usr/bin/env python
"""Idempotent schema migration: add chat_message.thinking (framework store).

chat_message is created via Base.metadata.create_all on first boot, so
pre-existing databases never receive new model columns -> drift
(chat QA message persistence fails with "Unknown column 'chat_message.thinking'").

This migration adds the column when missing and is safe to run repeatedly.

Usage:
  python scripts/migrate_chat_thinking_column.py [env_file]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TABLE = "chat_message"
COLUMN = "thinking"


def _load_env(env_file: str | None) -> None:
    if not env_file:
        env_file = str(ROOT / ".env")
    if os.path.exists(env_file):
        for line in Path(env_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    env_file = sys.argv[1] if len(sys.argv) > 1 else None
    _load_env(env_file)
    url = os.environ.get("DATABASE_URL") or os.environ.get("KB_DATABASE_URL")
    if not url:
        print("no DATABASE_URL / KB_DATABASE_URL in env; nothing to do")
        return 0

    engine = create_engine(url)
    try:
        insp = inspect(engine)
        if TABLE not in insp.get_table_names():
            print(f"table {TABLE} not present; nothing to do")
            return 0
        cols = {c["name"] for c in insp.get_columns(TABLE)}
        if COLUMN in cols:
            print(f"{TABLE}.{COLUMN} already present; nothing to do")
            return 0
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} TEXT"))
        print(f"migrated: added {TABLE}.{COLUMN}")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
