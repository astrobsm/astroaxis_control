"""Add SOP module tables

Revision ID: i8901234567h
Revises: h7890123456g
Create Date: 2026-04-26 09:30:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision = "i8901234567h"
down_revision = "h7890123456g"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sop_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("sop_code", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("sop_number", sa.String(64), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("form_schema", sa.JSON(), nullable=False),
        sa.Column("db_table_structure", sa.JSON(), nullable=False),
        sa.Column("validation_rules", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True)),
    )

    op.create_table(
        "sop_execution_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("template_id", UUID(as_uuid=True), sa.ForeignKey("sop_templates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sop_code", sa.String(64), nullable=False, index=True),
        sa.Column("operator_name", sa.String(255), nullable=False),
        sa.Column("supervisor_name", sa.String(255), nullable=False),
        sa.Column("executed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("batch_number", sa.String(100), nullable=False),
        sa.Column("material_equipment_used", sa.JSON(), nullable=False),
        sa.Column("checklist", sa.JSON(), nullable=False),
        sa.Column("numeric_inputs", sa.JSON(), nullable=False),
        sa.Column("operator_signature", sa.String(255), nullable=False),
        sa.Column("supervisor_signature", sa.String(255), nullable=False),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("deviation", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="submitted"),
        sa.Column("approved_by", sa.String(255), nullable=True),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True)),
    )

    op.create_index("ix_sop_execution_logs_batch_number", "sop_execution_logs", ["batch_number"])


def downgrade():
    op.drop_index("ix_sop_execution_logs_batch_number", table_name="sop_execution_logs")
    op.drop_table("sop_execution_logs")
    op.drop_table("sop_templates")
