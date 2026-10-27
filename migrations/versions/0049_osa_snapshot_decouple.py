"""Decouple public OSA snapshots from DACP-private asset and revision tables."""
from alembic import op
import sqlalchemy as sa

revision = '0049_osa_snapshot_decouple'
down_revision = '0048_merge_osa'
branch_labels = None
depends_on = None

NAMING_CONVENTION = {'fk': '%(table_name)s_%(column_0_name)s_%(referred_table_name)s_fkey'}
PUBLIC_FOREIGN_KEYS = (
    ('osa_action_snapshot', ('id', 'asset_kind'), 'osa_asset_revision', ('id', 'asset_kind'), 'fk_osa_action_revision'),
    ('osa_entity_snapshot', ('id', 'asset_kind'), 'osa_asset_revision', ('id', 'asset_kind'), 'fk_osa_entity_revision'),
    ('osa_event_snapshot', ('id', 'asset_kind'), 'osa_asset_revision', ('id', 'asset_kind'), 'fk_osa_event_revision'),
    ('osa_metric_snapshot', ('id', 'asset_kind'), 'osa_asset_revision', ('id', 'asset_kind'), 'fk_osa_metric_revision'),
    ('osa_relation_snapshot', ('id', 'asset_kind'), 'osa_asset_revision', ('id', 'asset_kind'), 'fk_osa_relation_revision'),
    ('osa_rule_snapshot', ('id', 'asset_kind'), 'osa_asset_revision', ('id', 'asset_kind'), 'fk_osa_rule_revision'),
    ('osa_vocabulary_snapshot', ('id', 'asset_kind'), 'osa_asset_revision', ('id', 'asset_kind'), 'fk_osa_vocabulary_revision'),
    ('osa_relation_snapshot', ('left_asset_id',), 'osa_asset', ('id',), None),
    ('osa_relation_snapshot', ('right_asset_id',), 'osa_asset', ('id',), None),
    ('osa_action_snapshot', ('bound_rule_asset_id',), 'osa_asset', ('id',), None),
    ('osa_metric_dimension_snapshot', ('target_asset_id',), 'osa_asset', ('id',), None),
    ('osa_rule_target_snapshot', ('target_asset_id',), 'osa_asset', ('id',), None),
)


def _foreign_key(inspector, table, columns, referred_table, referred_columns):
    matches = [fk for fk in inspector.get_foreign_keys(table)
               if tuple(fk['constrained_columns']) == columns
               and fk['referred_table'] == referred_table
               and tuple(fk['referred_columns']) == referred_columns]
    if len(matches) != 1:
        raise RuntimeError(f'Expected one public OSA foreign key on {table}{columns}; found {len(matches)}')
    return matches[0]


def _sqlite_name(table, columns, referred_table, fk):
    return fk['name'] or f'{table}_{columns[0]}_{referred_table}_fkey'


def upgrade():
    if op.get_context().as_sql:
        raise RuntimeError('OSA snapshot decoupling requires online foreign-key inspection')
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    sqlite = connection.dialect.name == 'sqlite'
    for table, columns, referred_table, referred_columns, _ in PUBLIC_FOREIGN_KEYS:
        fk = _foreign_key(inspector, table, columns, referred_table, referred_columns)
        name = _sqlite_name(table, columns, referred_table, fk) if sqlite else fk['name']
        if not name:
            raise RuntimeError(f'Unnamed public OSA foreign key requires inspection: {table}{columns}')
        with op.batch_alter_table(table, naming_convention=NAMING_CONVENTION) as batch:
            batch.drop_constraint(name, type_='foreignkey')


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError('OSA snapshot recoupling requires online reference validation')
    connection = op.get_bind()
    for table, columns, referred_table, referred_columns, original_name in PUBLIC_FOREIGN_KEYS:
        join = ' AND '.join(f'r.{remote} = s.{local}' for local, remote in zip(columns, referred_columns))
        missing = connection.execute(sa.text(
            f'SELECT 1 FROM {table} AS s LEFT JOIN {referred_table} AS r ON {join} '
            f'WHERE r.{referred_columns[0]} IS NULL LIMIT 1'
        )).first()
        if missing:
            raise RuntimeError(f'Cannot restore public OSA foreign key: unresolved references in {table}{columns}')
    for table, columns, referred_table, referred_columns, original_name in PUBLIC_FOREIGN_KEYS:
        name = original_name or f'{table}_{columns[0]}_{referred_table}_fkey'
        with op.batch_alter_table(table, naming_convention=NAMING_CONVENTION) as batch:
            batch.create_foreign_key(name, referred_table, list(columns), list(referred_columns), ondelete='RESTRICT')
