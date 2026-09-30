"""System management API routes — read-only users / roles / teams / menus / logs.

Backs the frontend "系统管理" pages. Read-only for this phase (see
api/services/system_admin.py docstring for the rationale).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.db import get_db
from api.services.system_admin import (
    list_menus,
    list_operation_logs,
    list_roles,
    list_teams,
    list_users,
)

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/users")
def get_users(
    page: int = 1,
    page_size: int = 10,
    keyword: str = "",
    db: Session = Depends(get_db),
) -> dict:
    return {"success": True, "data": list_users(db, page, page_size, keyword)}


@router.get("/roles")
def get_roles(page: int = 1, page_size: int = 10, db: Session = Depends(get_db)) -> dict:
    return {"success": True, "data": list_roles(db, page, page_size)}


@router.get("/teams")
def get_teams(page: int = 1, page_size: int = 10, db: Session = Depends(get_db)) -> dict:
    return {"success": True, "data": list_teams(db, page, page_size)}


@router.get("/menus")
def get_menus(db: Session = Depends(get_db)) -> dict:
    return {"success": True, "data": list_menus(db)}


@router.get("/operation-logs")
def get_operation_logs(
    page: int = 1,
    page_size: int = 20,
    keyword: str = "",
    db: Session = Depends(get_db),
) -> dict:
    return {"success": True, "data": list_operation_logs(db, page, page_size, keyword)}


__all__ = ["router"]
