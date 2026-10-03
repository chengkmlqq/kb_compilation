"""Tier splitters for the adaptive chunking engine.

Each `split_<tier>` returns a list of Chunk. Tiers are intentionally
conservative: they only propose boundaries, and the validator in
chunking.py decides whether the proposal is good enough (otherwise the
chain advances to the next tier and finally to legacy).

Ported from WeKnora internal/infrastructure/chunker:
    heading_splitter.go     -> split_heading
    heading_hierarchy.go    -> _split_by_hierarchy
    heuristic_splitter.go   -> split_heuristic
    splitter.go (legacy)    -> split_recursive / split_legacy
"""

from __future__ import annotations

import re

from api.services.chunking import (
    LANG_MIXED,
    Chunk,
    ChunkConfig,
    DocProfile,
)

# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _is_fence_boundary(text: str, idx: int) -> bool:
    """True when idx falls inside an unterminated code fence.

    A chunk boundary must never land mid-fence or the code block is broken
    across chunks with unbalanced markers.
    """
    if "```" not in text and "~~~" not in text:
        return False
    prefix = text[:idx]
    opens = 0
    for line in prefix.split("\n"):
        if _FENCE_RE.match(line):
            opens += 1
    return opens % 2 == 1


def _hard_wrap(segment: str, size: int, overlap: int) -> list[str]:
    """Split an oversized segment on line, then on sentence, then hard."""
    if len(segment) <= size:
        return [segment]

    out: list[str] = []
    for para in re.split(r"\n{2,}", segment):
        para = para.strip("\n")
        if not para.strip():
            continue
        if len(para) <= size:
            out.append(para)
            continue
        out.extend(_split_long_para(para, size, overlap))
    return out


_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?;；])|(?<=\.\s)")


def _split_long_para(para: str, size: int, overlap: int) -> list[str]:
    """Sentence-aware split with character-level fallback."""
    sentences = [s for s in _SENT_SPLIT_RE.split(para) if s]
    if len(sentences) <= 1:
        sentences = None

    pieces: list[str] = []
    if sentences:
        buf = ""
        for s in sentences:
            if len(buf) + len(s) <= size:
                buf += s
            else:
                if buf:
                    pieces.append(buf)
                if len(s) <= size:
                    buf = s
                else:
                    pieces.extend(_hard_chars(s, size))
                    buf = ""
        if buf:
            pieces.append(buf)
    else:
        pieces = _hard_chars(para, size)

    return _apply_overlap(pieces, size, overlap)


def _hard_chars(text: str, size: int, step: int | None = None) -> list[str]:
    step = step or size
    return [text[i : i + size] for i in range(0, len(text), step)]


def _apply_overlap(pieces: list[str], size: int, overlap: int) -> list[str]:
    """Prepend the tail of the previous piece so context bleeds across cuts."""
    if overlap <= 0 or len(pieces) < 2:
        return pieces
    out: list[str] = []
    for i, piece in enumerate(pieces):
        if i == 0:
            out.append(piece)
            continue
        prev_tail = pieces[i - 1][-overlap:]
        merged = prev_tail + piece
        if len(merged) > size * 2:
            out.append(piece)
        else:
            out.append(merged)
    return out


def _finalize(pieces: list[str], cfg: ChunkConfig) -> list[Chunk]:
    chunks: list[Chunk] = []
    for p in pieces:
        if p is None:
            continue
        text = p.strip()
        if not text:
            continue
        chunks.append(Chunk(seq=len(chunks), content=text))
    return chunks


# --------------------------------------------------------------------------- #
# Tier 1: heading-aware
# --------------------------------------------------------------------------- #


def _sections_by_heading(text: str, level: int) -> list[str]:
    """Split at headings of exactly `level`, keeping the heading line with
    its section (the heading carries the section's topic)."""
    if level <= 0:
        return [text]
    pattern = re.compile(rf"^(#{{{level}}})\s+\S", re.MULTILINE)
    starts = [m.start() for m in pattern.finditer(text)]
    if not starts:
        return [text]

    # No leading section -> preamble before the first heading
    sections: list[str] = []
    if starts[0] > 0:
        pre = text[: starts[0]].strip()
        if pre:
            sections.append(pre)

    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        seg = text[start:end].strip()
        if seg:
            sections.append(seg)
    return sections


def _split_by_hierarchy(text: str, cfg: ChunkConfig, profile: DocProfile) -> list[str]:
    """Try the dominant heading level, then finer levels.

    Each level is tried on its own: the caller runs the validator over the
    result, and if it is rejected the chain advances to the next tier.
    """
    level = profile.dominant_heading_level()
    if level <= 0:
        return []

    lang = cfg.lang_for(text)
    size = cfg.effective_size(lang)
    lang_label = lang if lang else LANG_MIXED

    candidates: list[str] = []
    for lvl in (level, min(level + 1, 6)):
        sections = _sections_by_heading(text, lvl)
        if len(sections) < 2:
            continue
        pieces: list[str] = []
        for sec in sections:
            if len(sec) <= size:
                pieces.append(sec)
            else:
                pieces.extend(_hard_wrap(sec, size, cfg.chunk_overlap))
        if pieces:
            candidates = pieces
            break
    return candidates


