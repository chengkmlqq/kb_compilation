"""Datasource API routes — 迁移 data-synth 完整数据源管理。

端点（对齐 ds system/datasources）：
- GET    /datasources                      列表（分页 + keyword）
- POST   /datasources                      新建
- PUT    /datasources/{ds_id}              编辑
- DELETE /datasources/{ds_id}              删除（vector_es 保护）
- POST   /datasources/test                 测试连接（dsId 或原始参数）
- GET    /datasources/categories           数据源分类（种子）
- GET    /datasources/types                数据源类型（按分类/搜索）
- GET    /datasources/types/{ds_type}      类型详情
- GET    /datasources/versions             版本列表
- GET    /datasources/form-fields          动态表单字段配置（向导驱动）
- GET    /datasources/team-auth/{team}     团队数据源授权列表
- PUT    /datasources/team-auth/{team}     保存团队数据源授权
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.db import get_db
from api.services.datasource import (
    delete_datasource,
    get_ds_type_detail,
    list_datasources,
    list_ds_categories,
    list_ds_form_fields,
    list_ds_types,
    list_ds_versions,
    list_team_ds_maps,
    save_datasource,
    save_team_ds_maps,
    test_datasource_by_id,
    test_datasource_connection,
)
from api.services.identity import decode_identity_cookie
from fastapi import Cookie

router = APIRouter(prefix="/datasources", tags=["datasources"])


def _caller_id(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> str:
    identity = decode_identity_cookie(x_next_identity or "")
    return identity.user_id if identity and identity.user_id else "system"


class TestDatasourceRequest(BaseModel):
    """Mirrors the TS TestDatasourceRequestBody (mode 1: dsId / mode 2: params)."""

    dsId: str | None = None
    dsType: str | None = None
    dsAcct: str | None = None
    dsAuth: str | None = None
    url: str | None = None
    endpoint: str | None = None
    dsConf: str | None = None
    dsSchema: str | None = None
    catalog: str | None = None
    protocol: str | None = None
    bucketName: str | None = None


@router.get("")
def get_datasources(
    page: int = 1,
    page_size: int = 10,
    keyword: str = "",
    db: Session = Depends(get_db),
) -> dict:
    return {"success": True, "data": list_datasources(db, page, page_size, keyword)}


class DatasourceSaveRequest(BaseModel):
    """新建/编辑数据源载荷（对齐 ds saveDataSourceAction）。"""

    id: str | None = None
    name: str = Field(min_length=1)
    label: str | None = None
    dsType: str = Field(min_length=1)
    dsAcct: str | None = None
    dsAuth: str | None = None
    dsCategory: str | None = None
    dsVersion: str | None = None
    url: str | None = None
    dsConf: str | None = None
    state: str | None = None


@router.post("")
def create_datasource(
    req: DatasourceSaveRequest,
    user_id: str = Depends(_caller_id),
    db: Session = Depends(get_db),
) -> dict:
    try:
        result = save_datasource(db, req.model_dump(exclude_none=True), user_id=user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"success": True, "data": result}


@router.put("/{ds_id}")
def update_datasource(
    ds_id: str,
    req: DatasourceSaveRequest,
    user_id: str = Depends(_caller_id),
    db: Session = Depends(get_db),
) -> dict:
    try:
        result = save_datasource(db, {**req.model_dump(exclude_none=True), "id": ds_id}, user_id=user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"success": True, "data": result}


@router.delete("/{ds_id}")
def remove_datasource(
    ds_id: str,
    _user_id: str = Depends(_caller_id),
    db: Session = Depends(get_db),
) -> dict:
    try:
        result = delete_datasource(db, ds_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"success": True, "data": result}


@router.post("/test")
def test_datasource(req: TestDatasourceRequest, db: Session = Depends(get_db)) -> dict:
    """Test connection: by saved dsId (mode 1) or by raw params (mode 2)."""
    if req.dsId:
        result = test_datasource_by_id(db, req.dsId, identity=None)
        if not result.get("success") and "无权访问" in result.get("message", ""):
            raise HTTPException(status_code=404, detail=result["message"])
        return {"success": True, "data": result}

    if not req.dsType:
        raise HTTPException(status_code=400, detail="dsType 不能为空（或请传 dsId 按已保存数据源测试）")

    url = req.url or req.endpoint or ""
    result = test_datasource_connection(
        req.dsType,
        url=url,
        ds_acct=req.dsAcct,
        ds_auth=req.dsAuth,
        ds_conf=req.dsConf,
        ds_schema=req.dsSchema,
    )
    return {"success": True, "data": result}


# ---------------------------------------------------------------------------
# 元数据（向导动态表单驱动，种子数据 modo_ds_*）
# ---------------------------------------------------------------------------


@router.get("/categories")
def categories(ds: Session = Depends(get_db)) -> dict:
    return {"success": True, "data": list_ds_categories(ds)}


@router.get("/types")
def ds_types(
    dsCategory: str | None = Query(default=None),
    search: str | None = Query(default=None),
    ds: Session = Depends(get_db),
) -> dict:
    return {"success": True, "data": list_ds_types(ds, ds_category=dsCategory, search=search)}


@router.get("/types/{ds_type}")
def ds_type_detail(ds_type: str, ds: Session = Depends(get_db)) -> dict:
    row = get_ds_type_detail(ds, ds_type)
    if row is None:
        raise HTTPException(status_code=404, detail=f"数据源类型不存在: {ds_type}")
    return {"success": True, "data": row}


@router.get("/versions")
def ds_versions(
    dsType: str | None = Query(default=None),
    ds: Session = Depends(get_db),
) -> dict:
    return {"success": True, "data": list_ds_versions(ds, ds_type=dsType)}


@router.get("/form-fields")
def ds_form_fields(
    dsType: str = Query(...),
    dsVersion: str | None = Query(default=None),
    ds: Session = Depends(get_db),
) -> dict:
    return {"success": True, "data": list_ds_form_fields(ds, dsType, ds_version=dsVersion)}


# ---------------------------------------------------------------------------
# 团队 → 数据源授权（modo_team_ds_map）
# ---------------------------------------------------------------------------


@router.get("/team-auth/{team_name}")
def team_ds_auth(
    team_name: str,
    dsName: str | None = Query(default=None),
    ds: Session = Depends(get_db),
) -> dict:
    return {"success": True, "data": list_team_ds_maps(ds, team_name, ds_name=dsName)}


@router.put("/team-auth/{team_name}")
def save_team_ds_auth(
    team_name: str,
    items: list[dict] = Body(...),
    ds: Session = Depends(get_db),
) -> dict:
    try:
        result = save_team_ds_maps(ds, team_name, items)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"success": True, "data": result}


# 注意：/{ds_id} 必须定义在所有静态子路由之后（否则会吞掉 /categories 等单段路径）
@router.get("/{ds_id}")
def datasource_detail(ds_id: str, ds: Session = Depends(get_db)) -> dict:
    """数据源详情（编辑用；口令不回显，留空表示不修改）。"""
    from api.models.framework import Datasource as _Ds
    from sqlalchemy import select as _select

    row = ds.execute(_select(_Ds).where(_Ds.id == ds_id)).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"数据源不存在: {ds_id}")
    return {
        "success": True,
        "data": {
            "id": row.id,
            "name": row.name,
            "label": row.label,
            "dsType": row.ds_type,
            "dsCategory": row.ds_category,
            "dsVersion": row.ds_version,
            "dsAcct": row.ds_acct,
            "dsAuth": None,
            "url": row.url,
            "dsConf": row.ds_conf,
            "state": row.state,
        },
    }
