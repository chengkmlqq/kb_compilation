"""Datasource API routes — mirrors data-synth /api/open/datasources endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.db import get_db
from api.services.datasource import (
    list_datasources,
    test_datasource_by_id,
    test_datasource_connection,
)

router = APIRouter(prefix="/datasources", tags=["datasources"])


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
