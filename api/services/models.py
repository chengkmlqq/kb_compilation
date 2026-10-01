"""Scoped model registry service.

Business scoping (system / personal / team):

- personal: owner_user_id == caller, only the owner sees/uses it
- team:     owner_team_name == caller's team, all team members see/use it
- system:   only administrators see/use it

Runtime resolution chain for consumers (chat QA / embedding):
  personal default > team default > system default > legacy modo_dim > env
Consumers without user context (worker/wiki build, MCP) resolve only
system-scope defaults, then legacy modo_dim, then env — i.e. a system
administrator's configuration is the platform-wide fallback.

Secrets (api_key) are AES-encrypted at rest via api.lib.crypto.aes_encrypt
and masked on every read (sk-s****3456). Saving a value starting with
'****' means "keep the existing stored value".
"""

from __future__ import annotations

import uuid

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from api.lib.crypto import aes_decrypt, aes_encrypt
from api.models.framework import TeamMember, User, UserRole, UserRoleRela
from api.models.model import KbModel
from api.services.runtime_config import mask_sensitive_value

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_SCOPES = ("personal", "team", "system")
MODEL_TYPES = ("chat", "embedding")

# (type, provider) -> default base URL (mirrors WeKnora's provider default URLs)
DEFAULT_PROVIDER_URLS: dict[tuple[str, str], str] = {
    ("chat", "openai"): "https://api.openai.com/v1",
    ("chat", "dashscope"): "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ("chat", "deepseek"): "https://api.deepseek.com/v1",
    ("chat", "siliconflow"): "https://api.siliconflow.cn/v1",
    ("chat", "zhipu"): "https://open.bigmodel.cn/api/paas/v4",
    ("chat", "generic"): "",
    ("embedding", "openai"): "https://api.openai.com/v1",
    ("embedding", "dashscope"): "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ("embedding", "siliconflow"): "https://api.siliconflow.cn/v1",
    ("embedding", "generic"): "",
}

PROVIDER_LABELS: dict[str, str] = {
    "openai": "OpenAI",
    "dashscope": "阿里云 DashScope",
    "deepseek": "DeepSeek",
    "siliconflow": "硅基流动 SiliconFlow",
    "zhipu": "智谱 AI",
    "generic": "通用 OpenAI 兼容",
}


# ---------------------------------------------------------------------------
# Admin / team helpers
# ---------------------------------------------------------------------------


def is_admin(db: Session, user_id: str) -> bool:
    """A user is an administrator if any bound role is named 'admin' or has
    role_type 'system' (modo_user_role_rela), or their team membership
    carries an 'admin' role_name (modo_team_member)."""
    user_id = (user_id or "").strip()
    if not user_id:
        return False
    role_ids = list(
        db.execute(select(UserRoleRela.role_id).where(UserRoleRela.user_id == user_id)).scalars()
    )
    if role_ids:
        rows = db.execute(
            select(UserRole.role_name, UserRole.role_type).where(UserRole.role_id.in_(role_ids))
        ).all()
        if any(
            (name or "").strip().lower() == "admin" or (rtype or "").strip().lower() == "system"
            for name, rtype in rows
        ):
            return True
    member_role = (
        db.execute(
            select(TeamMember.role_name).where(
                and_(TeamMember.user_id == user_id, TeamMember.state == "1")
            )
        )
        .scalars()
        .first()
    )
    return bool(member_role and member_role.strip().lower() == "admin")


# ---------------------------------------------------------------------------
# Encryption / masking
# ---------------------------------------------------------------------------


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return aes_encrypt(value)


def decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    plain = aes_decrypt(value)
    # aes_decrypt returns '' on failure; a legacy plaintext column falls back to itself
    return plain if plain else value


