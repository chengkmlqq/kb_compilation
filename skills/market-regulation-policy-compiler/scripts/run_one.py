#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单法规完整构建驱动（市场监督管理法规域版，2026-09-10 自供管版 run_one.py 域化）

用法：
    python3 run_one.py <kid> [--kb <kb_id>] [--format doc|docx|pdf|xls|xlsx]
                          [--title <摘要标题>] [--dry-run] [--skip-final] [--no-graph]

与供管版的差异（法规库无家族/版本层）：
- 不跑 build_relations.py 血缘解析：法规库一篇法规=一个根目录（法规名，去扩展名），
  根下直接 基础实体/长句原文/高频关键词 三子目录，无版本级目录、无家族索引页。
- 流程：查文档元数据 → gen_summary（建法规根目录+摘要卡片）→ build_full
  （长句/关键词/实体/Neo4j 图谱/合并/链接重建）。
- 传参方式与 agent-gateway 完全兼容：外部 agent 网关触发时通过环境变量
  WEKNORA_KB_ID / WEKNORA_KNOWLEDGE_ID / WEKNORA_DOC_NAME 注入任务上下文，
  wr.load_config()/wr.load_kb() 自动让环境变量优先于 config.yaml 固定 kb_id。
- 进度 trace：gen_summary/build_full 内部按大步骤调用 wr.log_progress()
  向 WeKnora wiki_log_write 写 agent_build_* 进度日志（失败不阻断）。
"""
import json, os, subprocess, sys, time, argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import weknora_rpc as wr


def run(cmd, timeout=600):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.stdout:
        print(r.stdout[-3000:])
    if r.returncode != 0 and r.stderr:
        print("STDERR:", r.stderr[-2000:])
    return r.returncode


def main():
    ap = argparse.ArgumentParser(description='单法规完整构建驱动')
    ap.add_argument('kid', help='文档 knowledge_id')
    ap.add_argument('--kb', default=None)
    ap.add_argument('--format', default=None, choices=['doc', 'docx', 'pdf', 'xls', 'xlsx'])
    ap.add_argument('--title', default=None, help='摘要页标题（默认用文件名）')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--skip-final', action='store_true', help='传 build_full，跳过批次内 merge/rebuild_links（批次末统一执行）')
    ap.add_argument('--no-graph', action='store_true', help='跳过 Neo4j 图谱写入')
    args = ap.parse_args()

    wr.load_config()
    kb = args.kb or wr.KB
    wr.mcp_init()

    # 0. 查文档元数据（agent-gateway 触发时 knowledge_id 也可来自环境变量）
    kid = args.kid
    env_kid = os.environ.get('WEKNORA_KNOWLEDGE_ID', '').strip()
    if not kid and env_kid:
        kid = env_kid
    d = wr.tool_call('get_document', {"knowledge_id": kid})
    meta = d.get('data', d) or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    fname = meta.get('file_name') or meta.get('title') or kid
    env_doc = os.environ.get('WEKNORA_DOC_NAME', '').strip()
    if env_doc and (not fname or fname == kid):
        fname = env_doc
    ftype = meta.get('file_type') or args.format or (fname.split('.')[-1] if '.' in fname else '')
    print(f"文档: {fname} ({ftype})")

    # 法规库：根目录名 = 法规名（去扩展名），无家族/版本层
    family = os.path.splitext(fname)[0]
    version = ''

    if args.dry_run:
        print(f"[dry-run] family={family} format={ftype} kb={kb}")
        return 0

    # 1. gen_summary（建法规根目录 + 摘要卡片）
    print("[1] gen_summary...")
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, 'gen_summary.py'),
           kid, family, version, fname, '--format', ftype, '--kb', kb]
    if args.title:
        cmd += ['--title', args.title]
    with wr.measure('gen_summary'):
        rc = run(cmd, timeout=450)
    if rc != 0:
        print("gen_summary 失败")
        return rc

    # 2. build_full（长句/关键词/实体/图谱/合并/链接）
    print("[2] build_full...")
    with wr.measure('build_full'):
        rc = run([sys.executable, os.path.join(SCRIPT_DIR, 'build_full.py'),
                  kid, family, version, fname, '--format', ftype, '--kb', kb]
                 + (['--skip-final'] if args.skip_final else [])
                 + (['--no-graph'] if args.no_graph else []), timeout=3600)
    if rc != 0:
        print("build_full 失败")
        return rc

    print(f"✅ 构建完成: {fname}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
