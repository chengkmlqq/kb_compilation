"""Pytest bootstrap: never auto-migrate a real database during tests.

api/main.py runs Alembic migrations on startup (lifespan). Tests use
sqlite :memory: fixtures + get_db overrides, so the runner must be disabled
here — otherwise the repo-root .env DATABASE_URL would point migrations at
the data_synth dev database.
"""

import os

os.environ.setdefault("AUTO_MIGRATE_ON_START", "0")

# --- test-llm 模型 seed（2026-10-28：create_kb 强制要求 summary_model_id）---
# system scope 的模型对所有用户读可见（can_see 对 system 行返回 True），
# 因此每个测试库只需 seed 一条 id="test-llm" 的 system 模型，即可满足
# create_kb 的绑定校验；create_test_kb 包装默认带上该模型 id。

TEST_LLM_MODEL_ID = "test-llm"


def seed_llm_model(db) -> None:
    """幂等插入 system scope 的 test-llm 模型行（沿用 kb_model 表结构）。"""
    from sqlalchemy import select

    from api.models.model import KbModel

    existing = db.execute(
        select(KbModel).where(KbModel.id == TEST_LLM_MODEL_ID)
    ).scalars().first()
    if existing is not None:
        return
    db.add(
        KbModel(
            id=TEST_LLM_MODEL_ID,
            scope="system",
            name="test-llm",
            display_name="测试LLM",
            type="chat",
            source="remote",
            provider="generic",
            interface_type="openai",
            status="active",
            state="1",
        )
    )
    db.commit()


def create_test_kb(db, name, **kwargs):
    """create_kb 的测试包装：默认绑定 test-llm 模型，省去逐个注入参数。

    用法与 api.services.kb_admin.create_kb 相同；未显式传
    summary_model_id 时自动补 test-llm（并保证模型行存在）。
    """
    seed_llm_model(db)
    kwargs.setdefault("summary_model_id", TEST_LLM_MODEL_ID)
    from api.services.kb_admin import create_kb

    return create_kb(db, name, **kwargs)
