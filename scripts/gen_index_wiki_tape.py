#!/usr/bin/env python3
"""生成「索引wiki创建」编排种子（2026-10-22）。

语义：从已索引（解析入库、有切片）的文档创建 wiki 的**通用**编排——
不依赖领域本体 Schema，任何知识库文档可用。执行体复用经过验证的
worker/builtin_engine/scripts/run_one.py（查文档元数据 → gen_summary 摘要
+ build_full 长句/关键词/实体/规则/图谱/合并/链接全阶段），编排仅做
输入定义 + 切片门禁 + 子进程调用 + 汇总，节点脚本短、可维护。

节点：
  n1 def     输入定义（kid/doc_name/source_file/kb_id/skip_final）
  n2 script  切片门禁（无切片阻断；文档元数据兜底 doc_name/source_file）
  n3 script  索引wiki创建（subprocess 调 run_one.py，超时 9000s）
  n4 print   完成汇总

输出：scripts/seed_tape_data/索引wiki创建.json（seed_framework_db 自动播种；
scripts/apply_index_wiki_tape.py 手动应用线上存量库）。
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "seed_tape_data" / "索引wiki创建.json"

_API = "http://" + "api-server:8000"

N1_ASSIGN = (
    "[{\"variable\": \"kid\", \"expression\": \"bindings.get('kid', '')\"}, "
    "{\"variable\": \"doc_name\", \"expression\": \"bindings.get('doc_name', '')\"}, "
    "{\"variable\": \"source_file\", \"expression\": \"bindings.get('source_file', '')\"}, "
    "{\"variable\": \"kb_id\", \"expression\": \"bindings.get('kb_id', '')\"}, "
    "{\"variable\": \"skip_final\", \"expression\": \"bindings.get('skip_final', 'false')\"}]"
)

# 公共 wrapper 前缀：注入 weknora_rpc 并定位 KB（与领域构建编排同款写法）
WRAPPER = (
    "import sys, os, json\n"
    "SD = '/srv/kb/worker/builtin_engine/scripts'\n"
    "if SD not in sys.path:\n"
    "    sys.path.insert(0, SD)\n"
    f"os.environ.setdefault('KB_API_BASE_URL', '{_API}')\n"
    "import weknora_rpc as wr\n"
    "G = globals()\n"
    "G['wr'] = wr\n"
    "G['bindings'] = bindings\n"
    "G['context'] = context\n"
    "G['config'] = config\n"
    "wr.load_config()\n"
    "_kb = bindings.get('kb_id') or os.environ.get('WEKNORA_KB_ID', '')\n"
    "if _kb:\n"
    "    wr.KB = _kb\n"
    "    os.environ['WEKNORA_KB_ID'] = _kb\n"
    "wr.mcp_init()\n"
)

N2_CODE = (
    WRAPPER
    + "\n"
    "_kid = str(bindings.get('kid') or '').strip()\n"
    "if not _kid:\n"
    "    raise ValueError('缺少 kid（文档 knowledge_id）')\n"
    "chunks = []\n"
    "page, psize = 1, 100\n"
    "while True:\n"
    "    r = wr.list_chunks(_kid, kb_id=wr.KB, page=page, page_size=psize)\n"
    "    data = (r or {}).get('data') or []\n"
    "    if not data:\n"
    "        break\n"
    "    chunks.extend(data)\n"
    "    if len(data) < psize:\n"
    "        break\n"
    "    page += 1\n"
    "if not chunks:\n"
    "    raise RuntimeError(f'文档 {_kid} 无切片（未解析完成或解析失败），阻断构建')\n"
    "bindings['chunk_count'] = len(chunks)\n"
    "# 文档元数据兜底 doc_name/source_file（编排页执行时可不填）\n"
    "if not bindings.get('doc_name') or not bindings.get('source_file'):\n"
    "    try:\n"
    "        meta = wr.tool_call('get_document', {'knowledge_id': _kid})\n"
    "        data = meta.get('data', meta) or {}\n"
    "        if isinstance(data, str):\n"
    "            data = json.loads(data)\n"
    "        fname = (data.get('file_name') or data.get('title') or '').strip()\n"
    "        if fname:\n"
    "            bindings['source_file'] = fname\n"
    "            bindings['doc_name'] = bindings.get('doc_name') or fname\n"
    "    except Exception as e:\n"
    "        print(f'[n2] 文档元数据获取失败（忽略，用入参继续）: {e}')\n"
    "print(f\"[n2] 门禁通过: chunks={len(chunks)} doc={bindings.get('doc_name') or _kid}\")\n"
)

N3_CODE = (
    "import sys, os, json, subprocess\n"
    "SD = '/srv/kb/worker/builtin_engine/scripts'\n"
    "if SD not in sys.path:\n"
    "    sys.path.insert(0, SD)\n"
    "_kid = str(bindings.get('kid') or '').strip()\n"
    "if not _kid:\n"
    "    raise ValueError('缺少 kid（文档 knowledge_id）')\n"
    "_kb = bindings.get('kb_id') or os.environ.get('WEKNORA_KB_ID', '')\n"
    "_env = dict(os.environ)\n"
    "if _kb:\n"
    "    _env['WEKNORA_KB_ID'] = _kb\n"
    "_env['WEKNORA_KNOWLEDGE_ID'] = _kid\n"
    "_fname = str(bindings.get('source_file') or bindings.get('doc_name') or '').strip()\n"
    "if _fname:\n"
    "    _env['WEKNORA_DOC_NAME'] = _fname\n"
    "_cmd = [sys.executable, os.path.join(SD, 'run_one.py'), _kid]\n"
    "if _kb:\n"
    "    _cmd += ['--kb', _kb]\n"
    "if str(bindings.get('skip_final') or 'false').strip().lower() in ('true', '1'):\n"
    "    _cmd += ['--skip-final']\n"
    "print(f'[n3] 执行: {\" \".join(_cmd)}')\n"
    "_r = subprocess.run(_cmd, env=_env, capture_output=True, text=True, timeout=9000)\n"
    "if _r.stdout:\n"
    "    print(_r.stdout[-4000:])\n"
    "if _r.returncode != 0:\n"
    "    if _r.stderr:\n"
    "        print('STDERR:', _r.stderr[-2000:])\n"
    "    raise RuntimeError(f'索引wiki创建失败（exit {_r.returncode}）')\n"
    "bindings['build_summary'] = (\n"
    "    f'索引wiki创建完成: doc={_fname or _kid} chunks={bindings.get(\"chunk_count\", 0)}'\n"
    ")\n"
    "print(f'[n3] {bindings[\"build_summary\"]}')\n"
)


def _node(nid: str, label: str, inst: str, cfg: dict, x: int, y: int) -> dict:
    return {
        "id": nid,
        "type": "stepNode",
        "data": {
            "label": label,
            "stepInst": inst,
            "config": cfg,
        },
        "position": {"x": x, "y": y},
    }


def _step(seq: int, nid: str, inst: str, label: str, cfg: dict, pre: list, nxt: list) -> dict:
    return {
        "step_seq": seq,
        "step_id": nid,
        "step_inst": inst,
        "step_label": label,
        "pre_step_ids": pre,
        "next_step_ids": nxt,
        "queue_name": "build",
        "step_config": cfg,
    }


def build() -> dict:
    nodes = [
        _node("n1", "输入定义", "def",
              {"queue": "build", "assignments": N1_ASSIGN}, 0, 0),
        _node("n2", "切片门禁", "script",
              {"queue": "build", "code": N2_CODE}, 0, 0),
        _node("n3", "索引wiki创建", "script",
              {"queue": "build", "code": N3_CODE}, 0, 0),
        _node("n4", "完成汇总", "print",
              {"queue": "build", "text": "{{build_summary}}"}, 0, 0),
    ]
    edges = [
        {"source": "n1", "target": "n2"},
        {"source": "n2", "target": "n3"},
        {"source": "n3", "target": "n4"},
    ]
    exec_params = {
        "kid": "",
        "doc_name": "",
        "source_file": "",
        "kb_id": "",
        "skip_final": "false",
    }
    steps = [
        _step(0, "n1", "def", "输入定义",
              {"queue": "build", "assignments": N1_ASSIGN}, [], ["n2"]),
        _step(1, "n2", "script", "切片门禁",
              {"queue": "build", "code": N2_CODE}, ["n1"], ["n3"]),
        _step(2, "n3", "script", "索引wiki创建",
              {"queue": "build", "code": N3_CODE}, ["n2"], ["n4"]),
        _step(3, "n4", "print", "完成汇总",
              {"queue": "build", "text": "{{build_summary}}"}, ["n3"], []),
    ]
    return {
        "type": "kb.orchestration.draft",
        "schemaVersion": 1,
        "exportedAt": "",
        "draft": {
            "tapeName": "索引wiki创建",
            "tapeLabel": "索引wiki创建（通用文档→wiki：摘要/长句/关键词/实体/规则/图谱/链接）",
            "tapeDescr": (
                "从已索引（解析入库、有切片）的文档创建 wiki 的通用编排，不依赖领域本体 "
                "Schema，任何知识库文档可用。链路：切片门禁 → 摘要（gen_summary）→ "
                "长句/关键词/实体/规则/图谱/合并/链接（build_full）。输入：kid（文档 "
                "knowledge_id，必填）/ kb_id（必填）；doc_name/source_file 缺省自动取文档 "
                "元数据；skip_final=true 时跳过跨文档合并+链接（批次末统一执行）。"
            ),
            "tapeType": "kb_build",
            "category": "通用",
            "status": "effective",
            "nodes": nodes,
            "edges": edges,
            "execParams": exec_params,
            "steps": steps,
        },
    }


def main() -> None:
    doc = build()
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    d = doc["draft"]
    print(f"[gen-index-wiki] 已生成 {OUT}")
    print(f"  编排: {d['tapeName']} ({d['tapeType']} / {d['category']} / {d['status']})")
    print(f"  节点: {len(d['nodes'])}  steps: {len(d['steps'])}  edges: {len(d['edges'])}")
    # 结构自检：steps 与 nodes 一一对应、edges 端点都在 nodes 里
    nids = {n["id"] for n in d["nodes"]}
    sids = {s["step_id"] for s in d["steps"]}
    assert nids == sids, f"steps/nodes 不一致: {nids ^ sids}"
    for e in d["edges"]:
        assert e["source"] in nids and e["target"] in nids, f"edge 端点缺失: {e}"
    print("[gen-index-wiki] 结构自检通过")


if __name__ == "__main__":
    main()
