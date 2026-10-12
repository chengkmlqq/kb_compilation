#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代码理解 → wiki 页（代码反向生成域的核心：真理解，不是切源码）

三层产出（全部带可回溯证据）：
  1) 代码单元页（page_type=original_sentence）：标题=中文语义名，正文=LLM 概览，表格逐字放该函数源码
  2) 规则卡（page_type=rule_ontology）：映射/清洗/生成/口径/约束/版本规则，evidence 指向代码行段
  3) 算子卡（page_type=business_ontology，类别 4-代码/算子）：职责/输入输出契约/处理步骤/
     关键规则/容错/迁移提示（新工单怎么套）/关联样例

用法：
  python3 build_code_wiki.py --kb <kb_id> --kid <文档kid> --file <x.py> [--analysis /tmp/code_analysis.json]
                             [--dry-run] [--no-llm]
"""
import argparse
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain
import weknora_rpc as wr

KB = None
TABLE_HEAD = '| 序号 | 原文 |'

COMPREHEND_PROMPT = """你是中国移动智能取数/代码反向生成知识解析专家。
下面给出一个 Python 脚本的**结构画像**（AST 解析：函数签名/依赖/IO 路径/SQL 关键字）和**源码全文**。

请产出「可复用的知识」，而不是复述代码。对每个函数（含模块级流程）输出：
- title：中文语义标题（≤20字，说清"做什么"，不用函数名直译）
- summary：这段代码做了什么（2-4 句，讲清 输入 → 处理 → 输出）
- steps：处理步骤（有序列表，每条 ≤30 字，用业务语言而非代码语言）
- rules：其中蕴含的**可复用规则**（0-3 条），每条含：
    title（≤20字）、category（映射规则/清洗规则/生成规则/口径规则/约束规则/版本规则）、
    structure（主体/条件/动作/结果 等字段）、
    description（规则内容，讲清"遇到什么情况怎么做"）、
    evidence_lines（源码行号区间，如 "12-30"，必须是本函数范围内的真实行号）
- reuse_hint：新工单/新场景下怎么复用这段（要改哪里、要注意什么）
- pitfalls：容错/边界（未命中、异常、兜底分支等），无则空数组

另外给出文件级：
- operator_title：算子卡标题（≤20字）
- operator_duty：这个脚本在整条"工单→SQL"链路里的职责（2-3 句）
- operator_io：{"input": [...], "output": [...]}
- pipeline_step：它在链路中的位置（如 "第2步：实体名变量化"）
- transferable：这套做法中可迁移到其他取数场景的通用经验（2-4 条）

只输出 JSON（无其他文字）：
{"operator_title":"...","operator_duty":"...","operator_io":{"input":[],"output":[]},
 "pipeline_step":"...","transferable":[],
 "units":[{"name":"函数名或模块级","title":"...","summary":"...","steps":[],"reuse_hint":"...","pitfalls":[],
           "rules":[{"title":"...","category":"生成规则","structure":{},"description":"...","evidence_lines":"1-9"}]}]}
