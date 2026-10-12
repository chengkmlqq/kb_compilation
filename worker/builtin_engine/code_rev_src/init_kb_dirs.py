#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""初始化「代码反向生成」知识库的平铺目录结构（幂等）。

结构（一个库 = 一套共享目录，无版本层）：
  代码反向生成/
  ├─ 基础实体/{业务本体(10 类), 规则本体(6 类)}
  ├─ 长句原文/
  ├─ 高频关键词/
  ├─ 资产摘要/                ← 每篇源文档一张资产卡
  └─ 结构化知识/{指标库, 表映射库, 取数样例库}

用法：python3 init_kb_dirs.py [--kb <kb_id>] [--dry-run]
输出：/tmp/catids_<kb8>.json（biz/rule/ls/kw/summary/metric/table/sample）
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain
import weknora_rpc as wr

KB = None


def ensure_folder(name, parent_id):
    r = wr.list_folders(kb_id=KB, parent_id=parent_id)
    for f in r.get('folders', []):
        if f['name'] == name:
            return f['id']
    r = wr.create_folder(name, parent_id=parent_id, kb_id=KB)
    fid = r.get('folder', {}).get('id', r.get('id', ''))
    print(f'  + {name} → {fid}')
    return fid


def main():
    global KB
    ap = argparse.ArgumentParser()
    ap.add_argument('--kb', default=None)
    ap.add_argument('--dry-run', action='store_true')
    ns = ap.parse_args()
    KB = ns.kb or wr.load_kb()
    wr.load_config(); wr.KB = KB; wr.mcp_init()

    root = None
    for f in wr.list_folders(kb_id=KB, parent_id='').get('folders', []):
        if f['name'] == domain.KB_ROOT:
            root = f['id']
    if root is None:
        root = ensure_folder(domain.KB_ROOT, '')
    print(f'库根 {domain.KB_ROOT}: {root}')

    base = ensure_folder('基础实体', root)
    leaf = {'ls': ensure_folder('长句原文', root),
            'kw': ensure_folder('高频关键词', root),
            'summary': ensure_folder(domain.SUMMARY_DIR, root)}
    struct = ensure_folder(domain.STRUCT_DIR, root)
    leaf['metric'] = ensure_folder(domain.METRIC_DIR, struct)
    leaf['table'] = ensure_folder(domain.TABLE_DIR, struct)
    leaf['sample'] = ensure_folder(domain.SAMPLE_DIR, struct)

    cat_ids = {'biz': {}, 'rule': {}, 'biz_short': domain.BIZ_CAT_SHORT,
               'rule_short': domain.RULE_CAT_SHORT, **leaf}
    for dname, key, cats in ((domain.BIZ_DIR, 'biz', domain.BIZ_CATS),
                             (domain.RULE_DIR, 'rule', domain.RULE_CATS)):
        onto = ensure_folder(dname, base)
        for c in cats:
            cat_ids[key][c] = ensure_folder(c, onto)

    out = f'/tmp/catids_{KB[:8]}.json'
    json.dump(cat_ids, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'目录就绪；catids → {out}')
    print(json.dumps({k: v for k, v in cat_ids.items() if not isinstance(v, dict) or k in ('ls', 'kw', 'summary', 'metric', 'table', 'sample')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
