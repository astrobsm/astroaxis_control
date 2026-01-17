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
    # Column already exists in initial migration b6c76a408167 - no-op
    pass


def downgrade() -> None:
    # No-op since upgrade does nothing
    pass
