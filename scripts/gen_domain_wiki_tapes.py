#!/usr/bin/env python3
"""按领域生成 wiki 构建编排（2026-10-14 v1）。

参照 wiki_build_监管制度_编排化（18 步全链路），把 7 个领域耦合步骤改为
运行期读本体 Schema（orch_schema.py：类别/专家角色/抽取总则/摘要/术语配置）：
  4 建法规目录树 → 类别来自 schema
  5 摘要生成     → summary_prompt(schema)
  9 关键词LLM过滤 → script 版 term_filter_prompt(schema)
  11 实体+规则联合抽取 / 12 规则补抽 → extract_prompt(schema)
  13 规则实体页 / 14 业务实体页 → 类别映射来自 schema
其余 11 步（切片/门禁/长句/关键词/图谱/合并/链接）与参考编排一致。

每领域一条编排：tape_name=wiki构建-<领域>，category=<一级分类名>，
exec_params.domain 默认该领域；运行入参 kid/doc_name/source_file/kb_id/doc_type。

用法:
  python3 scripts/gen_domain_wiki_tapes.py [--domains 财务与会计,采购与供应链] [--dry-run]
默认全量 20 领域（不含已存在的）。--dry-run 只生成 JSON 不调 API。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("KB_BASE_URL", "http://localhost")
COOKIE = os.environ.get("KB_COOKIE", "")

DOMAINS = [
    "通用办公与综合管理", "制度与企业治理", "财务与会计", "人力资源与组织管理",
    "法务与合同管理", "销售与市场营销", "客户服务与运营", "产品管理",
    "技术研发与软件工程", "IT 运维与信息安全", "项目管理", "生产制造与质量管理",
    "工程建设与设计", "物流与仓储", "战略与经营管理", "研究与知识创新",
    "培训与教育", "医疗健康", "采购与供应链", "行业监管与公共政策",
]

REF_NAME = "wiki_build_监管制度_编排化"

SCHEMA_INJECT = (
    "\n"
    "from orch_schema import load_schema, extract_prompt\n"
    "_sc = load_schema(_kb, bindings.get('domain', ''))\n"
    "BIZ_CAT_SHORT = _sc['biz_short']\n"
    "RULE_CAT_SHORT = _sc['rule_short']\n"
    "RO_NUM_MAP = _sc['rule_no']\n"
)

TERM_FILTER_SCRIPT = """import sys, os, json, re
SD = '/srv/kb/worker/builtin_engine/scripts'
if SD not in sys.path:
    sys.path.insert(0, SD)
os.environ.setdefault('KB_API_BASE_URL', 'http://api-server:8000')
import weknora_rpc as wr
G = globals()
G['wr'] = wr
G['bindings'] = bindings
G['context'] = context
G['config'] = config
wr.load_config()
_kb = bindings.get('kb_id') or os.environ.get('WEKNORA_KB_ID', '')
if _kb:
    wr.KB = _kb
    os.environ['WEKNORA_KB_ID'] = _kb
wr.mcp_init()
_kid = bindings.get('kid', '')
DIR = bindings.get('reg_dir', f"/tmp/reg_{_kid[:8]}")
src = f"{DIR}/terms.json"
out = f"{DIR}/terms_filtered_decisions.json"
if not os.path.exists(src):
    print('[n09] terms.json 不存在，跳过 LLM 过滤')
    raise SystemExit(0)
with open(src, encoding='utf-8') as f:
    items = json.load(f)
from orch_schema import load_schema, term_filter_prompt
_sc = load_schema(_kb, bindings.get('domain', ''))
prompt_tpl = term_filter_prompt(_sc)
sys_role = _sc.get('expert_role') or '你是术语筛选专家，只输出JSON。'
decisions = []
for i in range(0, len(items), 15):
    batch = items[i:i + 15]
    body = '\\n'.join(f"{j + 1}. {it.get('word', '')} — {it.get('context', '')}" for j, it in enumerate(batch))
    try:
        raw = wr.llm_call([{'role': 'system', 'content': sys_role},
                           {'role': 'user', 'content': prompt_tpl.replace('{{items}}', body)}],
                          max_tokens=3000)
        arr = json.loads(wr.ensure_full_json(raw))
        for it in arr:
            if isinstance(it, dict) and (it.get('word') or '').strip():
                decisions.append({'word': it.get('word'), 'keep': bool(it.get('keep')), 'reason': it.get('reason') or ''})
    except Exception as e:
        print(f"[n09] 批 {i // 15 + 1} 失败: {e}")
