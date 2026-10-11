#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WeKnora 原生 wiki + Neo4j 图谱构建 —— 服务编排（Tape）生成器。

把平台原生链路 KbWikiBuildTask（读全库 chunks → GraphExtractor 逐 chunk
LLM 抽实体 → build_wiki 建页+互链）与 KbGraphBuildTask（关系抽取 → Neo4j
写入）整体迁移为服务编排节点：业务逻辑内联在编排节点配置里，
仅保留 api.services 基础工具调用（ChatClient LLM / wiki 写库 / Neo4j 驱动）。

节点 DAG（输入：kb_id / language / created_by）：
  n01 def 输入定义
  n02 script 读全库 chunks（doc_chunk，按 seq 排序）
  n03 script 门禁（chunks>0，否则阻断）
  n04 script LLM 实体抽取（GraphExtractor 并发逐 chunk，entities_by_title 合并）
  n05 script 建 wiki 页（group_candidates → upsert_pages → build_links）
  n06 script 关系抽取（batch 分批，min-entity 守卫）
  n07 script Neo4j 写入（write_graph，MERGE 节点+关系，无端点跳过不中断）
  n08 print 完成汇总

用法：
  python3 scripts/build_weknora_wiki_graph_tape.py --out /tmp/tape_weknora.json
"""

import argparse
import json

# ─────────────────────────────────────────────────────────────────────
# 节点内联代码头：注入 api.services（编排 script 节点 exec 于 worker 进程，
# /srv/kb 在 sys.path，可直接 import 平台 service = 基础工具调用）
# ─────────────────────────────────────────────────────────────────────
SRV_HEAD = '''import sys, os, json, hashlib, time, traceback
G = globals()
G.update({'sys': sys, 'os': os, 'json': json, 'hashlib': hashlib, 'time': time})
for _p in ('/srv/kb',):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from api.db import get_sessionmaker
from api.services.chat import load_chat_config, ChatClient
from api.services.graph import GraphExtractor, Neo4jGraphStore
from api.services import wiki as wiki_svc
'''

CODE_READ_CHUNKS = SRV_HEAD + '''
_kb = bindings.get('kb_id', '')
if not _kb:
    raise ValueError("缺少 kb_id")
db = get_sessionmaker()()
try:
    from api.models.knowledge import DocChunk
    from sqlalchemy import select
    rows = db.execute(
        select(DocChunk.id, DocChunk.content)
        .where(DocChunk.kb_id == _kb, DocChunk.enabled.is_(True))
        .order_by(DocChunk.seq)
    ).all()
finally:
    db.close()
chunks = [{"id": r.id, "content": r.content} for r in rows]
bindings['chunks'] = chunks
bindings['chunk_count'] = len(chunks)
print(f"[n02] 读全库 chunks: {len(chunks)}")
'''

CODE_GATE = SRV_HEAD + '''
cc = int(bindings.get('chunk_count', 0) or 0)
if cc <= 0:
    raise RuntimeError(f"知识库无启用 chunks（{cc}），阻断构建")
print(f"[n03] 门禁通过：chunks={cc}")
'''

CODE_ENTITIES = SRV_HEAD + '''
_kb = bindings.get('kb_id', '')
_lang = str(bindings.get('language', '中文') or '中文')
chunks = bindings.get('chunks') or []
db = get_sessionmaker()()
try:
    chat_cfg = load_chat_config(db)
finally:
    db.close()
client = ChatClient(chat_cfg)
extractor = GraphExtractor(client, language=_lang)
# 逐 chunk 抽取 + 单 chunk 重试：InferAI 模型偶发把实体抽取 prompt 当对话
# 输出纯文本（非 JSON）→ parse 失败返回空。重试一次可救回（实测第 2 次成功）。
# 原生 KbWikiBuildTask 无重试（一次失败该 chunk 实体静默丢失）——编排内补业务逻辑。
empty_chunks = []
for c in chunks:
    if not c.get('content'):
        continue
    before = set(extractor.entities_by_title.keys())
    for attempt in range(2):
        try:
            extractor.extract_entities(c.get('id', ''), c.get('content', ''))
        except Exception as e:
            print(f"  chunk 实体抽取异常: {c.get('id','')[:8]} {e}")
        # 本次调用有新增实体 → 成功；否则重试一次
        if set(extractor.entities_by_title.keys()) != before or attempt == 1:
            break
        print(f"  chunk {c.get('id','')[:8]} 第{attempt + 1}次空，重试...")
    if set(extractor.entities_by_title.keys()) == before:
        empty_chunks.append(c.get('id', '')[:8])
ents = [
    {"title": e.title, "entity_type": e.entity_type, "description": e.description,
     "frequency": e.frequency, "chunk_ids": list(e.chunk_ids)}
    for e in extractor.entities_by_title.values()
]
bindings['entities'] = ents
bindings['entity_count'] = len(ents)
print(f"[n04] LLM 实体抽取: {len(ents)} 个（去重后）" + (f" 空块 {len(empty_chunks)}" if empty_chunks else ""))
'''

CODE_WIKI_PAGES = SRV_HEAD + '''
_kb = bindings.get('kb_id', '')
_created_by = str(bindings.get('created_by', '') or 'orch')
ents_raw = bindings.get('entities') or []
chunks = bindings.get('chunks') or []
chunks_by_id = {c.get('id', ''): c.get('content', '') for c in chunks}
db = get_sessionmaker()()
try:
    from api.services.graph import Entity
    entities = [
        Entity(title=e.get('title', ''), entity_type=e.get('entity_type', ''),
               description=e.get('description', ''), frequency=int(e.get('frequency', 1) or 1),
               chunk_ids=list(e.get('chunk_ids') or []))
        for e in ents_raw if (e.get('title') or '').strip()
    ]
    result = wiki_svc.build_wiki(db, _kb, entities, chunks_by_id, created_by=_created_by)
finally:
    db.close()
bindings['wiki_pages'] = result.get('pages', 0)
bindings['wiki_links'] = result.get('links', 0)
print(f"[n05] wiki 建页: {result.get('pages')} 页 / {result.get('links')} 互链")
'''

CODE_RELATIONS = SRV_HEAD + '''
chunks = bindings.get('chunks') or []
_lang = str(bindings.get('language', '中文') or '中文')
db = get_sessionmaker()()
try:
    chat_cfg = load_chat_config(db)
finally:
    db.close()
client = ChatClient(chat_cfg)
extractor = GraphExtractor(client, language=_lang)
# 复用上一步已抽实体（避免重复 LLM），只做关系抽取
from api.services.graph import Entity
for e in (bindings.get('entities') or []):
    if (e.get('title') or '').strip():
        extractor.entities_by_title[e['title']] = Entity(
            title=e['title'], entity_type=e.get('entity_type', ''),
            description=e.get('description', ''), frequency=int(e.get('frequency', 1) or 1),
            chunk_ids=list(e.get('chunk_ids') or []))
rels = []
RELATION_BATCH_SIZE = 4  # 单批 LLM 更稳（InferAI 偶发非 JSON 输出）
for i in range(0, len(chunks), RELATION_BATCH_SIZE):
    batch = chunks[i:i + RELATION_BATCH_SIZE]
    ids = [c.get('id', '') for c in batch]
    texts = [c.get('content', '') for c in batch]
    before = len(extractor.relationships)
    for attempt in range(2):
        try:
            extractor.extract_relationships(ids, texts)
        except Exception as e:
            print(f"  关系抽取异常: {e}")
        if len(extractor.relationships) != before or attempt == 1:
            break
        print(f"  批次{i // RELATION_BATCH_SIZE} 第{attempt + 1}次关系空，重试...")
rels = [
    {"source": r.source, "target": r.target, "relation_type": r.relation_type,
     "description": r.description, "strength": r.strength}
    for r in extractor.relationships
]
bindings['relationships'] = rels
bindings['relation_count'] = len(rels)
print(f"[n06] 关系抽取: {len(rels)} 条")
'''

CODE_NEO4J = SRV_HEAD + '''
_kb = bindings.get('kb_id', '')
ents_raw = bindings.get('entities') or []
rels_raw = bindings.get('relationships') or []
from api.services.graph import Entity, Relationship
ents = [Entity(title=e.get('title',''), entity_type=e.get('entity_type',''),
               description=e.get('description',''), frequency=int(e.get('frequency',1) or 1),
               chunk_ids=list(e.get('chunk_ids') or [])) for e in ents_raw if (e.get('title') or '').strip()]
rels = [Relationship(source=r.get('source',''), target=r.get('target',''),
                     relation_type=r.get('relation_type',''), description=r.get('description',''),
                     strength=int(r.get('strength',5) or 5)) for r in rels_raw
        if (r.get('source') or '').strip() and (r.get('target') or '').strip()]
from api.config import get_settings
settings = get_settings()
uri, pw = settings.NEO4J_URI, settings.NEO4J_PASSWORD
if not (uri and pw):
    print("[n07] Neo4j 未配置（NEO4J_URI/PASSWORD），仅抽取未落图")
    bindings['neo4j_nodes'] = len(ents)
    bindings['neo4j_rels'] = len(rels)
    bindings['neo4j_written'] = False
    raise SystemExit(0)
try:
    store = Neo4jGraphStore(uri=uri, user=settings.NEO4J_USERNAME or 'neo4j',
                            password=pw, database=settings.NEO4J_DATABASE or None)
    store.write_graph(_kb, ents, rels)
    bindings['neo4j_nodes'] = len(ents)
    bindings['neo4j_rels'] = len(rels)
    bindings['neo4j_written'] = True
    print(f"[n07] Neo4j 写入: {len(ents)} 节点 / {len(rels)} 关系")
except Exception as e:
    traceback.print_exc()
    print(f"[n07] Neo4j 写入失败: {e}（不中断 wiki 构建）")
    bindings['neo4j_written'] = False
'''

CODE_DONE = '''kb = bindings.get('kb_id', '')
summary = (
    f"WeKnora 原生 wiki+图谱构建完成: kb={kb}\\n"
    f"  chunks={bindings.get('chunk_count', 0)} "
    f"实体={bindings.get('entity_count', 0)} "
    f"wiki页={bindings.get('wiki_pages', 0)} 互链={bindings.get('wiki_links', 0)}\\n"
    f"  关系={bindings.get('relation_count', 0)} "
    f"Neo4j节点={bindings.get('neo4j_nodes', 0)} 关系={bindings.get('neo4j_rels', 0)} "
    f"写入={'是' if bindings.get('neo4j_written') else '否(未配置/失败)'}"
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
                {"variable": "kb_id", "expression": "bindings.get('kb_id', '')"},
                {"variable": "language", "expression": "bindings.get('language', '中文')"},
                {"variable": "created_by", "expression": "bindings.get('created_by', 'orch')"},
            ], ensure_ascii=False),
        }),
        node("n02", "script", "读全库chunks", {"code": CODE_READ_CHUNKS}),
        node("n03", "script", "chunks门禁", {"code": CODE_GATE}),
        node("n04", "script", "LLM实体抽取", {"code": CODE_ENTITIES}),
        node("n05", "script", "建wiki页+互链", {"code": CODE_WIKI_PAGES}),
        node("n06", "script", "关系抽取", {"code": CODE_RELATIONS}),
        node("n07", "script", "Neo4j写入", {"code": CODE_NEO4J}),
        node("n08", "print", "完成汇总", {"text": "{{build_summary}}"}),
    ]
    edges = [
        edge("e01", "n01", "n02"),
        edge("e02", "n02", "n03"),
        edge("e03", "n03", "n04"),
        edge("e04", "n04", "n05"),
        edge("e05", "n05", "n06"),
        edge("e06", "n06", "n07"),
        edge("e07", "n07", "n08"),
    ]
    return {
        "tape_name": "weknora原生_wiki+图谱构建",
        "tape_label": "WeKnora原生 wiki+Neo4j图谱构建（编排化）",
        "tape_descr": "WeKnora 原生 KbWikiBuildTask+KbGraphBuildTask 全链路编排化：读全库chunks→LLM实体抽取→建wiki页+互链→关系抽取→Neo4j写入。业务逻辑全部内联编排节点，仅保留 api.services 基础工具调用。输入：kb_id / language / created_by",
        "tape_type": "general",
        "nodes": nodes,
        "edges": edges,
        "exec_params": [
            {"paramName": "kb_id", "paramLabel": "知识库ID", "paramType": "input"},
            {"paramName": "language", "paramLabel": "抽取语言", "paramType": "input", "defaultValue": "中文"},
            {"paramName": "created_by", "paramLabel": "创建者", "paramType": "input", "defaultValue": "orch"},
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/tape_weknora_wiki_graph.json")
    args = ap.parse_args()
    tape = build_tape()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(tape, f, ensure_ascii=False, indent=2)
    print(f"tape payload → {args.out}")
    print(f"nodes={len(tape['nodes'])} edges={len(tape['edges'])} tape_name={tape['tape_name']}")


if __name__ == "__main__":
    main()
