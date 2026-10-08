#!/usr/bin/env python
"""Idempotent schema migration: add chat_attachment.media_type / file_data.

Same drift class as migrate_chat_parent_session_column.py: chat_attachment is
created via Base.metadata.create_all on first boot, so pre-existing databases
never receive new model columns (media_type / file_data were added later to
support image/text attachments).

Adds the columns when missing; safe to run repeatedly.

Usage:
  python scripts/migrate_chat_attachment_columns.py [env_file]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TABLE = "chat_attachment"
COLUMNS = {
    "media_type": "VARCHAR(16) NOT NULL DEFAULT 'text'",
    "file_data": "BLOB NULL",
}


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
        existing = {c["name"] for c in inspector.get_columns(TABLE)}
        missing = [name for name in COLUMNS if name not in existing]
        if not missing:
            print(f"{TABLE} already has all of {sorted(COLUMNS)}; doing nothing")
            return 0
        with engine.begin() as conn:
            for name in missing:
                conn.execute(
                    text(f"ALTER TABLE {TABLE} ADD COLUMN {name} {COLUMNS[name]}")
                )
        print(f"ALTER TABLE {TABLE} ADD COLUMN {', '.join(missing)} — done")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
