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
down_revision: Union[str, None] = 'df8466263df9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Columns already exist in initial migration b6c76a408167 - no-op
    pass


def downgrade() -> None:
    # No-op since upgrade does nothing
    pass
