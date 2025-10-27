"""add_payment_mode_and_product_prices

Revision ID: eeaba5084f0e
Revises: 582d7a82c0c5
Create Date: 2025-10-27 01:37:56.089031

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'eeaba5084f0e'
down_revision: Union[str, None] = '582d7a82c0c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add payment_mode column to staff table (replace monthly_salary concept)
    op.add_column('staff', sa.Column('payment_mode', sa.String(length=20), nullable=True))
    
    # Add retail_price and wholesale_price to products table
    op.add_column('products', sa.Column('retail_price', sa.Numeric(precision=18, scale=2), nullable=True))
    op.add_column('products', sa.Column('wholesale_price', sa.Numeric(precision=18, scale=2), nullable=True))


def downgrade() -> None:
    # Remove columns
    op.drop_column('products', 'wholesale_price')
    op.drop_column('products', 'retail_price')
    op.drop_column('staff', 'payment_mode')
