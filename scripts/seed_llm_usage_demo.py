#!/usr/bin/env python3
"""LLM 使用看板演示数据生成器（2026-10-16）。

一键灌入近 N 天的模拟 LLM 使用记录（kb_llm_usage），方便验收看板图表；
`--clear` 清空演示数据（只清本表，不影响任何业务表/真实埋点）。

用法：
    python3 scripts/seed_llm_usage_demo.py [--days 3] [--clear]

数据特征（确定性随机，seed 固定可复现）：
- 时段：近 N 天（含今天），每小时 5~40 次调用（构建峰值集中在 02-06 时批跑）
- 来源：wiki_build 65% / chat 30% / agent 5%
- 模型：deepseek-v4-pro / mimo-v2.5 / glm-4-plus
- 状态：成功 92% / error 5% / timeout 2% / context_overflow 1%
- 知识库：优先取真实 kb_datasource，空库时用演示名
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")  # 仓库根，保证 `from api...` 可导入

from sqlalchemy import select  # noqa: E402

from api.db import get_sessionmaker  # noqa: E402
from api.models.knowledge import KbDatasource, LlmUsageRecord  # noqa: E402

DEMO_KBS = [
    ("kb_demo_tech", "技术文档库"),
    ("kb_demo_reg", "监管制度库"),
    ("kb_demo_metric", "指标库"),
    ("kb_demo_spec", "规范分册库"),
    ("kb_demo_code", "代码知识库"),
]

MODELS = ["deepseek-v4-pro", "mimo-v2.5", "glm-4-plus"]
OPS = ["summary", "entity", "rule", "relation", "parse", "qa", "agent", "filter"]
PHASES = {
    "summary": "gen_summary",
    "entity": "extract_entities",
    "rule": "extract_entities",
    "relation": "build_relations",
    "parse": "extract_long_sentences",
    "qa": "",
    "agent": "",
    "filter": "filter_terms",
}
SOURCES = ["wiki_build", "wiki_build", "wiki_build", "chat", "chat", "agent"]
ERRORS = [
    ("上游 LLM 服务返回 500（瞬时故障）", "error"),
    ("LLM 响应解析失败: json decode error, retrying", "error"),
    ("LLM 总时长超限 660s（慢流挂起），抛错", "timeout"),
    ("上游超时（120s 无响应），已重试", "timeout"),
    ("输入超长：context length exceeded, max 131072", "context_overflow"),
    ("上下文超限：prompt is too long (18432 > 16384)", "context_overflow"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=3, help="生成近 N 天数据（默认 3）")
    ap.add_argument("--clear", action="store_true", help="仅清空 kb_llm_usage，不生成")
    args = ap.parse_args()

    db = get_sessionmaker()()
    try:
        if args.clear:
            n = db.query(LlmUsageRecord).delete()
            db.commit()
            print(f"已清空 kb_llm_usage（{n} 条）")
            return 0

        real_kbs = db.execute(select(KbDatasource.id, KbDatasource.name)).all()
        kb_pool = [(str(i), str(n)) for i, n in real_kbs] or DEMO_KBS
        docs = ["API手册.pdf", "监管办法.docx", "指标体系规划.xlsx", "流程规范.md",
                "合同管理办法.docx", "数据字典.pdf", "规章制度汇编.docx", "接口说明文档.docx"]

        rnd = random.Random(42)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        created = 0
        for day in range(args.days):
            day_start = (now - timedelta(days=day)).replace(hour=0, minute=0, second=0, microsecond=0)
            for hour in range(24):
                # 批跑高峰 02-06 时，其余平缓
                peak = 2 <= hour <= 6
                count = rnd.randint(20, 40) if peak else rnd.randint(5, 18)
                for _ in range(count):
                    ts = day_start + timedelta(hours=hour) + timedelta(seconds=rnd.randint(0, 3599))
                    kb_id, kb_name = kb_pool[rnd.randrange(len(kb_pool))]
                    source = SOURCES[rnd.randrange(len(SOURCES))]
                    op = rnd.choice(OPS if source == "wiki_build" else (["qa"] if source == "chat" else ["agent"]))
                    model = rnd.choice(MODELS)
                    if source == "chat":
                        ttft = round(rnd.uniform(0.8, 8.0), 2)
                        total = round(rnd.uniform(3.0, 60.0), 2)
                    else:
                        ttft = None
                        total = round(rnd.uniform(5.0, 300.0), 2)
                    input_tokens = rnd.randint(8000, 480000) if source != "chat" else rnd.randint(1500, 20000)
                    output_tokens = rnd.randint(500, 60000) if source != "chat" else rnd.randint(200, 2500)
                    # 缓存命中占比与 input 关联（重复调用高 → 命中高）
                    cache_read = int(input_tokens * rnd.uniform(0.1, 0.85))
                    cache_creation = int(input_tokens * rnd.uniform(0.0, 0.2))
                    roll = rnd.random()
                    if roll < 0.92:
                        status, err = "success", ""
                    else:
                        err, status = ERRORS[rnd.randrange(len(ERRORS))]
                    doc = rnd.choice(docs) if source == "wiki_build" else ""
                    db.add(LlmUsageRecord(
                        trace_id=f"req_{rnd.getrandbits(48):012x}" if source == "chat" else f"job_{rnd.getrandbits(48):012x}",
                        ts=ts, source=source, kb_id=kb_id, kb_name=kb_name,
                        doc_name=doc or None, model=model, operation_type=op,
                        phase=PHASES.get(op) or None,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                        cache_read_tokens=cache_read, cache_creation_tokens=cache_creation,
                        ttft_ms=ttft, total_ms=int(total * 1000), status=status,
                        error_msg=err or None, retries=rnd.randint(0, 3) if status != "success" else 0,
                    ))
                    created += 1
                    if created % 500 == 0:
                        db.commit()
        db.commit()
        total_cnt = db.query(LlmUsageRecord).count()
        print(f"已生成 {created} 条演示记录（kb_llm_usage 现有 {total_cnt} 条）")
        print("看板入口：左侧菜单「运维监控 → LLM 使用」，或直接访问 /usage")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())