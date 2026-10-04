"""WeKnora 对齐迁移：kb_datasource 加配置列 + 回填存量 indexing_strategy。

在 api-server 容器内执行（自带 DATABASE_URL）。逐列 ALTER、逐条 commit，幂等可重跑。

回填语义：存量 KB 的 indexing_strategy 补全为「迁移前的实际行为」
（vector/keyword=True, wiki=True 维持自动建 wiki, graph=False 新默认），
使严格对齐的新默认值不影响存量。**新建 KB 走 WeKnora 默认（wiki/graph=False）**。
"""
import json

from sqlalchemy import text

from api.config import get_settings
from api.db import get_engine

NEW_COLUMNS = [
    ("type", "VARCHAR(32) NOT NULL DEFAULT 'document'"),
    ("custom_wiki_generation", "TINYINT(1) NOT NULL DEFAULT 0"),
    ("embedding_model_id", "VARCHAR(64) NULL"),
    ("summary_model_id", "VARCHAR(64) NULL"),
    ("vlm_config", "JSON NULL"),
    ("asr_config", "JSON NULL"),
    ("image_processing_config", "JSON NULL"),
    ("extract_config", "JSON NULL"),
    ("faq_config", "JSON NULL"),
    ("question_generation_config", "JSON NULL"),
    ("wiki_config", "JSON NULL"),
    ("storage_provider_config", "JSON NULL"),
    ("storage_backend_id", "VARCHAR(36) NULL"),
    ("vector_store_id", "VARCHAR(36) NULL"),
]

# 存量行为保持：wiki=True（迁移前自动建 wiki），graph=False（原本无消费点）
BACKFILL = {
    "vector_enabled": True,
    "keyword_enabled": True,
    "wiki_enabled": True,
    "graph_enabled": False,
}


def main() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        # 1) 逐列 ALTER（幂等：已存在则跳过）
        for col, ddl in NEW_COLUMNS:
            try:
                conn.execute(text(f"ALTER TABLE kb_datasource ADD COLUMN {col} {ddl}"))
                conn.commit()
                print(f"ADDED  {col}")
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                if "Duplicate column" in str(exc) or "duplicate" in str(exc).lower():
                    print(f"SKIP   {col} (exists)")
                else:
                    print(f"FAIL   {col}: {exc}")
                    raise

        # embedding_model_id 索引
        try:
            conn.execute(text("CREATE INDEX ix_kb_datasource_embedding_model_id ON kb_datasource (embedding_model_id)"))
            conn.commit()
            print("INDEX  ix_kb_datasource_embedding_model_id created")
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            print(f"SKIP   index (exists?): {exc}")

        # 2) 回填存量 indexing_strategy 为完整四键（保持迁移前行为）
        rows = conn.execute(text("SELECT id, indexing_strategy FROM kb_datasource")).fetchall()
        for kb_id, raw in rows:
            try:
                cur = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (TypeError, json.JSONDecodeError):
                cur = {}
            merged = dict(cur)
            for k, v in BACKFILL.items():
                merged.setdefault(k, v)  # 仅补缺失，不覆盖已有显式值
            conn.execute(
                text("UPDATE kb_datasource SET indexing_strategy = :s WHERE id = :i"),
                {"s": json.dumps(merged, ensure_ascii=False), "i": kb_id},
            )
        conn.commit()
        print(f"BACKFILLED {len(rows)} kb rows with four-key indexing_strategy")

        # 3) 验证
        cols = {r[0] for r in conn.execute(text("SHOW COLUMNS FROM kb_datasource")).fetchall()}
        missing = [c for c, _ in NEW_COLUMNS if c not in cols]
        print("VERIFY missing columns:", missing or "NONE")
        print("MIGRATION OK")


if __name__ == "__main__":
    main()
