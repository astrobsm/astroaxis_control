"""add_settings_and_rbac_tables

Revision ID: 582d7a82c0c5
Revises: 890231f3ae6e
Create Date: 2025-10-27 00:10:28.313168

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = '582d7a82c0c5'
down_revision: Union[str, None] = '890231f3ae6e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # All tables/columns already exist in initial migration b6c76a408167 - no-op
    pass


def downgrade() -> None:
    # No-op since upgrade does nothing
    pass
