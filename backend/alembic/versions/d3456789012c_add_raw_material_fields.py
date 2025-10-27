"""add raw material fields

Revision ID: d3456789012c
Revises: c2345678901b
Create Date: 2025-10-26 22:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3456789012c'
down_revision = 'c2345678901b'
branch_labels = None
depends_on = None


def upgrade():
    # Add new columns to raw_materials table
    op.add_column('raw_materials', sa.Column('manufacturer', sa.String(255), nullable=True))
    op.add_column('raw_materials', sa.Column('unit', sa.String(50), nullable=True, server_default='kg'))
    op.add_column('raw_materials', sa.Column('reorder_level', sa.Numeric(18, 6), nullable=True, server_default='0'))
    
    # Alter unit_cost to Numeric(18,2) for consistency
    op.alter_column('raw_materials', 'unit_cost',
                    type_=sa.Numeric(18, 2),
                    existing_type=sa.Numeric(18, 6),
                    existing_nullable=False)


def downgrade():
    op.drop_column('raw_materials', 'reorder_level')
    op.drop_column('raw_materials', 'unit')
    op.drop_column('raw_materials', 'manufacturer')
    
    op.alter_column('raw_materials', 'unit_cost',
                    type_=sa.Numeric(18, 6),
                    existing_type=sa.Numeric(18, 2),
                    existing_nullable=False)