with open(out, 'w', encoding='utf-8') as f:
    json.dump(decisions, f, ensure_ascii=False, indent=1)
bindings['terms_filtered'] = len(decisions)
print(f"[n09] 术语过滤完成: 判定 {len(decisions)} 条")
"""


LS_LLM_GROUP = """
from orch_schema import load_schema
_sc = load_schema(_kb, bindings.get('domain', ''))
_ROLE = (_sc.get('expert_role') or '').replace('，只输出JSON，不要任何思考过程。', '').replace('只输出JSON，不要任何思考过程。', '') or f"你是{_sc.get('schema_name') or '领域'}知识单元提取专家"

def llm_group(sentences, depth=0):
    numbered = chr(10).join(f"[{i + 1}] {s}" for i, s in enumerate(sentences))
    sysp = (f"{_ROLE}。把句子清单归组为知识单元，输出 JSON 数组："
            '[{"title":"单元标题(≤15字概括)","sentence_ids":[编号列表],"summary":"一段汇总描述(50-100字)"}]。'
            "编号必须来自输入清单；同一句子只能属于一个单元；跳过章节标题/目录/页码/无实义碎片。只输出JSON。")
    items = None
    try:
        raw = wr.llm_call([{"role": "system", "content": sysp},
                           {"role": "user", "content": numbered}],
                          max_tokens=8000, temperature=0.2)
        import json as _j
        raw = wr.ensure_full_json(raw)
        parsed = _j.loads(raw)
        if isinstance(parsed, dict):
            for k in ('result', 'sentences', 'units', 'data'):
                if isinstance(parsed.get(k), list):
                    parsed = parsed[k]
                    break
        if isinstance(parsed, list) and parsed:
            items = parsed
    except Exception as e:
        print(f"  llm_group 失败({len(sentences)}句, depth={depth}): {str(e)[:100]}")
    if items is not None:
        return items
    # 超限/解析失败 → 对半切递归（skill 同款，收敛到 25 句）
    if len(sentences) <= 25 or depth >= 4:
        return []
    mid = len(sentences) // 2
    return llm_group(sentences[:mid], depth + 1) + llm_group(sentences[mid:], depth + 1)

"""


LINK_CLEANUP = """
# ── 断链清理（2026-10-14）：引用不存在页面的 [[slug|label]] 降级为纯文本 label ──
_slugs = set()
_page_no = 1
while True:
    _r = wr.list_wiki_pages(kb_id=wr.KB, page=_page_no, page_size=200)
    _ps = (_r or {}).get('pages') or []
    if not _ps:
        break
    for _p in _ps:
        if _p.get('slug'):
            _slugs.add(_p['slug'])
    if len(_ps) < 200:
        break
    _page_no += 1
_fixed = 0
_page_no = 1
while True:
    _r = wr.list_wiki_pages(kb_id=wr.KB, page=_page_no, page_size=200)
    _ps = (_r or {}).get('pages') or []
    if not _ps:
        break
    for _p in _ps:
        _c = _p.get('content') or ''
        def _rep(_m):
            global _fixed
            _t = _m.group(1).strip()
            _lab = _m.group(2) or _t
            if _t not in _slugs:
                _fixed += 1
                return _lab
            return _m.group(0)
        _nc = re.sub(r'\\[\\[([^\\]|]+)(?:\\|([^\\]]*))?\\]\\]', _rep, _c)
        if _nc != _c:
            try:
                wr.update_page(slug=_p['slug'], kb_id=wr.KB, title=_p.get('title', '') or '',
                               content=_nc, folder_id=_p.get('folder_id') or '',
                               page_type=_p.get('page_type') or '', status=_p.get('status') or 'published',
                               source_refs=_p.get('source_refs') or [])
            except Exception as _e:
                print(f"[n16b] 更新 {_p.get('slug')} 失败: {str(_e)[:80]}")
    if len(_ps) < 200:
        break
    _page_no += 1
