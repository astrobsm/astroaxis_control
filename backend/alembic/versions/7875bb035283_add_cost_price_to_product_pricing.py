"""add_cost_price_to_product_pricing

Revision ID: 7875bb035283
Revises: 07bc80bd3ddc
Create Date: 2025-11-05 07:01:48.505093

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7875bb035283'
down_revision: Union[str, None] = 'eeaba5084f0e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Column already exists in initial migration b6c76a408167 - no-op
    pass


def downgrade() -> None:
    # No-op since upgrade does nothing
    pass
