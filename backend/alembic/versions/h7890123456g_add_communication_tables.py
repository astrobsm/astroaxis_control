"""Add communication tables (notices, chat_messages, letters)

Revision ID: h7890123456g
Revises: g6789012345f
Create Date: 2026-04-10 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = 'h7890123456g'
down_revision = 'g6789012345f'
branch_labels = None
depends_on = None


def upgrade():
    # Notice Board table
    op.create_table(
        'notices',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('priority', sa.String(20), nullable=False, server_default='normal'),
        sa.Column('category', sa.String(50), nullable=False, server_default='general'),
        sa.Column('author', sa.String(255), nullable=False),
        sa.Column('author_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('pinned', sa.Boolean, server_default=sa.text('false')),
        sa.Column('is_active', sa.Boolean, server_default=sa.text('true')),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True)),
    )

    # Chat Messages table
    op.create_table(
        'chat_messages',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('channel', sa.String(50), nullable=False, server_default='general', index=True),
        sa.Column('sender', sa.String(255), nullable=False),
        sa.Column('sender_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('text', sa.Text, nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_chat_messages_channel_created', 'chat_messages', ['channel', 'created_at'])

    # Letters table (saved letterhead drafts for cross-device access)
    op.create_table(
        'letters',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('recipient', sa.String(255), nullable=True),
        sa.Column('body', sa.Text, nullable=False),
        sa.Column('author', sa.String(255), nullable=False),
        sa.Column('author_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True)),
    )


def downgrade():
    op.drop_table('letters')
    op.drop_index('ix_chat_messages_channel_created', table_name='chat_messages')
    op.drop_table('chat_messages')
    op.drop_table('notices')
