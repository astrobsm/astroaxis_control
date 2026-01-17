"""add product fields

Revision ID: b1234567890a
Revises: af6fe53902d3
Create Date: 2025-10-26 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'b1234567890a'
down_revision = 'af6fe53902d3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Columns already exist in initial migration b6c76a408167 - no-op
    pass


def downgrade() -> None:
    # No-op since upgrade does nothing
    pass
