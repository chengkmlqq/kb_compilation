#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把转换后的资产 md（+ 原始 docx）上传到 WeKnora 知识库并等待解析。

用法：
  python3 upload_docs.py --kb <kb_id> --manifest <md目录>/manifest.json [--only-docx|--only-md]
                         [--dry-run] [--wait] [--map-out /tmp/kid_map.json]

依赖：WeKnora app REST（默认 http://localhost:8085），鉴权用技能 config.yaml 的 weknora.api_key。
"""
import argparse
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr

APP = 'http://localhost:8085'


def api_key():
    cfg = wr._skill_config() or {}
    key = ((cfg.get('weknora') or {}).get('api_key') or '').strip()
    if not key:
        raise RuntimeError('config.yaml 缺 weknora.api_key')
    return os.path.expandvars(key)


def multipart_upload(kb_id, path, key, file_name=None):
    boundary = '----' + uuid.uuid4().hex
    name = file_name or os.path.basename(path)
    with open(path, 'rb') as f:
        content = f.read()
    ctype = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    body = b''
    body += f'--{boundary}\r\n'.encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode()
    body += f'Content-Type: {ctype}\r\n\r\n'.encode()
    body += content + b'\r\n'
    body += f'--{boundary}\r\n'.encode()
    body += b'Content-Disposition: form-data; name="fileName"\r\n\r\n'
    body += name.encode() + b'\r\n'
    body += f'--{boundary}--\r\n'.encode()
    req = urllib.request.Request(
        f'{APP}/api/v1/knowledge-bases/{kb_id}/knowledge/file', data=body, method='POST',
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}', 'X-API-Key': key})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {'error': e.code, 'body': e.read().decode()[:400]}


def list_docs(kb_id, key, page_size=100):
    out, page = [], 1
    while True:
        req = urllib.request.Request(f'{APP}/api/v1/knowledge-bases/{kb_id}/knowledge?page={page}&page_size={page_size}',
                                     headers={'X-API-Key': key})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            print('列文档失败', e.code, e.read().decode()[:200])
            break
        items = d.get('data') or []
        if isinstance(items, dict):
            items = items.get('items') or items.get('list') or []
        if not items:
            break
        out += items
        if len(items) < page_size:
            break
        page += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kb', required=True)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--only-docx', action='store_true')
    ap.add_argument('--only-md', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--wait', action='store_true')
    ap.add_argument('--map-out', default='/tmp/kid_map.json')
    ns = ap.parse_args()
    key = api_key()
    items = json.load(open(ns.manifest, encoding='utf-8'))
    todo = []
    for it in items:
        if ns.only_docx and it['kind'] != 'doc':
            continue
        if ns.only_md and it['kind'] == 'doc':
            continue
        todo.append(it)
    print(f'待上传 {len(todo)} 个文件 → KB {ns.kb}')
    if ns.dry_run:
        for it in todo[:20]:
            print('  ', it['rel'], '->', it['file'])
        return
    ok = fail = 0
    for it in todo:
        r = multipart_upload(ns.kb, it['upload'], key, it['file'])
        if isinstance(r, dict) and r.get('error'):
            fail += 1
            print(f"  ❌ {it['file']}: {r}")
        else:
            ok += 1
            print(f"  ✓ {it['file']}")
        time.sleep(0.3)
    print(f'上传完成: {ok} 成功 / {fail} 失败')

    # 等待解析并建立 file_name → kid 映射
    deadline = time.time() + (2400 if ns.wait else 60)
    mapping = {}
    while True:
        docs = list_docs(ns.kb, key)
        mapping = {}
        pending = 0
        for d in docs:
            fn = d.get('file_name') or d.get('title') or ''
            st = (d.get('parse_status') or d.get('parse_state') or '').lower()
            mapping[fn] = {'kid': d.get('id'), 'status': st, 'chunk_count': d.get('chunk_count')}
            if st not in ('completed', 'ready', 'failed', 'error'):
                pending += 1
        print(f'  文档 {len(mapping)} 篇，解析中 {pending}')
        if pending == 0 or time.time() > deadline:
            break
        time.sleep(20)
    json.dump(mapping, open(ns.map_out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    bad = {k: v for k, v in mapping.items() if v['status'] not in ('completed', 'ready')}
    print(f'kid 映射 → {ns.map_out}；未完成 {len(bad)}')
    for k, v in list(bad.items())[:10]:
        print('   ⚠️', k, v['status'])


if __name__ == '__main__':
    main()
