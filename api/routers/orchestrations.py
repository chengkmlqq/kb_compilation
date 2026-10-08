"""编排路由：组件定义 CRUD + 编排 CRUD + 发布/下线 + 执行。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException

from api.db import Session, get_db
from api.services import orchestration as svc
from api.services.orchestration_engine import (
    list_step_defines,
    create_step_define,
    update_step_define,
    delete_step_define,
)

router = APIRouter(prefix="/orchestrations", tags=["orchestrations"])


def _current_user(x_next_identity: Optional[str] = Cookie(default=None, alias="x-next-identity")) -> str:
    if not x_next_identity:
        raise HTTPException(status_code=401, detail="未登录")
    return x_next_identity


def _guarded(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ── 组件定义 ──

@router.get("/step-defines")
def step_defines(
    page: int = 1,
    page_size: int = 20,
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
    page: int = 1,
    page_size: int = 20,
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


@router.post("/{tape_id}/publish")
def publish(tape_id: str, db: Session = Depends(get_db), _user: str = Depends(_current_user)):
    return _guarded(svc.publish_tape, db, tape_id)


@router.post("/{tape_id}/offline")
def offline(tape_id: str, db: Session = Depends(get_db), _user: str = Depends(_current_user)):
    return _guarded(svc.offline_tape, db, tape_id)


@router.post("/{tape_id}/execute")
def execute(tape_id: str, payload: dict | None = None, db: Session = Depends(get_db), _user: str = Depends(_current_user)):
    return _guarded(svc.execute_tape, db, tape_id, (payload or {}).get("inputs") or {})
