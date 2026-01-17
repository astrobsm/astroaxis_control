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
    # Table already exists in initial migration b6c76a408167 - no-op
    pass


def downgrade() -> None:
    # No-op since upgrade does nothing
    pass
