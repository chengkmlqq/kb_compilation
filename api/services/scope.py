"""Shared scoped-resource helpers — visibility & permission for every
personal/team/system resource family (kb_model / kb_mcp_server / kb_skill /
kb_datasource).

Business rules (same contract everywhere):
- personal: only the owning user can see and use
- team:     every member of the owning team can see and use
- system:   only administrators can see and use

A resource's ownership is generic: caller provides the ORM object plus
getters for scope/owner_user_id/owner_team_name so this module stays
storage-agnostic (no SQLAlchemy model import).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from api.models.framework import TeamMember, User, UserRole, UserRoleRela


@dataclass
class ScopeOwner:
    """Extractor of the three scope fields from a row-like object."""

    scope: str = ""
    owner_user_id: str | None = ""
    owner_team_name: str | None = ""


@dataclass
class ResourceRow:
    scope: str = ""
    owner_user_id: str | None = None
    owner_team_name: str | None = None
    deleted: bool = False  # state != '1'
    getters: dict = field(default_factory=dict)  # {"scope": callable, ...} optional

    @classmethod
    def from_obj(cls, obj: Any, *, scope_attr: str = "scope",
                 owner_user: str = "owner_user_id", owner_team: str = "owner_team_name",
                 state_attr: str = "state") -> "ResourceRow":
        return cls(
            scope=str(getattr(obj, scope_attr) or ""),
            owner_user_id=str(getattr(obj, owner_user, "") or ""),
            owner_team_name=str(getattr(obj, owner_team, "") or ""),
            deleted=str(getattr(obj, state_attr, "1") or "1") != "1",
        )


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
            for (name, rtype) in rows
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


def caller_context(db: Session, user_id: str) -> dict:
    """{user_id, team_name, is_admin} for service-layer callers that only
    have a user_id (team_name resolved from default_team as a fallback)."""
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


def visible_clauses(
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
    model: Any,
) -> list[Any]:
    """SQLAlchemy OR-clauses implementing the visibility union:
    owned-by-me OR my-team OR (system AND admin). Caller supplies the ORM
    model class that has scope/owner_user_id/owner_team_name columns."""
    from sqlalchemy import or_

    or_clauses: list[Any] = []
    if caller_user_id:
        or_clauses.append(model.owner_user_id == caller_user_id)
    if caller_team_name:
        or_clauses.append(model.owner_team_name == caller_team_name)
    if is_sys_admin:
        or_clauses.append(model.scope == "system")
    if not or_clauses:
        return []
    return [or_(*or_clauses)]


def can_manage(
    row: ResourceRow,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
) -> bool:
    """Write permission (create/update/delete) for an existing row."""
    if is_sys_admin:
        return True
    if row.scope == "personal":
        return row.owner_user_id == caller_user_id
    if row.scope == "team":
        return row.owner_team_name == caller_team_name
    return False  # system scope: admin only


def can_see(
    row: ResourceRow,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
) -> bool:
    """Read-only visibility for a single row."""
    if row.deleted:
        return False
    if row.scope == "personal":
        return row.owner_user_id == caller_user_id
    if row.scope == "team":
        return bool(row.owner_team_name) and row.owner_team_name == caller_team_name
    if row.scope == "system":
        return is_sys_admin
    return False


def validate_scope_request(
    requested_scope: str,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
) -> tuple[str, str, str]:
    """Validate and normalize a requested scope at create time.

    Returns (scope, owner_user_id, owner_team_name):
    - personal → owner = caller, team = ''
    - team     → team must equal caller's team (unless admin), owner_user = ''
    - system   → admin only, both owner fields = ''
    Raises PermissionError / ValueError for invalid requests.
    """
    scope = (requested_scope or "").strip().lower()
    if scope not in ("personal", "team", "system"):
        raise ValueError(f"无效的归属级别: {requested_scope}")
    if scope == "system":
        if not is_sys_admin:
            raise PermissionError("只有管理员能创建系统级资源")
        return "system", "", ""
    if scope == "personal":
        if not caller_user_id:
            raise PermissionError("个人资源需要登录用户")
        return "personal", caller_user_id, ""
    # team
    team_name = (caller_team_name or "").strip()
    if not team_name:
        raise PermissionError("当前用户没有所属团队，无法创建团队级资源")
    return "team", "", team_name


__all__ = [
    "ResourceRow",
    "ScopeOwner",
    "is_admin",
    "caller_context",
    "can_manage",
    "can_see",
    "visible_clauses",
    "validate_scope_request",
]