"""


def code_lines(text):
    return [l.rstrip() for l in (text or '').split('\n')]


def render_code_page(title, summary, code):
    rows = '\n'.join(f"| {i} | {l.replace('|', chr(92) + '|')} |" for i, l in enumerate(code_lines(code), 1))
    head = f'# {title}\n\n' + (f'{summary.strip()}\n\n' if summary.strip() else '')
    return f'{head}{TABLE_HEAD}\n|---|---|\n{rows}\n'


def _comprehend_call(file_name, analysis, source, funcs, with_file_meta, only_note=''):
    payload = {
        'file': file_name,
        'module_doc': analysis.get('module_doc', '') if with_file_meta else '',
        'imports': analysis.get('imports', []),
        'externals': analysis.get('externals', []),
        'io_files': analysis.get('io_files', []),
        'module_level': analysis.get('module_level', []) if with_file_meta else [],
        'sql_keywords': analysis.get('sql_keywords', []),
        'functions': [{k: f[k] for k in ('name', 'args', 'docstring', 'start', 'end', 'calls', 'sql_keywords')}
                      for f in funcs],
        'comments': analysis.get('comments', []) if with_file_meta else [],
    }
    user = (f'【结构画像】\n{json.dumps(payload, ensure_ascii=False, indent=1)}\n\n'
            + (only_note + '\n\n' if only_note else '')
            + f'【源码全文（行号见函数 start/end）】\n{source}')
    last = None
    for budget in (8000, 16000, 24000):   # 逐级升档：截断/空内容→加预算重试
        try:
            raw = wr.llm_call([{'role': 'system', 'content': COMPREHEND_PROMPT},
                               {'role': 'user', 'content': user}], max_tokens=budget, temperature=0.2)
        except Exception as e:                       # noqa: BLE001
            last = e
            continue
        _dbg = f'[llm] budget={budget} raw={len(raw or "")}'
        print(_dbg, file=sys.stderr)
        raw = re.sub(r'\\\|', '|', wr.ensure_full_json(raw or ''))
        if not raw.strip():
            continue
        try:
            return json.loads(raw)
        except Exception as e:                       # noqa: BLE001
            last = e
    raise RuntimeError(f'LLM 理解输出无法解析为 JSON（已升档重试）: {last}')


def _comprehend_split(file_name, analysis, source, funcs, with_meta, note, depth=0):
    """失败即拆半重试；到单函数仍失败就放弃该函数（记日志），不让整篇报废。"""
    try:
        return _comprehend_call(file_name, analysis, source, funcs, with_meta, note)
    except Exception as e:                               # noqa: BLE001
        if len(funcs) <= 1 or depth >= 3:
            print(f'[WARN] 函数理解放弃: {[f["name"] for f in funcs]} ({e})', file=sys.stderr)
            return {'units': []}
        mid = len(funcs) // 2
        print(f'[WARN] 拆半重试({depth}): {[f["name"] for f in funcs]}', file=sys.stderr)
        d1 = _comprehend_split(file_name, analysis, source, funcs[:mid], with_meta, note, depth + 1)
        d2 = _comprehend_split(file_name, analysis, source, funcs[mid:], with_meta, note, depth + 1)
        d1.setdefault('units', []).extend(d2.get('units') or [])
        return d1


def llm_comprehend(file_name, analysis, source, batch_size=3):
    """大脚本分批理解：函数按 batch_size 分组分别调用，避免一次输出过长被截断。"""
    fns = analysis.get('functions', []) or []
    if len(fns) <= batch_size + 1:
        return _comprehend_call(file_name, analysis, source, fns, True)
    merged = {}
    for i in range(0, len(fns), batch_size):
        chunk = fns[i:i + batch_size]
        note = (f'【本次只需处理以下 {len(chunk)} 个函数（其余函数由后续批次处理，'
                f'不要重复输出）】：' + '、'.join(f['name'] for f in chunk))
        d = _comprehend_split(file_name, analysis, source, chunk, i == 0, note)
        if not merged:
            merged = {k: v for k, v in d.items() if k != 'units'}
        merged.setdefault('units', []).extend(d.get('units') or [])
    return merged


def main():
    global KB
    ap = argparse.ArgumentParser()
    ap.add_argument('--kb', default=None)
    ap.add_argument('--kid', required=True)
    ap.add_argument('--file', required=True, help='.py 源文件路径')
    ap.add_argument('--analysis', default='/tmp/code_analysis.json')
    ap.add_argument('--source-file', default=None, help='入库时的文档名（默认取文件名）')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-llm', action='store_true')
    ns = ap.parse_args()
    KB = ns.kb or wr.load_kb()
    wr.load_config(); wr.KB = KB; wr.mcp_init()

    fname = os.path.basename(ns.file)
    source_file = ns.source_file or fname
    source = open(ns.file, encoding='utf-8', errors='ignore').read()
    analysis_all = json.load(open(ns.analysis, encoding='utf-8')) if os.path.exists(ns.analysis) else {}
    analysis = analysis_all.get(fname) if isinstance(analysis_all, dict) and 'functions' not in analysis_all else analysis_all
    analysis = analysis or {'functions': [], 'imports': [], 'externals': [], 'module_doc': ''}

    kid_prefix = hashlib.md5(ns.kid.encode()).hexdigest()[:10]
    sref = f'{ns.kid}|{source_file}'
    cat_ids = json.load(open(f'/tmp/catids_{ns.kid[:8]}.json', encoding='utf-8')) \
        if os.path.exists(f'/tmp/catids_{ns.kid[:8]}.json') else {'biz': {}, 'rule': {}}

    if ns.no_llm:
        return
    data = llm_comprehend(fname, analysis, source)
    units = data.get('units') or []
    print(f"{fname}: 算子「{data.get('operator_title')}」 units={len(units)}")

    # --- 1) 代码单元页 + 规则卡 ---
    unit_pages, rule_rows = [], []
    lines = code_lines(source)
    for u in units:
        name = (u.get('name') or '').strip()
        fn = next((f for f in analysis.get('functions', []) if f['name'] == name), None)
        code = fn['code'] if fn else source
        code = '\n'.join(code.split('\n')[:200])
        slug = f"longsentence/{kid_prefix}-{hashlib.md5(code.encode()).hexdigest()[:8]}"
        title = (u.get('title') or name)[:60]
        summary = u.get('summary') or ''
        steps = u.get('steps') or []
        reuse = u.get('reuse_hint') or ''
        pitfalls = u.get('pitfalls') or []
        body = summary
        if steps:
            body += '\n\n**处理步骤**：' + '；'.join(f'{i}. {s}' for i, s in enumerate(steps, 1))
        if reuse:
            body += f'\n\n**复用提示**：{reuse}'
        if pitfalls:
            body += '\n\n**容错/边界**：' + '；'.join(pitfalls)
        content = render_code_page(title, body, code)
        if not ns.dry_run:
            r = wr.read_page(slug, kb_id=KB)
            exists = not (isinstance(r, dict) and 'error' in r)
            if exists:
                wr.update_page(slug, kb_id=KB, title=title, content=content,
                               folder_id=r.get('folder_id'), folder_ids=r.get('folder_ids'),
                               page_type='original_sentence', source_refs=[sref],
                               status=r.get('status') or 'published')
            else:
                wr.create_page(slug=slug, title=title, content=content,
                               folder_id=cat_ids.get('ls') or '',
                               kb_id=KB, page_type='original_sentence', source_refs=[sref])
                wr.update_page(slug, kb_id=KB, title=title, content=content,
                               folder_id=cat_ids.get('ls') or '', page_type='original_sentence',
                               source_refs=[sref], status='published')
        unit_pages.append({'name': name, 'title': title, 'slug': slug})

        for r_ in (u.get('rules') or []):
            cat = (r_.get('category') or '').strip()
            if cat not in domain.RULE_CAT_SHORT:
                continue
            evidence = ''
            ev_lines = str(r_.get('evidence_lines') or '')
            m = re.match(r'^\s*(\d+)\s*[-–~]\s*(\d+)', ev_lines)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                evidence = '\n'.join(lines[max(0, a - 1):b])[:1200]
            rule_rows.append({'title': r_.get('title'), 'category': cat, 'structure': r_.get('structure') or {},
                              'description': r_.get('description') or '', 'evidence': evidence,
                              'ls_slug': slug, 'ls_title': title})

    # --- 2) 算子卡 ---
    op_title = data.get('operator_title') or os.path.splitext(fname)[0]
    op_slug = f"entity-b-{hashlib.md5(('代码/算子|' + op_title).encode()).hexdigest()[:10]}"
    io = data.get('operator_io') or {}
    op_rows = [
        ('算子文件', fname),
        ('链路位置', data.get('pipeline_step') or ''),
        ('输入', '；'.join(io.get('input') or []) or '—'),
        ('输出', '；'.join(io.get('output') or []) or '—'),
        ('依赖', '；'.join(analysis.get('externals') or []) or '—'),
        ('来源文档', wr.bookname(source_file)),
    ]
    op_content = [f'# {op_title}', '', f"**业务对象**　4-代码/算子", '', '## 基本信息', '',
                  '| 字段 | 内容 |', '|---|---|']
    op_content += [f'| {k} | {v} |' for k, v in op_rows if v]
    op_content += ['', '## 职责', '', data.get('operator_duty') or '—', '',
                   '## 处理单元', '', '| 单元 | 代码单元页 |', '|---|---|']
    op_content += [f"| {u['title']} | [[{u['slug']}|{u['title']}]] |" for u in unit_pages]
    op_content += ['', '## 可迁移经验', '']
    op_content += [f"- {t}" for t in (data.get('transferable') or [])] or ['- —']
    op_content += ['', '## 关联规则', '', '| 规则 | 类别 | 内容 |', '|---|---|---|']
    for r_ in rule_rows[:20]:
        op_content.append(f"| {r_['title']} | {r_['category']} | {r_['description'][:60]} |")
    op_content += ['', '## 原文关联', '', f'**{wr.bookname(source_file)}**', '']
    op_content += [f"- [[{u['slug']}|{u['title']}]]" for u in unit_pages]
    op_text = '\n'.join(op_content) + '\n'
    if not ns.dry_run:
        r = wr.read_page(op_slug, kb_id=KB)
        exists = not (isinstance(r, dict) and 'error' in r)
        fid = (r.get('folder_id') if exists else '') or cat_ids.get('biz', {}).get('4-代码/算子') or ''
        if exists:
            wr.update_page(op_slug, kb_id=KB, title=op_title, content=op_text, folder_id=fid,
                           folder_ids=r.get('folder_ids'), page_type='business_ontology',
                           source_refs=[sref], status=r.get('status') or 'published')
        else:
            wr.create_page(slug=op_slug, title=op_title, content=op_text, folder_id=fid, kb_id=KB,
                           page_type='business_ontology', source_refs=[sref])
            wr.update_page(op_slug, kb_id=KB, title=op_title, content=op_text, folder_id=fid,
                           page_type='business_ontology', source_refs=[sref], status='published')
    print(f"  算子卡 {op_slug}「{op_title}」；代码单元 {len(unit_pages)}；规则 {len(rule_rows)}")

    # --- 3) 规则卡 ---
    for i, r_ in enumerate(rule_rows, 1):
        cat = r_['category']
        ro = domain.RO_NUM_MAP.get(cat, 0)
        t = (r_['title'] or '')[:30]
        rslug = f"entity-r-ro{ro:03d}-{hashlib.md5(t.encode()).hexdigest()[:10]}"
        struct = r_['structure'] or {}
        content = [f'# {t}', '', f"**关联规则**　{domain.RULE_CAT_SHORT[cat]}", '',
                   '## 基本信息', '', '| 字段 | 内容 |', '|---|---|',
                   f"| 规则类别 | {cat} |",
                   f"| 约束对象 | [[{op_slug}|{op_title}]] |",
                   f"| 来源文档 | {wr.bookname(source_file)} |", '',
                   '## 规则结构', '', '| 字段 | 内容 |', '|---|---|']
        content += [f'| {k} | {str(v)[:120]} |' for k, v in struct.items()] or ['| — | — |']
        content += ['', '## 规则说明', '', r_['description'] or '—', '', '## 原文关联', '',
                    f'**{wr.bookname(source_file)}**', '', f"- [[{r_['ls_slug']}|{r_['ls_title']}]]"]
        if r_['evidence']:
            content += ['', '**代码证据（逐字）**：', '```', r_['evidence'][:800], '```']
        if ns.dry_run:
            continue
        rr = wr.read_page(rslug, kb_id=KB)
        exists = not (isinstance(rr, dict) and 'error' in rr)
        fid = (rr.get('folder_id') if exists else '') or cat_ids.get('rule', {}).get(domain.RULE_CAT_SHORT[cat]) or ''
        if exists:
            wr.update_page(rslug, kb_id=KB, title=t, content='\n'.join(content), folder_id=fid,
                           folder_ids=rr.get('folder_ids'), page_type='rule_ontology',
                           source_refs=[sref], status=rr.get('status') or 'published')
        else:
            wr.create_page(slug=rslug, title=t, content='\n'.join(content), folder_id=fid, kb_id=KB,
                           page_type='rule_ontology', source_refs=[sref])
            wr.update_page(rslug, kb_id=KB, title=t, content='\n'.join(content), folder_id=fid,
                           page_type='rule_ontology', source_refs=[sref], status='published')


if __name__ == '__main__':
    main()
