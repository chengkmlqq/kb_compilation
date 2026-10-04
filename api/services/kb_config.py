"""KB 级 WeKnora 对齐配置：默认值规范化 + 各配置读取。

对齐 WeKnora `internal/types/knowledgebase.go` 的 KnowledgeBase 结构与
`internal/types/indexing_strategy.go` 的 IndexingStrategy。

**默认语义（严格对齐 WeKnora）**：新建 KB 的 `indexing_strategy` 四路 pipeline
默认 `vector_enabled=True / keyword_enabled=True / wiki_enabled=False /
graph_enabled=False`——wiki 与知识图谱需显式勾选才构建。存量 KB 由迁移脚本回填为
迁移前的实际行为（wiki_enabled=True），故本模块对「缺失 key」按 WeKnora 默认 false
处理时不会影响存量（存量已被回填成显式值）。

**KB 级技能绑定**：`wiki_config.skill` 指定 wiki 构建技能（WeKnora WikiConfig.Skill，
由 agent-gateway 执行）；为空回退全局 `WIKI_SKILL_NAME`。配合
`custom_wiki_generation` 开关——开启后不自动构建，需手动触发（WeKnora 同名字段）。
"""
from __future__ import annotations

import logging
from typing import Any

from api.config import get_settings

logger = logging.getLogger(__name__)

# WeKnora IndexingStrategy 四路 pipeline（internal/types/indexing_strategy.go）
INDEXING_KEYS = ("vector_enabled", "keyword_enabled", "wiki_enabled", "graph_enabled")
# 新建 KB 的默认开关值：严格对齐 WeKnora（wiki/graph 默认关）
DEFAULT_INDEXING_STRATEGY: dict[str, bool] = {
    "vector_enabled": True,
    "keyword_enabled": True,
    "wiki_enabled": False,
    "graph_enabled": False,
}

KB_TYPE_DOCUMENT = "document"
KB_TYPE_FAQ = "faq"
KB_TYPES = (KB_TYPE_DOCUMENT, KB_TYPE_FAQ)

# WeKnora WikiExtractionGranularity（focused / standard / exhaustive）
WIKI_GRANULARITIES = ("focused", "standard", "exhaustive")
DEFAULT_WIKI_GRANULARITY = "standard"


def normalize_indexing_strategy(raw: dict | None) -> dict[str, bool]:
    """补全四路开关，缺失 key 用 WeKnora 默认（wiki/graph 关闭）。"""
    src = raw or {}
    return {k: bool(src.get(k, DEFAULT_INDEXING_STRATEGY[k])) for k in INDEXING_KEYS}


def pipeline_enabled(kb: Any, key: str, default: bool | None = None) -> bool:
    """读取某路 pipeline 开关；kb 为 KbDatasource 或含 indexing_strategy 的对象。"""
    if key not in INDEXING_KEYS:
        raise ValueError(f"unknown indexing pipeline: {key}")
    strategy = normalize_indexing_strategy(getattr(kb, "indexing_strategy", None))
    if default is not None and not getattr(kb, "indexing_strategy", None):
        # 存量 KB 索引策略为空时按调用方的历史默认兼容
        return bool(default)
    return strategy[key]


def wiki_skill_name(kb: Any | None = None) -> str:
    """KB 绑定的 wiki 构建技能；未绑定回退全局 WIKI_SKILL_NAME。"""
    fallback = (get_settings().WIKI_SKILL_NAME or "kb-wiki-builder").strip()
    if kb is None:
        return fallback
    wiki_cfg = getattr(kb, "wiki_config", None) or {}
    bound = str(wiki_cfg.get("skill") or "").strip()
    return bound or fallback


def custom_wiki_generation(kb: Any | None) -> bool:
    """是否关闭自动 wiki 构建（需手动触发）。"""
    return bool(getattr(kb, "custom_wiki_generation", False)) if kb is not None else False


def normalize_wiki_config(raw: dict | None) -> dict:
    """wiki_config 规范化：抽取粒度校验 + 字段补全。"""
    src = dict(raw or {})
    gran = str(src.get("extraction_granularity") or "").strip()
    if gran not in WIKI_GRANULARITIES:
        gran = DEFAULT_WIKI_GRANULARITY
    src["extraction_granularity"] = gran
    src.setdefault("max_pages_per_ingest", 0)
    src.setdefault("skill", "")
    return src


def normalize_extract_config(raw: dict | None) -> dict:
    """知识图谱抽取配置规范化（WeKnora extract_config）。"""
    src = dict(raw or {})
    src["enabled"] = bool(src.get("enabled", False))
    src.setdefault("text", "")
    src.setdefault("tags", [])
    src.setdefault("custom_instructions", "")
    return src


def normalize_faq_config(raw: dict | None) -> dict:
    """FAQ 索引模式规范化（WeKnora faq_config）。"""
    src = dict(raw or {})
    im = str(src.get("index_mode") or "").strip()
    src["index_mode"] = im if im in ("question_only", "question_answer") else "question_answer"
    qm = str(src.get("question_index_mode") or "").strip()
    src["question_index_mode"] = qm if qm in ("combined", "separate") else "combined"
    return src


def normalize_question_generation_config(raw: dict | None) -> dict:
    src = dict(raw or {})
    src["enabled"] = bool(src.get("enabled", False))
    try:
        src["question_count"] = max(0, int(src.get("question_count") or 0))
    except (TypeError, ValueError):
        src["question_count"] = 0
    src.setdefault("custom_instructions", "")
    return src


def normalize_vlm_config(raw: dict | None) -> dict:
    src = dict(raw or {})
    src["enabled"] = bool(src.get("enabled", False))
    src.setdefault("model_id", "")
    src.setdefault("description_language", "")
    src.setdefault("custom_instructions", "")
    return src


def normalize_asr_config(raw: dict | None) -> dict:
    src = dict(raw or {})
    src["enabled"] = bool(src.get("enabled", False))
    src.setdefault("model_id", "")
    src.setdefault("language", "")
    return src


def normalize_storage_provider_config(raw: dict | None) -> dict:
    src = dict(raw or {})
    provider = str(src.get("provider") or "").strip() or "local"
    src["provider"] = provider
    return src
