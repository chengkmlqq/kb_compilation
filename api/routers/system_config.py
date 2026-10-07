"""System configuration API routes — model config + MCP servers + skills.

Three management surfaces backed by api.services.system_config:
- /system/model-config      read/write chat & embedding model settings (modo_dim)
- /system/mcp-servers       proxy to agent-gateway MCP management (hot-reload)
- /system/skills            proxy to agent-gateway skill management (install/delete)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.db import get_db
from api.services.system_config import (
    delete_mcp_server,
    delete_skill,
    get_model_config,
    get_platform_config,
    get_skill_detail,
    install_skill,
    list_mcp_servers,
    list_skills,
    save_mcp_servers,
    save_model_config,
    save_platform_config,
    test_mcp_server,
    test_model_endpoint,
)

router = APIRouter(prefix="/system", tags=["system-config"])


# ---------------------------------------------------------------------------
# 模型配置（modo_dim）
# ---------------------------------------------------------------------------


class ModelConfigItem(BaseModel):
    code: str
    value: str | None = None


class ModelConfigSaveRequest(BaseModel):
    items: list[ModelConfigItem]


@router.get("/model-config")
def read_model_config(db: Session = Depends(get_db)) -> dict:
    return {"success": True, "data": get_model_config(db)}


@router.put("/model-config")
def write_model_config(req: ModelConfigSaveRequest, db: Session = Depends(get_db)) -> dict:
    items = [{"code": i.code, "value": i.value} for i in req.items]
    result = save_model_config(db, items)
    return {"success": True, "data": result}




# ---------------------------------------------------------------------------
# 平台运行参数（wiki 构建模式 / agent 回合上限等，modo_dim=PLATFORM_CONFIG）
# ---------------------------------------------------------------------------


class PlatformConfigItem(BaseModel):
    code: str
    value: str | None = None


class PlatformConfigSaveRequest(BaseModel):
    items: list[PlatformConfigItem]


@router.get("/platform-config")
def read_platform_config(db: Session = Depends(get_db)) -> dict:
    return {"success": True, "data": get_platform_config(db)}


@router.put("/platform-config")
def write_platform_config(req: PlatformConfigSaveRequest, db: Session = Depends(get_db)) -> dict:
    result = save_platform_config(db, [{"code": i.code, "value": i.value} for i in req.items])
    return {"success": True, "data": result}


@router.post("/model-config/test")
def test_model_config(payload: dict, db: Session = Depends(get_db)) -> dict:
    """Probe a model endpoint (chat /models or embeddings) before saving."""
    base_url = str(payload.get("base_url") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    model = str(payload.get("model") or "").strip()
    if not base_url:
        raise HTTPException(status_code=400, detail="缺少 base_url")
    if not model:
        raise HTTPException(status_code=400, detail="缺少 model")
    result = test_model_endpoint(base_url, api_key, model)
    return {"success": result["ok"], "data": result}


# ---------------------------------------------------------------------------
# MCP 服务器管理（代理 agent-gateway）
# ---------------------------------------------------------------------------


class McpServersSaveRequest(BaseModel):
    servers: list[dict] = []
    verify: bool = False
    force: bool = False


@router.get("/mcp-servers")
def read_mcp_servers() -> dict:
    return {"success": True, "data": list_mcp_servers()}


@router.put("/mcp-servers")
def write_mcp_servers(req: McpServersSaveRequest) -> dict:
    return {"success": True, "data": save_mcp_servers(req.servers, verify=req.verify, force=req.force)}


@router.post("/mcp-servers/test")
def test_server(payload: dict) -> dict:
    return {"success": True, "data": test_mcp_server(payload)}


@router.delete("/mcp-servers/{server_name}")
def remove_mcp_server(server_name: str) -> dict:
    return {"success": True, "data": delete_mcp_server(server_name)}


# ---------------------------------------------------------------------------
# 技能管理（代理 agent-gateway）
# ---------------------------------------------------------------------------


@router.get("/skills")
def read_skills() -> dict:
    return {"success": True, "data": list_skills()}


@router.post("/skills/install")
async def upload_skill(
    file: UploadFile = File(...),
    name: str | None = Form(None),
) -> dict:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空")
    result = install_skill(data, file.filename or "skill.zip", name=name)
    return {"success": True, "data": result}


@router.get("/skills/{skill_name}")
def read_skill_detail(skill_name: str) -> dict:
    return {"success": True, "data": get_skill_detail(skill_name)}


@router.delete("/skills/{skill_name}")
def remove_skill(skill_name: str) -> dict:
    return {"success": True, "data": delete_skill(skill_name)}


__all__ = ["router"]
