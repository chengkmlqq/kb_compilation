#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代码反向生成知识转wiki —— 服务编排（Tape）生成器。

把 code-reverse-wiki-compiler 技能的叙述文档链路（init_kb_dirs 平铺目录 →
gen_summary 资产摘要卡 → build_full 长句/关键词/实体/图谱）实现为服务编排节点。
业务逻辑复用镜像内技能脚本（subprocess 调 CLI，技能代码零改动），编排节点负责
流程编排与参数派生；结构化建页（指标/表/样例，PG COPY）与代码理解（AST+LLM）
是技能的另一条链路，需宿主机资产+改代码适配，本编排先承载叙述文档链路。

节点 DAG（单文档构建，输入：kid / kb_id / source_file）：
  n01 def 输入定义
  n02 script 初始化+文档元数据+平铺目录（init_kb_dirs.py）
  n03 script 门禁（chunks>0）
  n04 script 资产摘要卡（gen_summary.py CLI）
  n05 script 长句/关键词/实体/图谱（build_full.py CLI）
  n06 print 汇总

用法：
  python3 scripts/build_code_rev_tape.py --out /tmp/tape_code_rev.json
"""

import argparse
import json

# ─────────────────────────────────────────────────────────────────────
# script 节点头部：注入 kb 版 weknora_rpc（技能脚本同目录自带）
# ─────────────────────────────────────────────────────────────────────
WR_HEAD = '''import sys, os, json, re, hashlib, subprocess
G = globals()
G.update({'sys': sys, 'os': os, 'json': json, 're': re,
          'hashlib': hashlib, 'time': __import__('time')})
SD = '/srv/kb/worker/builtin_engine/scripts'
CR = '/srv/kb/worker/builtin_engine/code_rev_src'
for _p in (SD, CR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault('KB_API_BASE_URL', 'http://api-server:8000')
_kb = bindings.get('kb_id') or os.environ.get('WEKNORA_KB_ID', '')
if _kb:
    os.environ['WEKNORA_KB_ID'] = _kb
G['_kid'] = bindings.get('kid', '')
G['_kb'] = _kb
'''

CODE_INIT = WR_HEAD + '''
import weknora_rpc as wr
wr.load_config()
if _kb:
    wr.KB = _kb
wr.mcp_init()
# 文档元数据
r = wr.tool_call('get_document', {"knowledge_id": _kid})
d = (r or {}).get('data') or {}
if isinstance(d, str):
    try:
        d = json.loads(d)
    except Exception:
        d = {}
fname = d.get('file_name') or d.get('title') or bindings.get('source_file', '') or _kid
print(f"[n02] 文档: {fname}")
# 资产分类名（技能按文件名识别资产类型，如 全量指标/历史工单/表映射…）
bindings['source_file'] = fname
# 平铺目录初始化（幂等：库根 → 基础实体/长句原文/高频关键词/资产摘要/结构化知识）
r = subprocess.run([sys.executable, os.path.join(CR, 'init_kb_dirs.py'), '--kb', _kb],
                   capture_output=True, text=True, timeout=300)
if r.stdout:
    print(r.stdout[-1500:])
if r.returncode != 0:
    if r.stderr:
        print("STDERR:", r.stderr[-1000:])
    raise RuntimeError(f"init_kb_dirs 失败 rc={r.returncode}")
print(f"[n02] 平铺目录就绪")
'''

CODE_GATE = WR_HEAD + '''
import weknora_rpc as wr
wr.load_config()
if _kb:
    wr.KB = _kb
wr.mcp_init()
if not (_kid and _kb):
    raise ValueError(f"参数缺失: kid={_kid} kb={_kb}")
r = wr.list_chunks(_kid, kb_id=wr.KB, page=1, page_size=5)
data = (r or {}).get('data') or []
if not data:
    raise RuntimeError("切片缺失（chunks=0），阻断构建")
print(f"[n03] 门禁通过：切片可用")
'''

CODE_SUMMARY = WR_HEAD + '''
# gen_summary.py CLI：资产摘要卡（平铺域参数：<kid> <库根> <资产摘要> <原版> <源文件>）
cmd = [sys.executable, os.path.join(CR, 'gen_summary.py'),
       _kid, '代码反向生成', '资产摘要', '原版', bindings.get('source_file', ''),
       '--kb', _kb]
print("[n04] gen_summary:", ' '.join(cmd))
r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
if r.stdout:
    print(r.stdout[-3000:])
if r.returncode != 0:
    if r.stderr:
        print("STDERR:", r.stderr[-2000:])
    raise RuntimeError(f"gen_summary 失败 rc={r.returncode}")
md5_kid = hashlib.md5(_kid.encode()).hexdigest()
bindings['summary_slug'] = f"summary/{md5_kid}"
print(f"[n04] 资产摘要卡完成: {bindings['summary_slug']}")
'''

CODE_BUILD_FULL = WR_HEAD + '''
# build_full.py CLI：长句/关键词/实体/图谱（平铺域参数：<kid> <库根> <资产摘要> <原版> <源文件>）
cmd = [sys.executable, os.path.join(CR, 'build_full.py'),
       _kid, '代码反向生成', '资产摘要', '原版', bindings.get('source_file', ''),
       '--kb', _kb]
print("[n05] build_full:", ' '.join(cmd))
r = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
if r.stdout:
    print(r.stdout[-4000:])
if r.returncode != 0:
    if r.stderr:
        print("STDERR:", r.stderr[-2000:])
    raise RuntimeError(f"build_full 失败 rc={r.returncode}")
print("[n05] 长句/关键词/实体/图谱构建完成")
'''

CODE_DONE = '''_kid = bindings.get('kid', '')
summary = (
    f"代码反向生成构建完成: kid={_kid}\\n"
    f"  文档={bindings.get('source_file', '')}\\n"
    f"  摘要页={bindings.get('summary_slug', '')}"
)
print(summary)
bindings['build_summary'] = summary
'''


# ─────────────────────────────────────────────────────────────────────
# 节点/边装配
# ─────────────────────────────────────────────────────────────────────
def node(nid, inst, label, config, queue='build'):
    return {
        "id": nid,
        "data": {
            "label": label,
            "stepInst": inst,
            "config": {**(config or {}), "queue": queue},
        },
    }


def edge(eid, src, tgt):
    return {"id": eid, "source": src, "target": tgt}


def build_tape() -> dict:
    nodes = [
        node("n01", "def", "输入定义", {
            "assignments": json.dumps([
                {"variable": "kid", "expression": "bindings.get('kid', '')"},
                {"variable": "kb_id", "expression": "bindings.get('kb_id', '')"},
                {"variable": "source_file", "expression": "bindings.get('source_file', '')"},
            ], ensure_ascii=False),
        }),
        node("n02", "script", "初始化+目录+元数据", {"code": CODE_INIT}),
        node("n03", "script", "切片门禁", {"code": CODE_GATE}),
        node("n04", "script", "资产摘要卡", {"code": CODE_SUMMARY}),
        node("n05", "script", "长句/关键词/实体/图谱", {"code": CODE_BUILD_FULL}),
        node("n06", "print", "完成汇总", {"text": "{{build_summary}}"}),
    ]
    edges = [
        edge("e01", "n01", "n02"),
        edge("e02", "n02", "n03"),
        edge("e03", "n03", "n04"),
        edge("e04", "n04", "n05"),
        edge("e05", "n05", "n06"),
    ]
    return {
        "tape_name": "wiki_build_代码反向生成",
        "tape_label": "代码反向生成知识转wiki（编排化）",
        "tape_descr": "code-reverse-wiki-compiler 技能叙述文档链路编排化：平铺目录（基础实体/长句原文/高频关键词/资产摘要/结构化知识）→资产摘要卡→长句→关键词→实体→图谱。业务逻辑复用镜像内技能脚本，编排节点负责流程。输入：kid / kb_id",
        "tape_type": "general",
        "nodes": nodes,
        "edges": edges,
        "exec_params": [
            {"paramName": "kid", "paramLabel": "文档ID", "paramType": "input"},
            {"paramName": "kb_id", "paramLabel": "知识库ID", "paramType": "input"},
            {"paramName": "source_file", "paramLabel": "源文件名（留空自动取）", "paramType": "input", "defaultValue": ""},
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/tape_code_rev.json")
    args = ap.parse_args()
    tape = build_tape()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(tape, f, ensure_ascii=False, indent=2)
    print(f"tape payload → {args.out}")
    print(f"nodes={len(tape['nodes'])} edges={len(tape['edges'])} tape_name={tape['tape_name']}")


if __name__ == "__main__":
    main()