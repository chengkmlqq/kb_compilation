"""One-off migration: import gateway-side MCP configs + skills into
kb_compilation's scoped registries (kb_mcp_server / kb_skill, system scope).

Run with a live DATABASE_URL (host venv -> container MySQL):
    DATABASE_URL=mysql+pymysql://weknora:weknora@172.20.0.5:3306/knowledge \
    DB_TYPE=mysql uv run python scripts/migrate_gateway_config.py

Sources (read via docker exec):
- MCP: /srv/gateway/mcp_servers.json (seed, PLAINTEXT secrets) +
       /srv/gateway/data/mcp_servers.managed.json (masked, runtime)
- Skills: /srv/gateway/data/skills/<name>/ (installed skill dirs)

Imported rows get scope='system' (admin-managed platform config). Secrets
are AES-encrypted into kb_mcp_server.headers the same way kb_model stores
api_key. The gateway keeps a read-only copy for backward compat until the
stateless-executor path (1A/2A) fully replaces it.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from sqlalchemy.orm import Session

from api.db import get_sessionmaker
from api.lib.crypto import aes_encrypt
from api.models.mcp_skill import KbMcpServer, KbSkill
from api.services.skills import parse_skill_zip

GW = "agent-gateway-deploy-agent-gateway-1"
SEED_PATH = "/srv/gateway/mcp_servers.json"
MANAGED_PATH = "/srv/gateway/data/mcp_servers.managed.json"
SKILLS_DIR = "/srv/gateway/data/skills"

SYSTEM_SCOPE = "system"


def _gw_cat(path: str) -> str:
    out = subprocess.run(
        ["docker", "exec", GW, "sh", "-c", f"cat {path}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if out.returncode != 0:
        print(f"[warn] cannot read {path}: {out.stderr.strip()[:120]}")
        return ""
    return out.stdout


def _gw_listdir(path: str) -> list[str]:
    out = subprocess.run(
        ["docker", "exec", GW, "sh", "-c", f"ls {path}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if out.returncode != 0:
        return []
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def _gw_skill_zip(skill_dir: str) -> bytes | None:
    """Inside the container, build a REAL zip (root layout <skill>/SKILL.md,
    matching UploadSkill) to a temp file, base64 it out, then delete it."""
    script = (
        "import zipfile,os,base64,glob\n"
        f"root='{SKILLS_DIR}/{skill_dir}'\n"
        "out='/tmp/_skill.zip'\n"
        "z=zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED)\n"
        "for f in glob.glob(root+'/**',recursive=True):\n"
        "    if os.path.isfile(f):\n"
        "        z.write(f, os.path.relpath(f, os.path.dirname(root)))\n"
        "z.close()\n"
        "print(base64.b64encode(open(out,'rb').read()).decode())\n"
        "os.remove(out)"
    )
    out = subprocess.run(
        ["docker", "exec", GW, "python3", "-c", script],
        capture_output=True,
        timeout=120,
    )
    if out.returncode != 0 or not out.stdout:
        print(f"[warn] zip skill {skill_dir} failed: {out.stderr.strip()[:160]}")
        return None
    import base64

    return base64.b64decode(out.stdout.strip())


def migrate_mcps(db: Session) -> int:
    raw = _gw_cat(SEED_PATH)
    if not raw:
        raw = _gw_cat(MANAGED_PATH)
    if not raw:
        print("no MCP config found on gateway; skipping")
        return 0
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"cannot parse MCP config: {e}")
        return 0
    if not isinstance(items, list):
        print(f"unexpected MCP config shape: {type(items)}")
        return 0

    imported = 0
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        existing = (
            db.query(KbMcpServer)
            .filter(KbMcpServer.name == name, KbMcpServer.state == "1")
            .first()
        )
        if existing:
            print(f"[skip] mcp {name} already exists")
            continue
        headers = {}
        for k, v in (item.get("headers") or {}).items():
            v = str(v or "")
            # 跳过掩码值（managed.json 是脱敏存储的）
            if v.startswith("****") or "..." in v:
                print(f"[warn] {name} header {k} is masked; import plaintext from .env MASTER_API_KEY manually")
                continue
            headers[k] = aes_encrypt(v)
        db.add(
            KbMcpServer(
                id=__import__("uuid").uuid4().hex[:36],
                scope=SYSTEM_SCOPE,
                name=name,
                type=str(item.get("type") or "streamable_http").strip(),
                url=str(item.get("url") or "").strip() or None,
                headers=headers or None,
                command=str(item.get("command") or "").strip() or None,
                args=item.get("args") or None,
                env=item.get("env") or None,
                enabled=True,
                state="1",
            )
        )
        imported += 1
        print(f"[import] mcp {name} (system)")
    db.commit()
    return imported


def migrate_skills(db: Session) -> int:
    dirs = _gw_listdir(SKILLS_DIR)
    imported = 0
    for d in dirs:
        existing = (
            db.query(KbSkill).filter(KbSkill.name == d, KbSkill.state == "1").first()
        )
        if existing:
            print(f"[skip] skill {d} already exists")
            continue
        zipped = _gw_skill_zip(d)
        if not zipped:
            print(f"[warn] cannot tar skill dir {d}")
            continue
        try:
            name, description, version = parse_skill_zip(zipped)
        except ValueError as e:
            print(f"[warn] skill {d} parse failed: {e}")
            continue
        db.add(
            KbSkill(
                id=__import__("uuid").uuid4().hex[:36],
                scope=SYSTEM_SCOPE,
                name=name,
                description=description,
                version=version,
                package_zip=zipped,
                package_size=len(zipped),
                state="1",
            )
        )
        imported += 1
        print(f"[import] skill {name} (system, {len(zipped)} bytes)")
    db.commit()
    return imported


def main() -> int:
    db: Session = get_sessionmaker()()
    try:
        n_mcp = migrate_mcps(db)
        n_skill = migrate_skills(db)
        print(f"DONE: imported {n_mcp} MCP servers, {n_skill} skills (system scope)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())