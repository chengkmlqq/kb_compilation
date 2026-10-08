"""本体 Schema 管理 API — /api/v1/ontology-schemas 与 KB 绑定。

本体 schema 配置化定义 wiki 构建技能的「抽取分类结构」（业务本体 × 规则本体
双维度）。schema 为全局配置（system scope，管理员管理，全平台可读可绑）。

Endpoints:
- GET    /ontology-schemas                  list schemas (?schema_name=&dimension=)
- POST   /ontology-schemas                  create one category row
- PUT    /ontology-schemas/{id}             update category row
- DELETE /ontology-schemas/{id}             delete category row
- POST   /ontology-schemas/copy             copy a full schema set to a new name
- GET    /kbs/{kb_id}/ontology-schema       resolve KB's bound schema (full rows)
- PUT    /kbs/{kb_id}/ontology-schema       bind KB to a schema name
Requires the x-next-identity cookie; admin gating for write ops.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Cookie, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.db import get_db
from api.models.ontology import KbOntologySchema, OntologySchema
from api.services.identity import decode_identity_cookie
from api.services.models import is_admin

router = APIRouter()

DIMENSIONS = ("business", "rule")


@dataclass
class Caller:
    user_id: str
    user_name: str
    team_name: str
    is_admin: bool


def _require_caller(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
    db: Session = Depends(get_db),
) -> Caller:
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return Caller(
        user_id=identity.user_id,
        user_name=identity.user_name,
        team_name=identity.team_name or "",
        is_admin=is_admin(db, identity.user_id),
    )


def _require_admin(caller: Caller) -> None:
    if not caller.is_admin:
        raise HTTPException(status_code=403, detail="仅管理员可操作本体 Schema")


class CategoryPayload(BaseModel):
    schema_name: str
    schema_label: str = ""
    schema_desc: str | None = None
    dimension: str
    cat_no: int
    cat_name: str
    cat_label: str | None = None
    prompt_hint: str | None = None
    neo4j_edge: str | None = None
    state: str = "1"


def _row_dict(r: OntologySchema) -> dict[str, Any]:
    return {
        "id": r.id,
        "schema_name": r.schema_name,
        "schema_label": r.schema_label,
        "schema_desc": r.schema_desc,
        "dimension": r.dimension,
        "cat_no": r.cat_no,
        "cat_name": r.cat_name,
        "cat_label": r.cat_label,
        "prompt_hint": r.prompt_hint,
        "neo4j_edge": r.neo4j_edge,
        "state": r.state,
        "sort": r.sort,
        "created_at": str(r.created_at) if r.created_at else None,
    }


@router.get("/ontology-schemas")
def list_schemas(
    caller: Caller = Depends(_require_caller),
    schema_name: str | None = None,
    dimension: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    q = select(OntologySchema).where(OntologySchema.state == "1")
    if schema_name:
        q = q.where(OntologySchema.schema_name == schema_name)
    if dimension:
        q = q.where(OntologySchema.dimension == dimension)
    q = q.order_by(OntologySchema.schema_name, OntologySchema.dimension, OntologySchema.cat_no)
    rows = db.execute(q).scalars().all()
    # 按 schema 聚合返回（含维度分类计数）
    grouped: dict[str, dict[str, Any]] = {}
    for r in rows:
        g = grouped.setdefault(r.schema_name, {
            "schema_name": r.schema_name,
            "schema_label": r.schema_label,
            "schema_desc": r.schema_desc,
            "business": [],
            "rule": [],
            "total": 0,
        })
        g[r.dimension].append(_row_dict(r))
        g["total"] += 1
    return {"success": True, "data": {"schemas": list(grouped.values())}}


@router.post("/ontology-schemas")
def create_category(
    payload: CategoryPayload,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    _require_admin(caller)
    if payload.dimension not in DIMENSIONS:
        raise HTTPException(status_code=400, detail="dimension 须为 business 或 rule")
    # 同 schema 同维度同序号唯一
    dup = db.execute(select(OntologySchema).where(
        OntologySchema.schema_name == payload.schema_name,
        OntologySchema.dimension == payload.dimension,
        OntologySchema.cat_no == payload.cat_no,
    )).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail=f"分类序号 {payload.cat_no} 已存在")
    row = OntologySchema(
        id=uuid4().hex[:24],
        schema_name=payload.schema_name,
        schema_label=payload.schema_label or payload.schema_name,
        schema_desc=payload.schema_desc,
        dimension=payload.dimension,
        cat_no=payload.cat_no,
        cat_name=payload.cat_name,
        cat_label=payload.cat_label or f"{payload.cat_no}-{payload.cat_name}",
        prompt_hint=payload.prompt_hint,
        neo4j_edge=payload.neo4j_edge,
        state=payload.state,
        sort=payload.cat_no,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "data": {"item": _row_dict(row)}}


@router.put("/ontology-schemas/{schema_id}")
def update_category(
    schema_id: str,
    payload: CategoryPayload,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    _require_admin(caller)
    row = db.get(OntologySchema, schema_id)
    if not row or row.state != "1":
        raise HTTPException(status_code=404, detail="分类不存在")
    for f in ("schema_name", "schema_label", "schema_desc", "dimension", "cat_no",
              "cat_name", "cat_label", "prompt_hint", "neo4j_edge", "state"):
        v = getattr(payload, f)
        if v is not None:
            setattr(row, f, v)
    if payload.cat_label is None:
        row.cat_label = f"{row.cat_no}-{row.cat_name}"
    row.sort = row.cat_no
    db.commit()
    db.refresh(row)
    return {"success": True, "data": {"item": _row_dict(row)}}


@router.delete("/ontology-schemas/{schema_id}")
def delete_category(
    schema_id: str,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    _require_admin(caller)
    row = db.get(OntologySchema, schema_id)
    if not row:
        raise HTTPException(status_code=404, detail="分类不存在")
    row.state = "0"
    db.commit()
    return {"success": True, "data": {"deleted": schema_id}}


class CopyPayload(BaseModel):
    source_schema: str
    new_schema_name: str
    new_schema_label: str = ""


class ImportPayload(BaseModel):
    """导入整套 schema（导出的 JSON 结构）。"""

    schema_name: str
    schema_label: str = ""
    schema_desc: str | None = None
    mode: str = "skip"  # skip=跳过已存在条目; overwrite=覆盖更新
    items: list[dict[str, Any]] = []


@router.get("/ontology-schemas/{schema_name}/export")
def export_schema(
    schema_name: str,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    """导出一整套 schema（含 business/rule 全部维度条目）为 JSON。"""
    rows = db.execute(select(OntologySchema).where(
        OntologySchema.schema_name == schema_name,
        OntologySchema.state == "1",
    ).order_by(OntologySchema.dimension, OntologySchema.cat_no)).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="schema 不存在")
    head = rows[0]
    return {
        "success": True,
        "data": {
            "schema_name": schema_name,
            "schema_label": head.schema_label,
            "schema_desc": head.schema_desc,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "items": [
                {
                    "dimension": r.dimension,
                    "cat_no": r.cat_no,
                    "cat_name": r.cat_name,
                    "cat_label": r.cat_label,
                    "prompt_hint": r.prompt_hint,
                    "neo4j_edge": r.neo4j_edge,
                    "state": r.state,
                }
                for r in rows
            ],
        },
    }


@router.post("/ontology-schemas/import")
def import_schema(
    payload: ImportPayload,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    """导入整套 schema：mode=skip 跳过已存在条目，overwrite 覆盖更新。"""
    _require_admin(caller)
    if payload.schema_name.strip() == "":
        raise HTTPException(status_code=400, detail="schema_name 不能为空")
    mode = (payload.mode or "skip").lower()
    if mode not in ("skip", "overwrite"):
        raise HTTPException(status_code=400, detail="mode 须为 skip 或 overwrite")
    created = updated = skipped = 0
    for it in payload.items:
        dim = it.get("dimension")
        cat_no = it.get("cat_no")
        cat_name = (it.get("cat_name") or "").strip()
        if dim not in DIMENSIONS:
            raise HTTPException(status_code=400, detail=f"dimension 须为 business 或 rule（收到 {dim}）")
        if not isinstance(cat_no, int) or cat_name == "":
            raise HTTPException(status_code=400, detail="每个 item 需要合法 cat_no 与 cat_name")
        row = db.execute(select(OntologySchema).where(
            OntologySchema.schema_name == payload.schema_name,
            OntologySchema.dimension == dim,
            OntologySchema.cat_no == cat_no,
        )).scalar_one_or_none()
        if row and mode == "skip":
            skipped += 1
            continue
        if row:
            row.cat_name = cat_name
            row.cat_label = it.get("cat_label") or f"{cat_no}-{cat_name}"
            row.prompt_hint = it.get("prompt_hint")
            row.neo4j_edge = it.get("neo4j_edge")
            row.state = it.get("state") or "1"
            row.schema_label = payload.schema_label or row.schema_label
            row.schema_desc = payload.schema_desc if payload.schema_desc is not None else row.schema_desc
            updated += 1
        else:
            db.add(OntologySchema(
                id=uuid4().hex[:24],
                schema_name=payload.schema_name,
                schema_label=payload.schema_label or payload.schema_name,
                schema_desc=payload.schema_desc,
                dimension=dim,
                cat_no=cat_no,
                cat_name=cat_name,
                cat_label=it.get("cat_label") or f"{cat_no}-{cat_name}",
                prompt_hint=it.get("prompt_hint"),
                neo4j_edge=it.get("neo4j_edge"),
                state=it.get("state") or "1",
                sort=cat_no,
            ))
            created += 1
    db.commit()
    return {
        "success": True,
        "data": {
            "schema_name": payload.schema_name,
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "mode": mode,
        },
    }


@router.post("/ontology-schemas/copy")
def copy_schema(
    payload: CopyPayload,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    """复制整套 schema 到新领域名（多领域扩展入口）。"""
    _require_admin(caller)
    src = db.execute(select(OntologySchema).where(
        OntologySchema.schema_name == payload.source_schema,
        OntologySchema.state == "1",
    )).scalars().all()
    if not src:
        raise HTTPException(status_code=404, detail="源 schema 不存在")
    exists = db.execute(select(func.count()).select_from(OntologySchema).where(
        OntologySchema.schema_name == payload.new_schema_name,
    )).scalar()
    if exists:
        raise HTTPException(status_code=409, detail="目标 schema 名已存在")
    for r in src:
        db.add(OntologySchema(
            id=uuid4().hex[:24],
            schema_name=payload.new_schema_name,
            schema_label=payload.new_schema_label or r.schema_label,
            schema_desc=r.schema_desc,
            dimension=r.dimension,
            cat_no=r.cat_no,
            cat_name=r.cat_name,
            cat_label=r.cat_label,
            prompt_hint=r.prompt_hint,
            neo4j_edge=r.neo4j_edge,
            state="1",
            sort=r.sort,
        ))
    db.commit()
    return {"success": True, "data": {"copied": len(src), "schema_name": payload.new_schema_name}}


# ---------------- KB 绑定 ----------------

@router.get("/kbs/{kb_id}/ontology-schema")
def get_kb_schema(
    kb_id: str,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    bind = db.get(KbOntologySchema, kb_id)
    if not bind:
        return {"success": True, "data": {"kb_id": kb_id, "schema_name": None, "schema": None}}
    rows = db.execute(select(OntologySchema).where(
        OntologySchema.schema_name == bind.schema_name,
        OntologySchema.state == "1",
    ).order_by(OntologySchema.dimension, OntologySchema.cat_no)).scalars().all()
    schema: dict[str, Any] = {
        "schema_name": bind.schema_name,
        "schema_label": rows[0].schema_label if rows else bind.schema_name,
        "schema_desc": rows[0].schema_desc if rows else None,
        "business": [_row_dict(r) for r in rows if r.dimension == "business"],
        "rule": [_row_dict(r) for r in rows if r.dimension == "rule"],
        "total": len(rows),
    }
    return {"success": True, "data": {"kb_id": kb_id, "schema_name": bind.schema_name, "schema": schema}}


class BindPayload(BaseModel):
    schema_name: str | None = None  # None = 解绑


@router.put("/kbs/{kb_id}/ontology-schema")
def bind_kb_schema(
    kb_id: str,
    payload: BindPayload,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    bind = db.get(KbOntologySchema, kb_id)
    if payload.schema_name is None:
        if bind:
            db.delete(bind)
            db.commit()
        return {"success": True, "data": {"kb_id": kb_id, "schema_name": None}}
    exists = db.execute(select(func.count()).select_from(OntologySchema).where(
        OntologySchema.schema_name == payload.schema_name,
        OntologySchema.state == "1",
    )).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail="schema 不存在")
    if bind:
        bind.schema_name = payload.schema_name
    else:
        db.add(KbOntologySchema(kb_id=kb_id, schema_name=payload.schema_name))
    db.commit()
    return {"success": True, "data": {"kb_id": kb_id, "schema_name": payload.schema_name}}