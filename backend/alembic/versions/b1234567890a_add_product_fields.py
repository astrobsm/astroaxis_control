"""add product fields

Revision ID: b1234567890a
Revises: af6fe53902d3
Create Date: 2025-10-26 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'b1234567890a'
down_revision = 'af6fe53902d3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new columns to products table
    op.add_column('products', sa.Column('manufacturer', sa.String(length=255), nullable=True))
    op.add_column('products', sa.Column('reorder_level', sa.Numeric(precision=18, scale=6), server_default='0', nullable=True))
    op.add_column('products', sa.Column('cost_price', sa.Numeric(precision=18, scale=2), server_default='0', nullable=True))
    op.add_column('products', sa.Column('selling_price', sa.Numeric(precision=18, scale=2), server_default='0', nullable=True))
    op.add_column('products', sa.Column('lead_time_days', sa.Integer(), server_default='0', nullable=True))
    op.add_column('products', sa.Column('minimum_order_quantity', sa.Numeric(precision=18, scale=6), server_default='1', nullable=True))


def downgrade() -> None:
    # Remove added columns
    op.drop_column('products', 'minimum_order_quantity')
    op.drop_column('products', 'lead_time_days')
    op.drop_column('products', 'selling_price')
    op.drop_column('products', 'cost_price')
    op.drop_column('products', 'reorder_level')
    op.drop_column('products', 'manufacturer')
