"""add damaged and returned stock tables

Revision ID: 890231f3ae6e
Revises: d3456789012c
Create Date: 2025-10-26 23:14:29.401283

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '890231f3ae6e'
down_revision: Union[str, None] = 'd3456789012c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create damaged_stock table
    op.create_table(
        'damaged_stock',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('warehouse_id', sa.UUID(), nullable=False),
        sa.Column('product_id', sa.UUID(), nullable=True),
        sa.Column('raw_material_id', sa.UUID(), nullable=True),
        sa.Column('quantity', sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column('damage_type', sa.String(length=100), nullable=False),
        sa.Column('damage_reason', sa.Text(), nullable=True),
        sa.Column('damage_date', sa.Date(), nullable=False, server_default=sa.func.current_date()),
        sa.Column('reported_by', sa.UUID(), nullable=True),
        sa.Column('disposal_status', sa.String(length=50), nullable=False, server_default='pending'),
        sa.Column('disposal_date', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id'], ),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ),
        sa.ForeignKeyConstraint(['raw_material_id'], ['raw_materials.id'], ),
        sa.ForeignKeyConstraint(['reported_by'], ['staff.id'], )
    )
    
    # Create returned_stock table
    op.create_table(
        'returned_stock',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('warehouse_id', sa.UUID(), nullable=False),
        sa.Column('product_id', sa.UUID(), nullable=False),
        sa.Column('sales_order_id', sa.UUID(), nullable=True),
        sa.Column('customer_id', sa.UUID(), nullable=True),
        sa.Column('quantity', sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column('return_reason', sa.Text(), nullable=False),
        sa.Column('return_condition', sa.String(length=50), nullable=False),
        sa.Column('return_date', sa.Date(), nullable=False, server_default=sa.func.current_date()),
        sa.Column('refund_status', sa.String(length=50), nullable=False, server_default='pending'),
        sa.Column('refund_amount', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('processed_by', sa.UUID(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['warehouse_id'], ['warehouses.id'], ),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ),
        sa.ForeignKeyConstraint(['sales_order_id'], ['sales_orders.id'], ),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
        sa.ForeignKeyConstraint(['processed_by'], ['staff.id'], )
    )


def downgrade() -> None:
    op.drop_table('returned_stock')
    op.drop_table('damaged_stock')
