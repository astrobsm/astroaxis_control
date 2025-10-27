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
    # Add new columns to staff table
    op.add_column('staff', sa.Column('phone', sa.String(length=20), nullable=True))
    op.add_column('staff', sa.Column('date_of_birth', sa.Date(), nullable=True))


def downgrade() -> None:
    # Remove added columns
    op.drop_column('staff', 'date_of_birth')
    op.drop_column('staff', 'phone')
