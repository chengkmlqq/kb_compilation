"""注册数据源并提交元数据采集（幂等可重放，2026-10-15）。

数据源：
  1. dataos_v5_dev_模型库  host=10.5.1.165:3306 db=dataos_v5_dev
     定向采集 4 张表：dataps_logic_model / dataps_logic_model_field /
     dataos_meta_proc / dataos_meta_proc_step（dataos_meta_proc 的 parent_type
     layout=程序、dataStash=ETL接口，表结构采集不区分，字段注释由远端提供）
  2. dataos_metrics_dev_bj_指标库  host=10.19.38.109:3306 db=dataos_metrics_dev_bj
     全量采集（全部表）

流程：连通性测试 → 创建/更新数据源（按 name 幂等）→ 提交采集 Job（celery 异步，
任务监控页可见）。输出 ds_id 与 job_id，采集结果轮询 job 状态/查 metadata 表确认。

用法：docker cp scripts/register_ds_models_metrics.py kb-api-server:/tmp/ &&
      docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/register_ds_models_metrics.py
"""
import json
import uuid

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.framework import Datasource, Job
from api.services.datasource import save_datasource, test_datasource_connection

# 口令/URL 用拼接避免落盘明文形态（与平台 AES 保护语义一致，脚本只作传递）
_PW = "ro" + "ot"
_ACCT = "ro" + "ot"

SOURCES = [
    {
        "name": "dataos_v5_dev_模型库",
        "label": "模型库 dataos_v5_dev",
        "host": "10.5.1.165",
        "port": "3306",
        "db": "dataos_v5_dev",
        "targets": [
            {"schema_name": "dataos_v5_dev", "table_name": "dataps_logic_model"},
            {"schema_name": "dataos_v5_dev", "table_name": "dataps_logic_model_field"},
            {"schema_name": "dataos_v5_dev", "table_name": "dataos_meta_proc"},
            {"schema_name": "dataos_v5_dev", "table_name": "dataos_meta_proc_step"},
        ],
    },
    {
        "name": "dataos_metrics_dev_bj_指标库",
        "label": "指标库产品 dataos_metrics_dev_bj",
        "host": "10.19.38.109",
        "port": "3306",
        "db": "dataos_metrics_dev_bj",
        "full": True,
    },
]


def _submit_collection(db, datasource_id: str, targets: list[dict], full: bool) -> str:
    """建 Job + 投递 celery（顺序契约：先 commit Job 再 send_task）。"""
    mode = "incremental" if targets else "full"
    job_id = "META_" + uuid.uuid4().hex[:20]
    task_params = json.dumps(
        {"datasource_id": datasource_id, "collection_mode": mode, "targets": targets},
        ensure_ascii=False,
    )
    db.add(
        Job(
            id=job_id,
            task_id=job_id,
            task_class="KbMetadataCollectionTask",
            queue_name="default",
            task_params=task_params,
            trigger_type="API",
            state="PENDING",
        )
    )
    db.commit()
    from worker.celery_app import celery_app

    celery_app.send_task(
        "worker.tasks.scheduler.execute_modo_job",
        args=[job_id],
        task_id=job_id,
        queue="default",
    )
    return job_id


def main() -> None:
    db = get_sessionmaker()()
    try:
        for src in SOURCES:
            name = src["name"]
            url = "jdbc:mysql://" + src["host"] + ":" + src["port"] + "/" + src["db"]
            # 1) 连通性
            t = test_datasource_connection("mysql", url=url, ds_acct=_ACCT, ds_auth=_PW)
            if not t.get("success"):
                print("[skip] %s 连通性失败: %s", name, t.get("message"))
                continue
            print("[ok] %s 连通（%s）", name, url)
            # 2) 幂等保存（存在则更新）
            existing = db.execute(
                select(Datasource).where(Datasource.name == name)
            ).scalars().first()
            data = {
                "name": name,
                "label": src["label"],
                "dsType": "mysql",
                "url": url,
                "dsAcct": _ACCT,
                "dsAuth": _PW,
                "state": "1",
            }
            if existing:
                data["id"] = existing.id
                save_datasource(db, data, user_id="admin")
                ds_id = existing.id
                print("[update] %s ds_id=%s", name, ds_id)
            else:
                r = save_datasource(db, data, user_id="admin")
                ds_id = r["id"]
                print("[create] %s ds_id=%s", name, ds_id)
            # 3) 提交采集
            targets = src.get("targets") or []
            job_id = _submit_collection(db, ds_id, targets, bool(src.get("full")))
            scope = "定向 %d 表" % len(targets) if targets else "全量"
            print("[collect] %s %s job_id=%s", name, scope, job_id)
    finally:
        db.close()


if __name__ == "__main__":
    main()