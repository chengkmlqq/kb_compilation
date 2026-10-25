"""Merge existing OSA storage and product migrations without rewriting history.

The two branches share 0033_model_max_tokens. Upgrading from a product-only
revision applies the missing OSA branch; existing product migrations retain
their original revision identifiers and dependency order.
"""

revision = '0048_merge_osa'
down_revision = ('0047_kb_enhancement_config', '0034_osa_storage')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
