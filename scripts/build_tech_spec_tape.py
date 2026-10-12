#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""集团技术规范文档转wiki —— 服务编排（Tape）生成器。

把 tech-spec-wiki-compiler 技能的完整构建能力（目录树/摘要/长句/关键词/实体/
规范要求/图谱/索引/合并链接）实现为服务编排节点。业务逻辑**复用技能脚本**
（镜像内 /srv/kb/worker/builtin_engine/tech_spec_src/，subprocess 调 CLI =
技能代码零改动），编排节点负责流程编排、参数派生与门禁；技能脚本内部经
kb 版 weknora_rpc（同目录）连 kb_compilation api-server。

节点 DAG（单文档构建，输入：kid / kb_id / system / volume / version）：
  n01 def 输入定义
  n02 script 初始化+文档元数据+规范名解析
  n03 script 门禁（chunks>0）
  n04 script 建目录树+摘要页（gen_summary.py CLI）
  n05 script 长句/关键词/实体/规范要求/图谱/索引（build_full.py CLI）
  n06 print 汇总

用法：
  python3 scripts/build_tech_spec_tape.py --out /tmp/tape_tech_spec.json
"""

import argparse
import json

# ─────────────────────────────────────────────────────────────────────
# script 节点头部：注入 kb 版 weknora_rpc（技能脚本同目录自带）
# ─────────────────────────────────────────────────────────────────────
WR_HEAD = '''import sys, os, json, re, hashlib, subprocess
G = globals()
G.update({'sys': sys, 'os': os, 'json': json, 're': re,
          'hashlib': hashlib, 'time': __import__('time')})
SD = '/srv/kb/worker/builtin_engine/scripts'
TS = '/srv/kb/worker/builtin_engine/tech_spec_src'
for _p in (SD, TS):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault('KB_API_BASE_URL', 'http://api-server:8000')
_kb = bindings.get('kb_id') or os.environ.get('WEKNORA_KB_ID', '')
if _kb:
    os.environ['WEKNORA_KB_ID'] = _kb
G['_kid'] = bindings.get('kid', '')
G['_kb'] = _kb
'''

CODE_INIT = WR_HEAD + '''
import weknora_rpc as wr
wr.load_config()
if _kb:
    wr.KB = _kb
wr.mcp_init()
import domain
# 文档元数据
r = wr.tool_call('get_document', {"knowledge_id": _kid})
d = (r or {}).get('data') or {}
if isinstance(d, str):
    try:
        d = json.loads(d)
    except Exception:
        d = {}
fname = d.get('file_name') or d.get('title') or bindings.get('source_file', '') or _kid
print(f"[n02] 文档: {fname}")

# 规范体系/分册/版本：显式传入 > 按文件名自动解析
system = str(bindings.get('system') or '').strip()
volume = str(bindings.get('volume') or '').strip()
version = str(bindings.get('version') or '').strip()
if not (system and volume):
    ps, pv, pver = domain.parse_spec_name(fname)
    system = system or ps
    volume = volume or pv
    version = version or pver or '原版'
    # 兜底解析（技能 parse_spec_name 预设只认「技术规范」格式；库里大量
    # 「…测试规范…分册」「…直投规范-…分册」文件名需兜底）：
    # 1) 剥离日期前缀(20201215-)与版本(V1.2/(V1.2)/-V1.18/（v1.0.2）)
    # 2) 含「分册」→ 体系=<...规范>，分册=体系后到「分册」的剩余
    # 3) 无分册 → 体系=整串，分册用「总册」兜底
    if not volume and '分册' in fname:
        stem = re.sub(r'\.(docx|doc|pdf|xlsx|xls)$', '', fname, flags=re.I)
        stem = re.sub(r'^\d{8}\s*[-—－]?\s*', '', stem)  # 日期前缀
        mver = re.search(r'[（(]\s*[vV](\d+(?:\.\d+)*)[）)]|[-—－]\s*[vV](\d+(?:\.\d+)*)\s*$|[vV](\d+(?:\.\d+)*)\s*$', stem)
        if mver:
            # 无条件重算 version：第 72 行 `or '原版'` 已把默认塞进来，or 会失效
            version = 'v' + (mver.group(1) or mver.group(2) or mver.group(3))
            stem = re.sub(r'[（(]\s*[vV]\d+(?:\.\d+)*[）)]|[-—－]\s*[vV]\d+(?:\.\d+)*\s*$|[vV]\d+(?:\.\d+)*\s*$', '', stem).rstrip('[-—－：: 　] ')
        # 体系 = 到「规范」结尾的词组（技术规范/测试规范/直投规范/管理规范…）
        msys = re.match(r'^(.*?[技测直管开发数据].{0,8}?规范)', stem)
        if msys and msys.end() < len(stem):
            # 兜底分支内无条件重算 system（domain.parse_spec_name 对非「技术规范」
            # 前缀文件名会返回整串当体系，含日期/版本尾巴——直接覆盖）
            system = msys.group(1).strip('[-—－：: 　]')
            volume = stem[msys.end():].strip('[-—－：: 　]')
            mf = re.match(r'^(.*?分册)', volume)
            if mf:
                volume = mf.group(1)
        if not volume:
            volume = stem.strip('[-—－：: 　]')
    if not volume:
        # 无分册的文档（如「中国移动大数据流数据开发技术规范(V1.2)」）→ 总册兜底
        volume = '总册'
        if not system:
            stem2 = re.sub(r'\.(docx|doc|pdf|xlsx|xls)$', '', fname, flags=re.I)
            stem2 = re.sub(r'^\d{8}\s*[-—－]?\s*', '', stem2)
            msys2 = re.search(r'^(.*?[技测直管开发数据].{0,8}?规范)', stem2)
            if msys2:
                system = msys2.group(1).strip('[-—－：: 　]')
if not system:
    raise ValueError("无法解析规范体系名，请显式传 system")
if not volume:
    raise ValueError("无法解析分册名，请显式传 volume")
ver_dir = (version or '').strip()
if not ver_dir or ver_dir == '原版':
    ver_dir = '原版'
elif not ver_dir.endswith('版'):
    ver_dir = ver_dir + '版'
bindings['system'] = system
bindings['volume'] = volume
bindings['version_dir'] = ver_dir
bindings['source_file'] = fname
print(f"[n02] 体系: {system} | 分册: {volume} | 版本目录: {ver_dir}")
'''

CODE_GATE = WR_HEAD + '''
import weknora_rpc as wr
wr.load_config()
if _kb:
    wr.KB = _kb
wr.mcp_init()
_sys = bindings.get('system', '')
_vol = bindings.get('volume', '')
if not (_kid and _sys and _vol and _kb):
    raise ValueError(f"参数缺失: kid={_kid} system={_sys} volume={_vol} kb={_kb}")
r = wr.list_chunks(_kid, kb_id=wr.KB, page=1, page_size=5)
data = (r or {}).get('data') or []
if not data:
    raise RuntimeError("切片缺失（chunks=0），阻断构建")
print(f"[n03] 门禁通过：切片可用")
'''

CODE_SUMMARY = WR_HEAD + '''
# gen_summary.py CLI：建目录树 + 摘要页（技能原逻辑，零改动）
cmd = [sys.executable, os.path.join(TS, 'gen_summary.py'),
       _kid, bindings.get('system', ''), bindings.get('volume', ''),
       bindings.get('version_dir', '原版'), bindings.get('source_file', ''),
       '--kb', _kb]
print("[n04] gen_summary:", ' '.join(cmd))
r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
if r.stdout:
    print(r.stdout[-3000:])
if r.returncode != 0:
    if r.stderr:
        print("STDERR:", r.stderr[-2000:])
    raise RuntimeError(f"gen_summary 失败 rc={r.returncode}")
md5_kid = hashlib.md5(_kid.encode()).hexdigest()
bindings['summary_slug'] = f"summary/{md5_kid}"
print(f"[n04] 目录树+摘要页完成: {bindings['summary_slug']}")
'''

CODE_BUILD_FULL = WR_HEAD + '''
# build_full.py CLI：长句/关键词/实体/规范要求/图谱/分册索引（技能原逻辑）
cmd = [sys.executable, os.path.join(TS, 'build_full.py'),
       _kid, bindings.get('system', ''), bindings.get('volume', ''),
       bindings.get('version_dir', '原版'), bindings.get('source_file', ''),
       '--kb', _kb]
print("[n05] build_full:", ' '.join(cmd))
r = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
if r.stdout:
    print(r.stdout[-4000:])
if r.returncode != 0:
    if r.stderr:
        print("STDERR:", r.stderr[-2000:])
    raise RuntimeError(f"build_full 失败 rc={r.returncode}")
print("[n05] 长句/关键词/实体/规范要求/图谱/索引构建完成")
'''

CODE_DONE = '''_kid = bindings.get('kid', '')
summary = (
    f"集团技术规范构建完成: kid={_kid}\\n"
    f"  体系={bindings.get('system', '')} 分册={bindings.get('volume', '')} "
    f"版本={bindings.get('version_dir', '')}\\n"
    f"  摘要页={bindings.get('summary_slug', '')}"
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
                {"variable": "kid", "expression": "bindings.get('kid', '')"},
                {"variable": "kb_id", "expression": "bindings.get('kb_id', '')"},
                {"variable": "system", "expression": "bindings.get('system', '')"},
                {"variable": "volume", "expression": "bindings.get('volume', '')"},
                {"variable": "version", "expression": "bindings.get('version', '')"},
                {"variable": "source_file", "expression": "bindings.get('source_file', '')"},
            ], ensure_ascii=False),
        }),
        node("n02", "script", "初始化+规范解析", {"code": CODE_INIT}),
        node("n03", "script", "切片门禁", {"code": CODE_GATE}),
        node("n04", "script", "建目录树+摘要页", {"code": CODE_SUMMARY}),
        node("n05", "script", "长句/关键词/实体/图谱/索引", {"code": CODE_BUILD_FULL}),
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
        "tape_name": "wiki_build_集团技术规范",
        "tape_label": "集团技术规范文档转wiki（编排化）",
        "tape_descr": "tech-spec-wiki-compiler 技能全链路编排化：目录树（体系根→分册→版本→基础实体/长句/关键词）→摘要→长句→关键词→知识对象/规范要求→Neo4j图谱→分册索引→合并链接。业务逻辑复用镜像内技能脚本，编排节点负责流程。输入：kid / kb_id / system / volume / version",
        "tape_type": "general",
        "nodes": nodes,
        "edges": edges,
        "exec_params": [
            {"paramName": "kid", "paramLabel": "文档ID", "paramType": "input"},
            {"paramName": "kb_id", "paramLabel": "知识库ID", "paramType": "input"},
            {"paramName": "system", "paramLabel": "规范体系（留空自动解析）", "paramType": "input", "defaultValue": ""},
            {"paramName": "volume", "paramLabel": "分册（留空自动解析）", "paramType": "input", "defaultValue": ""},
            {"paramName": "version", "paramLabel": "版本（留空自动解析）", "paramType": "input", "defaultValue": ""},
            {"paramName": "source_file", "paramLabel": "源文件名（留空自动取）", "paramType": "input", "defaultValue": ""},
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/tape_tech_spec.json")
    args = ap.parse_args()
    tape = build_tape()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(tape, f, ensure_ascii=False, indent=2)
    print(f"tape payload → {args.out}")
    print(f"nodes={len(tape['nodes'])} edges={len(tape['edges'])} tape_name={tape['tape_name']}")


if __name__ == "__main__":
    main()