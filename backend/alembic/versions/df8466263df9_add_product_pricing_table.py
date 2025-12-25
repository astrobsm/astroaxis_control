"""add_product_pricing_table

Revision ID: df8466263df9
Revises: 91614e428307
Create Date: 2025-10-31 14:37:38.998317

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'df8466263df9'
down_revision: Union[str, None] = '582d7a82c0c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'product_pricing',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('product_id', sa.UUID(), nullable=False),
        sa.Column('unit', sa.String(length=50), nullable=False),
        sa.Column('retail_price', sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column('wholesale_price', sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_product_pricing_product_id'), 'product_pricing', ['product_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_product_pricing_product_id'), table_name='product_pricing')
    op.drop_table('product_pricing')
