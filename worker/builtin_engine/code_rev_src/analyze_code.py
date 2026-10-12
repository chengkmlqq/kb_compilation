#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代码确定性结构解析（AST，不调 LLM）——代码理解层的骨架。

输出每个 .py 的结构画像 JSON：
  - module: 文件名/行数/docstring/imports
  - functions: [{name, args, docstring, start,end, calls, sql_kw, io}]
  - module_level: 顶层常量/路径/配置、外部 API 调用
  - io_files: 代码里出现的文件路径常量（输入/输出契约的线索）
  - externals: 调用的外部服务/库（openai、pandas、sqlparse、sqlglot…）

用法：python3 analyze_code.py <file.py> [-o out.json] [--all <dir> -o out.json]
"""
import argparse
import ast
import json
import os
import re
import sys

SQL_KW = ('select', 'from', 'where', 'group by', 'order by', 'join', 'insert', 'overwrite',
          'partition', 'union', 'left join', 'inner join', 'create table', 'with ')
EXTERNAL_LIBS = ('openai', 'pandas', 'sqlparse', 'sqlglot', 'numpy', 'requests', 'pymysql',
                 'sqlalchemy', 'xlsxwriter', 'openpyxl')


def _attr_chain(node):
    """把 ast.Attribute 链还原成 dotted name（openai.OpenAI(...) → openai.OpenAI）。"""
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return '.'.join(reversed(parts))


def analyze_file(path):
    src = open(path, encoding='utf-8', errors='ignore').read()
    lines = src.splitlines()
    tree = ast.parse(src) if src.strip() else ast.parse('')
    info = {
        'file': os.path.basename(path),
        'lines': len(lines),
        'module_doc': (ast.get_docstring(tree) or '')[:600],
        'imports': [],
        'externals': [],
        'functions': [],
        'module_level': [],
        'io_files': [],
        'sql_keywords': [],
        'llm_calls': [],
    }
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            info['imports'] += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            info['imports'].append(n.module or '')
    info['imports'] = sorted(set(i for i in info['imports'] if i))
    info['externals'] = sorted({l for l in EXTERNAL_LIBS if any(i.startswith(l) for i in info['imports'])})

    # 顶层赋值（路径/配置常量）
    for n in tree.body:
        if isinstance(n, ast.Assign):
            try:
                val = ast.literal_eval(n.value)
            except Exception:
                continue
            names = [t.id for t in n.targets if isinstance(t, ast.Name)]
            if isinstance(val, str) and (('//' in val) or val.endswith(('.xlsx', '.csv', '.sql')) or val.startswith('http')):
                info['io_files'].append({'var': names[0] if names else '', 'value': val[:300]})
            elif isinstance(val, str) and names:
                info['module_level'].append({'name': names[0], 'value': val[:160]})

    # 函数级
    for n in tree.body:
        if not isinstance(n, ast.FunctionDef):
            continue
        calls, sql_hits = set(), []
        for sub in ast.walk(n):
            if isinstance(sub, ast.Call):
                nm = _attr_chain(sub.func)
                if nm:
                    calls.add(nm)
                    if 'chat.completions' in nm or nm.endswith('.create'):
                        info['llm_calls'].append({'func': n.name, 'call': nm, 'line': getattr(sub, 'lineno', 0)})
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                low = sub.value.lower()
                for kw in SQL_KW:
                    if kw in low:
                        sql_hits.append(kw)
        seg = '\n'.join(lines[n.lineno - 1:getattr(n, 'end_lineno', n.lineno)])
        info['functions'].append({
            'name': n.name,
            'args': [a.arg for a in n.args.args],
            'docstring': (ast.get_docstring(n) or '')[:400],
            'start': n.lineno, 'end': getattr(n, 'end_lineno', n.lineno),
            'calls': sorted(c for c in calls if '.' in c or c in
                            ('read_excel', 'to_excel', 'groupby', 'apply', 'format', 'findall', 'sub', 'splitlines'))[:20],
            'sql_keywords': sorted(set(sql_hits)),
            'has_llm': any('chat.completions' in c or c.endswith('.create') for c in calls),
            'code': seg,
        })
    # 代码级 SQL/注释关键词统计
    low_src = re.sub(r'\s+', ' ', src.lower())
    info['sql_keywords'] = sorted({kw.strip() for kw in SQL_KW if kw in low_src})
    info['comments'] = [l.strip() for l in lines if l.strip().startswith('#')][:40]
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('-o', default=None)
    ap.add_argument('--all', action='store_true', help='path 为目录：批量解析所有 .py')
    ns = ap.parse_args()
    if ns.all or os.path.isdir(ns.path):
        out = {}
        for root, _dirs, files in os.walk(ns.path):
            for f in sorted(files):
                if f.endswith('.py'):
                    out[f] = analyze_file(os.path.join(root, f))
    else:
        out = analyze_file(ns.path)
    txt = json.dumps(out, ensure_ascii=False, indent=2)
    if ns.o:
        open(ns.o, 'w', encoding='utf-8').write(txt)
        print(f'写入 {ns.o}（{len(txt)} 字符）')
        if isinstance(out, dict) and 'functions' not in out:
            for k, v in out.items():
                print(f"  {k}: {v['lines']} 行, {len(v['functions'])} 个函数, externals={v['externals']}")
    else:
        print(txt)


if __name__ == '__main__':
    main()
