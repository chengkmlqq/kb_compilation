"""Identity session — cookie encode/decode + RBAC path permission check.

Ported from data-synth:
- src/lib/auth/identity-session.ts (cookie encode/decode, resolveActiveUserIdentity)
- src/app/actions/role-actions.ts rawCheckPathPermission (RBAC)
- src/lib/identity.ts (Identity type)

Interoperability requirements:
- `x-next-identity` cookie is AES-encrypted JSON (CryptoJS format) — shared
  with the Next.js frontend, so byte-compatibility matters.
- Legacy `x` cookie uses DES — we decode it for backward compat.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import and_, desc, select
from sqlalchemy.orm import Session

from api.lib.crypto import aes_decrypt, aes_encrypt, des_decrypt
from api.models.framework import Menu, RoleMenuRela, Team, TeamMember, User, UserRoleRela


@dataclass
class Identity:
    """User identity decoded from the session cookie."""

    login_id: str = ""
    user_id: str = ""
    user_name: str = ""
    team_name: str = ""
    team_id: str = ""
    team_label: str = ""
    raw: dict = field(default_factory=dict)


def _to_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def normalize_identity_payload(payload: dict | None) -> Identity | None:
    """Normalize camelCase/snake_case payload into Identity (TS normalizeIdentityPayload)."""
    if not payload:
        return None
    user_id = _to_text(payload.get("userId", payload.get("user_id")))
    if not user_id:
        return None
    return Identity(
        login_id=_to_text(payload.get("loginId", payload.get("login_id"))),
        user_id=user_id,
        user_name=_to_text(payload.get("userName", payload.get("user_name"))),
        team_name=_to_text(payload.get("teamName", payload.get("team_name"))),
        team_id=_to_text(payload.get("teamId", payload.get("team_id"))),
        team_label=_to_text(payload.get("teamLabel", payload.get("team_label"))),
        raw=payload,
    )


def encode_identity_cookie(identity: Identity) -> str:
    """AES-encrypt JSON identity — same as encodeIdentityCookie()."""
    payload = {
        "loginId": identity.login_id,
        "userId": identity.user_id,
        "userName": identity.user_name,
        "teamName": identity.team_name,
        "teamId": identity.team_id,
        "teamLabel": identity.team_label,
    }
    return aes_encrypt(json.dumps(payload, ensure_ascii=False))


def decode_identity_cookie(token: str) -> Identity | None:
    """Decode AES cookie; fall back to legacy DES cookie. Mirrors TS decode*."""
    if not token:
        return None
    for decrypt in (aes_decrypt, des_decrypt):
        text = decrypt(token)
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        identity = normalize_identity_payload(payload)
        if identity:
            return identity
    return None


def find_user_team_membership(db: Session, user_id: str, team_name: str) -> dict | None:
    """Direct default-team membership lookup (TS findUserTeamMembership)."""
    row = (
        db.execute(
            select(
                TeamMember.team_name,
                Team.team_id,
                Team.label.label("team_label"),
            )
            .join(Team, Team.team_name == TeamMember.team_name)
            .where(
                and_(
                    TeamMember.user_id == user_id,
                    TeamMember.team_name == team_name,
                )
            )
            .limit(1)
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def find_fallback_user_team_membership(db: Session, user_id: str) -> dict | None:
    """Most recent team membership (TS findFallbackUserTeamMembership)."""
    row = (
        db.execute(
            select(
                TeamMember.team_name,
                Team.team_id,
                Team.label.label("team_label"),
            )
            .join(Team, Team.team_name == TeamMember.team_name)
            .where(TeamMember.user_id == user_id)
            .order_by(desc(TeamMember.create_dt))
            .limit(1)
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def resolve_active_user_identity(
    db: Session,
    user_id: str,
    preferred_user_name: str | None = None,
    prefer_simple: bool = False,
) -> Identity | None:
    """Resolve identity for a user after login (TS resolveActiveUserIdentity).

    If prefer_simple is True, skips the user lookup and team resolution — used
    by service-to-service flows that already have the identity pieces.
    """
    normalized = user_id.strip()
    if not normalized:
        return None

    user = db.execute(select(User).where(User.user_id == normalized)).scalars().first()
    if not user or user.state != "1":
        return None

    login_id = user.id or ""
    actual_user_id = user.user_id or normalized
    default_team_name = _to_text(user.default_team)
    team_name = ""
    team_id = ""
    team_label = ""

    if default_team_name:
        membership = find_user_team_membership(db, actual_user_id, default_team_name)
        if membership:
            team_name = membership["team_name"] or ""
            team_id = membership["team_id"] or ""
            team_label = membership.get("team_label") or ""

    if not team_id:
        membership = find_fallback_user_team_membership(db, actual_user_id)
        if membership:
            team_name = membership["team_name"] or ""
            team_id = membership["team_id"] or ""
            team_label = membership.get("team_label") or ""

    if team_name and team_name != default_team_name:
        # Sync default_team like TS does.
        user.default_team = team_name
        db.commit()

    return Identity(
        login_id=login_id,
        user_id=actual_user_id,
        user_name=user.user_name or preferred_user_name or actual_user_id,
        team_name=team_name,
        team_id=team_id,
        team_label=team_label,
    )


def check_path_permission(db: Session, user_id: str, pathname: str) -> tuple[bool, str]:
    """RBAC: route -> menu -> user roles (direct + team) -> role-menu link.

    Ported from rawCheckPathPermission in src/app/actions/role-actions.ts.
    """
    # 1. Exact route match against modo_menu.
    menu = (
        db.execute(
            select(Menu.menu_id).where(and_(Menu.state == "1", Menu.route == pathname)).limit(1)
        )
        .scalars()
        .first()
    )
    if not menu:
        return True, "路径未受控"  # not a controlled route — allow

    # 2. Collect user role ids (direct relations).
    role_ids = list(
        db.execute(select(UserRoleRela.role_id).where(UserRoleRela.user_id == user_id)).scalars()
    )

    # 3. Team role via team membership (state='1').
    team_role_rows = (
        db.execute(
            select(TeamMember.role_name)
            .where(
                and_(
                    TeamMember.user_id == user_id,
                    TeamMember.state == "1",
                )
            )
            .limit(1)
        )
        .scalars()
        .first()
    )
    if team_role_rows and team_role_rows not in role_ids:
        role_ids.append(team_role_rows)

    if not role_ids:
        return False, "用户无角色"

    # 4. Does any role link this menu?
    matched = (
        db.execute(
            select(RoleMenuRela.rela_id)
            .where(and_(RoleMenuRela.menu_id == menu, RoleMenuRela.role_id.in_(role_ids)))
            .limit(1)
        )
        .scalars()
        .first()
    )
    if not matched:
        return False, "无菜单权限"
    return True, ""