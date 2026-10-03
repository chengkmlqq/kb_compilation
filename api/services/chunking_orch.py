"""Adaptive chunking entry points — port of WeKnora chunker/strategy.go.

Public API (kept close to the Go names so the port is auditable):

    split(text, cfg)                    -> list[Chunk]
    split_with_diagnostics(text, cfg)   -> (list[Chunk], Diagnostics)
    split_parent_child(text, cfg)       -> (child_chunks, parent_chunks)
"""

from __future__ import annotations

from api.services.chunking import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_PARENT_CHUNK_SIZE,
    Chunk,
    ChunkConfig,
    Diagnostics,
    ParentChildChunk,
    TIER_LEGACY,
    profile_document,
    resolve_chain,
    validate_chunks,
)
from api.services.chunking_tiers import run_tier


def ensure_defaults(cfg: ChunkConfig) -> ChunkConfig:
    """Fill unset fields with the standard defaults (mutates and returns)."""
    if cfg.chunk_size <= 0:
        cfg.chunk_size = DEFAULT_CHUNK_SIZE
    if cfg.chunk_overlap < 0:
        cfg.chunk_overlap = DEFAULT_CHUNK_OVERLAP
    if not cfg.separators:
        cfg.separators = ["\n## ", "\n\n", "\n"]
    if cfg.parent_chunk_size <= 0:
        cfg.parent_chunk_size = DEFAULT_PARENT_CHUNK_SIZE
    if cfg.child_chunk_size <= 0:
        cfg.child_chunk_size = 384
    return cfg


def _run_chain(
    text: str, cfg: ChunkConfig, chain: list[str], profile
) -> tuple[list[Chunk], Diagnostics]:
    diag = Diagnostics(selected_tier=TIER_LEGACY, tier_chain=list(chain))
    total_chars = len(text)
    lang = cfg.lang_for(text)

    for i, tier in enumerate(chain):
        out = run_tier(tier, text, cfg, profile)
        if i < len(chain) - 1:
            v = validate_chunks(out, total_chars, cfg, lang)
            if v.ok:
                diag.selected_tier = tier
                return out, diag
            diag.rejected.append({"tier": tier, "reason": v.reason})
        else:
            # final tier (legacy or pinned tier) is accepted unconditionally
            diag.selected_tier = tier
            return out if out else [], diag

    diag.selected_tier = TIER_LEGACY
    return [], diag


def split(text: str, cfg: ChunkConfig) -> list[Chunk]:
    """Chunk text using the configured strategy (empty -> auto chain)."""
    if not text or not text.strip():
        return []
    cfg = ensure_defaults(cfg)
    profile = profile_document(text)
    chain = resolve_chain(cfg, profile)
    out, _ = _run_chain(text, cfg, chain, profile)
    return out


def split_with_diagnostics(text: str, cfg: ChunkConfig) -> tuple[list[Chunk], Diagnostics]:
    """Chunk with a diagnostic trace for the preview/debug endpoint."""
    if not text or not text.strip():
        return [], Diagnostics(selected_tier=TIER_LEGACY)
    cfg = ensure_defaults(cfg)
    profile = profile_document(text)
    chain = resolve_chain(cfg, profile)
    out, diag = _run_chain(text, cfg, chain, profile)
    diag.profile = profile.to_dict()
    return out, diag


def split_parent_child(
    text: str, cfg: ChunkConfig
) -> tuple[list[Chunk], list[ParentChildChunk]]:
    """Two-level split: child chunks for matching, parent chunks for context.

    Children are produced from the parent's own text (small, dense) and each
    child records the parent_seq it belongs to. Retrieval should match on
    children and return the parent's full content.
    """
    if not text or not text.strip():
        return [], []

    # Parents use the (possibly token-limited) size, forced recursive-ish so
    # a parent is a coherent section.
    parent_cfg = ChunkConfig(
        chunk_size=cfg.parent_chunk_size,
        chunk_overlap=cfg.chunk_overlap,
        separators=cfg.separators,
        strategy=cfg.strategy,
        token_limit=cfg.token_limit,
        languages=cfg.languages,
    )
    parent_chunks = split(text, parent_cfg)

    child_cfg = ChunkConfig(
        chunk_size=cfg.child_chunk_size,
        chunk_overlap=cfg.chunk_overlap,
        separators=cfg.separators,
        strategy=cfg.strategy,
        token_limit=0,  # children always use char budget
        languages=cfg.languages,
    )

    children: list[Chunk] = []
    parents_out: list[ParentChildChunk] = []
    for pseq, parent in enumerate(parent_chunks):
        parents_out.append(ParentChildChunk(seq=pseq, content=parent.content, parent_seq=pseq))
        kids = split(parent.content, child_cfg)
        for kid in kids:
            children.append(
                Chunk(seq=len(children), content=kid.content, parent_seq=pseq)
            )
    return children, parents_out