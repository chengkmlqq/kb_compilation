#!/usr/bin/env python
"""Idempotent schema migration: add chat_session.parent_session_id.

chat_session is created via Base.metadata.create_all on first boot, so
pre-existing databases never receive new model columns -> drift
(fork fails with "Unknown column 'chat_session.parent_session_id'").

Adds the column when missing; safe to run repeatedly (same pattern as
migrate_chat_thinking_column.py).

Usage:
  python scripts/migrate_chat_parent_session_column.py [env_file]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TABLE = "chat_session"
COLUMN = "parent_session_id"


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
    _load_env(sys.argv[1] if len(sys.argv) > 1 else None)
    import sqlalchemy.engine.url as url_mod

    db_url = os.getenv("DATABASE_URL") or os.getenv("DB_DSN")
    if not db_url:
        print("DATABASE_URL not set — nothing to do")
        return 0
    url = url_mod.make_url(db_url)
    if url.get_backend_name() == "sqlite":
        print(f"sqlite {TABLE} is created fresh by create_all — no migration needed")
        return 0
    engine = create_engine(db_url)
    try:
        inspector = inspect(engine)
        cols = {c["name"] for c in inspector.get_columns(TABLE)}
        if COLUMN in cols:
            print(f"{TABLE}.{COLUMN} already present; doing nothing")
            return 0
        with engine.begin() as conn:
            conn.execute(
                text(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} VARCHAR(64) NULL")
            )
        print(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} — done")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
