"""Adaptive chunking engine — port of WeKnora's internal/infrastructure/chunker.

Python port covering the tiers, document profiler, conservative token
estimation, validator and parent-child splitting. WeKnora's tier chain
(heading -> heuristic -> recursive -> legacy) and its "first tier whose
chunks validate wins, otherwise fall through to legacy" contract are
preserved.

Tier semantics (aligned with WeKnora chunker/strategy.go):
    auto      — profiler picks the chain from the document profile
    heading   — markdown heading hierarchy drives section boundaries
    heuristic — non-markdown structural markers (numbered sections,
                chapter headings, ALL-CAPS lines, visual separators)
    recursive — separator-driven recursive splitting
    legacy    — the historical recursive splitter (final fallback)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- #
# Strategy / tier constants
# --------------------------------------------------------------------------- #

STRATEGY_AUTO = "auto"
STRATEGY_HEADING = "heading"
STRATEGY_HEURISTIC = "heuristic"
STRATEGY_RECURSIVE = "recursive"
STRATEGY_LEGACY = "legacy"

STRATEGY_VALUES = (
    STRATEGY_HEADING,
    STRATEGY_HEURISTIC,
    STRATEGY_RECURSIVE,
    STRATEGY_LEGACY,
)

TIER_HEADING = "heading"
TIER_HEURISTIC = "heuristic"
TIER_RECURSIVE = "recursive"
TIER_LEGACY = "legacy"

TIER_ORDER = (TIER_HEADING, TIER_HEURISTIC, TIER_RECURSIVE, TIER_LEGACY)

# Chain tried when strategy == auto, ordered by how much structure the
# profiler detected. Kept as (heading, heuristic, recursive, legacy) so a
# document that fails validation at each tier still lands on legacy.
AUTO_CHAIN_STRUCTURED = (TIER_HEADING, TIER_HEURISTIC, TIER_RECURSIVE, TIER_LEGACY)
AUTO_CHAIN_PLAIN = (TIER_RECURSIVE, TIER_HEURISTIC, TIER_LEGACY)

# --------------------------------------------------------------------------- #
# Defaults (WeKnora internal/infrastructure/chunker/splitter.go DefaultConfig)
# --------------------------------------------------------------------------- #

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 80
DEFAULT_PARENT_CHUNK_SIZE = 4096
DEFAULT_CHILD_CHUNK_SIZE = 384
DEFAULT_SEPARATORS = ["\n## ", "\n\n", "\n"]
DEFAULT_TOKEN_LIMIT = 0

PARENT_MIN, PARENT_MAX = 512, 8192
CHILD_MIN, CHILD_MAX = 64, 2048


# --------------------------------------------------------------------------- #
# Token estimation (port of chunker/tokens.go)
# --------------------------------------------------------------------------- #

LANG_EN = "en"
LANG_DE = "de"
LANG_ZH = "zh"
LANG_MIXED = "mixed"

CHARS_PER_TOKEN = {
    LANG_EN: 4.0,
    LANG_DE: 4.5,
    LANG_ZH: 1.7,
    LANG_MIXED: 3.0,
}

_GERMAN_UMLAUTS = set("äöüÄÖÜß")
_GERMAN_STOPWORDS = (
    " der ", " die ", " das ", " und ", " ist ", " nicht ", " mit ", " auf ",
)


def approx_token_count(text: str, lang: str = LANG_MIXED) -> int:
    """Conservative token estimate (no tokenizer dependency).

    Mirrors WeKnora: rune length divided by a per-language chars/token
    ratio, rounded up, minimum 1.
    """
    if not text:
        return 0
    ratio = CHARS_PER_TOKEN.get(lang, CHARS_PER_TOKEN[LANG_MIXED])
    approx = len(text) / ratio
    if approx < 1:
        return 1
    return int(approx + 0.5)


def approx_token_count_runes(rune_len: int, lang: str = LANG_MIXED) -> int:
    if rune_len <= 0:
        return 0
    ratio = CHARS_PER_TOKEN.get(lang, CHARS_PER_TOKEN[LANG_MIXED])
    approx = rune_len / ratio
    return 1 if approx < 1 else int(approx + 0.5)


def detect_language(text: str) -> str:
    """Coarse script-based language label (heuristic dispatch only)."""
    if not text:
        return LANG_MIXED
    cjk = latin = umlaut = 0
    for ch in text:
        code = ord(ch)
        if (
            0x4E00 <= code <= 0x9FFF
            or 0x3400 <= code <= 0x4DBF
            or 0x3040 <= code <= 0x30FF
            or 0xAC00 <= code <= 0xD7AF
        ):
            cjk += 1
        elif ch in _GERMAN_UMLAUTS:
            umlaut += 1
            latin += 1
        elif (65 <= code <= 90) or (97 <= code <= 122):
            latin += 1
    total = cjk + latin
    if total == 0:
        return LANG_MIXED
    cjk_ratio = cjk / total
    latin_ratio = latin / total
    if cjk_ratio >= 0.15 and latin_ratio >= 0.15:
        return LANG_MIXED
    if cjk_ratio > 0.3:
        return LANG_ZH
    if umlaut > 0 or _has_german_words(text):
        return LANG_DE
    return LANG_EN


def _has_german_words(text: str) -> bool:
    sample = text[:512].lower()
    return any(w in sample for w in _GERMAN_STOPWORDS)


# --------------------------------------------------------------------------- #
# Document profiler (port of chunker/profiler.go ProfileDocument)
# --------------------------------------------------------------------------- #

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")
_NUMBERED_SECTION_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)[.、)\s]\s+\S")
_CHINESE_CHAPTER_RE = re.compile(r"^\s*第\s*[一二三四五六七八九十百零〇\d]+\s*[章节節篇部分]")
_ENGLISH_CHAPTER_RE = re.compile(r"^\s*(chapter|section|part|appendix)\s+[0-9ivxIVX]+", re.I)
_VISUAL_SEP_RE = re.compile(r"^\s*([-=_*─-╿—–]{3,})\s*$")
_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass
class DocProfile:
    """Document-level signals driving tier selection."""

    total_chars: int = 0
    total_lines: int = 0
    avg_line_len: float = 0.0
    std_line_len: float = 0.0
    md_heading_counts: dict[int, int] = field(default_factory=dict)
    md_heading_total: int = 0
    numbered_section_count: int = 0
    all_caps_short_line_count: int = 0
    blank_paragraph_breaks: int = 0
    form_feed_count: int = 0
    visual_sep_count: int = 0
    german_chapter_count: int = 0
    english_chapter_count: int = 0
    chinese_chapter_count: int = 0
    repeated_footer_count: int = 0
    has_tables: bool = False
    has_code: bool = False
    code_ratio: float = 0.0
    detected_langs: list[str] = field(default_factory=list)

    @property
    def heading_density(self) -> float:
        if self.total_lines == 0:
            return 0.0
        return self.md_heading_total / self.total_lines

    def dominant_heading_level(self) -> int:
        """Lowest level with >=3 occurrences, else deepest present, else 0."""
        if self.md_heading_total == 0:
            return 0
        for level in range(1, 7):
            if self.md_heading_counts.get(level, 0) >= 3:
                return level
        for level in range(6, 0, -1):
            if self.md_heading_counts.get(level, 0) > 0:
                return level
        return 0

    def heuristic_marker_total(self) -> int:
        return (
            self.numbered_section_count
            + self.german_chapter_count
            + self.english_chapter_count
            + self.chinese_chapter_count
            + self.all_caps_short_line_count
            + self.visual_sep_count
            + self.form_feed_count
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_chars": self.total_chars,
            "total_lines": self.total_lines,
            "avg_line_len": round(self.avg_line_len, 2),
            "std_line_len": round(self.std_line_len, 2),
            "md_heading_counts": {str(k): v for k, v in sorted(self.md_heading_counts.items())},
            "md_heading_total": self.md_heading_total,
            "heading_density": round(self.heading_density, 4),
            "dominant_heading_level": self.dominant_heading_level(),
            "numbered_section_count": self.numbered_section_count,
            "all_caps_short_line_count": self.all_caps_short_line_count,
            "blank_paragraph_breaks": self.blank_paragraph_breaks,
            "form_feed_count": self.form_feed_count,
            "visual_sep_count": self.visual_sep_count,
            "german_chapter_count": self.german_chapter_count,
            "english_chapter_count": self.english_chapter_count,
            "chinese_chapter_count": self.chinese_chapter_count,
            "repeated_footer_count": self.repeated_footer_count,
            "has_tables": self.has_tables,
            "has_code": self.has_code,
            "code_ratio": round(self.code_ratio, 4),
            "detected_langs": list(self.detected_langs),
        }


def profile_document(text: str) -> DocProfile:
    """Single pass over the text collecting structure indicators."""
    p = DocProfile()
    if not text:
        return p

    p.total_chars = len(text)
    p.form_feed_count = text.count("\f")

    lines = text.split("\n")
    p.total_lines = len(lines)

    lengths: list[float] = []
    in_fence = False
    code_chars = 0
    table_lines = 0
    footer_candidates: dict[str, int] = {}

    for line in lines:
        lengths.append(len(line))

        fence = _FENCE_RE.match(line)
        if fence:
            in_fence = not in_fence
            p.has_code = True
            code_chars += len(line)
            continue
        if in_fence:
            code_chars += len(line)
            continue

        heading = _MD_HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            p.md_heading_counts[level] = p.md_heading_counts.get(level, 0) + 1
            p.md_heading_total += 1
            continue

        stripped = line.strip()
        if not stripped:
            p.blank_paragraph_breaks += 1
            continue

        if _NUMBERED_SECTION_RE.match(line):
            p.numbered_section_count += 1
        if _CHINESE_CHAPTER_RE.match(line):
            p.chinese_chapter_count += 1
        elif _ENGLISH_CHAPTER_RE.match(line):
            p.english_chapter_count += 1
        if _VISUAL_SEP_RE.match(line):
            p.visual_sep_count += 1
        if _TABLE_RE.match(line):
            p.has_tables = True
            table_lines += 1

        # ALL-CAPS short lines act as section markers in plain-text docs
        letters = [c for c in stripped if c.isalpha()]
        if stripped and len(stripped) <= 60 and letters and all(
            c.isupper() for c in letters
        ):
            p.all_caps_short_line_count += 1

        if len(stripped) <= 80:
            footer_candidates[stripped] = footer_candidates.get(stripped, 0) + 1

    if lengths:
        p.avg_line_len = sum(lengths) / len(lengths)
        mean = p.avg_line_len
        var = sum((x - mean) ** 2 for x in lengths) / len(lengths)
        p.std_line_len = math.sqrt(var)

    p.code_ratio = (code_chars / p.total_chars) if p.total_chars else 0.0
    p.repeated_footer_count = sum(
        c for c in footer_candidates.values() if c >= 3
    )
    if table_lines:
        p.has_tables = True

    lang = detect_language(text)
    p.detected_langs = [lang]
    return p


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #


@dataclass
class ChunkConfig:
    """Mirrors WeKnora's types.ChunkingConfig split/section."""

    chunk_size: int = DEFAULT_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
    separators: list[str] = field(default_factory=lambda: list(DEFAULT_SEPARATORS))
    strategy: str = STRATEGY_AUTO
    token_limit: int = DEFAULT_TOKEN_LIMIT
    languages: list[str] = field(default_factory=list)
    enable_parent_child: bool = False
    parent_chunk_size: int = DEFAULT_PARENT_CHUNK_SIZE
    child_chunk_size: int = DEFAULT_CHILD_CHUNK_SIZE

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "ChunkConfig":
        raw = raw or {}
        seps = raw.get("separators")
        langs = raw.get("languages")
        return cls(
            chunk_size=_clamp_int(
                raw.get("chunk_size"), DEFAULT_CHUNK_SIZE, 64, 32768
            ),
            chunk_overlap=_clamp_int(
                raw.get("chunk_overlap"), DEFAULT_CHUNK_OVERLAP, 0, 4096
            ),
            separators=(
                [str(s) for s in seps if str(s)]
                if isinstance(seps, list) and seps
                else list(DEFAULT_SEPARATORS)
            ),
            strategy=_norm_strategy(raw.get("strategy")),
            token_limit=_clamp_int(raw.get("token_limit"), DEFAULT_TOKEN_LIMIT, 0, 1_000_000),
            languages=(
                [str(x) for x in langs if str(x)]
                if isinstance(langs, list)
                else []
            ),
            enable_parent_child=bool(raw.get("enable_parent_child")),
            parent_chunk_size=_clamp_int(
                raw.get("parent_chunk_size"), DEFAULT_PARENT_CHUNK_SIZE, PARENT_MIN, PARENT_MAX
            ),
            child_chunk_size=_clamp_int(
                raw.get("child_chunk_size"), DEFAULT_CHILD_CHUNK_SIZE, CHILD_MIN, CHILD_MAX
            ),
        )

    def effective_size(self, lang: str) -> int:
        """Token limit caps the size budget; 0 means char count."""
        if self.token_limit and self.token_limit > 0:
            ratio = CHARS_PER_TOKEN.get(lang, CHARS_PER_TOKEN[LANG_MIXED])
            return max(64, int(self.token_limit * ratio))
        return self.chunk_size

    def lang_for(self, text: str) -> str:
        if self.languages:
            return self.languages[0]
        return detect_language(text)


