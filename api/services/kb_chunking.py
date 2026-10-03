"""KB-level chunking configuration.

Stored under `kb_datasource.indexing_strategy["chunking"]` (the JSON column
already backs pipeline toggles), keeping one source of truth for the KB's
indexing pipeline. Retrieval settings that also live in indexing_strategy
are read by their own code path and are untouched.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from api.services.chunking import ChunkConfig

CHUNKING_KEY = "chunking"


def defaults_dict() -> dict[str, Any]:
    cfg = ChunkConfig()
    return {
        "chunk_size": cfg.chunk_size,
        "chunk_overlap": cfg.chunk_overlap,
        "separators": list(cfg.separators),
        "strategy": cfg.strategy,
        "token_limit": cfg.token_limit,
        "languages": list(cfg.languages),
        "enable_parent_child": cfg.enable_parent_child,
        "parent_chunk_size": cfg.parent_chunk_size,
        "child_chunk_size": cfg.child_chunk_size,
        "parser_engine_rules": [],
        "table_metadata_instructions": "",
    }


def load_chunking(db: Session, kb_id: str) -> dict[str, Any]:
    """Read the KB's chunking config, filling defaults for missing keys.

    Falls back to defaults when the KB does not exist or the column is null.
    """
    from api.services.kb_admin import get_kb

    base = defaults_dict()
    kb = get_kb(db, kb_id)
    if kb is None:
        return base
    strategy = kb.indexing_strategy or {}
    chunking = strategy.get(CHUNKING_KEY)
    if not isinstance(chunking, dict):
        return base
    for key in base:
        if key in chunking:
            base[key] = chunking[key]
    return base


def load_chunk_config(db: Session, kb_id: str) -> ChunkConfig:
    """Typed view of the KB's chunking config for the ingest pipeline."""
    return ChunkConfig.from_dict(load_chunking(db, kb_id))


def load_engine_rules(db: Session, kb_id: str) -> list[dict[str, Any]]:
    raw = load_chunking(db, kb_id).get("parser_engine_rules")
    return raw if isinstance(raw, list) else []


def save_chunking(db: Session, kb_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Merge `patch` into the KB's chunking config and persist.

    Preserves the other indexing_strategy keys (vector/keyword/wiki/graph
    toggles) and the retrieval block.
    """
    from api.services.kb_admin import get_kb

    kb = get_kb(db, kb_id)
    if kb is None:
        raise ValueError("知识库不存在")

    strategy = dict(kb.indexing_strategy or {})
    current = strategy.get(CHUNKING_KEY)
    merged = defaults_dict()
    if isinstance(current, dict):
        for key in merged:
            if key in current:
                merged[key] = current[key]
    for key, value in (patch or {}).items():
        if key in merged:
            merged[key] = value

    # validate/normalize numeric + strategy fields through the dataclass
    cfg = ChunkConfig.from_dict(merged)
    merged["chunk_size"] = cfg.chunk_size
    merged["chunk_overlap"] = cfg.chunk_overlap
    merged["separators"] = list(cfg.separators)
    merged["strategy"] = cfg.strategy
    merged["token_limit"] = cfg.token_limit
    merged["languages"] = list(cfg.languages)
    merged["enable_parent_child"] = cfg.enable_parent_child
    merged["parent_chunk_size"] = cfg.parent_chunk_size
    merged["child_chunk_size"] = cfg.child_chunk_size

    strategy[CHUNKING_KEY] = merged
    kb.indexing_strategy = strategy
    db.commit()
    return merged