print(f"[n16b] 断链清理: 降级 {_fixed} 处链接")
"""


def _req(method: str, path: str, payload=None, timeout: int = 90):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    cookie = os.environ.get("KB_COOKIE", "")
    if cookie:
        headers["Cookie"] = f"x-next-identity={cookie}"
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"success": False, "detail": e.read().decode("utf-8", "replace")[:400]}


def transform_code(label: str, code: str) -> str:
    """把参考编排步骤代码改为 schema 驱动。"""
    if label == "建法规目录树":
        code = code.replace(
            "biz_cats = [\"1-法律法规\", \"2-监管对象\", \"3-许可事项\", \"4-违法行为\", \"5-行政处罚\",\n"
            "            \"6-监管职责\", \"7-标准规范\", \"8-程序事项\", \"9-时限要求\"]",
            "from orch_schema import load_schema\n"
            "_sc = load_schema(_kb, bindings.get('domain', ''))\n"
            "biz_cats = [c['cat_label'] for c in _sc.get('business', [])] or [\"1-制度对象\"]",
        )
        code = code.replace(
            "rule_cats = [\"1-定义规则\", \"2-条件规则\", \"3-约束规则\", \"4-职责规则\", \"5-审批规则\",\n"
            "             \"6-流程规则\", \"7-引用规则\", \"8-例外规则\", \"9-处罚规则\",\n"
            "             \"10-时限规则\", \"11-版本规则\", \"12-程序规则\"]",
            "rule_cats = [c['cat_label'] for c in _sc.get('rule', [])] or [\"1-定义规则\"]",
        )
        return code
    if label == "摘要生成+建页":
        # 整个 f-string prompt 替换为 schema 摘要模板（三重引号 f-string，与参考编排同款写法）
        pat = re.compile(r"prompt = f\"\"\".*?\"\"\"", re.DOTALL)
        repl = (
            "from orch_schema import load_schema, summary_prompt\n"
            "_sc = load_schema(_kb, bindings.get('domain', ''))\n"
            "prompt = f\"\"\"# {family_name}\n\n\"\"\" + summary_prompt(_sc, _src, _doc) + f\"\"\"\n\n"
            "【文档信息】\n- 文件名：{_src or _doc}\n\n【文档原文切片】\n{full_text}\n\"\"\"\n"
        )
        code = pat.sub(repl, code)
        return code
    if label == "关键词LLM过滤":
        return TERM_FILTER_SCRIPT  # 整个节点换成 script 实现
    if label == "长句抽取":
        # LLM 归组加长度韧性：超限对半切递归（原实现 5 次重试全败即步骤失败）+ schema 角色
        pat = re.compile(r"def llm_group\(sentences\):.*?\nsents = split_sentences\(full_text\)", re.DOTALL)
        code = pat.sub(LS_LLM_GROUP + "\nsents = split_sentences(full_text)", code)
        return code
    if label == "合并+链接重建":
        # 断链清理：rebuild_links 前把引用不存在页面的 wikilink 降级为纯文本
        anchor = "try:\n    r = wr.rebuild_links(kb_id=wr.KB)"
        if anchor in code:
            code = code.replace(anchor, LINK_CLEANUP + "\n" + anchor)
        return code
    if label in ("实体+规则联合抽取", "规则补抽兜底", "规则实体页建页", "业务实体页建页"):
        # 注入 schema 加载 + 替换常量映射
        if label in ("实体+规则联合抽取", "规则补抽兜底"):
            code = code.replace(
                "G['_src'] = _src",
                "G['_src'] = _src" + SCHEMA_INJECT,
            )
        else:
            code = code.replace(
                "G['_src'] = _src",
                "G['_src'] = _src\n"
                "from orch_schema import load_schema\n"
                "_sc = load_schema(_kb, bindings.get('domain', ''))\n"
                "BIZ_CAT_SHORT = _sc['biz_short']\n"
                "RULE_CAT_SHORT = _sc['rule_short']\n"
                "RO_NUM_MAP = _sc['rule_no']",
            )
        # 替换常量字面量（步骤 11/12 有 BIZ/RULE/SHORT/RO_NUM；13 有 RULE/RO_NUM；14 有 BIZ/RULE/RO_NUM）
        for name, repl in (
            ("BIZ_CAT_SHORT", "BIZ_CAT_SHORT = _sc['biz_short']"),
            ("RULE_CAT_SHORT", "RULE_CAT_SHORT = _sc['rule_short']"),
            ("RO_NUM_MAP", "RO_NUM_MAP = _sc['rule_no']"),
        ):
            pat = re.compile(rf"{name} = \{{.*?\}}", re.DOTALL)
            code = pat.sub(repl, code)
        # 步骤 11 的 JOINT_PROMPT 长字面量 → extract_prompt(_sc)
        if label == "实体+规则联合抽取":
            pat = re.compile(r"JOINT_PROMPT = \('你是市场监管法规解析专家.*?\ndef canonicalize", re.DOTALL)
            code = pat.sub("JOINT_PROMPT = extract_prompt(_sc)\ndef canonicalize", code)
            # system 角色
            code = code.replace("'你是市场监管法规解析专家，只输出JSON，不要任何思考过程。'",
                                "(_sc['expert_role'])")
        if label == "规则补抽兜底":
            pat = re.compile(r"RULE_PROMPT = \('你是市场监管法规解析专家.*?\nadded = 0", re.DOTALL)
            code = pat.sub("RULE_PROMPT = extract_prompt(_sc)\nadded = 0", code)
            code = code.replace("'你是市场监管法规解析专家，只输出JSON。'", "(_sc['expert_role'])")
        if label == "业务实体页建页":
            # 规则对象程序兜底补建的默认类别 → schema 首个业务类别
            code = code.replace("'category': '监管对象',", "'category': _sc['business'][0]['cat_name'],")
        return code
    return code


def build_tape(draft: dict, domain: str) -> dict:
    """由参考编排草稿生成某领域编排（nodes/edges/exec_params）。"""
    nodes = [copy.deepcopy(n) for n in draft["draft"]["nodes"]]
    edges = [copy.deepcopy(e) for e in draft["draft"]["edges"]]
    for n in nodes:
        data = n["data"]
        label = data.get("label", "")
        inst = data.get("stepInst", "")
        cfg = dict(data.get("config") or {})
        if label == "输入定义":
            try:
                assigns = json.loads(cfg.get("assignments") or "[]")
            except Exception:
                assigns = []
            new_assigns = []
            for a in assigns:
                if a.get("variable") == "kb_id":
                    a = dict(a)
                    a["expression"] = "bindings.get('kb_id', '')"
                new_assigns.append(a)
            new_assigns.append({"variable": "domain", "expression": f"bindings.get('domain', '{domain}')"})
            new_assigns.append({"variable": "doc_type", "expression": "bindings.get('doc_type', '')"})
            cfg["assignments"] = json.dumps(new_assigns, ensure_ascii=False)
        elif inst == "script" and label in ("建法规目录树", "摘要生成+建页", "长句抽取",
                                            "实体+规则联合抽取",
                                            "规则补抽兜底", "规则实体页建页", "业务实体页建页",
                                            "合并+链接重建"):
            cfg["code"] = transform_code(label, cfg.get("code", ""))
        elif label == "关键词LLM过滤":
            cfg = {"queue": "build", "code": TERM_FILTER_SCRIPT}
            data["stepInst"] = "script"
        data["config"] = cfg
        n["data"] = data
    exec_params = [
        {"paramName": "kid", "paramType": "input", "paramLabel": "文档 knowledge_id"},
        {"paramName": "doc_name", "paramType": "input", "paramLabel": "文档名"},
        {"paramName": "source_file", "paramType": "input", "paramLabel": "源文件名"},
        {"paramName": "kb_id", "paramType": "input", "paramLabel": "知识库ID", "defaultValue": ""},
        {"paramName": "doc_type", "paramType": "input", "paramLabel": "文档类型(二级分类，空=自动)", "defaultValue": ""},
        {"paramName": "domain", "paramType": "input", "paramLabel": "领域(本体Schema)", "defaultValue": domain},
        {"paramName": "skip_final", "paramType": "input", "paramLabel": "跳过全库合并+链接(批次末统一)", "defaultValue": "false"},
    ]
    return {"nodes": nodes, "edges": edges, "exec_params": exec_params}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--domains", default="", help="逗号分隔；空=全量")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    domains = [d.strip() for d in args.domains.split(",") if d.strip()] or DOMAINS
    cookie = COOKIE
    if not cookie:
        r = _req("POST", "/api/v1/auth/login", {"userId": "admin", "pwd": "sys"})
        cookie = r.get("identity_cookie") or ""
        if not cookie:
            print("[FAIL] 登录失败:", json.dumps(r, ensure_ascii=False)[:200])
            return
    os.environ["KB_COOKIE"] = cookie

    # 拉参考编排草稿
    tapes = _req("GET", "/api/v1/orchestrations?page=1&page_size=100") or {}
    data = tapes.get("data") or {}
    rows = data.get("items") or data.get("tapes") or []
    if isinstance(rows, dict):
        rows = rows.get("rows") or rows.get("list") or []
    ref_id = ""
    for t in rows:
        if t.get("tape_name") == REF_NAME:
            ref_id = t.get("id")
            break
    if not ref_id:
        print("[FAIL] 找不到参考编排", REF_NAME)
        return
    draft = _req("GET", f"/api/v1/orchestrations/{ref_id}/export")
    if not draft.get("success", True) or not (draft.get("data") or {}).get("content"):
        print("[FAIL] 导出参考编排失败:", json.dumps(draft, ensure_ascii=False)[:200])
        return
    try:
        draft = json.loads(draft["data"]["content"])
    except Exception as e:
        print("[FAIL] 解析参考编排草稿失败:", e)
        return

    # 幂等：跳过已存在
    existing = {t.get("tape_name") for t in rows}
    for domain in domains:
        tape_name = f"wiki构建-{domain}"
        if tape_name in existing:
            print(f"[skip] {tape_name} 已存在")
            continue
        built = build_tape(draft, domain)
        if args.dry_run:
            os.makedirs("/tmp/gen_tapes", exist_ok=True)
            with open(f"/tmp/gen_tapes/{domain}.json", "w", encoding="utf-8") as f:
                json.dump(built, f, ensure_ascii=False, indent=1)
            print(f"[dry-run] {tape_name} → /tmp/gen_tapes/{domain}.json")
            continue
        payload = {
            "tape_name": tape_name,
            "tape_label": f"{domain}文档转wiki（编排化全链路）",
            "tape_descr": (
                f"{domain}领域文档→wiki 全链路服务编排（参照监管制度编排）：切片清洗→门禁→目录树→摘要→长句→"
                f"关键词→实体/规则→图谱→合并→链接。本体类别/抽取/摘要/术语配置运行期读「{domain}」本体Schema，"
                f"文档类型模式读 kb_doc_type_config。输入：kid/doc_name/source_file/kb_id/doc_type。"
            ),
            "tape_type": "general",
            "category": domain,
            "status": "draft",
            "nodes": built["nodes"],
            "edges": built["edges"],
            "exec_params": built["exec_params"],
        }
        r = _req("POST", "/api/v1/orchestrations", payload)
        data = r.get("data", r)
        tape_id = data.get("id") or ""
        if not tape_id:
            print(f"[FAIL] {tape_name} 创建失败: {json.dumps(r, ensure_ascii=False)[:300]}")
            continue
        d = _req("POST", f"/api/v1/orchestrations/{tape_id}/design",
                 {"nodes": built["nodes"], "edges": built["edges"], "exec_params": built["exec_params"]})
        print(f"[ok] {tape_name} created={tape_id} design={bool(d.get('success', True))}")
        p = _req("POST", f"/api/v1/orchestrations/{tape_id}/publish")
        print(f"    publish={bool(p.get('success', True))} {json.dumps(p.get('data', {}))[:100]}")
    print("done")


if __name__ == "__main__":
    main()
