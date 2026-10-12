#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""规范分册转 wiki —— 服务编排（Tape）生成器（wiki_build_规范分册）。

链路：文档上传（逻辑模型分册/外部接口分册 docx）→ 编排自动构建「规范/」wiki。
业务逻辑复用 code_rev_src/{parse_spec_docx.py, spec_wiki_lib.py}。

节点 DAG：
  n01 def 输入定义（kb_id / doc_id_lm / doc_id_if / limit / cookie）
  n02 script 拉取两个规范文档并落盘
  n03 script 门禁 + 解析两分册 → spec_lm.json / spec_if.json
  n04 script 建「规范」根目录（幂等）+ 渲染 + 批量建页（静态全量）
  n05 script LLM 解读补位（entity/concept 占位页，可 limit，断点续跑）
  n06 print 汇总

用法：
  python3 scripts/build_spec_tape.py --out /tmp/tape_spec_wiki.json
"""
import argparse
import json

# ─────────────────────────────────────────────────────────────────────
# script 节点头部：注入 kb 版 weknora_rpc / parse_spec_docx / spec_wiki_lib
# ─────────────────────────────────────────────────────────────────────
WR_HEAD = '''import sys, os, json, re, hashlib, subprocess, time
G = globals()
G.update({'sys': sys, 'os': os, 'json': json, 're': re,
          'hashlib': hashlib, 'time': time})
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
G['_doc_lm'] = bindings.get('doc_id_lm', '')
G['_doc_if'] = bindings.get('doc_id_if', '')
G['_limit'] = int(bindings.get('limit') or 0) or 0
G['_root'] = bindings.get('root_folder', '规范')
'''

CODE_DOWNLOAD = WR_HEAD + '''
import urllib.request as _ur
ck = bindings.get('cookie') or os.environ.get('IDENTITY_COOKIE', '')
hdr = {'Content-Type': 'application/json'}
if ck:
    hdr['Cookie'] = f'x-next-identity={ck}'
if not (_doc_lm and _doc_if):
    raise ValueError("doc_id_lm / doc_id_if 缺失：请先在知识库上传两个规范分册（逻辑模型/外部接口）")
os.makedirs('/tmp/spec_vols', exist_ok=True)
def _dl(doc_id, out):
    url = f"http://api-server:8000/api/v1/kbs/{_kb}/documents/{doc_id}/download"
    try:
        raw = _ur.urlopen(_ur.Request(url, headers=hdr), timeout=300).read()
    except Exception as e:
        print(f"[n02] 下载 {doc_id} 失败: {e}")
        raise
    open(out, 'wb').write(raw)
    print(f"[n02] {doc_id} → {out} ({len(raw)} 字节)")
_dl(_doc_lm, '/tmp/spec_vols/lm.docx')
_dl(_doc_if, '/tmp/spec_vols/if.docx')
'''

CODE_PARSE = WR_HEAD + '''
import parse_spec_docx as psd
# 文档名（供 source_refs 展示）：优先从平台文档信息取，回退占位
LM_NAME = '中国移动省级NG2-BASS(v5.0)技术规范逻辑模型分册.docx'
IF_NAME = '中国移动省级NG2-BASS(v5.0)技术规范外部接口分册.docx'
lm = psd.parse('/tmp/spec_vols/lm.docx')
if_ = psd.parse('/tmp/spec_vols/if.docx')
if not lm['records'] and not if_['records']:
    raise RuntimeError("两分册未解析出任何表格记录，阻断")
lm_out = {"volume": "逻辑模型分册", "doc_id": _doc_lm, "file": LM_NAME, **lm}
if_out = {"volume": "外部接口分册", "doc_id": _doc_if, "file": IF_NAME, **if_}
json.dump(lm_out, open('/tmp/spec_vols/spec_lm.json', 'w', encoding='utf-8'), ensure_ascii=False)
json.dump(if_out, open('/tmp/spec_vols/spec_if.json', 'w', encoding='utf-8'), ensure_ascii=False)
print(f"[n03] 解析完成：逻辑模型章节 {len(lm['sections'])} 表 {lm['n_tables']} / 外部接口章节 {len(if_['sections'])} 表 {if_['n_tables']}")
for k, v in lm['records'].items():
    if v:
        print(f"  LM {k}: {len(v)}")
'''

CODE_BUILD = WR_HEAD + '''
import weknora_rpc as wr
wr.load_config()
if _kb:
    wr.KB = _kb
import spec_wiki_lib as swl
# 找/建「规范」根（幂等）
_r = wr.tool_call('list_wiki_folders', {"kb_id": _kb}) or {}
ext = _r.get('folders') or (_r.get('data') or {}).get('folders') or []
root_id = next((f.get('id') for f in ext if isinstance(f, dict) and f.get('name') == _root), None)
if not root_id:
    rr = wr.tool_call('create_wiki_folder', {"kb_id": _kb, "name": _root, "parent_id": ""})
    root_id = ((rr.get('data') or rr).get('id') if isinstance(rr.get('data') or rr, dict) else None)
if not root_id:
    raise RuntimeError(f"无法获取/创建 {_root} 根目录")
print(f"[n04] 根目录「{_root}」= {root_id}")
r = swl.build_from_specs('/tmp/spec_vols/spec_lm.json', '/tmp/spec_vols/spec_if.json',
                         kb_id=_kb, root_folder_id=root_id, page_limit=_limit)
bindings['spec_rendered'] = r['rendered']
bindings['spec_created'] = r['created']
print(f"[n04] 渲染 {r['rendered']} created={r['created']} errors={r['errors']}")
'''

CODE_LLM = WR_HEAD + '''
import weknora_rpc as wr
wr.load_config()
if _kb:
    wr.KB = _kb
import spec_wiki_lib as swl
import urllib.parse as _uq
import urllib.request as _ur

PROMPT = """你是企业数据模型/系统接口规范专家。下面是一个 wiki 页的原文内容（来自中国移动 NG2-BASS v5.0 技术规范）。
请用中文输出 JSON（不要解释文字、不要 markdown 代码块），字段：
1. "meaning": 该{kind}的业务含义与定位，40-80 字，说明它是什么、用在什么场景。
2. "notes": 口径/使用注意点列表，没有给 []。

