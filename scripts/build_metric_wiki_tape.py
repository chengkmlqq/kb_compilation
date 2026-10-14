#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全量指标表格转指标wiki —— 服务编排（Tape）生成器。

把一个指标表格（.xlsx）构建为「一指标一 wiki 页 + 两级分类目录」的完整链路
编排化。业务逻辑复用 code_rev_src/metric_wiki_demo.py 的函数（静态 SQL 分析、
LLM SQL 分析、页面渲染），编排节点负责流程编排、参数派生与门禁。

设计要点（面向「知识库文档上传 → 自动调编排构建」）：
  - 输入：kb_id + doc_id（文档上传后的文档ID）。n02 从平台下载端点拉取
    原始文件字节 → xlsx 解析 → 指标行 JSON 落盘。
  - n03 按 指标分类-I/II 建两级目录（幂等：按名称复用已有 folder）。
  - n04 逐指标 LLM 分析 SQL（结构化五字段），中间结果落盘，断点续跑。
  - n05 批量建页（create_wiki_pages_batch）；同 slug 已存在则跳过，
    新指标只增不删。
  - n06 汇总。

节点 DAG：
  n01 def 输入定义（kb_id / doc_id / limit / 白名单编码）
  n02 script 拉取文档+解析指标行
  n03 script 门禁+建两级目录
  n04 script SQL 分析（LLM，可断点）
  n05 script 批量建页
  n06 print 汇总

用法：
  python3 scripts/build_metric_wiki_tape.py --out /tmp/tape_metric_wiki.json
"""

import argparse
import json

# ─────────────────────────────────────────────────────────────────────
# script 节点头部：注入 kb 版 weknora_rpc 与 xlsx_lite
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
G['_limit'] = int(bindings.get('limit') or 0) or 0
G['_only'] = (bindings.get('only_codes') or '').strip()
'''

CODE_DOWNLOAD = WR_HEAD + '''
import weknora_rpc as wr
import urllib.request as _ur
ck = bindings.get('cookie') or os.environ.get('IDENTITY_COOKIE', '')
hdr = {'Content-Type': 'application/json'}
if ck:
    hdr['Cookie'] = f'x-next-identity={ck}'
if not _doc:
    raise ValueError("doc_id 缺失：请先上传指标表格到知识库")
url = f"http://api-server:8000/api/v1/kbs/{_kb}/documents/{_doc}/download"
if not ck:
    # 无 cookie 时用 open 下载端点（需免密 cookie 或匿名），否则走存储兜底
    pass
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
sp = '/tmp/metric_upload.xlsx'
open(sp, 'wb').write(raw)
print(f"[n02] 文档字节 {len(raw)} 已落盘 {sp}")

import xlsx_lite
sh = xlsx_lite.read_sheets(sp)[0]
rows = sh['rows']
header = rows[0]
data = [dict(zip(header, r)) for r in rows[1:]]
print(f"[n02] sheet=<{sh.get('name')}> 指标行={len(data)}")
if _only:
    codes = [c.strip() for c in _only.split(',') if c.strip()]
    data = [r for r in data if r.get('指标编码') in codes]
    print(f"[n02] 白名单 {codes} → {len(data)} 行")
if _limit and len(data) > _limit:
    # 采样：优先覆盖二级分类广度（每 分类-II 取 1 条）
    seen, picked = set(), []
    for r in data:
        k = (r.get('指标分类-I'), r.get('指标分类-II'))
        if k in seen:
            continue
        seen.add(k)
        picked.append(r)
        if len(picked) >= _limit:
            break
    data = picked
    print(f"[n02] 采样 {_limit} 条（覆盖 {len(seen)} 个二级分类）")
os.makedirs('/tmp/metric_wiki', exist_ok=True)
json.dump(data, open('/tmp/metric_wiki/rows.json', 'w', encoding='utf-8'), ensure_ascii=False)
bindings['metric_total'] = len(data)
print(f"[n02] 指标行已落盘 → /tmp/metric_wiki/rows.json ({len(data)} 条)")
'''

CODE_FOLDERS = WR_HEAD + '''
import weknora_rpc as wr, os, json
wr.load_config()
wr.mcp_init()
if _kb:
    wr.KB = _kb
rows = json.load(open('/tmp/metric_wiki/rows.json', encoding='utf-8'))
if not rows:
    raise RuntimeError("无指标行，阻断")
# 幂等建两级目录
existing = wr.tool_call('list_wiki_folders', {"kb_id": _kb}) or {}
ext = existing.get('folders') or (existing.get('data') or {}).get('folders') or []
by_name = {f.get('name'): f.get('id') for f in ext if isinstance(f, dict) and f.get('name')}
folders = {}
for meta in rows:
    c1 = meta.get('指标分类-I') or '未分类'
    c2 = meta.get('指标分类-II') or '未分类'
    key = (c1, c2)
    if key in folders:
        continue
    f1 = by_name.get(c1)
    if not f1:
        r1 = wr.tool_call('create_wiki_folder', {"kb_id": _kb, "name": c1, "parent_id": ""})
        rr = r1.get('data') or r1
        f1 = rr.get('id') if isinstance(rr, dict) else None
        by_name[c1] = f1
    r2 = wr.tool_call('list_wiki_folders', {"kb_id": _kb, "parent_id": f1 or ""})
    d2 = r2.get('folders') or (r2.get('data') or {}).get('folders') or []
    sub_by = {f.get('name'): f.get('id') for f in d2 if isinstance(f, dict) and f.get('name')}
    f2 = sub_by.get(c2)
    if not f2:
        r3 = wr.tool_call('create_wiki_folder', {"kb_id": _kb, "name": c2, "parent_id": f1 or ""})
        rr3 = r3.get('data') or r3
        f2 = rr3.get('id') if isinstance(rr3, dict) else None
    folders[key] = (f1, f2)
    print(f"[n03] 目录 {c1}/{c2} → {f2}")
json.dump({f"{a}||{b}": v for (a, b), v in folders.items()},
          open('/tmp/metric_wiki/folders.json', 'w', encoding='utf-8'), ensure_ascii=False)
print(f"[n03] 目录就绪：{len(folders)} 个二级分类")
'''

CODE_ANALYZE = WR_HEAD + '''
import weknora_rpc as wr, os, json, re
wr.load_config()
if _kb:
    wr.KB = _kb
rows = json.load(open('/tmp/metric_wiki/rows.json', encoding='utf-8'))

SQL_PROMPT = """你是一名数据仓库 SQL 专家。下面是一个电信经营分析指标的取数 SQL 脚本及其元信息。
请做严谨分析，输出 JSON（不要任何解释文字、不要 markdown 代码块），字段如下：

