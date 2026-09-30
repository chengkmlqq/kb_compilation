"""Framework model sanity checks.

Verifies the ORM models ported from data-synth schema.ts:
1. Exactly the framework tables are present (synth business tables excluded).
2. Every model maps to the expected physical table name.
3. Models can create tables on an in-memory engine (structural self-consistency).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from api.db import Base
from api.models import framework

# Expected framework tables (kept from data-synth schema.ts).
EXPECTED_TABLES = {
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

# Synth business tables that MUST NOT be present after extraction.
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
# - modo_requirement / modo_requirement_entity are synth business (extracted out)
# - modo_user_synth_100 is an ad-hoc table from the old synth link
IGNORED_DB_TABLES = {
    "modo_requirement",
    "modo_requirement_entity",
    "modo_user_synth_100",
}


def test_only_framework_tables_defined() -> None:
    mapped = {t.name for t in Base.metadata.sorted_tables}
    assert mapped == EXPECTED_TABLES, f"missing={EXPECTED_TABLES - mapped}, extra={mapped - EXPECTED_TABLES}"


def test_no_synth_business_tables() -> None:
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