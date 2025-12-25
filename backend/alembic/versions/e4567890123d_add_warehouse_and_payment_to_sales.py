"""add warehouse and payment to sales orders

Revision ID: e4567890123d
Revises: d3456789012c
Create Date: 2025-11-25 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = 'e4567890123d'
down_revision = '7875bb035283'
branch_labels = None
depends_on = None


def upgrade():
    # Add warehouse_id and payment_status to sales_orders
    op.add_column('sales_orders', sa.Column('warehouse_id', UUID(as_uuid=True), nullable=True))
    op.add_column('sales_orders', sa.Column('payment_status', sa.String(32), nullable=False, server_default='unpaid'))
    op.add_column('sales_orders', sa.Column('payment_date', sa.TIMESTAMP(timezone=True), nullable=True))
    
    # Add foreign key constraint
    op.create_foreign_key(
        'sales_orders_warehouse_id_fkey',
        'sales_orders',
        'warehouses',
        ['warehouse_id'],
        ['id']
    )
    
    # Add index
    op.create_index('ix_sales_orders_warehouse_id', 'sales_orders', ['warehouse_id'])
    op.create_index('ix_sales_orders_payment_status', 'sales_orders', ['payment_status'])


def downgrade():
    op.drop_index('ix_sales_orders_payment_status', 'sales_orders')
    op.drop_index('ix_sales_orders_warehouse_id', 'sales_orders')
    op.drop_constraint('sales_orders_warehouse_id_fkey', 'sales_orders', type_='foreignkey')
    op.drop_column('sales_orders', 'payment_date')
    op.drop_column('sales_orders', 'payment_status')
    op.drop_column('sales_orders', 'warehouse_id')
