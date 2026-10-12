#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""极简 xlsx 读取（只依赖标准库：zipfile + xml）。零第三方依赖，避免 bytes/file-like 解析坑。

用法：
    from xlsx_lite import read_sheets
    sheets = read_sheets('a.xlsx')      # [{'name': 'Sheet1', 'rows': [[cell,...], ...]}]
"""
import re
import zipfile
from xml.etree import ElementTree as ET

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
NS_R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


def _shared_strings(z):
    out = []
    if 'xl/sharedStrings.xml' not in z.namelist():
        return out
    root = ET.fromstring(z.read('xl/sharedStrings.xml'))
    for si in root.findall(f'{NS}si'):
        parts = [t.text or '' for t in si.iter(f'{NS}t')]
        out.append(''.join(parts))
    return out


def _col_index(ref):
    m = re.match(r'([A-Z]+)', ref or '')
    if not m:
        return 0
    idx = 0
    for ch in m.group(1):
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


def read_sheets(path, max_rows=None):
    z = zipfile.ZipFile(path)
    shared = _shared_strings(z)
    wb = ET.fromstring(z.read('xl/workbook.xml'))
    rels = {}
    if 'xl/_rels/workbook.xml.rels' in z.namelist():
        rr = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        for rel in rr:
            rels[rel.get('Id')] = rel.get('Target')
    sheets = []
    for sh in wb.iter(f'{NS}sheet'):
        name = sh.get('name')
        rid = sh.get(f'{NS_R}id')
        target = rels.get(rid, '')
        if not target:
            continue
        target = 'xl/' + target.lstrip('/').replace('xl/', '', 1) if not target.startswith('xl/') else target
        if target not in z.namelist():
            continue
        root = ET.fromstring(z.read(target))
        rows = []
        for row in root.iter(f'{NS}row'):
            cells = {}
            for c in row.iter(f'{NS}c'):
                ci = _col_index(c.get('r'))
                t = c.get('t')
                v = c.find(f'{NS}v')
                is_node = c.find(f'{NS}is')
                if t == 's' and v is not None:
                    val = shared[int(v.text)] if v.text and int(v.text) < len(shared) else ''
                elif t == 'inlineStr' and is_node is not None:
                    val = ''.join(x.text or '' for x in is_node.iter(f'{NS}t'))
                else:
                    val = (v.text if v is not None else '') or ''
                cells[ci] = (val or '').strip()
            if cells:
                width = max(cells) + 1
                rows.append([cells.get(i, '') for i in range(width)])
            if max_rows and len(rows) >= max_rows:
                break
        sheets.append({'name': name, 'rows': rows})
    return sheets


if __name__ == '__main__':
    import sys
    for s in read_sheets(sys.argv[1], max_rows=int(sys.argv[2]) if len(sys.argv) > 2 else 3):
        print(f"== {s['name']} ({len(s['rows'])} 行)")
        for r in s['rows'][:3]:
            print('   ', ' | '.join(x[:22] for x in r[:10]))
