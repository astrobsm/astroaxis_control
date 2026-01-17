"""add_clock_pin_to_staff

Revision ID: af6fe53902d3
Revises: f3209ab69b1f
Create Date: 2025-10-25 09:20:38.374314

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'af6fe53902d3'
down_revision: Union[str, None] = 'b6c76a408167'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # clock_pin column and index already exist in initial migration b6c76a408167
    # This migration is now a no-op to avoid duplicate creation errors
    pass


def downgrade() -> None:
    # No-op since upgrade does nothing
    pass
