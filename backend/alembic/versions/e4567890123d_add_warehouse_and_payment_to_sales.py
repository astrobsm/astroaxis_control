"""add warehouse and payment to sales orders

Revision ID: e4567890123d
Revises: d3456789012c
Create Date: 2025-11-25 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = 'e4567890123d'
down_revision = '7875bb035283'
branch_labels = None
depends_on = None


def upgrade():
    # Columns already exist in initial migration b6c76a408167 - no-op
    pass


def downgrade():
    # No-op since upgrade does nothing
    pass
