"""编排路由：组件定义 CRUD + 编排 CRUD + 发布/下线 + 执行。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query

from api.db import Session, get_db
from api.services.identity import decode_identity_cookie
from api.services import orchestration as svc
from api.services.orchestration_engine import (
    list_step_defines,
    create_step_define,
    update_step_define,
    delete_step_define,
)

router = APIRouter(prefix="/orchestrations", tags=["orchestrations"])


def _current_user(x_next_identity: Optional[str] = Cookie(default=None, alias="x-next-identity")) -> str:
    """从 x-next-identity Cookie 解出用户名（不能直接存 token：256 位会超列长）。"""
    identity = decode_identity_cookie(x_next_identity or "")
    if not identity or not identity.user_id:
        raise HTTPException(status_code=401, detail="未登录")
    return identity.user_id


def _guarded(fn, *args, **kwargs):
    """编排端点统一返回 envelope {success, data}（对齐前端 request 契约）。

    2026-10-08 fix: 此前裸返回 {total, items} 等，前端 request 读 res.success
    恒 undefined → 编排/组件页面「加载失败」。异常（KeyError/ValueError）
    转对应 HTTP 状态码。
    """
    try:
        return {"success": True, "data": fn(*args, **kwargs)}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ── 组件定义 ──

@router.get("/step-defines")
def step_defines(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = "",
    group_type: str = "",
    db: Session = Depends(get_db),
    _user: str = Depends(_current_user),
):
    return _guarded(list_step_defines, db, page, page_size, keyword, group_type)


@router.post("/step-defines")
def create_step_define_route(payload: dict, db: Session = Depends(get_db), _user: str = Depends(_current_user)):
    return _guarded(create_step_define, db, payload)


@router.put("/step-defines/{define_id}")
def update_step_define_route(define_id: str, payload: dict, db: Session = Depends(get_db), _user: str = Depends(_current_user)):
    return _guarded(update_step_define, db, define_id, payload)


@router.delete("/step-defines/{define_id}")
def delete_step_define_route(define_id: str, db: Session = Depends(get_db), _user: str = Depends(_current_user)):
    return _guarded(delete_step_define, db, define_id)


# ── 编排 ──

@router.get("")
def tapes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = "",
    status: str = "",
    db: Session = Depends(get_db),
    _user: str = Depends(_current_user),
):
    return _guarded(svc.list_tapes, db, page, page_size, keyword, status)


@router.post("")
def create_tape_route(payload: dict, db: Session = Depends(get_db), user: str = Depends(_current_user)):
    return _guarded(svc.create_tape, db, payload, user)


@router.get("/{tape_id}")
def tape_detail(tape_id: str, db: Session = Depends(get_db), _user: str = Depends(_current_user)):
    return _guarded(lambda: svc.serialize_tape(svc.get_tape(db, tape_id), with_design=True))


@router.put("/{tape_id}")
def update_tape_route(tape_id: str, payload: dict, db: Session = Depends(get_db), _user: str = Depends(_current_user)):
    return _guarded(svc.update_tape, db, tape_id, payload)


@router.delete("/{tape_id}")
def delete_tape_route(tape_id: str, db: Session = Depends(get_db), _user: str = Depends(_current_user)):
    return _guarded(svc.delete_tape, db, tape_id)


@router.post("/{tape_id}/design")
def save_design(tape_id: str, payload: dict, db: Session = Depends(get_db), _user: str = Depends(_current_user)):
    return _guarded(
        svc.save_tape_design, db, tape_id, payload.get("nodes") or [], payload.get("edges") or [], payload.get("exec_params")
    )


@router.get("/{tape_id}/export")
def export_draft(tape_id: str, db: Session = Depends(get_db), _user: str = Depends(_current_user)):
    """导出编排草稿 JSON（对齐 data-synth 导出语义）。"""
    return _guarded(svc.export_tape_draft, db, tape_id)


@router.post("/{tape_id}/import")
def import_draft(tape_id: str, payload: dict, db: Session = Depends(get_db), _user: str = Depends(_current_user)):
    """导入编排草稿：上传 JSON 覆盖当前草稿（校验组件存在，不自动发布）。"""
    return _guarded(svc.import_tape_draft, db, tape_id, str(payload.get("content") or ""))


@router.post("/{tape_id}/publish")
def publish(tape_id: str, db: Session = Depends(get_db), _user: str = Depends(_current_user)):
    return _guarded(svc.publish_tape, db, tape_id)


@router.post("/{tape_id}/offline")
def offline(tape_id: str, db: Session = Depends(get_db), _user: str = Depends(_current_user)):
    return _guarded(svc.offline_tape, db, tape_id)


@router.post("/{tape_id}/execute")
def execute(tape_id: str, payload: dict | None = None, db: Session = Depends(get_db), _user: str = Depends(_current_user)):
    return _guarded(svc.execute_tape, db, tape_id, (payload or {}).get("inputs") or {})


# ── 执行记录（异步编排 2026-10-09 新增）──

@router.get("/{tape_id}/runs")
def tape_runs(tape_id: str, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
              db: Session = Depends(get_db), _user: str = Depends(_current_user)):
    # 编排不存在应 404（与其它编排端点一致）——原先直接查子表返回 200 空列表。
    return _guarded(lambda: svc.list_tape_runs(db, tape_id, page, page_size))


@router.get("/runs/{run_id}")
def run_detail(run_id: str, db: Session = Depends(get_db), _user: str = Depends(_current_user)):
    return _guarded(lambda: svc.serialize_tape_run(svc.get_tape_run(db, run_id)))
