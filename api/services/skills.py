"""Scoped skill registry service.

kb_compilation stores the skill *package* (original upload ZIP) with
system / personal / team scoping — it is the source of truth. The
agent-gateway is a stateless executor: at task submission the worker pulls
the ZIP (base64) and ships it in the task config (1A); the gateway extracts
it into a per-task temp dir, executes, and discards it. No skill data
persists on the gateway side.

The package stays whole (bytes) so a later task can re-transmit it; the
frontmatter name is parsed at upload time for validation + description.
"""

from __future__ import annotations

import io
import re
import uuid
import zipfile

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from api.models.mcp_skill import KbSkill
from api.services.scope import ResourceRow, can_manage, validate_scope_request, visible_clauses

# ZIP 布局: 根级 SKILL.md 或 <skill>/SKILL.md（与网关 UploadSkill 解析一致）
_SKILL_MD_RE = re.compile(r"(?:^|/)([^/]+)/SKILL\.md$", re.IGNORECASE)


def parse_skill_zip(data: bytes) -> tuple[str, str, str]:
    """Parse a skill ZIP -> (name, description, version).

    Mirrors the gateway's UploadSkill: scan entries for the FIRST SKILL.md;
    its parent dir is the skill name (root SKILL.md -> zip-root name).
    Raises ValueError for malformed archives / missing frontmatter name.
    """
    if not data:
        raise ValueError("技能包为空")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise ValueError(f"无法解析技能 ZIP: {e}") from e

    names = zf.namelist()
    skill_root = None
    for n in names:
        norm = n.replace("\\", "/").lower()
        if norm.endswith("skill.md") and "/" not in norm:
            skill_root = ""
            break
        m = _SKILL_MD_RE.match(n)
        if m:
            skill_root = m.group(1)
            break
    if skill_root is None:
        raise ValueError("ZIP 内未找到 SKILL.md")
    # 定位该技能根的 SKILL.md 条目（父目录 == skill_root；根级则路径无 '/'）
    def _is_skill_md(n: str) -> bool:
        norm = n.replace("\\", "/").lower()
        if not norm.endswith("skill.md"):
            return False
        if skill_root == "":
            return "/" not in norm
        return norm.startswith(f"{skill_root.lower()}/") and norm.count("/") == 1

    skill_md_name = next(n for n in names if _is_skill_md(n))
    raw = zf.read(skill_md_name)[:64 * 1024]
    text = raw.decode("utf-8", errors="replace")

    name = ""
    description = ""
    version = ""
    m = re.search(r"^---\s*$", text, re.MULTILINE)
    if m:
        fm = text[m.end():]
        end = re.search(r"^---\s*$", fm, re.MULTILINE)
        if end:
            fm = fm[:end.start()]
        for line in fm.splitlines():
            stripped = line.strip()
            if stripped.startswith("name:"):
                name = stripped[len("name:"):].strip().strip("\"'")
            elif stripped.startswith("description:"):
                description = stripped[len("description:"):].strip().strip("\"'")
            elif stripped.startswith("version:"):
                version = stripped[len("version:"):].strip().strip("\"'")
    if not name:
        name = skill_root
    return name, description, version


def skill_to_dict(m: KbSkill, *, include_package: bool = False) -> dict:
    out = {
        "id": m.id,
        "scope": m.scope,
        "name": m.name,
        "description": m.description or "",
        "version": m.version or "",
        "package_size": m.package_size,
        "owner_user_id": m.owner_user_id or "",
        "owner_team_name": m.owner_team_name or "",
        "state": m.state,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }
    if include_package and m.package_zip:
        out["package_base64"] = m.package_zip.decode("latin1") if isinstance(m.package_zip, bytes) else m.package_zip
    return out


def list_skills(
    db: Session,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
    scope: str | None = None,
) -> list[dict]:
    stmt = select(KbSkill).where(KbSkill.state == "1", *visible_clauses(caller_user_id, caller_team_name, is_sys_admin, KbSkill))
    if scope:
        stmt = stmt.where(KbSkill.scope == scope)
    rows = db.execute(stmt.order_by(KbSkill.scope, KbSkill.name)).scalars().all()
    return [skill_to_dict(m) for m in rows]


def get_skill(db: Session, skill_id: str) -> KbSkill | None:
    return (
        db.execute(select(KbSkill).where(and_(KbSkill.id == skill_id, KbSkill.state == "1")))
        .scalars()
        .first()
    )


def create_skill(
    db: Session,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
    package_zip: bytes,
    scope: str,
    name_override: str | None = None,
) -> dict:
    parsed_scope, owner_user_id, owner_team_name = validate_scope_request(
        scope,
        caller_user_id=caller_user_id,
        caller_team_name=caller_team_name,
        is_sys_admin=is_sys_admin,
    )
    name, description, version = parse_skill_zip(package_zip)
    if name_override and name_override.strip():
        name = name_override.strip()
    dup = db.execute(
        select(KbSkill).where(and_(KbSkill.name == name, KbSkill.state == "1"))
    ).scalars().first()
    if dup:
        raise ValueError(f"技能名已存在: {name}（同名重新安装=更新，请用编辑接口）")

    m = KbSkill(
        id=uuid.uuid4().hex[:36],
        scope=parsed_scope,
        name=name,
        description=description,
        version=version,
        package_zip=package_zip,
        package_size=len(package_zip),
        owner_user_id=owner_user_id or None,
        owner_team_name=owner_team_name or None,
        state="1",
    )
    db.add(m)
    db.commit()
    return skill_to_dict(m)


def update_skill(
    db: Session,
    skill_id: str,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
    package_zip: bytes | None = None,
    description: str | None = None,
) -> dict:
    m = get_skill(db, skill_id)
    if not m:
        raise KeyError("技能不存在")
    if not can_manage(ResourceRow.from_obj(m), caller_user_id, caller_team_name, is_sys_admin):
        raise PermissionError("无权修改该技能")

    if package_zip:
        name, new_desc, version = parse_skill_zip(package_zip)
        dup = db.execute(
            select(KbSkill).where(
                and_(KbSkill.name == name, KbSkill.state == "1", KbSkill.id != skill_id)
            )
        ).scalars().first()
        if dup:
            raise ValueError(f"技能名冲突: {name}")
        m.name = name
        m.description = new_desc
        m.version = version
        m.package_zip = package_zip
        m.package_size = len(package_zip)
    if description is not None:
        m.description = description
    db.commit()
    return skill_to_dict(m)


def delete_skill(
    db: Session,
    skill_id: str,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
) -> None:
    m = get_skill(db, skill_id)
    if not m:
        raise KeyError("技能不存在")
    if not can_manage(ResourceRow.from_obj(m), caller_user_id, caller_team_name, is_sys_admin):
        raise PermissionError("无权删除该技能")
    m.state = "0"
    db.commit()


def get_skill_package(db: Session, skill_id: str) -> KbSkill | None:
    """Full row (incl. package bytes) for task transmission (1A)."""
    return get_skill(db, skill_id)


__all__ = [
    "parse_skill_zip",
    "list_skills",
    "get_skill",
    "create_skill",
    "update_skill",
    "delete_skill",
    "get_skill_package",
    "skill_to_dict",
]