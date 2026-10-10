"""幂等建表：kb_metadata_table / kb_metadata_column（2026-10-10 元数据采集）。

放容器内跑：docker cp scripts/migrate_metadata_tables.py kb-api-server:/tmp/ && \\
              docker exec -w /srv/kb kb-api-server python /tmp/migrate_metadata_tables.py

幂等：用 SQLAlchemy inspect 检查表/列，已存在则跳过（支持重复执行）。
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect

from api.db import get_sessionmaker
from api.models.metadata import MetadataColumn, MetadataTable

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migrate_metadata_tables")


def main() -> None:
    db = get_sessionmaker()()
    try:
        insp = inspect(db.bind)
        existing = set(insp.get_table_names())
        created = 0
        for model in (MetadataTable, MetadataColumn):
            if model.__tablename__ not in existing:
                model.__table__.create(db.bind)
                created += 1
                logger.info("created table %s", model.__tablename__)
            else:
                logger.info("table %s already exists, skip", model.__tablename__)
        db.commit()
        logger.info("done, created=%d", created)
    finally:
        db.close()


if __name__ == "__main__":
    main()