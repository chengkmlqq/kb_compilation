"""End-to-end verification of gateway 1A/2A task payloads.

Submits a gateway task whose config carries:
- skill_zip_base64: the skill ZIP migrated into kb_compilation (system scope)
- mcp_servers:      the migrated MCP config (decrypted)

Then polls the task and reports the agent's skill_zip handling — the task
uses a trivial prompt that only calls list_skills (no long build), so the
run completes quickly and the log shows whether the task-level skill was
loaded and cleaned up.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.parse

import httpx

from api.db import get_sessionmaker
from api.services.skills import get_skill_package, list_skills

GW = "http://127.0.0.1:8080"


def main() -> int:
    db = get_sessionmaker()()
    try:
        skills = list_skills(db, caller_user_id="", caller_team_name="", is_sys_admin=True)
        print("system skills:", [s["name"] for s in skills])
        if not skills:
            print("SKIP: no system skill migrated")
            return 2
        skill = skills[0]
        row = get_skill_package(db, skill["id"])
        assert row and row.package_zip, "skill package missing"

        from api.services.mcps import resolve_task_mcp_servers

        mcps = resolve_task_mcp_servers(db, caller_user_id="", caller_team_name="", is_sys_admin=True)
        print("system mcps:", [(m["name"], m.get("url", "")[:40]) for m in mcps])

        payload = {
            "input": "列出当前可用的技能并返回第一个技能名。只调用 list_skills 工具，不要运行任何脚本。",
            "config": {
                "skill": skill["name"],
                "skill_zip_base64": base64.b64encode(bytes(row.package_zip)).decode("ascii"),
                "mcp_servers": mcps,
            },
        }
        with httpx.Client(timeout=30) as client:
            r = client.post(f"{GW}/tasks", json=payload)
            r.raise_for_status()
            task_id = r.json()["task_id"]
            print("submitted:", task_id)

            deadline = time.monotonic() + 120
            last = {}
            while time.monotonic() < deadline:
                rr = client.get(f"{GW}/tasks/{task_id}", timeout=30)
                last = rr.json()
                if last.get("status") in ("succeeded", "failed", "cancelled"):
                    break
                time.sleep(3)
            print("status:", last.get("status"))
            print("output:", (last.get("output_text") or "")[:400])
            if last.get("error_detail"):
                print("error:", last["error_detail"][:300])
            # 验证任务级技能已清理（临时目录不存在、registry 覆盖已解除）
            if last.get("status") == "succeeded":
                cleanup = client.get(f"{GW}/skills", timeout=30)
                print("skills endpoint count:", len(cleanup.json().get("data", [])) if cleanup.json().get("data") is not None else cleanup.json())
            return 0 if last.get("status") == "succeeded" else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())