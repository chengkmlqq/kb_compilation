"""编排运行期本体 Schema / 文档类型加载器（build worker 内脚本步骤使用）。

各领域编排的步骤代码不再硬编码类别/prompt，统一调本模块：
- load_schema(kb_id, domain)  → kb 绑定 schema（kb_ontology_schema），缺省按领域名找
- 返回 schema_name / business / rule 类别清单（含 prompt_hint、neo4j_edge）
- extract_prompt() / summary_prompt() / term_filter_prompt() 由 4 列配置拼 LLM prompt
- doc_type_mode(doc_type)      → full | notice | table（kb_doc_type_config）

DB 直连框架库（worker 与 api-server 同 env DATABASE_URL=KB_DATABASE_URL），
进程内 lru_cache 避免每步骤重复查询。
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.ontology import DocTypeConfig, KbOntologySchema, OntologySchema


@lru_cache(maxsize=64)
def load_schema(kb_id: str = "", domain: str = "") -> dict[str, Any]:
    """按 kb 绑定（优先）或领域名取 schema；找不到返回空 dict（调用方降级）。"""
    db = get_sessionmaker()()
    try:
        name = ""
        if kb_id:
            bind = db.get(KbOntologySchema, kb_id)
            if bind:
                name = bind.schema_name
        if not name and domain:
            name = domain
        if not name:
            return {}
        rows = db.execute(
            select(OntologySchema).where(
                OntologySchema.schema_name == name, OntologySchema.state == "1"
            ).order_by(OntologySchema.dimension, OntologySchema.cat_no)
        ).scalars().all()
        if not rows:
            return {}
        head = rows[0]
        business = [
            {"cat_name": r.cat_name, "cat_label": r.cat_label, "prompt_hint": r.prompt_hint or ""}
            for r in rows if r.dimension == "business"
        ]
        rule = [
            {"cat_name": r.cat_name, "cat_label": r.cat_label,
             "prompt_hint": r.prompt_hint or "", "neo4j_edge": r.neo4j_edge or ""}
            for r in rows if r.dimension == "rule"
        ]
        sc = {
            "schema_name": name,
            "business": business,
            "rule": rule,
            "expert_role": head.expert_role or f"你是{name}解析专家，只输出JSON，不要任何思考过程。",
            "extract_rules": head.extract_rules or "",
            "summary_config": _json_or(head.summary_config, {}),
            "term_filter_config": _json_or(head.term_filter_config, {}),
        }
        # 便捷映射：短名→带序号名（1-名称）、规则编号、规则边映射
        sc["biz_short"] = {c["cat_name"]: c["cat_label"] for c in business}
        sc["rule_short"] = {c["cat_name"]: c["cat_label"] for c in rule}
        sc["rule_no"] = {c["cat_name"]: int(c["cat_label"].split("-")[0]) for c in rule if "-" in c["cat_label"]}
        sc["rule_edge"] = {c["cat_name"]: c["neo4j_edge"] for c in rule}
        return sc
    finally:
        db.close()


@lru_cache(maxsize=256)
def doc_type_mode(doc_type: str) -> str:
    """文档类型 → 构建模式 full|notice|table。"""
    if not doc_type:
        return "full"
    db = get_sessionmaker()()
    try:
        row = db.get(DocTypeConfig, doc_type)
        return row.build_mode if row else "full"
    finally:
        db.close()


def _json_or(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else default
    except Exception:
        return default


def _cat_list(cats: list[dict[str, Any]]) -> str:
    return "\n".join(f"{c['cat_label'].split('-')[0]}. {c['cat_name']}：{c['prompt_hint']}" for c in cats)


def extract_prompt(sc: dict[str, Any]) -> str:
    """实体+规则联合抽取 prompt：专家角色 + 类别清单 + 抽取总则。"""
    biz_txt = "\n".join(f"{c['cat_label'].split('-')[0]}. {c['cat_name']}" for c in sc["business"])
    rule_txt = "\n".join(f"{c['cat_label'].split('-')[0]}. {c['cat_name']}" for c in sc["rule"])
    rules = sc.get("extract_rules") or (
        "constraint/objects[].name 必须逐字等于 entities[].name 之一；name/evidence 必须逐字来自长句原文；"
        "禁止纯日期/指示定语；同一概念只输出一次。"
    )
    return (
        f"你是{sc['schema_name']}解析专家，只输出JSON，不要任何思考过程。\n"
        f"业务实体类别：\n{biz_txt}\n\n规则类别：\n{rule_txt}\n\n"
        f"{rules}\n"
        f"参考术语：{{terms}}\n"
        f"只输出JSON对象 {{\"entities\":[{{\"name\":\"实体名\",\"category\":\"{sc['business'][0]['cat_name'] if sc['business'] else ''}\",\"definition\":\"一句话定义\"}}],"
        f"\"rules\":[{{\"title\":\"规则概括≤20字\",\"category\":\"{sc['rule'][0]['cat_name'] if sc['rule'] else ''}\",\"constraint\":\"实体名\",\"evidence\":\"原文片段\","
        f"\"description\":\"...\",\"structure\":{{\"主体\":\"x\"}},\"objects\":[{{\"name\":\"实体名\",\"role\":\"客体\"}}]}}]}}"
    )


def summary_prompt(sc: dict[str, Any], source_file: str = "", doc_name: str = "") -> str:
    """摘要卡片 prompt：summary_config 的专家角色 + 卡片字段表 + 章节 + 关系表规则；通知类特化。"""
    cfg = sc.get("summary_config") or {}
    role = cfg.get("expert_role") or f"你是{sc['schema_name']}知识库的文档摘要专家。"
    fields = cfg.get("card_fields") or ["一句话定位"]
    output_rule = cfg.get("output_rule") or "严格按 markdown 结构输出文档卡片，禁止任何问候/聊天内容"
    sections = cfg.get("sections") or ["## 文档定位", "## 重点内容概览", "## 与其他文档的关系"]
    rel_cols = cfg.get("relation_table_cols") or ["相关文档", "关系说明", "原文依据"]
    rel_rules = cfg.get("relation_rules") or []
    field_rows = "\n".join(f"| {f} | <{f}> |" for f in fields)
    p = (
        f"{role}请根据下面给定的原文切片，生成一张「文档卡片」形式的 markdown 摘要。\n"
        f"【输出要求——{output_rule}】：\n"
        f"<一句话定位>（本文档管什么，一句话）\n"
        f"{field_rows}\n"
        f"## 文档定位\n<本文档在制度/体系中的位置与效力，2~4 句>\n"
        f"本文档重点覆盖以下内容：\n"
        f"- <重点内容概览，分点>\n"
        f"## 与其他文档的关系\n"
        f"| {rel_cols[0]} | {rel_cols[1]} | {rel_cols[2]} |\n|---|---|---|\n"
    )
    if rel_rules:
        p += "\n".join(f"{i + 1}. {r}" for i, r in enumerate(rel_rules)) + "\n"
    if "通知" in (source_file or "") or "修订" in (source_file or ""):
        import re
        m = re.findall(r"《([^》]+)》", source_file or "")
        target = m[-1] if m else ""
        p += (
            f"\n【通知类文档特化】本文件是" + (f"《{target}》" if target else "被通知制度") +
            f"的印发/修订通知：关系表只列这一条核心关系（本通知是《{target}》的印发/修订通知），"
            f"不罗列正文提到的其他制度；原文依据可用标题/文号（正文无逐字句也成立）。\n"
        )
    return p


def term_filter_prompt(sc: dict[str, Any]) -> str:
    """术语筛选 prompt：term_filter_config 的保留/剔除标准 + 统计碎片规则 + 输出格式。"""
    cfg = sc.get("term_filter_config") or {}
    role = cfg.get("expert_role") or f"你是{sc['schema_name']}知识库的术语筛选专家。"
    keep = cfg.get("keep_standards") or "具有业务含义的术语、业务对象、业务事件、业务概念"
    rejects = cfg.get("reject_standards") or []
    frags = cfg.get("fragment_rejects") or []
    out = cfg.get("output_format") or "只输出JSON数组：[{\"word\":\"词\",\"keep\":true,\"reason\":\"一句话理由\"}]，不要输出任何其他文字"
    p = (
        f"{role}以下是文档中通过统计方法发现的候选高频词，请判断每个词是否**具有业务含义**，值得作为关键词/术语保留。\n\n"
        f"【判断标准】\n- 保留（keep=true）：{keep}\n"
    )
    if rejects:
        p += "- 剔除（keep=false）：\n" + "\n".join(f"  - {r}" for r in rejects) + "\n"
    if frags:
        p += "【剔除以下高频统计碎片（即使频次/凝固度很高——它们只是统计噪音，不是术语）】：\n"
        p += "\n".join(f"  {i + 1}. {f}" for i, f in enumerate(frags)) + "\n"
    p += (
        "\n每个词附其在原文中的出现片段（上下文），请结合上下文判断该词在本文档中是否承载业务含义。\n"
        f"{out}\n\n候选词及上下文：\n{{{{items}}}}"
    )
    return p


def load_all() -> None:  # 供调试
    pass
