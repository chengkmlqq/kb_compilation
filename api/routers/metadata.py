"""元数据采集 API —— 迁移 data-synth 的 metadata-collection 功能。

端点（对齐 ds src/app/actions/metadata-actions.ts 的 10 个 action）：
  GET    /metadata/datasources            可采集数据源列表（表数量 + 采集状态）
  POST   /metadata/collection             提交采集任务（立即返回 job_id）
  GET    /metadata/collection/{job_id}    采集任务状态（含 summary）
  GET    /metadata/runs                   某数据源的采集记录（modo_job）
  GET    /metadata/template               下载导入模板（xlsx）
  POST   /metadata/import                 上传 xlsx 导入定向采集配置
  DELETE /metadata/datasources/{ds_id}    级联删除该数据源元数据
  GET    /metadata/tables                 表列表（分页 + column_count）
  GET    /metadata/tables/{table_id}/columns  字段列表
  GET    /metadata/config                  全量采集开关（modo_dim）

采集在 celery worker 内异步跑（task_class=KbMetadataCollectionTask）。
需要登录（x-next-identity cookie）；未登录 401。
"""
from __future__ import annotations

import io
import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from api.db import get_db, get_sessionmaker
from api.models.framework import Datasource, Dim, Job
from api.models.metadata import MetadataColumn, MetadataTable
from api.services.identity import Identity, decode_identity_cookie

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/metadata", tags=["metadata"])

TASK_CLASS_METADATA = "KbMetadataCollectionTask"

# 模板列（对齐 ds buildMetadataCollectionImportTemplateBuffer）
TEMPLATE_COLUMNS = [
    "schema_name",
    "table_name",
    "table_type",
    "table_comment",
    "row_count",
    "table_size",
    "create_time",
    "update_time",
    "scene_tag",
    "sampling_method",
    "sample_size",
    "sample_ratio",
]


