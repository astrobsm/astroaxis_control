"""Add RBAC permissions table

Revision ID: g6789012345f
Revises: f5678901234e
Create Date: 2025-10-27 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = 'g6789012345f'
down_revision = 'f5678901234e'
branch_labels = None
depends_on = None


def upgrade():
    # Tables already exist in initial migration b6c76a408167 - no-op
    pass


def downgrade():
    # No-op since upgrade does nothing
    pass
