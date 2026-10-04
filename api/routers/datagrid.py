"""数据查询（DataGrid）API —— 迁移 data-synth 的 datagrid 功能到 kb 后端。

端点（对齐 ds python-api-client 的 /api/datagrid/*）：
  GET  /datagrid/datasources        数据源列表（团队授权）
  POST /datagrid/execute            执行 SQL（select 返回 columns+rows，其他返回 rowcount）
  POST /datagrid/tables             表列表
  POST /datagrid/columns            表字段
  POST /datagrid/ddl                表 DDL
  POST /datagrid/table-info         表结构信息
  POST /datagrid/views              视图列表
  POST /datagrid/functions          函数列表
  POST /datagrid/procedures         存储过程列表
  POST /datagrid/sequences          序列列表

需要登录（x-next-identity cookie）；未登录 401。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.db import get_db
from api.services.datagrid import (
    execute_sql,
    get_columns,
    get_functions,
    get_procedures,
    get_sequences,
    get_table_ddl,
    get_table_info,
    get_tables,
    get_views,
    list_datagrid_datasources,
)
from api.services.identity import Identity, decode_identity_cookie

router = APIRouter(prefix="/datagrid", tags=["datagrid"])


def _require_identity(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> Identity:
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return identity


def _uid(idn: Identity) -> str:
    return idn.user_id or ""


def _team(idn: Identity) -> str:
    return idn.team_name or ""


class ExecuteReq(BaseModel):
    dsName: str
    sql: str
    schemaName: Optional[str] = None


class MetaReq(BaseModel):
    dsName: str
    tableName: Optional[str] = None
    # 用 schemaName 而非 schema（pydantic BaseModel.schema 是保留属性名）
    schemaName: Optional[str] = None
    searchName: Optional[str] = None
    limit: int = 10000


def _schema(req: MetaReq) -> str:
    return req.schemaName or ""


@router.get("/datasources")
def datasources(db: Session = Depends(get_db), idn: Identity = Depends(_require_identity)) -> dict:
    return {"success": True, "data": list_datagrid_datasources(db, _uid(idn), _team(idn))}


@router.post("/execute")
def execute(req: ExecuteReq, db: Session = Depends(get_db), idn: Identity = Depends(_require_identity)) -> dict:
    result = execute_sql(db, req.dsName, req.sql, _uid(idn), _team(idn))
    if not result.get("success"):
        return {"success": True, "data": [{"success": False, "msg": result.get("msg", "执行失败")}]}
    return {"success": True, "data": [result]}


@router.post("/tables")
def tables(req: MetaReq, db: Session = Depends(get_db), idn: Identity = Depends(_require_identity)) -> dict:
    return get_tables(db, req.dsName, _schema(req), req.searchName or "", req.limit, _uid(idn), _team(idn))


@router.post("/columns")
def columns(req: MetaReq, db: Session = Depends(get_db), idn: Identity = Depends(_require_identity)) -> dict:
    if not req.tableName:
        return {"success": False, "msg": "tableName 必填"}
    return get_columns(db, req.dsName, req.tableName, _schema(req), _uid(idn), _team(idn))


@router.post("/ddl")
def ddl(req: MetaReq, db: Session = Depends(get_db), idn: Identity = Depends(_require_identity)) -> dict:
    if not req.tableName:
        return {"success": False, "msg": "tableName 必填"}
    return get_table_ddl(db, req.dsName, req.tableName, _schema(req), _uid(idn), _team(idn))


@router.post("/table-info")
def table_info(req: MetaReq, db: Session = Depends(get_db), idn: Identity = Depends(_require_identity)) -> dict:
    if not req.tableName:
        return {"success": False, "msg": "tableName 必填"}
    return get_table_info(db, req.dsName, req.tableName, _schema(req), _uid(idn), _team(idn))


@router.post("/views")
def views(req: MetaReq, db: Session = Depends(get_db), idn: Identity = Depends(_require_identity)) -> dict:
    return get_views(db, req.dsName, _schema(req), _uid(idn), _team(idn))


@router.post("/functions")
def functions(req: MetaReq, db: Session = Depends(get_db), idn: Identity = Depends(_require_identity)) -> dict:
    return get_functions(db, req.dsName, _schema(req), _uid(idn), _team(idn))


@router.post("/procedures")
def procedures(req: MetaReq, db: Session = Depends(get_db), idn: Identity = Depends(_require_identity)) -> dict:
    return get_procedures(db, req.dsName, _schema(req), _uid(idn), _team(idn))


@router.post("/sequences")
def sequences(req: MetaReq, db: Session = Depends(get_db), idn: Identity = Depends(_require_identity)) -> dict:
    return get_sequences(db, req.dsName, _schema(req), _uid(idn), _team(idn))
