#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""维度表SQL转维度wiki —— 服务编排（Tape）生成器。

把 dim-table-wiki-compiler 技能的完整构建能力（SQL解析→总wiki→子wiki(维度值页)→
数据含义分析→出处→关联）实现为服务编排节点。业务逻辑复用镜像内技能脚本
（/srv/kb/worker/builtin_engine/code_rev_src/parse_dim_sql.py + build_dim_wiki.py），
编排节点负责流程编排、参数派生与门禁。

设计要点（面向「知识库文档上传 → 自动调编排构建」）：
  - 输入：kb_id + doc_id（SQL 文件上传后的文档ID）。n02 从平台下载端点拉取
    原始文件字节 → parse_dim_sql 解析 → 维度数据 JSON 落盘。
  - n03 门禁（表数>0）+ 建目录。
  - n04 生成总wiki+子wiki + 逐行 LLM 数据含义分析。
  - n05 批量建页（create_wiki_pages_batch；同 slug 已存在则跳过，只增不删）。
  - n06 汇总。

节点 DAG：
  n01 def 输入定义（kb_id / doc_id / tables / no_llm）
  n02 script 拉取文档+解析维度表
  n03 script 门禁+建目录
  n04 script 生成总wiki+子wiki（含LLM含义分析）
  n05 script 批量建页
  n06 print 汇总

用法：
  python3 scripts/build_dim_wiki_tape.py --out /tmp/tape_dim_wiki.json
