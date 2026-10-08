"""Alembic migration environment — KB framework store.

Target metadata: Base (framework store, DATABASE_URL). The vector store
(KnowledgeBase) is intentionally NOT migrated here (see alembic.ini header).
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the project importable regardless of cwd (alembic runs from repo root
# in containers and locally).
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Import ALL model modules so every table registers on Base.metadata.
import api.models  # noqa: F401
from api.config import get_settings  # noqa: E402
from api.db import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# URL resolved at runtime from env (never stored in alembic.ini).
config.set_main_option("sqlalchemy.url", get_settings().require_database_url())

target_metadata = Base.metadata


def _include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Only diff framework tables — ignore orphan/legacy DB tables that have
    no model (modo_*_bak_*, data-synth leftovers)."""
    return type_ != "table" or name in target_metadata.tables


def _include_name(name, type_, parent_names) -> bool:
    """Never filter by name at the autogenerate reflection layer.

    Returning False for anything here hides reflected objects from the diff
    (e.g. filtering "index" types makes every existing index look newly
    added). Index/FK noise is instead suppressed via compare_indexes=False /
    compare_foreign_keys=False, which skip those comparison passes entirely.
    """
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL, no DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
        include_name=_include_name,
        compare_type=False,  # TIMESTAMP/DATETIME etc. are equivalent here
        compare_indexes=False,  # idx_*/ix_* name drift is legacy noise
        compare_foreign_keys=False,  # unnamed-FK drops would fail; manual
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect to the DB)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=_include_object,
            include_name=_include_name,
            compare_type=False,
            compare_indexes=False,
            compare_foreign_keys=False,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()