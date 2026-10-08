"""Compare SQLAlchemy metadata against live DB schemas (schema drift audit).

Usage:
  python scripts/check_schema_drift.py

Reports, for the framework engine (Base metadata) and the vector engine
(KnowledgeBase metadata):
  - MISSING TABLE   model defines it, DB doesn't have it
  - MISSING COLUMN  model has it, DB doesn't  -> the class of bug that makes
                   any INSERT/UPDATE touching the column fail with
                   "Unknown column ..." (create_all never alters existing DBs)
  - EXTRA COLUMN    DB has it, model doesn't (usually harmless leftovers)
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_all_models() -> None:
    import api.models as pkg

    for _, name, _ in pkgutil.iter_modules(pkg.__path__):
        importlib.import_module(f"api.models.{name}")


def _norm_type(t: str) -> str:
    """Normalise a type string for loose comparison."""
    s = str(t).lower()
    # strip " collate utf8mb4_unicode_ci" etc.
    s = s.split(" collate")[0].strip()
    s = s.split("(")[0].strip()
    return {
        "int": "integer",
        "integer": "integer",
        "bigint": "bigint",
        "smallint": "smallint",
        "boolean": "tinyint",  # MySQL stores BOOLEAN as tinyint(1)
        "tinyint": "tinyint",
        "varchar": "varchar",
        "text": "text",
        "longtext": "text",
        "mediumtext": "text",
        "datetime": "timestamp",  # DATETIME/TIMESTAMP interchangeable for our use
        "timestamp": "timestamp",
        "float": "float",
        "double": "double",
        "bool": "tinyint",
        "json": "json",
        "longblob": "blob",  # LONG/BLOB all store bytes; model uses plain BLOB
        "mediumblob": "blob",
        "blob": "blob",
    }.get(s, s)


def audit(engine, base, label: str) -> list[str]:
    from sqlalchemy import inspect

    insp = inspect(engine)
    db_tables = set(insp.get_table_names())
    lines: list[str] = []

    for table_name in sorted(base.metadata.tables):
        table = base.metadata.tables[table_name]
        if table_name not in db_tables:
            lines.append(f"[{label}] MISSING TABLE  {table_name}")
            continue
        db_cols = {c["name"]: c for c in insp.get_columns(table_name)}
        for col in table.columns:
            if col.name not in db_cols:
                lines.append(
                    f"[{label}] MISSING COLUMN {table_name}.{col.name} "
                    f"(model type={col.type})"
                )
            else:
                want = _norm_type(str(col.type))
                got = _norm_type(db_cols[col.name]["type"])
                if want != got and not (want == "varchar" and got.startswith("varchar")):
                    lines.append(
                        f"[{label}] TYPE DIFF     {table_name}.{col.name} "
                        f"model={want} db={got}"
                    )
        model_cols = {c.name for c in table.columns}
        for name in sorted(set(db_cols) - model_cols):
            lines.append(f"[{label}] EXTRA COLUMN   {table_name}.{name}")

    for name in sorted(db_tables - set(base.metadata.tables)):
        if name.startswith(("modo_", "kb_", "wiki_", "chat_", "ai_", "ontology_")):
            # heuristic: only report tables that look app-owned
            lines.append(f"[{label}] ORPHAN TABLE   {name} (in DB, no model)")

    return lines


def main() -> int:
    _load_all_models()
    from api.db import Base, KnowledgeBase, get_engine, get_knowledge_engine

    all_lines = audit(get_engine(), Base, "framework")
    try:
        all_lines += audit(get_knowledge_engine(), KnowledgeBase, "vector")
    except Exception as exc:  # pragma: no cover - vector DB may be down
        all_lines.append(f"[vector] SKIPPED ({type(exc).__name__}: {exc})")

    if not all_lines:
        print("No schema drift detected.")
        return 0
    print(f"Schema drift issues: {len(all_lines)}")
    for line in all_lines:
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())