"""

import argparse
import json

# ─────────────────────────────────────────────────────────────────────
# script 节点头部
# ─────────────────────────────────────────────────────────────────────
WR_HEAD = '''import sys, os, json, re, hashlib, subprocess
G = globals()
G.update({'sys': sys, 'os': os, 'json': json, 're': re,
          'hashlib': hashlib, 'time': __import__('time')})
SD = '/srv/kb/worker/builtin_engine/scripts'
TS = '/srv/kb/worker/builtin_engine/code_rev_src'
for _p in (SD, TS):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault('KB_API_BASE_URL', 'http://api-server:8000')
_kb = bindings.get('kb_id') or ''
if _kb:
    os.environ['WEKNORA_KB_ID'] = _kb
G['_kb'] = _kb
G['_doc'] = bindings.get('doc_id', '')
G['_tables'] = (bindings.get('tables') or '').strip()
G['_no_llm'] = str(bindings.get('no_llm') or '') == 'true'
'''

CODE_DOWNLOAD = WR_HEAD + '''
import weknora_rpc as wr
import urllib.request as _ur
ck = bindings.get('cookie') or os.environ.get('IDENTITY_COOKIE', '')
if not _doc:
    raise ValueError("doc_id 缺失：请先上传维度表SQL文件到知识库")
url = f"http://api-server:8000/api/v1/kbs/{_kb}/documents/{_doc}/download"
hdr = {}
if ck:
    hdr['Cookie'] = f'x-next-identity={ck}'
try:
    req = _ur.Request(url, headers=hdr)
    raw = _ur.urlopen(req, timeout=120).read()
except Exception as e:
    print(f"[n02] 下载失败({e})，尝试 wr 内部通道")
    raw = None
if raw is None:
    d = wr.tool_call('get_document', {"knowledge_id": _doc})
    doc = (d or {}).get('data') or {}
    raise RuntimeError(f"无法取到文档字节: {_doc} ({doc.get('file_name', '')})")
sp = '/tmp/dim_upload.sql'
open(sp, 'wb').write(raw)
print(f"[n02] 文档字节 {len(raw)} 已落盘 {sp}")

import parse_dim_sql
tables = parse_dim_sql.parse_sql_file(sp)
if _tables:
    wanted = {t.strip() for t in _tables.split(',') if t.strip()}
    tables = {t: v for t, v in tables.items() if t in wanted}
os.makedirs('/tmp/dim_wiki', exist_ok=True)
json.dump(tables, open('/tmp/dim_wiki/tables.json', 'w', encoding='utf-8'), ensure_ascii=False)
n_rows = sum(len(v['rows']) for v in tables.values())
print(f"[n02] 表={len(tables)} 总行={n_rows}")
for t, v in tables.items():
    print(f"  {t}: {len(v['rows'])} 行 PK={v.get('pk')}")
bindings['dim_tables'] = len(tables)
bindings['dim_rows'] = n_rows
'''

CODE_FOLDERS = WR_HEAD + '''
import os, json, weknora_rpc as wr
wr.load_config()
wr.mcp_init()
if _kb:
    wr.KB = _kb
tables = json.load(open('/tmp/dim_wiki/tables.json', encoding='utf-8'))
if not tables:
    raise RuntimeError("无维度表，阻断")
# 幂等建目录（每表一个一级目录）
existing = wr.tool_call('list_wiki_folders', {"kb_id": _kb}) or {}
ext = existing.get('folders') or (existing.get('data') or {}).get('folders') or []
by_name = {f.get('name'): f.get('id') for f in ext if isinstance(f, dict) and f.get('name')}
LABEL = {
    'dim_area': '地域', 'dim_channel': '渠道', 'dim_customer': '客户',
    'dim_dept': '组织', 'dim_kpi': '指标', 'dim_product': '产品', 'dim_time': '时间',
}
fids = {}
for t in tables:
    lname = f"{LABEL.get(t, t)}维度"
    fid = by_name.get(lname)
    if not fid:
        r = wr.tool_call('create_wiki_folder', {"kb_id": _kb, "name": lname, "parent_id": ""})
        rr = r.get('data') or r
        fid = rr.get('id') if isinstance(rr, dict) else None
        by_name[lname] = fid
    fids[t] = fid
    print(f"[n03] 目录 {lname} → {fid}")
json.dump(fids, open('/tmp/dim_wiki/folders.json', 'w', encoding='utf-8'), ensure_ascii=False)
print(f"[n03] 目录就绪：{len(fids)}")
'''

CODE_BUILD = WR_HEAD + '''
import os, json, sys

TS = '/srv/kb/worker/builtin_engine/code_rev_src'
if TS not in sys.path:
    sys.path.insert(0, TS)
import build_dim_wiki as bdw
import weknora_rpc as wr
wr.load_config()
if _kb:
    wr.KB = _kb

# 复用技能脚本完整构建（总wiki+子wiki+LLM含义分析+目录+建页）
tables = json.load(open('/tmp/dim_wiki/tables.json', encoding='utf-8'))
data = {t: {**v, 'rows': v['rows'], 'cols': v['cols'],
            'col_comments': v.get('col_comments', {}), 'table_comment': v.get('table_comment', ''),
            'pk': v.get('pk', []), 'col_defs': v.get('col_defs', [])}
        for t, v in tables.items()}
bdw.cfg.kb = _kb
bdw.cfg.no_llm = _no_llm
pages, created = bdw.build_all(data)
print(f"[n04] 构建完成：页={pages} 建页={created}")
bindings['dim_pages'] = pages
bindings['dim_created'] = created
'''

CODE_DONE = '''_kb = bindings.get('kb_id', '')
_doc = bindings.get('doc_id', '')
summary = (
    f"维度wiki构建完成：kb={_kb} doc={_doc}\\\\n"
    f"  表={bindings.get('dim_tables', 0)} 行={bindings.get('dim_rows', 0)} 页={bindings.get('dim_pages', 0)}\\\\n"
    f"  结构：每表总wiki + 维度值子wiki（含数据含义分析/出处/关联）"
)
print(summary)
bindings['build_summary'] = summary
'''


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
                {"variable": "kb_id", "expression": "bindings.get('kb_id', '')"},
                {"variable": "doc_id", "expression": "bindings.get('doc_id', '')"},
                {"variable": "tables", "expression": "bindings.get('tables', '')"},
                {"variable": "no_llm", "expression": "str(bindings.get('no_llm') or '')"},
                {"variable": "cookie", "expression": "bindings.get('x_next_identity', '')"},
            ], ensure_ascii=False),
        }),
        node("n02", "script", "拉取文档+解析维度表", {"code": CODE_DOWNLOAD}),
        node("n03", "script", "门禁+建目录", {"code": CODE_FOLDERS}),
        node("n04", "script", "生成总wiki+子wiki(LLM含义分析)", {"code": CODE_BUILD}),
        node("n05", "script", "批量建页", {"code": CODE_BUILD}),
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
        "tape_name": "wiki_build_维度wiki",
        "tape_label": "维度表SQL转维度wiki（编排化）",
        "tape_descr": "维度表 DDL+INSERT SQL → 每表总wiki + 维度值子wiki（含数据含义分析/出处标识/层级关联/跨表关联）。业务逻辑复用镜像内 dim-table 技能脚本（parse_dim_sql + build_dim_wiki）：文档下载→SQL解析→目录幂等→总wiki+子wiki生成→逐行 LLM 含义分析→批量建页（同 slug 只增不删）。输入：kb_id / doc_id / tables(可选过滤) / no_llm",
        "tape_type": "general",
        "nodes": nodes,
        "edges": edges,
        "exec_params": [
            {"paramName": "kb_id", "paramLabel": "知识库ID", "paramType": "input"},
            {"paramName": "doc_id", "paramLabel": "文档ID（维度表SQL）", "paramType": "input"},
            {"paramName": "tables", "paramLabel": "只构建指定表（逗号分隔，留空全部）", "paramType": "input", "defaultValue": ""},
            {"paramName": "no_llm", "paramLabel": "跳过LLM含义分析（true/false）", "paramType": "input", "defaultValue": "false"},
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/tape_dim_wiki.json")
    args = ap.parse_args()
    tape = build_tape()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(tape, f, ensure_ascii=False, indent=2)
    print(f"tape payload → {args.out}")
    print(f"nodes={len(tape['nodes'])} edges={len(tape['edges'])} tape_name={tape['tape_name']}")


if __name__ == "__main__":
    main()