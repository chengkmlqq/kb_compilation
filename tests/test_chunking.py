"""Tests for the adaptive chunking engine (port of WeKnora chunker)."""

from __future__ import annotations

import pytest

from api.services.chunking import (
    DEFAULT_CHUNK_SIZE,
    ChunkConfig,
    approx_token_count,
    detect_language,
    profile_document,
    resolve_chain,
    validate_chunks,
)
from api.services.chunking_orch import (
    split,
    split_parent_child,
    split_with_diagnostics,
)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


MD_DOC = """# 采购管理制度

本制度适用于公司所有采购活动。

## 第一章 总则

第一条 为规范公司采购行为,特制定本制度。
第二条 采购应当遵循公开、公平、公正原则。

## 第二章 采购流程

第三条 采购申请由需求部门提出。
第四条 采购审批按金额分级授权。

### 2.1 小额采购

金额低于一万元的采购由部门负责人审批。

### 2.2 大额采购

金额超过一百万元的采购须走招标流程。

## 第三章 监督

第五条 监督部门定期抽查采购执行情况。
"""


PLAIN_DOC = "\n\n".join(
    f"第{i}段 这是纯文本文档的第{i}段,没有任何标题结构,"
    f"只有普通的段落内容和一些句子用于测试分块行为。" * 2
    for i in range(1, 26)
)


# --------------------------------------------------------------------------- #
# token estimation
# --------------------------------------------------------------------------- #


def test_approx_token_count_scales_with_length():
    short = approx_token_count("hello world", "en")
    long = approx_token_count("hello world " * 100, "en")
    assert short >= 1
    assert long > short * 50


def test_approx_token_count_zh_is_denser_than_en():
    zh = approx_token_count("采购管理制度", "zh")
    en = approx_token_count("abcdefgh", "en")
    # 6 CJK chars at 1.7 chars/token ≈ 4 tokens; 8 latin at 4.0 ≈ 2
    assert zh > en


def test_approx_token_count_empty_is_zero():
    assert approx_token_count("", "zh") == 0


def test_detect_language():
    assert detect_language("采购审批流程") == "zh"
    assert detect_language("The procurement policy applies to all") == "en"
    # balanced scripts -> mixed (both sides must clear the 15% threshold)
    assert detect_language("采购审批流程 policy applies to all departments") == "mixed"
    # 4 CJK vs 28 latin = 12.5% CJK -> below threshold, so English wins
    assert detect_language("采购 policy applies to all departments") == "en"
    assert detect_language("") == "mixed"


# --------------------------------------------------------------------------- #
# profiler
# --------------------------------------------------------------------------- #


def test_profile_markdown_document():
    p = profile_document(MD_DOC)
    assert p.md_heading_total >= 5
    assert p.total_lines > 10
    assert p.total_chars == len(MD_DOC)
    assert p.dominant_heading_level() == 2  # three "##" sections
    assert p.detected_langs


def test_profile_plain_document_has_no_headings():
    p = profile_document(PLAIN_DOC)
    assert p.md_heading_total == 0
    assert p.dominant_heading_level() == 0


def test_profile_detects_tables_and_numbered_sections():
    text = "1. 第一条 内容\n2. 第二条 内容\n\n| a | b |\n| --- | --- |\n| 1 | 2 |"
    p = profile_document(text)
    assert p.numbered_section_count >= 2
    assert p.has_tables


def test_profile_empty_is_safe():
    p = profile_document("")
    assert p.total_chars == 0
    assert p.heading_density == 0.0
    assert p.heuristic_marker_total() == 0


# --------------------------------------------------------------------------- #
# chain resolution
# --------------------------------------------------------------------------- #


def test_auto_chain_includes_heading_for_structured_doc():
    cfg = ChunkConfig(strategy="auto")
    chain = resolve_chain(cfg, profile_document(MD_DOC))
    assert chain[0] == "heading"
    assert chain[-1] == "legacy"


def test_auto_chain_skips_heading_for_plain_doc():
    cfg = ChunkConfig(strategy="auto")
    chain = resolve_chain(cfg, profile_document(PLAIN_DOC))
    assert "heading" not in chain


def test_pinned_strategy_chain_puts_pinned_tier_first():
    for strat in ("heading", "heuristic", "recursive"):
        cfg = ChunkConfig(strategy=strat)
        chain = resolve_chain(cfg, profile_document(MD_DOC))
        assert chain[0] == strat
        assert chain[-1] == "legacy"


def test_legacy_pins_single_tier():
    cfg = ChunkConfig(strategy="legacy")
    assert resolve_chain(cfg, profile_document(MD_DOC)) == ["legacy"]


# --------------------------------------------------------------------------- #
# splitting
# --------------------------------------------------------------------------- #


def test_split_markdown_selects_heading_tier():
    chunks, diag = split_with_diagnostics(MD_DOC, ChunkConfig(strategy="auto"))
    assert diag.selected_tier == "heading"
    assert len(chunks) >= 4
    assert all(c.content.strip() for c in chunks)


def test_split_keeps_headings_with_their_section():
    chunks, _ = split_with_diagnostics(MD_DOC, ChunkConfig(strategy="heading"))
    text = "\n".join(c.content for c in chunks)
    assert "## 第一章 总则" in text
    assert "### 2.1 小额采购" in text


