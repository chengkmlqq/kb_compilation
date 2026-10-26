"""导出框架表种子数据（配置型，非运行时数据）为 SQL 文件。

用途：data_supply_ontology 等新库初始化——应用启动时若框架表为空，
自动导入本文件生成的种子，保证登录/RBAC/菜单/团队/数据源类型可用。

导出范围（仅配置型表，排除运行时增长数据）：
  modo_user / modo_user_role / modo_user_role_rela / modo_team /
  modo_team_member / modo_menu / modo_role_menu_rela / modo_ds_type /
  modo_ds_category / modo_ds_form_field / modo_dim / modo_datasource

用法：docker exec kb-api-server python /srv/kb/scripts/export_framework_seed.py
     （输出 scripts/framework_seed.sql，utf8mb4，INSERT IGNORE 幂等）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SEED_TABLES = [
    "modo_user",
    "modo_user_role",
    "modo_user_role_rela",
    "modo_team",
    "modo_team_member",
    "modo_menu",
    "modo_role_menu_rela",
    "modo_ds_type",
    "modo_ds_category",
    "modo_ds_form_field",
    "modo_dim",
    "modo_datasource",
]

OUT = Path(__file__).resolve().parent / "framework_seed.sql"


def main() -> int:
    from sqlalchemy import create_engine, text

    url = os.environ.get("DATABASE_URL") or os.environ.get("KB_DATABASE_URL") or ""
    if not url:
        print("DATABASE_URL 未设置", file=sys.stderr)
        return 2
    engine = create_engine(url)
    lines = [
        "-- Framework seed data (exported from data_supply).",
        "-- Idempotent: INSERT IGNORE — safe to re-run on an existing DB.",
        "SET NAMES utf8mb4;",
        "",
    ]
    with engine.connect() as conn:
        for table in SEED_TABLES:
            rows = conn.execute(text(f"SELECT * FROM `{table}`")).fetchall()
            if not rows:
                continue
            cols = conn.execute(text(f"SHOW COLUMNS FROM `{table}`")).fetchall()
            colnames = [r[0] for r in cols]
            # 逐行 INSERT IGNORE（值转义，NULL/数字原样）
            for row in rows:
                vals = []
                for v in row:
                    if v is None:
                        vals.append("NULL")
                    elif isinstance(v, (int, float)):
                        vals.append(str(v))
                    else:
                        esc = str(v).replace("\\", "\\\\").replace("'", "''")
                        vals.append(f"'{esc}'")
                lines.append(
                    f"INSERT IGNORE INTO `{table}` (`{'`,`'.join(colnames)}`) "
                    f"VALUES ({','.join(vals)});"
                )
            lines.append("")
    lines.append("")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"OK: {len(SEED_TABLES)} 表导出 → {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
