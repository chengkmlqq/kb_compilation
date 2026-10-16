"""诊断：api-server 容器内 get_sessionmaker 的默认 env 与 KB/文档可见性。"""
from sqlalchemy import func, select

from api.db import get_sessionmaker
from api.models.knowledge import KbDatasource, KbDocument

KB_ID = "087150f1cd2c47649fa2362334cfb1a9"
DOC_ID = "520e15f3899f4f74aea554cbc221d031"


def main() -> None:
    db = get_sessionmaker()()
    try:
        print("kb_total:", db.execute(select(func.count()).select_from(KbDatasource)).scalar())
        print("doc_total:", db.execute(select(func.count()).select_from(KbDocument)).scalar())
        print("kb_found:", db.execute(
            select(KbDatasource.id).where(KbDatasource.id == KB_ID)
        ).scalar())
        print("doc_found:", db.execute(
            select(KbDocument.id).where(KbDocument.id == DOC_ID)
        ).scalar())
        # 库名确认
        from sqlalchemy import text
        print("current_db:", db.execute(text("SELECT DATABASE()")).scalar())
    finally:
        db.close()


if __name__ == "__main__":
    main()