def mask_api_key(value: str | None) -> str:
    if not value:
        return ""
    return mask_sensitive_value(decrypt_secret(value))


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def model_to_dict(m: KbModel) -> dict:
    """API-facing shape. api_key is masked; a `credentials` presence flag is
    derived so the frontend can show '已配置' without the secret."""
    return {
        "id": m.id,
        "scope": m.scope,
        "name": m.name,
        "display_name": m.display_name or "",
        "type": m.type,
        "source": m.source,
        "provider": m.provider or "",
        "description": m.description or "",
        "base_url": m.base_url or "",
        "interface_type": m.interface_type or "openai",
        "dimension": m.dimension,
        "supports_vision": bool(m.supports_vision),
        "custom_headers": m.custom_headers or {},
        "owner_user_id": m.owner_user_id or "",
        "owner_team_name": m.owner_team_name or "",
        "is_default": bool(m.is_default),
        "status": m.status,
        "state": m.state,
        "api_key_masked": mask_api_key(m.api_key),
        "api_key_configured": bool(decrypt_secret(m.api_key)),
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Visibility / permission helpers
# ---------------------------------------------------------------------------


def can_manage(db: Session, m: KbModel, caller_user_id: str, caller_team_name: str, is_sys_admin: bool) -> bool:
    """Write permission (create/update/delete)."""
    if is_sys_admin:
        return True
    if m.scope == "personal":
        return m.owner_user_id == caller_user_id
    if m.scope == "team":
        return m.owner_team_name == caller_team_name
    return False  # system scope: admin only


def list_visible_models(
    db: Session,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
    model_type: str | None = None,
    scope: str | None = None,
) -> list[dict]:
    """List models visible to the caller, optionally filtered by type/scope.

    Visibility is a union: models I own (personal), models of my team
    (team), and system models when I'm an admin.
    """
    from sqlalchemy import or_

    or_clauses = []
    if caller_user_id:
        or_clauses.append(KbModel.owner_user_id == caller_user_id)
    if caller_team_name:
        or_clauses.append(KbModel.owner_team_name == caller_team_name)
    if is_sys_admin:
        or_clauses.append(KbModel.scope == "system")
    if not or_clauses:
        return []
    stmt = select(KbModel).where(and_(KbModel.state == "1", or_(*or_clauses)))
    if model_type:
        stmt = stmt.where(KbModel.type == model_type)
    if scope:
        stmt = stmt.where(KbModel.scope == scope)
    rows = db.execute(stmt.order_by(KbModel.scope, KbModel.type, KbModel.is_default.desc(), KbModel.name)).scalars().all()
    return [model_to_dict(m) for m in rows]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def get_model(db: Session, model_id: str) -> KbModel | None:
    return (
        db.execute(select(KbModel).where(and_(KbModel.id == model_id, KbModel.state == "1")))
        .scalars()
        .first()
    )


def create_model(
    db: Session,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
    payload: dict,
) -> dict:
    """Create a scoped model. permission: personal=any user, team=must name
    the caller's own team, system=admin only."""
    scope = str(payload.get("scope") or "personal").strip().lower()
    if scope not in MODEL_SCOPES:
        raise ValueError(f"无效的模型级别: {scope}")
    model_type = str(payload.get("type") or "").strip().lower()
    if model_type not in MODEL_TYPES:
        raise ValueError(f"无效的模型类型: {model_type}")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("模型名称不能为空")

    owner_user_id = caller_user_id
    owner_team_name = caller_team_name
    if scope == "team":
        team_name = str(payload.get("owner_team_name") or caller_team_name or "").strip()
        if not team_name:
            raise ValueError("团队模型必须指定团队")
        if team_name != caller_team_name and not is_sys_admin:
            raise PermissionError("只能创建本团队的模型")
        owner_team_name = team_name
        owner_user_id = ""  # team-scoped rows belong to the team, not an individual
    elif scope == "system":
        if not is_sys_admin:
            raise PermissionError("只有管理员能创建系统级模型")
        owner_user_id = ""
        owner_team_name = ""
    elif scope == "personal":
        if not caller_user_id:
            raise PermissionError("个人模型需要登录用户")
        owner_team_name = ""  # personal rows are NOT team-visible

    raw_api_key = str(payload.get("api_key") or "").strip()
    m = KbModel(
        id=uuid.uuid4().hex[:36],
        scope=scope,
        name=name,
        display_name=str(payload.get("display_name") or "").strip() or None,
        type=model_type,
        source=str(payload.get("source") or "remote").strip().lower() or "remote",
        provider=str(payload.get("provider") or "").strip() or None,
        description=str(payload.get("description") or "").strip() or None,
        base_url=str(payload.get("base_url") or "").strip() or None,
        api_key=encrypt_secret(raw_api_key) if raw_api_key else None,
        interface_type=str(payload.get("interface_type") or "openai").strip() or "openai",
        dimension=int(payload["dimension"]) if payload.get("dimension") not in (None, "") else None,
        supports_vision=bool(payload.get("supports_vision", False)),
        custom_headers=payload.get("custom_headers") or None,
        owner_user_id=owner_user_id or None,
        owner_team_name=owner_team_name or None,
        is_default=bool(payload.get("is_default", False)),
        status="active",
        state="1",
    )
    if m.is_default:
        _clear_defaults(db, scope, model_type, exclude_id=m.id)
    db.add(m)
    db.commit()
    return model_to_dict(m)


def update_model(
    db: Session,
    model_id: str,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
    payload: dict,
) -> dict:
    m = get_model(db, model_id)
    if not m:
        raise KeyError("模型不存在")
    if not can_manage(db, m, caller_user_id, caller_team_name, is_sys_admin):
        raise PermissionError("无权修改该模型")

    if "name" in payload:
        v = str(payload.get("name") or "").strip()
        if not v:
            raise ValueError("模型名称不能为空")
        m.name = v
    if "display_name" in payload:
        m.display_name = str(payload.get("display_name") or "").strip() or None
    if "description" in payload:
        m.description = str(payload.get("description") or "").strip() or None
    if "base_url" in payload:
        m.base_url = str(payload.get("base_url") or "").strip() or None
    if "provider" in payload:
        m.provider = str(payload.get("provider") or "").strip() or None
    if "interface_type" in payload:
        m.interface_type = str(payload.get("interface_type") or "openai").strip() or "openai"
    if "source" in payload:
        m.source = str(payload.get("source") or "remote").strip().lower() or "remote"
    if "dimension" in payload:
        m.dimension = (
            int(payload["dimension"]) if payload.get("dimension") not in (None, "") else None
        )
    if "supports_vision" in payload:
        m.supports_vision = bool(payload.get("supports_vision", False))
    if "custom_headers" in payload:
        m.custom_headers = payload.get("custom_headers") or None
    # api_key: masked ('****...') = keep existing; empty = clear
    if "api_key" in payload:
        v = str(payload.get("api_key") or "").strip()
        if v.startswith("****"):
            pass  # keep existing
        elif v:
            m.api_key = encrypt_secret(v)
        else:
            m.api_key = None
    if "is_default" in payload:
        want = bool(payload.get("is_default", False))
        if want and not m.is_default:
            _clear_defaults(db, m.scope, m.type, exclude_id=m.id)
            m.is_default = True
        elif not want:
            m.is_default = False
    db.commit()
    return model_to_dict(m)


def delete_model(
    db: Session,
    model_id: str,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
) -> None:
    m = get_model(db, model_id)
    if not m:
        raise KeyError("模型不存在")
    if not can_manage(db, m, caller_user_id, caller_team_name, is_sys_admin):
        raise PermissionError("无权删除该模型")
    m.state = "0"
    db.commit()


def _clear_defaults(db: Session, scope: str, model_type: str, exclude_id: str | None = None) -> None:
    stmt = select(KbModel).where(
        and_(
            KbModel.state == "1",
            KbModel.scope == scope,
            KbModel.type == model_type,
            KbModel.is_default.is_(True),
        )
    )
    if exclude_id:
        stmt = stmt.where(KbModel.id != exclude_id)
    for row in db.execute(stmt).scalars().all():
        row.is_default = False


def set_default(
    db: Session,
    model_id: str,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
) -> dict:
    m = get_model(db, model_id)
    if not m:
        raise KeyError("模型不存在")
    if not can_manage(db, m, caller_user_id, caller_team_name, is_sys_admin):
        raise PermissionError("无权设置默认模型")
    _clear_defaults(db, m.scope, m.type, exclude_id=m.id)
    m.is_default = True
    db.commit()
    return model_to_dict(m)


# ---------------------------------------------------------------------------
# Runtime resolution (personal > team > system > legacy > env)
# ---------------------------------------------------------------------------


def resolve_default_model(
    db: Session,
    model_type: str,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
) -> KbModel | None:
    """Highest-priority visible default model for the type.

    Scopes follow the visibility rule: a logged-in non-admin user sees
    personal + team only (system models are admin-only); an admin sees
    personal + team + system; a caller WITHOUT user context (worker / MCP
    / batch task) resolves the system default — the platform-wide
    configuration managed by administrators.
    """
    scopes: list[str] = []
    if caller_user_id:
        scopes.append("personal")
    if caller_team_name:
        scopes.append("team")
    if is_sys_admin:
        scopes.append("system")
    for scope in scopes:
        row = (
            db.execute(
                select(KbModel).where(
                    and_(
                        KbModel.state == "1",
                        KbModel.scope == scope,
                        KbModel.type == model_type,
                        KbModel.is_default.is_(True),
                        (
                            (KbModel.owner_user_id == caller_user_id)
                            if scope == "personal"
                            else (
                                (KbModel.owner_team_name == caller_team_name)
                                if scope == "team"
                                else KbModel.scope == "system"
                            )
                        ),
                    )
                )
            )
            .scalars()
            .first()
        )
        if row:
            return row
    # System default as the platform-wide fallback ONLY for callers without
    # user context (worker/wiki build, MCP); logged-in users fall back to
    # legacy/env instead so system models stay admin-only.
    if not caller_user_id:
        row = (
            db.execute(
                select(KbModel).where(
                    and_(
                        KbModel.state == "1",
                        KbModel.scope == "system",
                        KbModel.type == model_type,
                        KbModel.is_default.is_(True),
                    )
                )
            )
            .scalars()
            .first()
        )
        if row:
            return row
    return None


def resolve_model_config(
    db: Session,
    model_type: str,
    *,
    caller_user_id: str = "",
    caller_team_name: str = "",
    is_sys_admin: bool = False,
) -> dict | None:
    """Return {base_url, api_key, model, dimension} for the resolved default,
    or None when no scoped default exists (callers then fall back to the
    legacy modo_dim / env path)."""
    m = resolve_default_model(
        db,
        model_type,
        caller_user_id=caller_user_id,
        caller_team_name=caller_team_name,
        is_sys_admin=is_sys_admin,
    )
    if not m:
        return None
    return {
        "base_url": m.base_url or "",
        "api_key": decrypt_secret(m.api_key),
        "model": m.name,
        "dimension": m.dimension,
        "model_id": m.id,
        "scope": m.scope,
    }


def caller_context(db: Session, user_id: str) -> dict:
    """Convenience: (user_id, team_name, is_admin) for a resolved identity
    user_id (team_name from the identity cookie is preferred by callers, but
    this fallback resolves the user's default team when only user_id is
    available — e.g. service-layer callers)."""
    team_name = ""
    if user_id:
        user = db.execute(select(User).where(User.user_id == user_id)).scalars().first()
        if user and user.default_team:
            team_name = user.default_team or ""
    return {
        "user_id": user_id or "",
        "team_name": team_name or "",
        "is_admin": is_admin(db, user_id),
    }


__all__ = [
    "MODEL_SCOPES",
    "MODEL_TYPES",
    "DEFAULT_PROVIDER_URLS",
    "PROVIDER_LABELS",
    "is_admin",
    "list_visible_models",
    "get_model",
    "create_model",
    "update_model",
    "delete_model",
    "set_default",
    "resolve_model_config",
    "resolve_default_model",
    "mask_api_key",
    "decrypt_secret",
    "model_to_dict",
]