页面标题：{title}
原文：
{body}"""

def _llm_json(kind, title, body):
    prompt = PROMPT.format(kind=kind, title=title, body=body[:3000])
    raw = wr.llm_call([
        {'role': 'system', 'content': '你是规范解读专家，只输出JSON。'},
        {'role': 'user', 'content': prompt}], max_tokens=1200)
    m = re.search(r'\\{.*\\}', raw, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}

def _put(slug, content):
    url = f"http://api-server:8000/api/v1/kbs/{_kb}/wiki/pages/{_uq.quote(slug, safe='')}"
    ck = bindings.get('cookie') or os.environ.get('IDENTITY_COOKIE', '')
    hdr = {'Content-Type': 'application/json'}
    if ck:
        hdr['Cookie'] = f'x-next-identity={ck}'
    _ur.urlopen(_ur.Request(url, json.dumps({'content': content}).encode(), headers=hdr, method='PUT'), timeout=300).read()

# 重新渲染生成页面清单（与 n04 同源）
swl.PAGES.clear()
lm = json.load(open('/tmp/spec_vols/spec_lm.json', encoding='utf-8'))
ifd = json.load(open('/tmp/spec_vols/spec_if.json', encoding='utf-8'))
swl.build_lm(lm); swl.build_if(ifd); swl.build_extras(lm, ifd)
todo = [p for p in swl.PAGES if p['page_type'] in ('entity', 'concept')
        and '*由 LLM 生成*' in p['content']
        and (p['slug'].startswith('数据模型-') or p['slug'].startswith('外部接口-'))]
if _limit:
    todo = todo[:_limit]
todo = [p for p in todo if not p['slug'].endswith(('总览',)) or True]
done = 0
skip = 0
for i, p in enumerate(todo, 1):
    if '*由 LLM 生成*' not in p['content']:
        skip += 1
        continue
    an = _llm_json('实体' if p['page_type'] == 'entity' else '接口方法/数据对象', p['title'], p['content'])
    if not an.get('meaning'):
        print(f"  \\u26a0 {p['slug']} 解读失败", flush=True)
        continue
    L = []
    for ln in p['content'].split('\\n'):
        if '数据含义分析' in ln:
            L.append(ln); L.append('')
            L.append(f"**业务含义**：{an['meaning']}\\n")
            if an.get('notes'):
                L.append('**注意点**')
                if isinstance(an['notes'], list):
                    L.extend(f"- {x}" for x in an['notes'])
                else:
                    L.append(str(an['notes']))
                L.append('')
        else:
            if ln.strip() == '*由 LLM 生成*':
                continue
            L.append(ln)
    try:
        _put(p['slug'], '\\n'.join(L))
        done += 1
    except Exception as e:
        print(f"  \\u26a0 PUT {p['slug']} 失败: {str(e)[:120]}", flush=True)
    if i % 5 == 0:
        json.dump({'i': i, 'done': done}, open('/tmp/spec_vols/llm_progress.json', 'w'))
        print(f"  {i}/{len(todo)} done={done}", flush=True)
bindings['spec_llm_done'] = done
print(f"[n05] LLM 解读完成：{done}/{len(todo)}（跳过 {skip}）")
'''

CODE_DONE = '''summary = (
    f"规范分册wiki构建完成：kb={bindings.get('kb_id', '')} \\\\n"
    f"  静态页渲染={bindings.get('spec_rendered', 0)} created={bindings.get('spec_created', 0)} \\\\n"
    f"  LLM解读补位={bindings.get('spec_llm_done', 0)} 页"
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
                {"variable": "doc_id_lm", "expression": "bindings.get('doc_id_lm', '')"},
                {"variable": "doc_id_if", "expression": "bindings.get('doc_id_if', '')"},
                {"variable": "limit", "expression": "str(bindings.get('limit') or '')"},
                {"variable": "root_folder", "expression": "bindings.get('root_folder', '规范')"},
                {"variable": "cookie", "expression": "bindings.get('x_next_identity', '')"},
            ], ensure_ascii=False),
        }),
        node("n02", "script", "拉取两个规范文档", {"code": CODE_DOWNLOAD}),
        node("n03", "script", "门禁+解析两分册", {"code": CODE_PARSE}),
        node("n04", "script", "建根目录+静态全量建页", {"code": CODE_BUILD}),
        node("n05", "script", "LLM解读补位(可断点)", {"code": CODE_LLM}),
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
        "tape_name": "wiki_build_规范分册",
        "tape_label": "规范分册转wiki（编排化）",
        "tape_descr": "两个规范 docx（逻辑模型分册/外部接口分册）→ 「规范/」根下内容领域目录 + 一实体一页/一方法一页 + 表格/条文/出处 + LLM 解读补位。业务逻辑复用 code_rev_src/parse_spec_docx.py + spec_wiki_lib.py：下载→解析→建根目录（幂等）→批量建页→LLM补位（断点）。输入：kb_id / doc_id_lm / doc_id_if / limit",
        "tape_type": "general",
        "nodes": nodes,
        "edges": edges,
        "exec_params": [
            {"paramName": "kb_id", "paramLabel": "知识库ID", "paramType": "input"},
            {"paramName": "doc_id_lm", "paramLabel": "文档ID（逻辑模型分册docx）", "paramType": "input"},
            {"paramName": "doc_id_if", "paramLabel": "文档ID（外部接口分册docx）", "paramType": "input"},
            {"paramName": "limit", "paramLabel": "页数上限（0=全部，留空步测用小数）", "paramType": "input", "defaultValue": "0"},
            {"paramName": "root_folder", "paramLabel": "根目录名（默认 规范）", "paramType": "input", "defaultValue": "规范"},
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/tape_spec_wiki.json")
    args = ap.parse_args()
    tape = build_tape()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(tape, f, ensure_ascii=False, indent=2)
    print(f"tape payload → {args.out}")
    print(f"nodes={len(tape['nodes'])} edges={len(tape['edges'])} tape_name={tape['tape_name']}")


if __name__ == "__main__":
    main()