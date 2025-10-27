"""merge rbac and warehouse branches

Revision ID: 97f3b81e3202
Revises: rbac_permissions_001, 38bc3fba57a6
Create Date: 2025-10-27 07:39:46.984828

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '97f3b81e3202'
down_revision: Union[str, None] = ('rbac_permissions_001', '38bc3fba57a6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