def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _norm_strategy(value: Any) -> str:
    s = str(value or "").strip().lower()
    if s in (STRATEGY_AUTO, ""):
        return STRATEGY_AUTO
    if s in STRATEGY_VALUES:
        return s
    return STRATEGY_AUTO


# --------------------------------------------------------------------------- #
# Chunk model
# --------------------------------------------------------------------------- #


@dataclass
class Chunk:
    seq: int
    content: str
    parent_seq: int | None = None

    @property
    def is_parent(self) -> bool:
        return False


@dataclass
class ParentChildChunk:
    seq: int
    content: str
    parent_seq: int


@dataclass
class Diagnostics:
    selected_tier: str = TIER_LEGACY
    tier_chain: list[str] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    profile: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_tier": self.selected_tier,
            "tier_chain": list(self.tier_chain),
            "rejected": list(self.rejected),
            "profile": self.profile,
        }


def resolve_chain(cfg: ChunkConfig, profile: DocProfile) -> list[str]:
    """Pick the tier chain for this config + profile."""
    strat = cfg.strategy
    if strat in (TIER_HEADING, TIER_HEURISTIC, TIER_RECURSIVE, TIER_LEGACY):
        if strat == TIER_LEGACY:
            return [TIER_LEGACY]
        return [strat, TIER_LEGACY]
    # auto: structured docs get the full chain, plain docs skip heading
    structured = (
        profile.md_heading_total >= 2
        or profile.heuristic_marker_total() >= 3
    )
    return list(AUTO_CHAIN_STRUCTURED if structured else AUTO_CHAIN_PLAIN)


# --------------------------------------------------------------------------- #
# Validator (port of chunker/validator.go)
# --------------------------------------------------------------------------- #


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""


def validate_chunks(
    chunks: list[Chunk], total_chars: int, cfg: ChunkConfig, lang: str = LANG_MIXED
) -> ValidationResult:
    """Reject tiers that produce empty output or absurdly oversized chunks."""
    if not chunks:
        return ValidationResult(False, "no chunks produced")
    nonempty = [c for c in chunks if c.content.strip()]
    if not nonempty:
        return ValidationResult(False, "all chunks empty")

    budget = cfg.effective_size(lang)
    hard_cap = budget * 4  # a legit chunk may exceed the target after merge
    oversized = [c for c in nonempty if len(c.content) > hard_cap]
    if oversized and len(oversized) >= max(1, len(nonempty) // 4):
        return ValidationResult(
            False, f"{len(oversized)}/{len(nonempty)} chunks exceed {hard_cap} chars"
        )

    # degenerate output: one giant chunk when the text clearly should split
    if len(nonempty) == 1 and total_chars > budget * 3:
        return ValidationResult(
            False, f"single chunk for {total_chars} chars (budget {budget})"
        )

    return ValidationResult(True)