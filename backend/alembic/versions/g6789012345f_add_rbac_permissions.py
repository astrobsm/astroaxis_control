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
    # Create permissions table
    op.create_table(
        'permissions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False, unique=True),
        sa.Column('display_name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text),
        sa.Column('module', sa.String(50), nullable=False),  # staff, sales, production, etc.
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )

    # Create role_permissions junction table
    op.create_table(
        'user_permissions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('permission_id', UUID(as_uuid=True), sa.ForeignKey('permissions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('granted_by', UUID(as_uuid=True), sa.ForeignKey('users.id')),
        sa.Column('granted_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )

    # Create indexes
    op.create_index('idx_user_permissions_user_id', 'user_permissions', ['user_id'])
    op.create_index('idx_user_permissions_permission_id', 'user_permissions', ['permission_id'])
    
    # Add unique constraint to prevent duplicate permissions
    op.create_unique_constraint(
        'uq_user_permission',
        'user_permissions',
        ['user_id', 'permission_id']
    )


def downgrade():
    op.drop_table('user_permissions')
    op.drop_table('permissions')
