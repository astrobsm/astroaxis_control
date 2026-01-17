"""add raw material fields

Revision ID: d3456789012c
Revises: c2345678901b
Create Date: 2025-10-26 22:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3456789012c'
down_revision = 'c2345678901b'
branch_labels = None
depends_on = None


def upgrade():
    # Columns already exist in initial migration b6c76a408167 - no-op
    pass


def downgrade():
    # No-op since upgrade does nothing
    pass
