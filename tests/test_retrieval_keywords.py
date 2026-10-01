"""Keyword-arm tokenizer + scoring tests (pure Python, no DB).

Covers the multi-term matching behavior introduced for no-embedding
environments: query splitting (`_split_terms`) and coverage-based scoring
(`_keyword_score`).
"""

from __future__ import annotations

from api.services.retrieval import _keyword_score, _split_terms


def test_split_terms_english_words() -> None:
    assert _split_terms("What is the policy") == ["what", "is", "the", "policy"]


def test_split_terms_punctuation() -> None:
    assert _split_terms("住宿费,标准；报销") == ["住宿费", "标准", "报销"]


def test_split_terms_long_chinese_uses_bigrams() -> None:
    # 9 字段 → 2-gram 滑窗；停用词过滤后仍有代表性的「住宿」「费标」「标准」
    terms = _split_terms("出差住宿费标准是什么？")
    assert "出差" in terms
    assert "住宿" in terms
    assert "标准" in terms
    # 纯停用词的段不产生词
    assert not _split_terms("是什么")


def test_split_terms_deduplicates() -> None:
    terms = _split_terms("标准 标准")
    assert terms.count("标准") == 1


def test_keyword_score_coverage() -> None:
    content = "第三条 员工出差住宿费原则上按照城市等级确定标准：一线城市每日不超过800元。"
    terms = _split_terms("出差住宿费标准是什么？")
    score = _keyword_score(terms, content)
    assert score > 0.3  # 多个 2-gram 命中 → 覆盖率驱动高分


def test_keyword_score_misses_zero() -> None:
    content = "第三条 员工出差住宿费原则上按照城市等级确定标准。"
    score = _keyword_score(["今天", "天气"], content)
    assert score == 0.0


def test_keyword_score_no_keywords_zero() -> None:
    assert _keyword_score([], "any content") == 0.0
    assert _keyword_score(["abc"], "") == 0.0