#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单篇完整构建驱动（技术规范域）：gen_summary（建目录+摘要）→ build_full（长句/关键词/实体/图谱/索引）。

用法：
    python3 run_one.py <kid> [--kb <kb_id>] [--system <知识库>] [--volume <资料集>] [--version <版本>]
                       [--format docx] [--title <摘要标题>] [--skip-final] [--no-graph] [--dry-run]

规则：
- 未传 --system/--volume/--version 时按文件名自动解析（domain.parse_spec_name）：
    中国移动省级NG2-BASS(v5.0)技术规范逻辑模型资料集.docx
      → (中国移动省级NG2-BASS技术规范, 逻辑模型资料集, v5.0)
    附录一：中国移动省级NG2-BASS(v5.0)技术规范核心元模型规范.docx
      → (中国移动省级NG2-BASS技术规范, 附录一：核心元模型规范, v5.0)
    中国移动企业级省大数据平台技术规范数据模型资料集-已修订.docx
      → (中国移动企业级省大数据平台技术规范, 数据模型资料集, 已修订)
- 目录模型：知识库根 → 资料集 → 版本 → {基础实体/{业务对象,关联规则}, 长句原文, 高频关键词}
"""
import argparse
import json
import os
import re
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import domain
import weknora_rpc as wr


def version_dir(v):
    """版本目录名（用户 2026-10-11 定稿）：v5.0 → v5.0版；已修订 → 已修订版；原版 → 原版。"""
    v = (v or '').strip()
    if not v or v == '原版':
        return '原版'
    if v.endswith('版'):
        return v
    if re.match(r'^v?\d+(\.\d+)*$', v):
        return f'{v}版'
    return f'{v}版'


def run(cmd, timeout=600):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.stdout:
        print(r.stdout[-3000:])
    if r.returncode != 0 and r.stderr:
        print("STDERR:", r.stderr[-2000:])
    return r.returncode


def main():
    ap = argparse.ArgumentParser(description='单篇完整构建驱动（集团技术规范）')
    ap.add_argument('kid', help='文档 knowledge_id')
    ap.add_argument('--kb', default=None)
    ap.add_argument('--system', default=None, help='知识库名（默认按文件名解析）')
    ap.add_argument('--volume', default=None, help='资料集名（默认按文件名解析）')
    ap.add_argument('--version', default=None, help='版本标识（默认按文件名解析）')
    ap.add_argument('--format', default=None, choices=['doc', 'docx', 'pdf', 'xls', 'xlsx'])
    ap.add_argument('--title', default=None, help='摘要页标题（默认用文件名）')
    ap.add_argument('--skip-final', action='store_true',
                    help='跳过批次内 merge/rebuild_links（批次末统一执行，批量构建必带）')
    ap.add_argument('--no-graph', action='store_true', help='不写 Neo4j 图谱')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    wr.load_config()
    kb = args.kb or wr.load_kb()
    wr.KB = kb
    wr.mcp_init()

    # 0. 文档元数据
    d = wr.tool_call('get_document', {"knowledge_id": args.kid})
    meta = d.get('data', d) or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    fname = meta.get('file_name') or meta.get('title') or args.kid
    ftype = meta.get('file_type') or args.format or (fname.split('.')[-1] if '.' in fname else '')
    print(f"文档: {fname} ({ftype})")

    system, volume, version = args.system, args.volume, args.version
    if not (system and volume):
        ps, pv, pver = domain.parse_spec_name(fname)
        system = system or ps
        volume = volume or pv
        version = version or pver or '原版'
    if not system:
        print("ERROR: 无法解析知识库名，请用 --system 显式指定")
        return 1
    if not volume:
        print("ERROR: 无法解析资料集名，请用 --volume 显式指定")
        return 1
    version = version_dir(version or '原版')
    print(f"知识库: {system} | 资料集: {volume} | 版本目录: {version}")

    if args.dry_run:
        print(f"[dry-run] system={system} volume={volume} version={version} format={ftype}")
        return 0

    # 1. gen_summary（建目录+摘要）
    print("[1] gen_summary...")
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, 'gen_summary.py'),
           args.kid, system, volume, version, fname, '--kb', kb]
    if args.format:
        cmd += ['--format', args.format]
    if args.title:
        cmd += ['--title', args.title]
    rc = run(cmd, timeout=900)
    if rc != 0:
        print("gen_summary 失败")
        return rc

    # 2. build_full（长句/关键词/实体/图谱/索引）
    print("[2] build_full...")
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, 'build_full.py'),
           args.kid, system, volume, version, fname, '--kb', kb]
    if args.format:
        cmd += ['--format', args.format]
    if args.skip_final:
        cmd += ['--skip-final']
    if args.no_graph:
        cmd += ['--no-graph']
    rc = run(cmd, timeout=7200)
    if rc != 0:
        print("build_full 失败")
        return rc

    print(f"✅ 构建完成: {fname}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
