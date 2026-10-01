"""Scoped skill registry API routes — /api/v1/skills.

kb_compilation stores the skill package ZIP (system / personal / team
scopes) as the source of truth; the agent-gateway is a stateless executor
(1A — the task submission carries the ZIP as base64). Requires the
x-next-identity cookie; admin gating for system scope.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Cookie, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.db import get_db
from api.services.identity import decode_identity_cookie
from api.services.scope import is_admin
from api.services.skills import (
    create_skill,
    delete_skill,
    get_skill,
    list_skills,
    skill_to_dict,
    update_skill,
)

router = APIRouter(prefix="/skills", tags=["skill-registry"])


@dataclass
class Caller:
    user_id: str
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
        team_name=identity.team_name or "",
        is_admin=is_admin(db, identity.user_id),
    )


@router.get("")
def list_skill_route(
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
    scope: str | None = None,
) -> dict:
    items = list_skills(
        db,
        caller_user_id=caller.user_id,
        caller_team_name=caller.team_name,
        is_sys_admin=caller.is_admin,
        scope=scope,
    )
    return {"success": True, "data": {"items": items, "is_admin": caller.is_admin}}


@router.post("/install")
async def install_skill_route(
    file: UploadFile = File(...),
    scope: str = Form("personal"),
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空")
    try:
        item = create_skill(
            db,
            caller_user_id=caller.user_id,
            caller_team_name=caller.team_name,
            is_sys_admin=caller.is_admin,
            package_zip=data,
            scope=scope,
        )
        return {"success": True, "data": {"item": item}}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/{skill_id}")
def detail_skill_route(
    skill_id: str,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    m = get_skill(db, skill_id)
    if not m:
        raise HTTPException(status_code=404, detail="技能不存在")
    from api.services.scope import ResourceRow, can_see

    if not can_see(
        ResourceRow.from_obj(m),
        caller.user_id,
        caller.team_name,
        caller.is_admin,
    ):
        raise HTTPException(status_code=404, detail="技能不存在")
    return {"success": True, "data": {"item": skill_to_dict(m)}}


class SkillUpdatePayload(BaseModel):
    description: str | None = None


@router.put("/{skill_id}")
def update_skill_route(
    skill_id: str,
    req: SkillUpdatePayload,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    try:
        item = update_skill(
            db,
            skill_id,
            caller_user_id=caller.user_id,
            caller_team_name=caller.team_name,
            is_sys_admin=caller.is_admin,
            description=req.description,
        )
        return {"success": True, "data": {"item": item}}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e


@router.post("/{skill_id}/package")
async def replace_skill_package_route(
    skill_id: str,
    file: UploadFile = File(...),
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空")
    try:
        item = update_skill(
            db,
            skill_id,
            caller_user_id=caller.user_id,
            caller_team_name=caller.team_name,
            is_sys_admin=caller.is_admin,
            package_zip=data,
        )
        return {"success": True, "data": {"item": item}}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/{skill_id}")
def delete_skill_route(
    skill_id: str,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    try:
        delete_skill(
            db,
            skill_id,
            caller_user_id=caller.user_id,
            caller_team_name=caller.team_name,
            is_sys_admin=caller.is_admin,
        )
        return {"success": True, "data": {"deleted": True}}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e


__all__ = ["router"]