"""验证 metadata 列表接口 jobs like 查询修复（2026-10-15）：or_ 兼容两种分隔符。

修复前两个 like 是隐式 AND（json.dumps 带空格分隔，无空格变体恒不匹配）→ jobs 恒空
→ COLLECTING/FAILED 分支永不触发，full 重采清空表格窗口期显示「未采集」。
"""
from sqlalchemy import func, or_, select

from api.db import get_sessionmaker
from api.models.framework import Job

DS_ID = "38de21b5779d4b7a899e4d9d046d0cd0"  # 线上有 META 任务的数据源


def main() -> None:
    db = get_sessionmaker()()
    try:
        n_and = db.execute(
            select(func.count()).select_from(Job).where(
                Job.task_class == "KbMetadataCollectionTask",
                Job.task_params.like(f'%"datasource_id": "{DS_ID}"%'),
                Job.task_params.like(f'%"datasource_id":"{DS_ID}"%'),
            )
        ).scalar()
        n_or = db.execute(
            select(func.count()).select_from(Job).where(
                Job.task_class == "KbMetadataCollectionTask",
                or_(
                    Job.task_params.like(f'%"datasource_id": "{DS_ID}"%'),
                    Job.task_params.like(f'%"datasource_id":"{DS_ID}"%'),
                ),
            )
        ).scalar()
        states = db.execute(
            select(Job.state).where(
                Job.task_class == "KbMetadataCollectionTask",
                or_(
                    Job.task_params.like(f'%"datasource_id": "{DS_ID}"%'),
                    Job.task_params.like(f'%"datasource_id":"{DS_ID}"%'),
                ),
            )
        ).scalars().all()
        print(f"修复前(AND): {n_and} 条 | 修复后(OR): {n_or} 条 | 状态: {list(states)}")
        assert n_and == 0, "修复前 AND 应匹配不到（带空格/无空格矛盾）"
        assert n_or >= 1, "修复后 OR 应匹配到线上 META 任务"
        print("VERIFY_OK")
    finally:
        db.close()


if __name__ == "__main__":
    main()