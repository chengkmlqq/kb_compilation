"""应用「农业」领域构建编排 + 本体 Schema 到线上库（幂等可重放）。

2026-10-21 用户需求：茶叶知识库「4-茶树病虫害绿色防控.docx」题材分析判定
domain_raw=农业，但平台 20 个领域清单里没有「农业」→ resolved_tape_id 空 →
串行链停止，文档停在「已就绪·未构建」。

本脚本补三样东西：
1) 编排「wiki构建-农业」（数据源 scripts/seed_tape_data/wiki构建-农业.json，
   与生产制造与质量管理同链路 18 节点），status=effective；
2) 本体 Schema「农业」（数据源 scripts/seed_ontology_data/农业.json，
   10 business + 12 rule 分类行）；
3) 编排分类 kb_tape_category 建「农业」。

题材分析编排 n1 动态读 kb_tape 清单（tape_type=kb_build OR tape_name LIKE
'wiki构建-%'），新领域自动进判定列表，无需改编排或重启。

用法：
    docker cp scripts/seed_tape_data/wiki构建-农业.json kb-api-server:/tmp/ && \\
    docker cp scripts/seed_ontology_data/农业.json kb-api-server:/tmp/ && \\
    docker cp scripts/apply_agriculture_domain.py kb-api-server:/tmp/ && \\
    docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_agriculture_domain.py
"""
from __future__ import annotations

import json
import sys
import uuid as _uuid
from pathlib import Path

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.ontology import OntologySchema
from api.models.orchestration import Tape

DOMAIN = "农业"
TAPE_NAME = "wiki构建-农业"
TAPE_JSON = Path("/tmp/wiki构建-农业.json")
SCHEMA_JSON = Path("/tmp/农业.json")


def _create_tape(db) -> Tape:
    doc = json.loads(TAPE_JSON.read_text(encoding="utf-8"))
    d = doc.get("draft") or doc
    name = str(d.get("tapeName") or TAPE_NAME)
    nodes = list(d.get("nodes") or [])
    edges = list(d.get("edges") or [])
    exec_params = d.get("execParams")
    category = str(d.get("category") or DOMAIN)

    from api.services.orchestration import ensure_tape_category, save_tape_design

    ensure_tape_category(db, category, cat_type="tape")
    tape = Tape(
        id=_uuid.uuid4().hex[:32],
        tape_name=name,
        tape_label=str(d.get("tapeLabel") or name),
        tape_descr=str(d.get("tapeDescr") or ""),
        tape_type=str(d.get("tapeType") or "kb_build") or "kb_build",
        category=category,
        status=str(d.get("status") or "effective") or "effective",
        nodes=nodes,
        edges=edges,
        exec_params=exec_params,
        create_user=None,
    )
    db.add(tape)
    db.commit()
    steps = d.get("steps") or []
    if steps:
        _restore_tape_steps(db, tape.id, steps)
    else:
        save_tape_design(db, tape.id, nodes, edges, exec_params)
    return tape


def _restore_tape_steps(db, tape_id: str, steps: list) -> None:
    """复用 seed_framework_db 的步骤重建逻辑（step id 前缀重映射 + pre/next）。"""
    sys.path.insert(0, "/srv/kb/scripts")
    from seed_framework_db import _restore_tape_steps as _restore
    _restore(db, tape_id, steps)


def _create_schema(db) -> int:
    d = json.loads(SCHEMA_JSON.read_text(encoding="utf-8"))
    name = str(d.get("schema_name") or DOMAIN)
    items = d.get("items") or []
    n = 0
    for it in items:
        dim = str(it.get("dimension") or "")
        if dim not in ("business", "rule"):
            continue
        cat_no = int(it.get("cat_no") or 0)
        cat_name = str(it.get("cat_name") or "").strip()
        if not cat_name:
            continue
        db.add(OntologySchema(
            id=_uuid.uuid4().hex[:24],
            schema_name=name,
            schema_label=str(d.get("schema_label") or name),
            schema_desc=d.get("schema_desc"),
            dimension=dim,
            cat_no=cat_no,
            cat_name=cat_name,
            cat_label=str(it.get("cat_label") or f"{cat_no}-{cat_name}"),
            prompt_hint=it.get("prompt_hint"),
            neo4j_edge=it.get("neo4j_edge"),
            expert_role=d.get("expert_role"),
            extract_rules=d.get("extract_rules"),
            summary_config=d.get("summary_config"),
            term_filter_config=d.get("term_filter_config"),
            state=str(it.get("state") or "1") or "1",
            sort=cat_no,
        ))
        n += 1
    db.commit()
    return n


def main() -> None:
    db = get_sessionmaker()()
    try:
        # 1) 编排
        from api.models.orchestration import TapeStep

        tape = db.execute(select(Tape).where(Tape.tape_name == TAPE_NAME)).scalars().first()
        if tape is None:
            tape = _create_tape(db)
            print(f"[apply-agri] 编排「{TAPE_NAME}」已创建 id={tape.id[:12]}… "
                  f"category={tape.category} status={tape.status}")
        else:
            print(f"[apply-agri] 编排「{TAPE_NAME}」已存在 id={tape.id[:12]}… status={tape.status} (skip)")
        # 编排已存在但步骤缺失（如中途失败）时补建执行层
        step_count = db.execute(
            select(TapeStep).where(TapeStep.tape_id == tape.id)
        ).scalars().all()
        if not step_count:
            doc = json.loads(TAPE_JSON.read_text(encoding="utf-8"))
            steps = (doc.get("draft") or doc).get("steps") or []
            if steps:
                _restore_tape_steps(db, tape.id, steps)
                print(f"[apply-agri] 编排步骤缺失，已补建 {len(steps)} 个")
            else:
                print("[apply-agri] ⚠️ 编排步骤仍为空（种子 JSON 无 steps 字段）")

        # 2) 本体 Schema
        exists = db.execute(
            select(OntologySchema).where(OntologySchema.schema_name == DOMAIN).limit(1)
        ).scalars().first()
        if exists is None:
            n = _create_schema(db)
            print(f"[apply-agri] 本体 Schema「{DOMAIN}」已创建：{n} 分类行")
        else:
            print(f"[apply-agri] 本体 Schema「{DOMAIN}」已存在 (skip)")

        # 3) 校验清单是否已含农业（题材分析 n1 动态读库，无需重启）
        rows = db.execute(
            select(Tape.tape_name, Tape.category).where(Tape.status != "offline")
        ).all()
        cats = sorted({(c or "通用").strip() for _, c in rows})
        print(f"[apply-agri] 当前领域清单 {len(cats)} 个，含「农业」：{DOMAIN in cats}")

        from api.models.orchestration import TapeStep
        steps = db.execute(
            select(TapeStep).where(TapeStep.tape_id == tape.id)
        ).scalars().all()
        print(f"[apply-agri] 编排步骤 {len(steps)} 个")
        print("[apply-agri] DONE：上传链未选构建引擎的农业/农林题材文档将命中「农业」领域")
        print("[apply-agri] 提示：题材分析编排动态读库，新领域即时生效，无需重启服务")
    finally:
        db.close()


if __name__ == "__main__":
    main()
