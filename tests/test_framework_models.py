"""Framework + knowledge model sanity checks.

Verifies the ORM models ported from the source Drizzle schema:
1. Exactly the expected tables are present on each declarative base —
   the framework tables + the knowledge BUSINESS tables
   (kb_datasource / kb_document / doc_chunk / wiki_* / kb_agent) on `Base`
   (portable relational types, live on the framework relational store), and
   ONLY the vector table `kb_embedding` on `KnowledgeBase` (pgvector store).
   Legacy data-synthesis business tables stay excluded.
2. Every model maps to the expected physical table name.
3. Models can create tables on an in-memory engine (structural self-consistency).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from api.db import Base, KnowledgeBase
from api.models import framework

# Expected framework tables (kept from the source Drizzle schema).
FRAMEWORK_TABLES = {
    "modo_user",
    "modo_user_role",
    "modo_user_role_rela",
    "modo_team",
    "modo_team_member",
    "modo_menu",
    "modo_role_menu_rela",
    "modo_team_ds_map",
    "modo_dim",
    "modo_datasource",
    "modo_grid_datasource",
    "modo_ds_category",
    "modo_ds_form_field",
    "modo_ds_type",
    "modo_ds_version",
    "modo_metadata_table",
    "modo_metadata_column",
    "modo_cron_task",
    "modo_job",
    "modo_job_queue",
    "modo_oper_log",
    "modo_sync_log",
    "modo_sync_detail",
    "modo_interface_log",
    "modo_system_message",
    "modo_seq",
    "modo_sys_file",
    "modo_operation_doc",
    "ai_chat_conversation",
    "ai_chat_message",
}

# Knowledge-domain BUSINESS tables — ported onto the framework base so they
# live on the same relational store as the framework tables (MySQL by
# default, portable to any relational DB). Only portable types, no vectors.
KNOWLEDGE_BUSINESS_TABLES = {
    "kb_datasource",
    "kb_document",
    "doc_chunk",
    "wiki_folder",
    "wiki_page",
    "wiki_link",
    "wiki_operation_log",
    "wiki_feedback",
    "kb_agent",
}

# Chat conversation persistence (chat_session / chat_message / chat_attachment) — framework store.
CHAT_TABLES = {
    "chat_session",
    "chat_message",
    "chat_attachment",
}

# The single vector-only table lives on the vector store base (PG + pgvector).
VECTOR_ONLY_TABLES = {
    "kb_embedding",
}

# Scoped model registry (kb_model) — framework store.
MODEL_TABLES = {
    "kb_model",
}

# Scoped MCP / skill registry (kb_mcp_server / kb_skill) — framework store.
MCP_SKILL_TABLES = {
    "kb_mcp_server",
    "kb_skill",
}

# Scoped WebSearch provider registry (kb_websearch_provider) — framework store.
WEBSEARCH_TABLES = {
    "kb_websearch_provider",
}

# Everything expected on the framework base.
EXPECTED_TABLES = FRAMEWORK_TABLES | KNOWLEDGE_BUSINESS_TABLES | CHAT_TABLES | MODEL_TABLES | MCP_SKILL_TABLES | WEBSEARCH_TABLES

# Legacy business tables (data-synthesis domain) that MUST NOT be present
# after extraction. Names are the REAL table names in the source DB — they
# stay verbatim so the exclusion assertion matches the physical schema.
EXCLUDED_TABLES = {
    "synth_task",
    "synth_host",
    "synth_tape",
    "synth_tape_version",
    "synth_tape_step",
    "synth_tape_step_draft",
    "synth_tape_step_define",
    "synth_step_define_version",
    "synth_tape_log",
    "synth_raw_dataset",
    "synth_raw_dataset_version",
    "synth_raw_dataset_version_element",
    "synth_raw_dataset_version_field",
    "synth_raw_dataset_element",
    "synth_raw_dataset_field",
    "synth_raw_dataset_relation",
    "synth_raw_dataset_biz_relation_task",
    "synth_raw_dataset_biz_relation",
    "synth_raw_dataset_fk_task",
    "synth_raw_dataset_fk_relation",
    "synth_raw_dataset_feature_task",
    "synth_raw_dataset_feature_result",
    "synth_raw_dataset_sensitive_task",
    "synth_raw_dataset_sensitive_result",
    "synth_raw_dataset_preview_sample",
    "synth_dataset",
    "synth_training",
    "synth_training_version",
    "synth_wizard_session",
    "synth_sensitive_rule",
    "synth_sensitive_rule_test_log",
    "synth_quality_alert_rule",
    "synth_quality_alert_log",
    "modo_requirement",
    "modo_requirement_entity",
    "synth_models",
    "synth_model_fields",
}

# Tables present in the live DB that are intentionally NOT modeled:
# - `*_bak_*` are operator backup tables (schema drift artifacts)
# - modo_requirement / modo_requirement_entity are legacy synthesis business (extracted out)
# - modo_user_synth_100 is an ad-hoc table from the old synthesis link
IGNORED_DB_TABLES = {
    "modo_requirement",
    "modo_requirement_entity",
    "modo_user_synth_100",
}


def test_only_framework_tables_defined() -> None:
    mapped = {t.name for t in Base.metadata.sorted_tables}
    assert mapped == EXPECTED_TABLES, f"missing={EXPECTED_TABLES - mapped}, extra={mapped - EXPECTED_TABLES}"


def test_vector_only_table_on_knowledge_base() -> None:
    """The knowledge (vector) base holds ONLY kb_embedding.

    Every other knowledge table is a business table on the framework base
    (portable relational types); the vector base is reserved for pgvector.
    """
    mapped = {t.name for t in KnowledgeBase.metadata.sorted_tables}
    assert mapped == VECTOR_ONLY_TABLES, (
        f"missing={VECTOR_ONLY_TABLES - mapped}, extra={mapped - VECTOR_ONLY_TABLES}"
    )
    assert mapped.isdisjoint(EXPECTED_TABLES)


def test_no_legacy_business_tables() -> None:
    mapped = {t.name for t in Base.metadata.sorted_tables}
    assert mapped.isdisjoint(EXCLUDED_TABLES)


@pytest.mark.parametrize("table_name", sorted(EXPECTED_TABLES))
def test_every_framework_table_has_primary_key(table_name: str) -> None:
    table = Base.metadata.tables[table_name]
    assert table.primary_key.columns, f"{table_name} lacks a primary key"


def test_models_make_tables_on_memory_engine() -> None:
    """Structural self-consistency: all models DDL-compile without error."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    created = set(inspector.get_table_names())
    assert created == EXPECTED_TABLES


def test_framework_module_exports() -> None:
    for cls_name in framework.__all__:
        assert hasattr(framework, cls_name)