"""add unit to sales_order_lines

Revision ID: f5678901234e
Revises: e4567890123d
Create Date: 2025-11-06 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f5678901234e'
down_revision = 'e4567890123d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add unit column to sales_order_lines table
    op.add_column('sales_order_lines', sa.Column('unit', sa.String(length=50), nullable=True))


def downgrade() -> None:
    # Remove unit column from sales_order_lines table
    op.drop_column('sales_order_lines', 'unit')
