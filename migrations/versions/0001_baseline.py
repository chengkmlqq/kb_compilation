"""Baseline: adopt the existing framework store without recreating it.

The framework store (MySQL kb_frame) already contains all 47 tables matching
Base.metadata (verified by scripts/check_schema_drift.py on 2026-10-08, all
missing columns migrated manually beforehand). Recreating them here would
fail on a live DB; instead this empty revision anchors the Alembic version
line at the current schema state, and `alembic stamp <this>` records the
live DB as up-to-date. Future model changes generate real incremental
migrations via `alembic revision --autogenerate`.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-08

"""
from __future__ import annotations

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass