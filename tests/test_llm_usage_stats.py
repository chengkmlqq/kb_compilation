"""LLM 使用记录与统计服务测试（2026-10-16 LLM 使用看板）。

对齐 test_feedback_stats 的 SQLite 内存库模式：服务级直测，
覆盖 写入（record_llm_usage）/ 汇总 / 分布 / 趋势分桶 / 明细筛选排序 /
业务产出口径 / 错误分类。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models import knowledge  # noqa: F401  (register tables)
from api.models.knowledge import KbDocument, WikiPage
from api.services import llm_usage as usage_svc
from api.services import llm_usage_stats as stats

NOW = datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _seed_rows(db, base: datetime | None = None, n: int = 10):
    """写 n 条使用记录：变量分布在近 6 小时内（summary/summary/qa + 缓存命中）。"""
    base = base or NOW
    for i in range(n):
        usage_svc.record_llm_usage(
            source="wiki_build" if i % 3 else "chat",
            kb_id="kb1" if i % 2 == 0 else "kb2",
            kb_name="知识库A" if i % 2 == 0 else "知识库B",
            doc_name=f"文档{i}.pdf" if i % 3 else "",
            model="deepseek-v4-pro" if i % 2 == 0 else "mimo-v2.5",
            operation_type="summary" if i % 3 != 2 else "qa",
            input_tokens=1000 + i * 100,
            output_tokens=200 + i * 10,
            cache_read_tokens=(i * 300) if i % 2 == 0 else 0,
            ttft_ms=2.0 if i % 3 else None,
            total_ms=30000 + i * 100,
            status="success",
            ts=base - timedelta(hours=i),
            db=db,
        )


def _seed_business(db):
    for i, status in ((1, "active"), (2, "active"), (3, "draft")):
        db.add(
            WikiPage(
                id=f"w{i}",
                kb_id="kb1",
                slug=f"page-{i}",
                title=f"页面{i}",
                status=status,
                created_at=NOW - timedelta(hours=2),
            )
        )
    for i, state in ((1, "READY"), (2, "READY"), (3, "FAILED")):
        db.add(
            KbDocument(
                id=f"d{i}",
                kb_id="kb1",
                file_name=f"doc{i}.pdf",
                parse_state=state,
                created_at=NOW - timedelta(hours=5),
                updated_at=NOW - timedelta(hours=2),
            )
        )
    db.commit()


# ---------- 写入与错误分类 ----------

def test_record_and_counts(db) -> None:
    _seed_rows(db, n=6)
    s = stats.summary(db, start=NOW - timedelta(days=1), end=NOW)
    assert s["totalRequests"] == 6
    assert s["totalTokens"] == s["inputTokens"] + s["outputTokens"]
    assert s["inputTokens"] > 0
    assert s["cacheReadTokens"] > 0
    assert s["cacheHitRate"] > 0
    assert s["avgLatency"]["total"] is not None
    assert s["avgLatency"]["ttft"] is not None  # chat 记录有 ttft，wiki 无 → 均值仅算非空
    assert s["failedCount"] == 0


def test_errors_classified(db) -> None:
    usage_svc.record_llm_usage(
        source="chat", model="m", input_tokens=10, total_ms=5000,
        ts=NOW - timedelta(minutes=1),
        status=usage_svc.classify_error("上游超时（120s 无响应），已重试"), error_msg="超时",
        db=db,
    )
    usage_svc.record_llm_usage(
        source="chat", model="m", total_ms=100,
        ts=NOW - timedelta(minutes=2),
        status=usage_svc.classify_error("context length exceeded, max 131072"), error_msg="上下文超限",
        db=db,
    )
    usage_svc.record_llm_usage(
        source="chat", model="m", total_ms=100,
        ts=NOW - timedelta(minutes=3),
        status=usage_svc.classify_error("HTTP 500"), error_msg="500",
        db=db,
    )
    s = stats.summary(db, start=NOW - timedelta(hours=1), end=NOW)
    assert s["totalRequests"] == 3
    assert s["failedCount"] == 3
    breakdown = stats.details(
        db, start=NOW - timedelta(hours=1), end=NOW, status="failed"
    )["errorBreakdown"]
    assert breakdown.get("context_overflow") == 1
    assert breakdown.get("timeout") == 1
    assert breakdown.get("error") == 1


def test_business_output_counts(db) -> None:
    _seed_rows(db, n=2)
    _seed_business(db)
    s = stats.summary(db, start=NOW - timedelta(days=1), end=NOW)
    assert s["processedDocs"] == 2  # READY
    assert s["generatedKnowledge"] == 2  # active


# ---------- 分布 ----------

def test_distribution_by_kb_and_model(db) -> None:
    _seed_rows(db, n=10)
    by_kb = stats.distribution(db, start=NOW - timedelta(days=1), end=NOW, dim="kb")
    names = {x["name"] for x in by_kb}
    assert names == {"知识库A", "知识库B"}
    assert all(x["requests"] > 0 and x["inputTokens"] > 0 for x in by_kb)
    assert all("cacheTokens" in x and "outputTokens" in x for x in by_kb)

    by_model = stats.distribution(db, start=NOW - timedelta(days=1), end=NOW, dim="model")
    assert {x["name"] for x in by_model} == {"deepseek-v4-pro", "mimo-v2.5"}

    by_op = stats.distribution(db, start=NOW - timedelta(days=1), end=NOW, dim="operation")
    labels = {x["name"] for x in by_op}
    assert "摘要提取" in labels
    assert "问答生成" in labels


def test_distribution_filters(db) -> None:
    _seed_rows(db, n=10)
    by_kb = stats.distribution(
        db, start=NOW - timedelta(days=1), end=NOW, dim="kb", kb_id="kb1"
    )
    assert {x["key"] for x in by_kb} == {"kb1"}


# ---------- 趋势 ----------

def test_trend_hourly_buckets_with_zero_fill(db) -> None:
    _seed_rows(db, n=4)  # 近 4 小时
    t = stats.trend(db, start=NOW - timedelta(days=1), end=NOW, granularity="hour")
    assert len(t["timestamps"]) == 25  # 昨天此刻 → 现在，共 25 个整点桶（含零填充）
    assert len(t["inputTokens"]) == len(t["timestamps"])
    assert len(t["cacheHitRate"]) == len(t["timestamps"])
    assert all(x >= 0 for x in t["inputTokens"])
    # 至少一个桶有数据
    assert sum(t["inputTokens"]) > 0


def test_trend_daily(db) -> None:
    _seed_rows(db, n=2)
    t = stats.trend(db, start=NOW - timedelta(days=3), end=NOW, granularity="day")
    assert t["granularity"] == "day"
    assert len(t["timestamps"]) == 4


# ---------- 明细 ----------

def test_details_pagination_sort_filter(db) -> None:
    _seed_rows(db, n=15)
    r = stats.details(db, start=NOW - timedelta(days=1), end=NOW, page=1, page_size=5)
    assert r["total"] == 15
    assert len(r["items"]) == 5

    # 排序：total_ms desc
    r2 = stats.details(db, start=NOW - timedelta(days=1), end=NOW, sort="total_ms", order="desc")
    ms = [x["latency"]["total"] for x in r2["items"]]
    assert ms == sorted(ms, reverse=True)

    # 状态过滤
    r3 = stats.details(db, start=NOW - timedelta(days=1), end=NOW, status="failed")
    assert r3["total"] == 0

    # 关键字过滤（子串匹配：用完整文件名避免命中 文档10/13/14）
    r4 = stats.details(db, start=NOW - timedelta(days=1), end=NOW, keyword="文档1.pdf")
    assert r4["total"] == 1
    assert r4["items"][0]["docName"] == "文档1.pdf"

    item = r["items"][0]
    assert set(item.keys()) >= {
        "id", "kbName", "docName", "model", "operationType", "operationLabel",
        "inputTokens", "outputTokens", "cacheTokens", "latency", "status",
    }
    # 无任何金额字段
    assert not any("cost" in k.lower() or "price" in k.lower() or "fee" in k.lower() for k in item)


def test_details_ts_filter_respects_range(db) -> None:
    _seed_rows(db, n=5, base=NOW - timedelta(days=5))
    r = stats.details(db, start=NOW - timedelta(days=1), end=NOW, page_size=50)
    assert r["total"] == 0  # 记录都在 5 天前


def test_time_range_preset(db) -> None:
    s, e = stats.resolve_range("24h")
    assert (e - s).total_seconds() == 86400
    s2, e2 = stats.resolve_range("custom", start="2026-10-10T00:00:00Z", end="2026-10-12T00:00:00Z")
    assert (e2 - s2).total_seconds() == 2 * 86400
    s3, e3 = stats.resolve_range("bogus")
    assert (e3 - s3).total_seconds() == 86400