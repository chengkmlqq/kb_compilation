"""幂等迁移：kb_tape_step 补 queue_name 列。

背景（2026-10-09 编排导入导出 e2e 撞出）：2026-10-09 编排异步化批次给
TapeStep 模型加了 queue_name 列（指定节点投递到哪个 celery 队列）并写了
0002 alembic 迁移，但**线上 kb_tape_step 存量表没有该列**（表由
Base.metadata.create_all 建的，create_all 只建新表不改旧表，alembic 未跑）。
症状：POST /orchestrations/{id}/design（编排保存）HTTP 500，
traceback = `Unknown column 'queue_name' in 'field list'`。

用法（必须在 kb-api-server 容器内执行，宿主连不上 compose 内网 mysql）：

    docker cp scripts/migrate_orch_queue_name.py kb-api-server:/tmp/
    docker exec -w /srv/kb kb-api-server python /tmp/migrate_orch_queue_name.py

幂等：列已存在则 no-op，可重复跑。
"""
import os

# 容器内 DATABASE_URL 由 compose 注入（指向 mysql:3306/kb_frame）
url = os.environ.get("KB_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not url:
    raise SystemExit("Missing KB_DATABASE_URL/DATABASE_URL")

import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text


def main() -> None:
    print("[db] target:", url.split("@")[-1].split("/")[0])
    engine = create_engine(url)
    insp = inspect(engine)
    cols = [c["name"] for c in insp.get_columns("kb_tape_step")]
    print("[before] kb_tape_step cols:", cols)
    if "queue_name" in cols:
        print("ALREADY: queue_name exists, no-op")
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE kb_tape_step ADD COLUMN queue_name VARCHAR(64) NULL"))
    cols2 = [c["name"] for c in inspect(engine).get_columns("kb_tape_step")]
    print("[after] kb_tape_step cols:", cols2)
    assert "queue_name" in cols2, "migration failed: queue_name still missing"
    print("MIGRATION OK: queue_name added to kb_tape_step")


if __name__ == "__main__":
    main()