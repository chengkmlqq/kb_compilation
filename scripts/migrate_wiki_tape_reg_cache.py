#!/usr/bin/env python3
"""wiki构建 tape 中间产物 /tmp/reg_* → MinIO reg_cache 迁移（2026-10-27）。

背景：wiki构建-* 编排节点用容器本地 /tmp/reg_<kid8>/ 传中间产物（chunks.json /
fulltext.txt / ls_all.json / terms.json / terms_filtered.json /
terms_filtered_decisions.json / esm.json / catids.json），容器重建即丢失 →
在途 run 批量 FileNotFoundError（10-24/25/26 三次重启共 883 个失败 run）。
reg_cache.py（MinIO shim，2026-10-16 已入库）从未接入本 tape 家族。

本脚本把 seed 的 nodes（设计层）+ steps（执行层）的 code 统一改造成：
  - 路径字面量 f"/tmp/reg_{_kid[:8]}" → f"reg-cache/reg_{_kid[:8]}"（MinIO 前缀，
    经 reg_cache._key 落到 minio://kb-compilation/reg-cache/reg_<kid8>/）
  - 本地 open() 读写 → rc.write_json/read_json/write_text/read_text
  - os.path.exists(src/dec_path/tf) → rc.exists(DIR, 文件名)
  - 删除 os.makedirs（MinIO 无需建目录）
  - 节点头部注入 import reg_cache as rc

用法：python3 scripts/migrate_wiki_tape_reg_cache.py [--check]（默认直接改文件；
--check 只编译校验不改写）
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SEED_DIR = Path(__file__).resolve().parent / "seed_tape_data"
GENERATOR = Path(__file__).resolve().parent / "build_regulation_wiki_tape.py"

# 间接路径变量 → 真实文件名
INDIRECT_FNAME = {
    "src": "terms.json",
    "dec_path": "terms_filtered_decisions.json",
    "tf": "terms_filtered.json",
    "out": "terms_filtered_decisions.json",
}

# ───────────────────────── 转换规则 ─────────────────────────


def _inject_rc(code: str) -> str:
    """在 weknora_rpc import 后注入 reg_cache import（幂等）。"""
    if "import reg_cache as rc" in code:
        return code
    marker = "import weknora_rpc as wr"
    if marker in code:
        return code.replace(marker, marker + "\nimport reg_cache as rc", 1)
    return code


def _strip_makedirs(code: str) -> str:
    return re.sub(r"^[ \t]*os\.makedirs\(DIR, exist_ok=True\)[ \t]*\n", "", code, flags=re.M)


def _strip_indirect_assign(code: str) -> str:
    """删除间接路径变量赋值行（src/out/dec_path/tf = f"{DIR}/..."），
    后续 exists/open 由专门规则替换成 rc 调用。"""
    return re.sub(
        r"^[ \t]*(?:src|out|dec_path|tf)\s*=\s*f[\"']\{DIR\}/([\w.]+)[\"'][ \t]*\n",
        "",
        code,
        flags=re.M,
    )


def _rewrite_file_ops(code: str) -> str:
    """DIR 路径字面量 + 本地 open()/exists 读写 → MinIO reg_cache 调用。"""
    # 1) 路径字面量：/tmp/reg_<kid8> → reg-cache/reg_<kid8>（保留引号风格）
    code = re.sub(
        r'f(["\'])/tmp/reg_\{_kid\[:8\]\}\1',
        r'f\1reg-cache/reg_{_kid[:8]}\1',
        code,
    )
    # 2) 读 JSON（直接路径）：with open(f"{DIR}/x.json", ...) as f:\n    var = json.load(f)
    code = re.sub(
        r'with open\(f"\{DIR}/([\w.]+\.json)", [^)]*\) as f:\n[ \t]*(\w+) = json\.load\(f\)',
        r"\2 = rc.read_json(DIR, '\1')",
        code,
    )
    # 3) 读 JSON（间接 src/dec_path/tf/out）
    code = re.sub(
        r"with open\((src|dec_path|tf|out), [^)]*\) as f:\n[ \t]*(\w+) = json\.load\(f\)",
        lambda m: f"{m.group(2)} = rc.read_json(DIR, '{INDIRECT_FNAME[m.group(1)]}')",
        code,
    )
    # 4) 写 JSON（直接路径）
    code = re.sub(
        r'with open\(f"\{DIR}/([\w.]+\.json)", [^)]*\) as f:\n[ \t]*json\.dump\((.*?), f, ensure_ascii=False(?:, indent=\d+)?\)',
        r"rc.write_json(DIR, '\1', \2)",
        code,
    )
    # 5) 写 JSON（间接 out）
    code = re.sub(
        r"with open\((out), [^)]*\) as f:\n[ \t]*json\.dump\((.*?), f, ensure_ascii=False(?:, indent=\d+)?\)",
        r"rc.write_json(DIR, 'terms_filtered_decisions.json', \2)",
        code,
    )
    # 6) 读 TXT（直接路径）
    code = re.sub(
        r'with open\(f"\{DIR}/([\w.]+\.txt)", [^)]*\) as f:\n[ \t]*(\w+) = f\.read\(\)',
        r"\2 = rc.read_text(DIR, '\1')",
        code,
    )
    # 7) 写 TXT（直接路径）
    code = re.sub(
        r'with open\(f"\{DIR}/([\w.]+\.txt)", [^)]*\) as f:\n[ \t]*f\.write\((.*?)\)',
        r"rc.write_text(DIR, '\1', \2)",
        code,
    )
    # 8) exists 检查（间接 src/dec_path/tf）
    code = re.sub(
        r"if (not )?os\.path\.exists\((src|dec_path|tf)\):",
        lambda m: f"if {m.group(1) or ''}rc.exists(DIR, '{INDIRECT_FNAME[m.group(2)]}'):",
        code,
    )
    # 9) 剩留的裸 f"{DIR}/..." open（漏网标记）
    code = re.sub(
        r'with open\(f"\{DIR}/([\w.]+)", [^)]*\) as f:',
        r"RAW_OPEN: rc.read_text(DIR, '\1')",
        code,
    )
    return code


def transform_code(code: str) -> tuple[str, list[str]]:
    """单节点 code 全量转换。返回 (新 code, 遗留问题列表)。"""
    problems: list[str] = []
    code = _inject_rc(code)
    code = _strip_makedirs(code)
    code = _strip_indirect_assign(code)
    code = _rewrite_file_ops(code)

    if "/tmp/reg_" in code:
        problems.append("残留 /tmp/reg_")
    if 'open(f"{DIR}' in code or "open(f'{DIR}" in code:
        problems.append("残留裸 open(f\"{DIR}")
    if "os.makedirs(DIR" in code:
        problems.append("残留 os.makedirs(DIR")
    if re.search(r"with open\((src|out|dec_path|tf),", code):
        problems.append("残留间接变量 open")
    if "RAW_OPEN" in code:
        problems.append("RAW_OPEN 标记未还原")
    if "import reg_cache as rc" not in code and re.search(r"rc\.(read|write|exists)|reg-cache", code):
        problems.append("未注入 import reg_cache")
    return code, problems


# ───────────────────────── 遍历改写 ─────────────────────────


def migrate_seed_file(path: Path, dry: bool) -> tuple[int, list[str]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    draft = doc.get("draft") or doc
    changed = 0
    all_problems: list[str] = []

    for st in draft.get("steps", []):
        cfg = st.get("step_config") or {}
        code = cfg.get("code")
        if not code:
            continue
        new_code, problems = transform_code(code)
        if problems:
            all_problems.append(f"steps::{st.get('step_id')}: {problems}")
        if new_code != code:
            cfg["code"] = new_code
            changed += 1

    for n in draft.get("nodes", []):
        cfg = n.get("data", {}).get("config", {})
        code = cfg.get("code")
        if not code:
            continue
        new_code, problems = transform_code(code)
        if problems:
            all_problems.append(f"nodes::{n.get('id')}: {problems}")
        if new_code != code:
            cfg["code"] = new_code
            changed += 1

    if changed and not dry:
        path.write_text(
            json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
    return changed, all_problems


def migrate_generator(dry: bool) -> tuple[int, list[str]]:
    text = GENERATOR.read_text(encoding="utf-8")
    n = 0
    new_text = _inject_rc(text)
    if new_text != text:
        n += 1
    new_text = _strip_makedirs(new_text)
    new_text = _strip_indirect_assign(new_text)
    new_text = _rewrite_file_ops(new_text)
    problems = []
    if "/tmp/reg_" in new_text:
        problems.append("生成器残留 /tmp/reg_")
    if 'open(f"{DIR}' in new_text:
        problems.append("生成器残留裸 open(f\"{DIR}")
    if new_text != text:
        n += 1
    if n and not dry:
        GENERATOR.write_text(new_text, encoding="utf-8")
    return n, problems


def _ast_check_all(seed_dir: Path) -> list[str]:
    """编译校验所有内联 code。"""
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


def main() -> None:
    dry = "--check" in sys.argv
    seeds = sorted(SEED_DIR.glob("wiki构建-*.json"))
    total_changed = 0
    all_problems: list[str] = []
    for p in seeds:
        c, probs = migrate_seed_file(p, dry)
        total_changed += c
        all_problems += [f"{p.name}: {x}" for x in probs]
    gc, gprobs = migrate_generator(dry)
    all_problems += [f"build_regulation_wiki_tape.py: {x}" for x in gprobs]

    mode = "检查" if dry else "改写"
    print(f"[migrate] {mode}: {len(seeds)} 个种子，改动 {total_changed} 处；生成器改动 {gc} 处")
    if all_problems:
        print("[migrate] ⚠️ 遗留问题：")
        for p in all_problems:
            print("  ", p)
    else:
        print("[migrate] ✅ 无遗留问题")
    if not dry:
        errs = _ast_check_all(SEED_DIR)
        if errs:
            print(f"[migrate] ⚠️ 语法错误 {len(errs)} 条：")
            for e in errs[:20]:
                print("  ", e)
        else:
            print("[migrate] ✅ 全部 code 语法校验通过")


if __name__ == "__main__":
    main()