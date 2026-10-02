"""System file management API — 迁移自 data-synth 文件管理。

资源管理器式浏览（业务模块 → 团队 → 日期 → 文件）+ 全局搜索 + 上传 +
下载 + 逻辑删除 + 目录打包下载。底层本地存储（storage_type=local），
物理路径 [module]/[team]/[date]/[id]_[name] 多租户隔离。

权限（对齐 ds isAdmin）:
- admin（user_id=admin 或 team_id=system）可见/操作全部文件
- 普通用户仅可见/操作自己 team_id 的文件
删除为逻辑删除（state=0），物理文件保留（后续可加定时清理）。
"""

from __future__ import annotations

import io
import os
import re
import uuid
import zipfile
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Cookie, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from api.config import get_settings
from api.db import get_db
from api.models.framework import SysFile
from api.services.identity import Identity, decode_identity_cookie

router = APIRouter(prefix="/files", tags=["files"])

# 默认业务模块（对齐 ds uploadSysFileAction 默认值）
DEFAULT_MODULE = "default"
EXPLORER_PAGE_SIZE_DEFAULT = 20
EXPLORER_PAGE_SIZE_MAX = 200


def _current_identity(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> Identity | None:
    return decode_identity_cookie(x_next_identity or "")


def _require_identity(
    x_next_identity: str | None = Cookie(default=None, alias="x-next-identity"),
) -> Identity:
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return identity


def _is_admin(identity: Identity) -> bool:
    return identity.user_id == "admin" or identity.team_id == "system"


def _file_root() -> str:
    base = get_settings().kb_storage_dir
    root = os.path.join(base, "sys_files")
    os.makedirs(root, exist_ok=True)
    return root


def _sanitize_segment(value: str, fallback: str) -> str:
    """清洗路径段（对齐 ds sanitizePathSegment）。"""
    trimmed = str(value or "").strip()
    normalized = (
        re.sub(r"[\\/]+", "_", trimmed)
        .replace("..", "_")
        .replace("...", "_")
    )
    normalized = re.sub(r"[^A-Za-z0-9._-]", "_", normalized).strip(".")
    if not normalized or normalized in (".", ".."):
        return fallback
    return normalized[:64]


def _sanitize_file_name(value: str) -> str:
    """清洗上传文件名：保留 Unicode 原名（中文等），仅剔除路径分隔/危险字符。

    物理路径已含 file_id 前缀保证唯一，文件名无需 ASCII 化；展示名保留原样。
    """
    base = os.path.basename(str(value or "").strip().replace("\\", "/"))
    safe = re.sub(r"[\x00-\x1f\x7f]", "", base)
    safe = re.sub(r"[\\/:*?\"<>|]", "_", safe).strip().strip(".")
    return safe[:128] or "file"


def _extension(file_name: str) -> str:
    ext = os.path.splitext(file_name)[1].lower()
    return ext if re.match(r"^[.][a-z0-9]{1,16}$", ext) else ""


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _file_to_dict(f: SysFile) -> dict[str, Any]:
    return {
        "id": f.id,
        "file_name": f.file_name,
        "file_extension": f.file_extension,
        "file_size": f.file_size,
        "mime_type": f.mime_type,
        "storage_type": f.storage_type,
        "ds_name": f.ds_name,
        "bucket_name": f.bucket_name,
        "storage_path": f.storage_path,
        "team_id": f.team_id,
        "business_module": f.business_module,
        "created_by": f.created_by,
        "create_date": f.create_date,
        "update_date": f.update_date,
        "state": f.state,
    }


def _build_physical_path(module: str, team_id: str, file_name: str, file_id: str) -> str:
    date_str = datetime.now().strftime("%Y%m%d")
    safe_module = _sanitize_segment(module, DEFAULT_MODULE)
    safe_team = _sanitize_segment(team_id, "system")
    safe_name = _sanitize_file_name(file_name)
    return f"{safe_module}/{safe_team}/{date_str}/{file_id}_{safe_name}"


def _parse_storage_date(storage_path: str) -> str | None:
    parts = [p for p in str(storage_path or "").split("/") if p]
    if len(parts) < 3:
        return None
    raw = parts[-2]
    return raw if re.match(r"^\d{8}$", raw) else None


# ---------------------------------------------------------------------------
# 浏览 / 搜索
# ---------------------------------------------------------------------------


@router.get("/explore")
def explore_files(
    identity: Identity = Depends(_require_identity),
    current_path: str = Query("/"),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(EXPLORER_PAGE_SIZE_DEFAULT, ge=1, le=EXPLORER_PAGE_SIZE_MAX),
    db: Session = Depends(get_db),
) -> dict:
    """资源管理器式浏览 + 搜索（对齐 ds getSysFilesAction）。"""
    admin = _is_admin(identity)
    current_team = identity.team_id or ""
    if not admin and not current_team:
        return {"success": False, "message": "缺少团队信息，无法查询文件"}

    base = [SysFile.state == "1"]
    if not admin:
        base.append(SysFile.team_id == current_team)

    search_text = (search or "").strip()
    if search_text:
        rows = db.execute(
            select(SysFile)
            .where(and_(*base, SysFile.file_name.like(f"%{search_text}%")))
            .order_by(desc(SysFile.create_date))
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars().all()
        total = db.execute(
            select(func.count()).select_from(SysFile).where(
                and_(*base, SysFile.file_name.like(f"%{search_text}%"))
            )
        ).scalar() or 0
        items = [
            {
                **_file_to_dict(f),
                "name": f.file_name,
                "isFolder": False,
                "sourcePath": f"/{f.business_module}/{f.team_id}/{_parse_storage_date(f.storage_path) or ''}",
            }
            for f in rows
        ]
        return {
            "success": True,
            "data": {"list": items, "total": total, "page": page, "pageSize": page_size},
        }

    parts = [p for p in str(current_path or "/").split("/") if p]
    depth = len(parts)

    if depth == 0:
        # 业务模块列表
        rows = db.execute(
            select(SysFile.business_module)
            .where(and_(*base))
            .group_by(SysFile.business_module)
        ).all()
        items = [
            {"name": str(r[0]), "isFolder": True, "type": "folder"}
            for r in rows
            if r[0]
        ]
        return {
            "success": True,
            "data": {"list": items, "total": len(items), "page": 1, "pageSize": len(items) or 1},
        }

    if depth == 1:
        # 团队列表
        module = parts[0]
        rows = db.execute(
            select(SysFile.team_id)
            .where(and_(*base, SysFile.business_module == module))
            .group_by(SysFile.team_id)
        ).all()
        items = [
            {"name": str(r[0]), "isFolder": True, "type": "folder"}
            for r in rows
            if r[0]
        ]
        return {
            "success": True,
            "data": {"list": items, "total": len(items), "page": 1, "pageSize": len(items) or 1},
        }

    if depth == 2:
        # 日期列表
        module, team = parts[0], parts[1]
        if not admin and current_team != team:
            return {"success": False, "message": "无权限访问该团队文件"}
        rows = db.execute(
            select(SysFile.storage_path)
            .where(and_(*base, SysFile.business_module == module, SysFile.team_id == team))
        ).all()
        dates: dict[str, None] = {}
        for (path,) in rows:
            d = _parse_storage_date(path)
            if d:
                dates[d] = None
        items = [
            {"name": d, "isFolder": True, "type": "folder"}
            for d in sorted(dates, reverse=True)
        ]
        return {
            "success": True,
            "data": {"list": items, "total": len(items), "page": 1, "pageSize": len(items) or 1},
        }

    # depth >= 3: 具体文件列表
    module, team, date = parts[0], parts[1], parts[2]
    if not admin and current_team != team:
        return {"success": False, "message": "无权限访问该团队文件"}
    conds = and_(
        *base,
        SysFile.business_module == module,
        SysFile.team_id == team,
        SysFile.storage_path.like(f"%/{date}/%"),
    )
    total = db.execute(select(func.count()).select_from(SysFile).where(conds)).scalar() or 0
    rows = db.execute(
        select(SysFile)
        .where(conds)
        .order_by(desc(SysFile.create_date))
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()
    items = [
        {
            **_file_to_dict(f),
            "name": f.file_name,
            "isFolder": False,
            "sourcePath": f"/{module}/{team}/{date}",
        }
        for f in rows
    ]
    return {
        "success": True,
        "data": {"list": items, "total": total, "page": page, "pageSize": page_size},
    }


# ---------------------------------------------------------------------------
# 上传
# ---------------------------------------------------------------------------


@router.post("/upload")
async def upload_file(
    identity: Identity = Depends(_require_identity),
    file: UploadFile = File(...),
    module: str = Form(DEFAULT_MODULE),
    db: Session = Depends(get_db),
) -> dict:
    """上传文件：写本地存储 + modo_sys_file 行（对齐 ds uploadSysFileAction）。"""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="空文件不可上传")

    file_id = uuid.uuid4().hex
    file_name = _sanitize_file_name(file.filename or "untitled")
    team_id = identity.team_id or "system"
    storage_path = _build_physical_path(module, team_id, file_name, file_id)
    root = _file_root()
    abs_path = os.path.join(root, storage_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(content)

    row = SysFile(
        id=file_id,
        file_name=file_name,
        file_extension=_extension(file_name),
        file_size=len(content),
        mime_type=file.content_type,
        storage_type="local",
        ds_name=None,
        bucket_name=None,
        storage_path=storage_path,
        team_id=team_id,
        business_module=_sanitize_segment(module, DEFAULT_MODULE),
        created_by=identity.user_id,
        create_date=_now_text(),
        update_date=_now_text(),
        state="1",
    )
    db.add(row)
    db.commit()
    return {"success": True, "data": _file_to_dict(row)}


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------


@router.get("/{file_id}/download")
def download_file(
    file_id: str,
    identity: Identity = Depends(_require_identity),
    db: Session = Depends(get_db),
):
    """单文件下载（对齐 ds getSysFileUrlAction 语义，直接返回文件流）。"""
    f = db.execute(
        select(SysFile).where(SysFile.id == file_id, SysFile.state == "1")
    ).scalars().first()
    if not f:
        raise HTTPException(status_code=404, detail="文件不存在或已删除")
    if not _is_admin(identity) and f.team_id != (identity.team_id or ""):
        raise HTTPException(status_code=403, detail="无权限下载该文件")

    abs_path = os.path.join(_file_root(), f.storage_path)
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="物理文件缺失")
    return FileResponse(
        abs_path,
        media_type=f.mime_type or "application/octet-stream",
        filename=f.file_name,
    )


# ---------------------------------------------------------------------------
# 逻辑删除（文件 / 目录）
# ---------------------------------------------------------------------------


@router.delete("/{file_id}")
def remove_file(
    file_id: str,
    identity: Identity = Depends(_require_identity),
    db: Session = Depends(get_db),
) -> dict:
    """单文件逻辑删除（state=0，对齐 ds removeSysFileAction）。"""
    f = db.execute(
        select(SysFile).where(SysFile.id == file_id, SysFile.state == "1")
    ).scalars().first()
    if not f:
        raise HTTPException(status_code=404, detail="文件不存在或已删除")
    if not _is_admin(identity) and f.team_id != (identity.team_id or ""):
        raise HTTPException(status_code=403, detail="无权限删除该文件")
    f.state = "0"
    f.update_date = _now_text()
    db.add(f)
    db.commit()
    return {"success": True, "data": {"id": file_id, "deleted": True}}


@router.post("/rmdir")
def remove_directory(
    identity: Identity = Depends(_require_identity),
    path: str = Query(...),
    db: Session = Depends(get_db),
) -> dict:
    """目录递归逻辑删除（对齐 ds removeSysDirectoryAction）。"""
    parts = [p for p in str(path or "/").split("/") if p]
    if len(parts) < 3:
        return {"success": False, "message": "目录路径无效（需 module/team/date）"}
    module, team, date = parts[0], parts[1], parts[2]
    if not _is_admin(identity) and team != (identity.team_id or ""):
        return {"success": False, "message": "无权限删除该目录"}

    rows = db.execute(
        select(SysFile).where(
            and_(
                SysFile.state == "1",
                SysFile.business_module == module,
                SysFile.team_id == team,
                SysFile.storage_path.like(f"%/{date}/%"),
            )
        )
    ).scalars().all()
    if not rows:
        return {"success": False, "message": "目录下没有可删除文件"}
    now = _now_text()
    for f in rows:
        f.state = "0"
        f.update_date = now
    db.commit()
    return {"success": True, "data": {"total": len(rows)}}


# ---------------------------------------------------------------------------
# 目录打包下载
# ---------------------------------------------------------------------------


@router.get("/zip")
def download_directory_zip(
    identity: Identity = Depends(_require_identity),
    path: str = Query(...),
    db: Session = Depends(get_db),
):
    """目录打包下载 zip（对齐 ds downloadSysDirectoryZipAction，内存生成）。"""
    parts = [p for p in str(path or "/").split("/") if p]
    if len(parts) < 3:
        raise HTTPException(status_code=400, detail="目录路径无效（需 module/team/date）")
    module, team, date = parts[0], parts[1], parts[2]
    if not _is_admin(identity) and team != (identity.team_id or ""):
        raise HTTPException(status_code=403, detail="无权限下载该目录")

    rows = db.execute(
        select(SysFile).where(
            and_(
                SysFile.state == "1",
                SysFile.business_module == module,
                SysFile.team_id == team,
                SysFile.storage_path.like(f"%/{date}/%"),
            )
        )
    ).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="目录下没有可下载文件")

    buffer = io.BytesIO()
    zip_name = f"{team}_{date}.zip"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in rows:
            abs_path = os.path.join(_file_root(), f.storage_path)
            if os.path.exists(abs_path):
                zf.write(abs_path, arcname=f.file_name)
    buffer.seek(0)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )
