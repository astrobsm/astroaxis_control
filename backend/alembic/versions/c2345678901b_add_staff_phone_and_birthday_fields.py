"""add staff phone and birthday fields

Revision ID: c2345678901b
Revises: b1234567890a
Create Date: 2025-10-26 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c2345678901b'
down_revision = 'b1234567890a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Columns already exist in initial migration b6c76a408167 - no-op
    pass


def downgrade() -> None:
    # No-op since upgrade does nothing
    pass
