#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""资产离线转换（不依赖平台解析器）：py/xlsx/html → markdown；docx 原样保留。

产物：<out>/ 下的 md 文件 + manifest.json（src / file / label / kind / parts）
每个 xlsx 按 sheet 分片（默认 ≤1200 行/片）——便于 RAG 检索命中而非超长文档。

用法：python3 prepare_assets.py --src <解压后的资产目录> --out ~/桌面/代码反向生成知识_md
      [--analysis /tmp/code_analysis.json]
"""
import argparse
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain
import xlsx_lite

ROWS_PER_PART = 1200


def md_table(rows):
    if not rows:
        return ''
    width = max(len(r) for r in rows)
    norm = [list(r) + [''] * (width - len(r)) for r in rows]
    head = norm[0]
    out = ['| ' + ' | '.join(x.replace('|', '\\|') or ' ' for x in head) + ' |',
           '|' + '---|' * width]
    for r in norm[1:]:
        out.append('| ' + ' | '.join((x or '').replace('|', '\\|').replace('\n', ' ') for x in r) + ' |')
    return '\n'.join(out)


def conv_py(path, out_dir, analysis):
    fname = os.path.basename(path)
    src = open(path, encoding='utf-8', errors='ignore').read()
    info = (analysis or {}).get(fname) or {}
    label = domain.asset_label(fname)
    parts = [f'# {label}（{fname}）', '',
             f'> 智能取数链路的 Python 实现：{len(info.get("functions", []))} 个函数，'
             f'{info.get("lines", len(src.splitlines()))} 行。', '']
    if info.get('module_doc'):
        parts += ['## 脚本说明', '', info['module_doc'], '']
    if info.get('functions'):
        parts += ['## 函数清单', '', '| 函数 | 行号 | 形参 | 说明 |', '|---|---|---|---|']
        for f in info['functions']:
            parts.append(f"| {f['name']} | {f['start']}-{f['end']} | {', '.join(f.get('args') or [])} | "
                         f"{(f.get('docstring') or '').splitlines()[0][:60] if f.get('docstring') else ''} |")
        parts += ['']
    if info.get('io_files'):
        parts += ['## 读写文件（输入/输出契约线索）', '']
        parts += [f"- `{x['var']}` = {x['value'][:160]}" for x in info['io_files']]
        parts += ['']
    parts += ['## 源码', '', '```python', src, '```', '']
    out = os.path.join(out_dir, fname.replace('.py', '') + '.md')
    open(out, 'w', encoding='utf-8').write('\n'.join(parts))
    return out, label


def conv_xlsx(path, out_dir):
    fname = os.path.basename(path)
    label = domain.asset_label(fname)
    sheets = xlsx_lite.read_sheets(path)
    outs = []
    for sh in sheets:
        rows = sh['rows']
        if not rows:
            continue
        head, body = rows[0], rows[1:]
        if len(sheets) == 1 and len(body) <= ROWS_PER_PART:
            parts = [(1, body)]
        else:
            parts = [(i // ROWS_PER_PART + 1, body[i:i + ROWS_PER_PART])
                     for i in range(0, len(body), ROWS_PER_PART)] or [(1, [])]
        for pno, chunk in parts:
            suffix = '' if len(sheets) == 1 and len(parts) == 1 else f"__{sh['name']}__part{pno}"
            out = os.path.join(out_dir, f'{fname[:-5]}{suffix}.md')
            title_suffix = '' if suffix == '' else f'（{sh["name"]} 第 {pno} 部分）'
            text = [f'# {label}{title_suffix}', '',
                    f'> 来源表：{fname} / sheet「{sh["name"]}」，本片 {len(chunk)} 行（共 {len(body)} 行）。', '',
                    md_table([head] + chunk), '']
            open(out, 'w', encoding='utf-8').write('\n'.join(text))
            outs.append(out)
    return outs, label


def conv_html(path, out_dir):
    fname = os.path.basename(path)
    label = domain.asset_label(fname)
    raw = open(path, encoding='utf-8', errors='ignore').read()
    raw = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', '', raw)
    # 表格 → md
    def tbl(m):
        rows = re.findall(r'(?is)<tr[^>]*>(.*?)</tr>', m.group(0))
        out = []
        for r in rows:
            cells = [html.unescape(re.sub('<[^>]+>', '', c)).strip().replace('\n', ' ')
                     for c in re.findall(r'(?is)<t[dh][^>]*>(.*?)</t[dh]>', r)]
            if cells:
                out.append('| ' + ' | '.join(c.replace('|', '\\|') for c in cells) + ' |')
        return '\n'.join(out)
    raw = re.sub(r'(?is)<table.*?</table>', tbl, raw)
    text = re.sub(r'(?is)<br\s*/?>', '\n', raw)
    text = re.sub(r'(?is)</(p|div|h[1-6]|li|tr)>', '\n', text)
    text = re.sub(r'(?s)<[^>]+>', '', text)
    text = html.unescape(text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    out = os.path.join(out_dir, fname.replace('.html', '').replace('.htm', '') + '.md')
    open(out, 'w', encoding='utf-8').write(f'# {label}\n\n> 来源：{fname}（HTML 转换）\n\n{text}\n')
    return out, label


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--analysis', default='/tmp/code_analysis.json')
    ns = ap.parse_args()
    os.makedirs(ns.out, exist_ok=True)
    analysis = {}
    if os.path.exists(ns.analysis):
        analysis = json.load(open(ns.analysis, encoding='utf-8'))
    if isinstance(analysis, dict) and 'functions' in analysis:
        analysis = {}

    manifest = []
    for root, _dirs, files in os.walk(ns.src):
        for f in sorted(files):
            p = os.path.join(root, f)
            kind = domain.asset_kind(f)
            label = domain.asset_label(f)
            try:
                if kind == 'code':
                    out, label = conv_py(p, ns.out, analysis)
                    outs = [out]
                elif f.lower().endswith(('.xlsx', '.xls')):
                    outs, label = conv_xlsx(p, ns.out)
                elif kind == 'html':
                    out, label = conv_html(p, ns.out)
                    outs = [out]
                elif f.lower().endswith(('.docx', '.doc')):
                    outs = []
                    manifest.append({'src': p, 'file': f, 'label': label, 'kind': 'doc',
                                     'upload': p, 'parts': [], 'rel': os.path.relpath(p, ns.src)})
                    continue
                else:
                    continue
                for o in outs:
                    manifest.append({'src': p, 'file': os.path.basename(o), 'label': label,
                                     'kind': kind, 'upload': o,
                                     'rel': os.path.relpath(p, ns.src)})
                print(f'  {f} → {len(outs)} 个 md')
            except Exception as e:
                print(f'  ❌ {f}: {e}')
    json.dump(manifest, open(os.path.join(ns.out, 'manifest.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    print(f'manifest: {len(manifest)} 项 → {os.path.join(ns.out, "manifest.json")}')


if __name__ == '__main__':
    main()
