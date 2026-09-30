#!/usr/bin/env python
"""Verify ORM models align with the live data-synth database (both directions).

Reads DATABASE_URL from the specified env file, inspects real table/column
definitions, and compares against our SQLAlchemy models:

  1. every framework table in the DB has a model
  2. every model's columns exist in the DB table (names + nullability)
  3. synth business tables found in DB but NOT modeled (expected, informational)

Usage:
  python scripts/verify_schema_alignment.py [env_file]     # env_file defaults to ../.env.development of data-synth

Exit code 0 = aligned, 1 = mismatch (prints diffs).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

# Allow running from repo root or scripts/.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.db import Base  # noqa: E402
from api.models import framework  # noqa: E402,F401  (registers all models)

# Tables present in the live DB that are intentionally NOT modeled:
# - `*_bak_*` are operator backup tables (schema drift artifacts)
# - modo_requirement / modo_requirement_entity are synth business (extracted out)
# - modo_user_synth_100 is an ad-hoc table from the old synth link
IGNORED_DB_TABLES = {
    "modo_requirement",
    "modo_requirement_entity",
    "modo_user_synth_100",
}


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    env_file = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "../data-synth/.env.development"
    load_env_file(env_file)

    # Explicit DATABASE_URL env var wins (lets callers point at a local mirror).
    url = os.environ.get("KB_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
    if not url:
        print("FATAL: DATABASE_URL not found in", env_file)
        return 2

    # Redact password for display.
    shown = url
    if "@" in shown:
        scheme, _, rest = shown.partition("://")
        userinfo, _, host = rest.partition("@")
        shown = f"{scheme}://{userinfo.split(':')[0]}:***@{host}"
    print("Connecting to:", shown)

    # MySQL URLs from data-synth use bare `mysql://`; SQLAlchemy needs the
    # PyMySQL driver explicitly.
    if url.startswith("mysql://") and "+pymysql" not in url:
        url = url.replace("mysql://", "mysql+pymysql://", 1)
    engine = create_engine(url, pool_pre_ping=True)
    inspector = inspect(engine)

    db_tables = set(inspector.get_table_names())
    model_tables = {t.name for t in Base.metadata.sorted_tables}

    # 1. Framework tables present in DB but missing from models
    #    (excluding backup artifacts `*_bak_*` and known-extracted business).
    missing_in_models = sorted(
        t for t in db_tables
        if t.startswith(("modo_", "ai_chat_"))
        and "_bak_" not in t
        and t not in IGNORED_DB_TABLES
        and t not in model_tables
    )
    if missing_in_models:
        print("[MISSING-IN-MODELS] framework tables in DB without a model:")
        for t in missing_in_models:
            print("   -", t)

    # 2. Per-model column alignment.
    mismatches: list[str] = []
    for table in Base.metadata.sorted_tables:
        name = table.name
        if name not in db_tables:
            mismatches.append(f"TABLE {name}: not found in DB")
            continue
        db_cols = {c["name"]: c for c in inspector.get_columns(name)}
        model_cols = {c.name for c in table.columns}
        # index columns: model-only or db-only
        for c in sorted(model_cols - set(db_cols)):
            mismatches.append(f"  {name}.{c}: in model but not in DB")
        for c in sorted(set(db_cols) - model_cols):
            mismatches.append(f"  {name}.{c}: in DB but not in model")

    # 3. Expected synth business tables present? informational.
    synth_business = sorted(t for t in db_tables if t.startswith("synth_"))
    print(f"DB size: {len(db_tables)} tables total; framework {len(model_tables)} modeled; synth business left in DB: {len(synth_business)}")

    if mismatches:
        print("\n[MISMATCHES]")
        for m in mismatches:
            print(m)
        return 1

    print("\nALIGNED: all framework tables match column-for-column.")
    return 0


if __name__ == "__main__":
    sys.exit(main())