1. "calculation_logic": 该 SQL 的计算逻辑，用中文简明分步骤说明（每步一行，"1. xxx" 格式），要说清：取哪张表、怎么过滤、怎么聚合、最终字段怎么算出来的。
2. "filters": 关键过滤条件的业务含义，逐条中文解释（例如 STATIS_YM=调度月份参数 表示取调度传入的月份）。
3. "dimensions": 输出粒度/维度说明（GROUP BY 出来的维度分别是什么）。
4. "caveats": 口径风险与注意点（例如除以2的原因推测、排除10000汇总行、空值处理等），没有就给 []。
5. "plain_summary": 一句话（40字内）说明这个 SQL 算的是什么。

指标名称：{name}
指标编码：{code}
业务口径：{biz}
技术口径：{tech}
指标SQL：{expr}
SQL脚本：
{sql}
"""

def llm_analyze(meta):
    sql = meta.get('SQL脚本') or ''
    prompt = SQL_PROMPT.format(
        name=meta.get('指标名称') or '', code=meta.get('指标编码') or '',
        biz=(meta.get('业务口径') or '')[:800], tech=(meta.get('技术口径') or '')[:400],
        expr=meta.get('指标SQL') or '',
        sql=sql.replace('{', '{{').replace('}', '}}'))
    raw = wr.llm_call([
        {'role': 'system', 'content': '你是数据仓库SQL分析专家，只输出JSON，不要思考过程。'},
        {'role': 'user', 'content': prompt}], max_tokens=3000)
    m = re.search(r'\\{.*\\}', raw, re.S)
    d = json.loads(m.group(0)) if m else {}
    def _fix(v):
        if isinstance(v, list):
            return [_fix(x) for x in v]
        if isinstance(v, str):
            return v.replace('${{', '${')
        return v
    return {k: _fix(v) for k, v in d.items()}

# 断点：已有分析结果直接跳过
done_path = '/tmp/metric_wiki/analysis.json'
done = {}
if os.path.exists(done_path):
    try:
        done = json.load(open(done_path, encoding='utf-8'))
    except Exception:
        done = {}
out = {}
for i, meta in enumerate(rows, 1):
    code = meta.get('指标编码') or ''
    if code in done and done.get(code):
        out[code] = done[code]
        print(f"[n04] {i}/{len(rows)} {code} 复用已有分析")
        continue
    print(f"[n04] {i}/{len(rows)} LLM 分析 {code} {meta.get('指标名称', '')}")
    try:
        out[code] = llm_analyze(meta)
    except Exception as e:
        print(f"  ⚠️ {code} 分析失败: {e}，留空")
        out[code] = {}
    json.dump(out, open(done_path, 'w', encoding='utf-8'), ensure_ascii=False)
print(f"[n04] 分析完成 {len(out)}/{len(rows)} → {done_path}")
'''

CODE_BUILD = WR_HEAD + '''
import os, json

TS = '/srv/kb/worker/builtin_engine/code_rev_src'
if TS not in sys.path:
    sys.path.insert(0, TS)
import metric_wiki_lib as mwl

rows = json.load(open('/tmp/metric_wiki/rows.json', encoding='utf-8'))
analysis = json.load(open('/tmp/metric_wiki/analysis.json', encoding='utf-8'))
folders = json.load(open('/tmp/metric_wiki/folders.json', encoding='utf-8'))

pages = []
for meta in rows:
    c1 = meta.get('指标分类-I') or '未分类'
    c2 = meta.get('指标分类-II') or '未分类'
    _, f2 = folders.get(f"{c1}||{c2}", (None, None))
    if not f2:
        print(f"  ⚠️ 缺目录 {c1}/{c2}，跳过 {meta.get('指标编码')}")
        continue
    code = meta.get('指标编码') or ''
    an = analysis.get(code) or {}
    sql = meta.get('SQL脚本') or ''
    pages.append(mwl.build_page(meta, sql, f2, an))

print(f"[n05] 建页 {len(pages)} 个 …")
import weknora_rpc as wr
wr.load_config()
if _kb:
    wr.KB = _kb
# 分批（每批 50）防请求体过大
created = skipped = 0
for i in range(0, len(pages), 50):
    batch = pages[i:i+50]
    r = wr.tool_call('create_wiki_pages_batch', {"kb_id": _kb, "pages": batch})
    if r.get('error'):
        print(f"  批次 {i//50} 失败: {str(r['error'])[:200]}")
        continue
    d = r.get('data') or {}
    created += int(d.get('created') or 0) + int(d.get('updated') or 0)
    print(f"  批次 {i//50}: {json.dumps(d, ensure_ascii=False)}")
print(f"[n05] 建页完成：created/updated={created}, 总={len(pages)}")
bindings['metric_pages'] = len(pages)
'''

CODE_DONE = '''_kb = bindings.get('kb_id', '')
_doc = bindings.get('doc_id', '')
summary = (
    f"指标wiki构建完成：kb={_kb} doc={_doc}\\\\n"
    f"  指标行={bindings.get('metric_total', 0)} 页={bindings.get('metric_pages', 0)}\\\\n"
    f"  目录={bindings.get('metric_folders', 0)} 个二级分类"
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
                {"variable": "kb_id", "expression": "bindings.get('kb_id', '')"},
                {"variable": "doc_id", "expression": "bindings.get('doc_id', '')"},
                {"variable": "limit", "expression": "str(bindings.get('limit') or '')"},
                {"variable": "only_codes", "expression": "bindings.get('only_codes', '')"},
                {"variable": "cookie", "expression": "bindings.get('x_next_identity', '')"},
            ], ensure_ascii=False),
        }),
        node("n02", "script", "拉取文档+解析指标行", {"code": CODE_DOWNLOAD}),
        node("n03", "script", "门禁+建两级目录", {"code": CODE_FOLDERS}),
        node("n04", "script", "SQL分析(LLM,可断点)", {"code": CODE_ANALYZE}),
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
        "tape_name": "wiki_build_指标wiki",
        "tape_label": "指标表格转指标wiki（编排化）",
        "tape_descr": "指标 xlsx 表格 → 一指标一 wiki 页（两级分类目录 + 关键信息 + SQL 脚本分析）。业务逻辑复用 code_rev_src/metric_wiki_demo.py：文档下载→xlsx 解析→目录幂等建立→逐指标 LLM SQL 分析（断点续跑）→批量建页（同 slug 只增不删）。输入：kb_id / doc_id / limit / only_codes",
        "tape_type": "general",
        "nodes": nodes,
        "edges": edges,
        "exec_params": [
            {"paramName": "kb_id", "paramLabel": "知识库ID", "paramType": "input"},
            {"paramName": "doc_id", "paramLabel": "文档ID（指标表格）", "paramType": "input"},
            {"paramName": "limit", "paramLabel": "指标数上限（0=全部，留空采样覆盖分类）", "paramType": "input", "defaultValue": ""},
            {"paramName": "only_codes", "paramLabel": "只构建指定指标编码（逗号分隔，留空全部）", "paramType": "input", "defaultValue": ""},
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/tape_metric_wiki.json")
    args = ap.parse_args()
    tape = build_tape()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(tape, f, ensure_ascii=False, indent=2)
    print(f"tape payload → {args.out}")
    print(f"nodes={len(tape['nodes'])} edges={len(tape['edges'])} tape_name={tape['tape_name']}")


if __name__ == "__main__":
    main()