def _require_identity(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> Identity:
    """当前登录用户（返回 Identity；绝不能把 token 原文当身份返回）。"""
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return identity


class CollectionSubmitReq(BaseModel):
    datasource_id: str = Field(min_length=1)
    collection_mode: str = Field(default="full")  # full | incremental
    targets: Optional[list[dict]] = None


def _collectable_types() -> tuple[str, ...]:
    from api.services.metadata_collector import COLLECTABLE_DS_TYPES

    return COLLECTABLE_DS_TYPES


def _ds_status(ds: Datasource, table_count: int, last_time: str | None, jobs: list[dict]) -> str:
    """采集状态：COLLECTING（运行中 Job）> FAILED（最近失败）> COLLECTED（有记录）> UNCOLLECTED。"""
    if any(j.get("state") in ("PENDING", "RUNNING") for j in jobs):
        return "COLLECTING"
    if any(j.get("state") == "FAILED" for j in jobs):
        return "FAILED"
    if table_count > 0 or last_time:
        return "COLLECTED"
    return "UNCOLLECTED"


@router.get("/datasources")
def list_metadata_datasources(
    identity: Identity = Depends(_require_identity),
    db: Session = Depends(get_db),
) -> dict:
    """可采集数据源列表（表数量 + 最近采集时间 + 状态）。"""
    from api.services.metadata_collector import DS_TYPE_GROUPS

    types = _collectable_types()
    dss = db.execute(
        select(Datasource).where(
            Datasource.ds_type.in_(types),
            or_(Datasource.state.is_(None), Datasource.state == "1"),
        )
    ).scalars().all()

    out: list[dict] = []
    for ds in dss:
        cnt = db.execute(
            select(func.count(MetadataTable.id)).where(MetadataTable.datasource_id == ds.id)
        ).scalar() or 0
        last = db.execute(
            select(func.max(MetadataTable.collection_time)).where(
                MetadataTable.datasource_id == ds.id
            )
        ).scalar()
        # 该数据源最近 5 条采集任务（用于状态判定）
        rows = db.execute(
            select(Job.state, Job.create_time, Job.error_message)
            .where(
                Job.task_class == TASK_CLASS_METADATA,
                Job.task_params.like(f'%"datasource_id": "{ds.id}"%'),
                Job.task_params.like(f'%"datasource_id":"{ds.id}"%'),
            )
            .order_by(Job.create_time.desc())
            .limit(5)
        ).all()
        jobs = [{"state": r[0], "create_time": r[1], "error_message": r[2]} for r in rows]
        out.append(
            {
                "id": ds.id,
                "name": ds.name or "",
                "label": ds.label or ds.name or "",
                "dsType": ds.ds_type or "",
                "dsTypeGroup": DS_TYPE_GROUPS.get((ds.ds_type or "").lower(), ds.ds_type or ""),
                "tableCount": int(cnt),
                "lastCollectionTime": last,
                "collectionStatus": _ds_status(ds, int(cnt), last, jobs),
                "lastError": next((j["error_message"] for j in jobs if j["state"] == "FAILED"), None),
            }
        )
    return {"success": True, "data": {"items": out, "total": len(out)}}


@router.post("/collection")
def submit_metadata_collection(
    req: CollectionSubmitReq,
    identity: Identity = Depends(_require_identity),
    db: Session = Depends(get_db),
) -> dict:
    """提交元数据采集任务（建 Job + 投递 celery，立即返回 job_id）。"""
    from worker.celery_app import celery_app

    ds = db.execute(select(Datasource).where(Datasource.id == req.datasource_id)).scalars().first()
    if ds is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    if (ds.ds_type or "").lower() not in _collectable_types():
        raise HTTPException(status_code=400, detail=f"数据源类型不支持元数据采集: {ds.ds_type}")

    mode = req.collection_mode if req.collection_mode in ("full", "incremental") else "full"
    job_id = f"META_{uuid.uuid4().hex[:20]}"
    task_params = json.dumps(
        {
            "datasource_id": ds.id,
            "collection_mode": mode,
            "targets": req.targets or [],
        },
        ensure_ascii=False,
    )
    db.add(
        Job(
            id=job_id,
            task_id=job_id,
            task_class=TASK_CLASS_METADATA,
            queue_name="default",
            task_params=task_params,
            trigger_type="API",
            state="PENDING",
        )
    )
    db.commit()

    celery_app.send_task(
        "worker.tasks.scheduler.execute_modo_job",
        args=[job_id],
        task_id=job_id,
        queue="default",
    )
    return {"success": True, "message": "采集任务已提交", "data": {"job_id": job_id}}


@router.get("/collection/{job_id}")
def get_collection_status(
    job_id: str,
    identity: Identity = Depends(_require_identity),
    db: Session = Depends(get_db),
) -> dict:
    """采集任务状态（含结果 summary）。"""
    job = db.execute(select(Job).where(Job.id == job_id)).scalars().first()
    if job is None or job.task_class != TASK_CLASS_METADATA:
        raise HTTPException(status_code=404, detail="采集任务不存在")
    summary = None
    try:
        summary = (json.loads(job.task_params or "{}") or {}).get("summary")
    except (TypeError, ValueError):
        summary = None
    return {
        "success": True,
        "data": {
            "job_id": job.id,
            "state": job.state,
            "create_time": job.create_time,
            "start_time": job.start_time,
            "end_time": job.end_time,
            "duration_ms": job.duration_ms,
            "error_message": job.error_message,
            "summary": summary,
        },
    }


@router.get("/runs")
def list_collection_runs(
    datasource_id: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    identity: Identity = Depends(_require_identity),
    db: Session = Depends(get_db),
) -> dict:
    """采集记录（modo_job，按 datasource_id 过滤 task_params）。"""
    stmt = select(Job).where(Job.task_class == TASK_CLASS_METADATA)
    if datasource_id:
        stmt = stmt.where(
            or_(
                Job.task_params.like(f'%"datasource_id": "{datasource_id}"%'),
                Job.task_params.like(f'%"datasource_id":"{datasource_id}"%'),
            )
        )
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0
    rows = db.execute(
        stmt.order_by(Job.create_time.desc()).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()

    items = []
    for j in rows:
        try:
            params = json.loads(j.task_params or "{}")
        except (TypeError, ValueError):
            params = {}
        items.append(
            {
                "job_id": j.id,
                "state": j.state,
                "datasource_id": params.get("datasource_id"),
                "collection_mode": params.get("collection_mode"),
                "targets_count": len(params.get("targets") or []),
                "summary": params.get("summary"),
                "create_time": j.create_time,
                "start_time": j.start_time,
                "end_time": j.end_time,
                "duration_ms": j.duration_ms,
                "error_message": j.error_message,
            }
        )
    return {"success": True, "data": {"items": items, "total": int(total)}}


@router.get("/template")
def download_import_template(
    datasource_id: str = "",
    identity: Identity = Depends(_require_identity),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """下载定向采集导入模板（xlsx）。带 datasource_id 时预填其表清单。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "metadata_import"
    ws.append(TEMPLATE_COLUMNS)

    if datasource_id:
        rows = db.execute(
            select(MetadataTable)
            .where(MetadataTable.datasource_id == datasource_id)
            .order_by(MetadataTable.table_name)
        ).scalars().all()
        for t in rows:
            ws.append(
                [
                    t.schema_name,
                    t.table_name,
                    t.table_type,
                    t.table_comment,
                    t.row_count,
                    t.table_size,
                    t.create_time,
                    t.update_time,
                    "",
                    "",
                    None,
                    None,
                ]
            )

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = "metadata_import_template.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import/{datasource_id}")
async def import_metadata_collection_for_ds(
    datasource_id: str,
    file: UploadFile = File(...),
    identity: Identity = Depends(_require_identity),
    db: Session = Depends(get_db),
) -> dict:
    """上传 xlsx 导入定向采集配置（显式路径参数版，前端主用）。"""
    from worker.celery_app import celery_app

    ds = db.execute(select(Datasource).where(Datasource.id == datasource_id)).scalars().first()
    if ds is None:
        raise HTTPException(status_code=404, detail="数据源不存在")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="上传文件为空")
    try:
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"xlsx 解析失败: {e}") from e
    if not rows:
        raise HTTPException(status_code=400, detail="模板内容为空")

    header = [str(c or "").strip().lower() for c in rows[0]]
    idx = {name: i for i, name in enumerate(header) if name}

    def cell(row: tuple, name: str) -> str:
        i = idx.get(name)
        if i is None or i >= len(row) or row[i] is None:
            return ""
        return str(row[i]).strip()

    targets: list[dict] = []
    for row in rows[1:]:
        tn = cell(row, "table_name")
        if not tn:
            continue
        t: dict = {"schema_name": cell(row, "schema_name"), "table_name": tn}
        for k in ("scene_tag", "sampling_method"):
            v = cell(row, k)
            if v:
                t[k] = v
        for k in ("sample_size", "sample_ratio"):
            v = cell(row, k)
            if v:
                try:
                    t[k] = float(v) if k == "sample_ratio" else int(float(v))
                except (TypeError, ValueError):
                    pass
        targets.append(t)
    if not targets:
        raise HTTPException(status_code=400, detail="模板中没有任何有效表名（table_name 不能为空）")
    if len(targets) > 2000:
        raise HTTPException(status_code=400, detail="单次导入表数量不得超过 2000")

    job_id = f"META_{uuid.uuid4().hex[:20]}"
    task_params = json.dumps(
        {"datasource_id": datasource_id, "collection_mode": "incremental", "targets": targets},
        ensure_ascii=False,
    )
    db.add(
        Job(
            id=job_id,
            task_id=job_id,
            task_class=TASK_CLASS_METADATA,
            queue_name="default",
            task_params=task_params,
            trigger_type="API",
            state="PENDING",
        )
    )
    db.commit()
    celery_app.send_task(
        "worker.tasks.scheduler.execute_modo_job", args=[job_id], task_id=job_id, queue="default"
    )
    return {
        "success": True,
        "message": f"已提交 {len(targets)} 张表的定向采集",
        "data": {"job_id": job_id, "targetCount": len(targets)},
    }


@router.delete("/datasources/{datasource_id}")
def delete_datasource_metadata(
    datasource_id: str,
    identity: Identity = Depends(_require_identity),
    db: Session = Depends(get_db),
) -> dict:
    """级联删除某数据源全部元数据（表 + 字段）。"""
    from api.services.metadata_collector import delete_metadata_by_datasource

    exists = db.execute(select(Datasource).where(Datasource.id == datasource_id)).scalars().first()
    if exists is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    res = delete_metadata_by_datasource(db, datasource_id)
    return {"success": True, "message": "已删除该数据源的元数据", "data": res}


@router.get("/tables")
def list_metadata_tables(
    datasource_id: str = "",
    keyword: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    identity: Identity = Depends(_require_identity),
    db: Session = Depends(get_db),
) -> dict:
    """元数据表列表（分页，含字段数 column_count）。"""
    stmt = select(MetadataTable)
    if datasource_id:
        stmt = stmt.where(MetadataTable.datasource_id == datasource_id)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(MetadataTable.table_name.like(like), MetadataTable.table_comment.like(like)))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0
    rows = db.execute(
        stmt.order_by(MetadataTable.datasource_id, MetadataTable.table_name)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()

    items = []
    for t in rows:
        cnt = db.execute(
            select(func.count(MetadataColumn.id)).where(MetadataColumn.metadata_table_id == t.id)
        ).scalar() or 0
        items.append(
            {
                "id": t.id,
                "datasource_id": t.datasource_id,
                "schemaName": t.schema_name,
                "tableName": t.table_name,
                "tableType": t.table_type,
                "tableComment": t.table_comment,
                "rowCount": t.row_count,
                "tableSize": t.table_size,
                "createTime": t.create_time,
                "updateTime": t.update_time,
                "collectionTime": t.collection_time,
                "state": t.state,
                "columnCount": int(cnt),
            }
        )
    return {"success": True, "data": {"items": items, "total": int(total)}}


@router.get("/tables/{table_id}/columns")
def list_metadata_columns(
    table_id: str,
    identity: Identity = Depends(_require_identity),
    db: Session = Depends(get_db),
) -> dict:
    """某张表的字段列表（按 ordinal_position）。"""
    t = db.execute(select(MetadataTable).where(MetadataTable.id == table_id)).scalars().first()
    if t is None:
        raise HTTPException(status_code=404, detail="元数据表不存在")
    rows = db.execute(
        select(MetadataColumn)
        .where(MetadataColumn.metadata_table_id == table_id)
        .order_by(MetadataColumn.ordinal_position)
    ).scalars().all()
    items = [
        {
            "id": c.id,
            "metadataTableId": c.metadata_table_id,
            "columnName": c.column_name,
            "columnType": c.column_type,
            "dataType": c.data_type,
            "columnLength": c.column_length,
            "columnPrecision": c.column_precision,
            "columnScale": c.column_scale,
            "isNullable": c.is_nullable,
            "columnDefault": c.column_default,
            "columnComment": c.column_comment,
            "ordinalPosition": c.ordinal_position,
            "isPrimaryKey": c.is_primary_key,
            "isUnique": c.is_unique,
        }
        for c in rows
    ]
    return {"success": True, "data": {"items": items, "total": len(items)}}


@router.get("/config")
def get_metadata_config(
    identity: Identity = Depends(_require_identity),
    db: Session = Depends(get_db),
) -> dict:
    """元数据全量采集开关（modo_dim dim_code=metadata_full_collection_enabled，默认 true）。"""
    row = db.execute(
        select(Dim).where(Dim.dim_code == "metadata_full_collection_enabled")
    ).scalars().first()
    enabled = True
    if row is not None and str(row.dim_value or "").strip():
        enabled = str(row.dim_value).strip().lower() not in ("0", "false", "off", "no")
    return {"success": True, "data": {"fullCollectionEnabled": enabled}}