def split_heading(text: str, cfg: ChunkConfig, profile: DocProfile) -> list[Chunk]:
    if profile.md_heading_total < 2:
        return []
    pieces = _split_by_hierarchy(text, cfg, profile)
    if not pieces:
        return []
    return _finalize(pieces, cfg)


# --------------------------------------------------------------------------- #
# Tier 2: heuristic (non-markdown structure)
# --------------------------------------------------------------------------- #

_HEUR_PATTERNS = (
    re.compile(r"^\s*第\s*[一二三四五六七八九十百零〇\d]+\s*[章节節篇部分]", re.MULTILINE),
    re.compile(r"^\s*(chapter|section|part|appendix)\s+[0-9ivxIVX]+", re.MULTILINE | re.I),
    re.compile(r"^\s*\d+(?:\.\d+)*[.、)\s]\s+\S", re.MULTILINE),
    re.compile(r"^\s*([-=_*─-╿—–]{3,})\s*$", re.MULTILINE),
    re.compile(r"^\s*[A-Z][A-Z\s]{4,60}$", re.MULTILINE),
    re.compile(r"\f"),
)


def _heuristic_boundaries(text: str) -> list[int]:
    """Offsets where a structural marker line starts."""
    offsets: set[int] = set()
    for pat in _HEUR_PATTERNS:
        for m in pat.finditer(text):
            # trim the leading indentation/newlines so the marker stays with
            # its section content
            offsets.add(m.start())
    return sorted(offsets)


def split_heuristic(text: str, cfg: ChunkConfig, profile: DocProfile) -> list[Chunk]:
    if profile.heuristic_marker_total() < 2:
        return []

    lang = cfg.lang_for(text)
    size = cfg.effective_size(lang)

    bounds = _heuristic_boundaries(text)
    if not bounds:
        return []

    pieces: list[str] = []
    if bounds[0] > 0:
        pre = text[: bounds[0]].strip()
        if pre:
            pieces.append(pre)
    for i, start in enumerate(bounds):
        end = bounds[i + 1] if i + 1 < len(bounds) else len(text)
        seg = text[start:end].strip()
        if not seg:
            continue
        if len(seg) <= size:
            pieces.append(seg)
        else:
            pieces.extend(_hard_wrap(seg, size, cfg.chunk_overlap))
    return _finalize(pieces, cfg)


# --------------------------------------------------------------------------- #
# Tier 3/4: recursive + legacy
# --------------------------------------------------------------------------- #


def _recursive_split(
    text: str, size: int, overlap: int, separators: list[str]
) -> list[str]:
    """Recursive-character style splitting on the configured separators."""
    for sep in separators:
        if not sep:
            continue
        parts = text.split(sep)
        parts = [p for p in parts if p and p.strip()]
        if len(parts) < 2:
            continue

        pieces: list[str] = []
        buf = ""
        for part in parts:
            candidate = buf + sep + part if buf else part
            if len(candidate) <= size:
                buf = candidate
                continue
            if buf:
                pieces.append(buf)
            if len(part) <= size:
                buf = part
            else:
                # separator did not help -> recurse into the long part
                pieces.extend(_recursive_split(part, size, overlap, separators))
                buf = ""
        if buf:
            pieces.append(buf)
        if pieces:
            return pieces
    # no separator matched -> hard wrap
    return _hard_wrap(text, size, overlap)


def split_recursive(text: str, cfg: ChunkConfig) -> list[Chunk]:
    lang = cfg.lang_for(text)
    size = cfg.effective_size(lang)
    if not text.strip():
        return []
    pieces = _recursive_split(text, size, cfg.chunk_overlap, cfg.separators)
    return _finalize(pieces, cfg)


def split_legacy(text: str, cfg: ChunkConfig) -> list[Chunk]:
    """Final fallback: hard wrap with overlap, fence-aware."""
    if not text.strip():
        return []
    lang = cfg.lang_for(text)
    size = cfg.effective_size(lang)
    pieces: list[str] = []
    for block in re.split(r"\n{2,}", text):
        block = block.strip("\n")
        if not block.strip():
            continue
        if len(block) <= size:
            pieces.append(block)
        else:
            pieces.extend(_hard_wrap(block, size, cfg.chunk_overlap))
    if not pieces:
        pieces = [text.strip()]
    return _finalize(_apply_overlap(pieces, size, cfg.chunk_overlap), cfg)


# --------------------------------------------------------------------------- #
# Tier dispatch
# --------------------------------------------------------------------------- #


def run_tier(tier: str, text: str, cfg: ChunkConfig, profile: DocProfile) -> list[Chunk]:
    if tier == "heading":
        return split_heading(text, cfg, profile)
    if tier == "heuristic":
        return split_heuristic(text, cfg, profile)
    if tier == "recursive":
        return split_recursive(text, cfg)
    if tier == "legacy":
        return split_legacy(text, cfg)
    return []