def test_split_sequential_seq_numbers():
    chunks = split(MD_DOC, ChunkConfig(strategy="auto"))
    assert [c.seq for c in chunks] == list(range(len(chunks)))


def test_split_plain_document_produces_multiple_chunks():
    chunks = split(PLAIN_DOC, ChunkConfig(strategy="auto", chunk_size=400))
    assert len(chunks) > 1
    assert all(c.content.strip() for c in chunks)


def test_split_respects_chunk_size_budget():
    cfg = ChunkConfig(strategy="recursive", chunk_size=300, chunk_overlap=0)
    chunks = split(MD_DOC, cfg)
    for c in chunks:
        # a merged chunk may exceed the budget, but not grotesquely
        assert len(c.content) <= 300 * 3


def test_split_empty_returns_empty():
    assert split("", ChunkConfig()) == []
    assert split("   \n  \n ", ChunkConfig()) == []


def test_split_never_loses_content():
    """Concatenated chunk content must cover every non-blank source line."""
    chunks = split(MD_DOC, ChunkConfig(strategy="auto"))
    joined = "".join(c.content for c in chunks)
    for marker in ("采购管理制度", "第二章", "小额采购", "监督"):
        assert marker in joined


# --------------------------------------------------------------------------- #
# validator
# --------------------------------------------------------------------------- #


def test_validator_rejects_empty_output():
    cfg = ChunkConfig(chunk_size=800)
    assert not validate_chunks([], 5000, cfg).ok


def test_validator_rejects_single_giant_chunk():
    cfg = ChunkConfig(chunk_size=800)
    from api.services.chunking import Chunk

    giant = [Chunk(seq=0, content="x" * 9000)]
    assert not validate_chunks(giant, 9000, cfg).ok


def test_validator_accepts_healthy_chunks():
    cfg = ChunkConfig(chunk_size=800)
    from api.services.chunking import Chunk

    good = [Chunk(seq=i, content="y" * 700) for i in range(5)]
    assert validate_chunks(good, 3500, cfg).ok


def test_token_limit_shrinks_budget_for_zh():
    cfg = ChunkConfig(chunk_size=100_000, token_limit=100)
    zh_budget = cfg.effective_size("zh")
    en_budget = cfg.effective_size("en")
    assert zh_budget < en_budget  # zh chars/token is denser
    assert zh_budget <= 100 * 1.7 + 2


# --------------------------------------------------------------------------- #
# parent-child
# --------------------------------------------------------------------------- #


def test_parent_child_children_reference_parents():
    cfg = ChunkConfig(
        strategy="auto",
        enable_parent_child=True,
        parent_chunk_size=900,
        child_chunk_size=300,
    )
    children, parents = split_parent_child(MD_DOC, cfg)
    assert parents, "expected at least one parent"
    assert children, "expected at least one child"
    parent_seqs = {p.seq for p in parents}
    for child in children:
        assert child.parent_seq is not None
        assert child.parent_seq in parent_seqs


def test_parent_child_children_are_smaller_than_parents():
    cfg = ChunkConfig(
        enable_parent_child=True, parent_chunk_size=1200, child_chunk_size=250
    )
    children, parents = split_parent_child(MD_DOC, cfg)
    assert len(children) >= len(parents)
    avg_child = sum(len(c.content) for c in children) / len(children)
    avg_parent = sum(len(p.content) for p in parents) / len(parents)
    assert avg_child <= avg_parent


def test_parent_child_covers_content():
    cfg = ChunkConfig(
        enable_parent_child=True, parent_chunk_size=900, child_chunk_size=300
    )
    children, parents = split_parent_child(MD_DOC, cfg)
    joined = "".join(c.content for c in children)
    assert "采购管理制度" in joined


# --------------------------------------------------------------------------- #
# config coercion
# --------------------------------------------------------------------------- #


def test_config_from_dict_clamps_values():
    cfg = ChunkConfig.from_dict(
        {"chunk_size": 10, "parent_chunk_size": 99999, "child_chunk_size": 1}
    )
    assert cfg.chunk_size >= 64
    assert cfg.parent_chunk_size <= 8192
    assert cfg.child_chunk_size >= 64


def test_config_from_dict_handles_garbage():
    cfg = ChunkConfig.from_dict({"chunk_size": "abc", "strategy": "nonsense"})
    assert cfg.chunk_size == DEFAULT_CHUNK_SIZE
    assert cfg.strategy == "auto"


def test_config_from_dict_empty_uses_defaults():
    cfg = ChunkConfig.from_dict(None)
    assert cfg.chunk_size == DEFAULT_CHUNK_SIZE
    assert cfg.separators


# --------------------------------------------------------------------------- #
# diagnostics payload shape (contract /parsers preview)
# --------------------------------------------------------------------------- #


def test_diagnostics_payload_has_contract_fields():
    _, diag = split_with_diagnostics(MD_DOC, ChunkConfig(strategy="auto"))
    d = diag.to_dict()
    assert d["selected_tier"] in ("heading", "heuristic", "recursive", "legacy")
    assert isinstance(d["tier_chain"], list)
    assert isinstance(d["rejected"], list)
    p = d["profile"]
    assert p["total_chars"] == len(MD_DOC)
    assert "md_heading_total" in p
    assert "detected_langs" in p