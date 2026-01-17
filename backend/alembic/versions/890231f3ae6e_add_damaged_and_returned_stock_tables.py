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
    # Tables already exist in initial migration b6c76a408167 - no-op
    pass


def downgrade() -> None:
    # No-op since upgrade does nothing
    pass


def downgrade() -> None:
    op.drop_table('returned_stock')
    op.drop_table('damaged_stock')
