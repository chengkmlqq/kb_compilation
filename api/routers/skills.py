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
    # SKILL.md 内容预览（从包内提取，供详情展示）+ 文件清单（文件树预览）
    markdown_preview = ""
    files: list[dict] = []
    if m.package_zip:
        import io
        import zipfile

        try:
            with zipfile.ZipFile(io.BytesIO(bytes(m.package_zip))) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename
                    if "__MACOSX" in name or "__pycache__" in name or name.endswith("/"):
                        continue
                    files.append({"path": name, "size": info.file_size})
                for name in zf.namelist():
                    if name.lower().endswith(".md") and name.count("/") <= 1:
                        preview_bytes = zf.read(name)
                        markdown_preview = preview_bytes.decode("utf-8", errors="replace")
                        if len(markdown_preview) > 4000:
                            markdown_preview = markdown_preview[:4000] + "\n…（截断）"
                        break
        except Exception:  # noqa: BLE001
            markdown_preview = ""
    files.sort(key=lambda f: f["path"])
    return {
        "success": True,
        "data": {
            "item": skill_to_dict(m),
            "markdown_preview": markdown_preview,
            "files": files[:500],
            "total_files": len(files),
        },
    }


@router.get("/{skill_id}/files/{file_path:path}")
def skill_file_route(
    skill_id: str,
    file_path: str,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    """读取技能包内单个文件内容（文件树点击预览，从 DB 存的 ZIP 现解）。

    返回 {path, size, content, truncated, binary}；文本超 512KB 只回元信息。
    """
    m = get_skill(db, skill_id)
    if not m or not m.package_zip:
        raise HTTPException(status_code=404, detail="技能不存在或无包内容")
    from api.services.scope import ResourceRow, can_see

    if not can_see(
        ResourceRow.from_obj(m),
        caller.user_id,
        caller.team_name,
        caller.is_admin,
    ):
        raise HTTPException(status_code=404, detail="技能不存在")
    import io
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(bytes(m.package_zip))) as zf:
            names = set(zf.namelist())
            target = file_path if file_path in names else None
            if target is None:
                # 容忍前缀差异：按结尾匹配（安装时可能剥掉顶层技能目录）
                cands = [n for n in names if n.endswith("/" + file_path) or n == file_path]
                if not cands:
                    raise HTTPException(status_code=404, detail=f"文件不存在: {file_path}")
                target = sorted(cands, key=len)[0]
            info = zf.getinfo(target)
            if info.file_size > 512 * 1024:
                return {
                    "success": True,
                    "data": {
                        "path": target,
                        "size": info.file_size,
                        "content": "",
                        "truncated": True,
                        "binary": False,
                    },
                }
            data = zf.read(target)
            try:
                content = data.decode("utf-8")
                binary = False
            except UnicodeDecodeError:
                content = ""
                binary = True
            return {
                "success": True,
                "data": {
                    "path": target,
                    "size": info.file_size,
                    "content": content,
                    "truncated": False,
                    "binary": binary,
                },
            }
    except zipfile.BadZipFile as e:
        raise HTTPException(status_code=400, detail="技能包损坏") from e


@router.get("/{skill_id}/package")
def export_skill_package_route(
    skill_id: str,
    caller: Caller = Depends(_require_caller),
    db: Session = Depends(get_db),
) -> dict:
    """导出技能 ZIP 包（base64，供前端下载/备份）。"""
    from api.services.skills import get_skill_package

    m = get_skill_package(db, skill_id)
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
    if not m.package_zip:
        raise HTTPException(status_code=404, detail="技能包为空")
    import base64

    raw = bytes(m.package_zip)
    return {
        "success": True,
        "data": {
            "name": m.name,
            "version": m.version,
            "package_size": len(raw),
            "package_base64": base64.b64encode(raw).decode("ascii"),
        },
    }


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