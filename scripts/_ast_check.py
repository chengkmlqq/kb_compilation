#!/usr/bin/env python3
"""ast_check_all(seed_dir) — 编译校验 wiki构建 种子所有内联 code 片段。

迁移脚本 migrate_wiki_tape_reg_cache.py 的语法校验后端：
对 seed_tape_data/wiki构建-*.json 的 nodes(设计层)+steps(执行层) 的
code 逐个 compile()，返回错误清单（空 = 全部通过）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def ast_check_all(seed_dir: Path) -> list[str]:
    errs: list[str] = []
    for p in sorted(Path(seed_dir).glob("wiki构建-*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            errs.append(f"{p.name}: JSON 解析失败 {e}")
            continue
        draft = doc.get("draft") or doc
        for st in draft.get("steps", []):
            code = (st.get("step_config") or {}).get("code")
            if code:
                try:
                    compile(code, f"<{p.name}::{st.get('step_id')}>", "exec")
                except SyntaxError as e:
                    errs.append(f"{p.name}::steps::{st.get('step_id')} 语法错误: {e}")
        for n in draft.get("nodes", []):
            code = (n.get("data", {}).get("config", {}) or {}).get("code")
            if code:
                try:
                    compile(code, f"<{p.name}::{n.get('id')}>", "exec")
                except SyntaxError as e:
                    errs.append(f"{p.name}::nodes::{n.get('id')} 语法错误: {e}")
    return errs


if __name__ == "__main__":
    errs = ast_check_all(Path(sys.argv[1] if len(sys.argv) > 1 else "scripts/seed_tape_data"))
    if errs:
        for e in errs:
            print("ERR", e)
        sys.exit(1)
    print("OK all codes compile")