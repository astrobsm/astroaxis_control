"""Add Network & WiFi Management tables

Revision ID: j9012345678i
Revises: i8901234567h
Create Date: 2026-06-14 10:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision = "j9012345678i"
down_revision = "i8901234567h"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "wifi_settings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_ssid", sa.String(64), nullable=True),
        sa.Column("encrypted_company_password", sa.Text(), nullable=True),
        sa.Column("guest_ssid", sa.String(64), nullable=True),
        sa.Column("encrypted_guest_password", sa.Text(), nullable=True),
        sa.Column("encrypted_current_wifi_password", sa.Text(), nullable=True),
        sa.Column("radius_server_ip", sa.String(45), nullable=True),
        sa.Column("encrypted_radius_secret", sa.Text(), nullable=True),
        sa.Column("captive_portal_url", sa.String(255), nullable=True),
        sa.Column("session_timeout", sa.Integer(), nullable=True, server_default="60"),
        sa.Column("max_devices", sa.Integer(), nullable=True, server_default="3"),
        sa.Column("bandwidth_limit", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("guest_network_enabled", sa.Boolean(), nullable=True, server_default=sa.text("false")),
        sa.Column("attendance_on_login", sa.Boolean(), nullable=True, server_default=sa.text("false")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.create_table(
        "wifi_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("employee_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("device_mac", sa.String(64), nullable=True, index=True),
        sa.Column("device_name", sa.String(255), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("login_time", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), index=True),
        sa.Column("logout_time", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("session_status", sa.String(20), nullable=True, server_default="active", index=True),
        sa.Column("data_used_mb", sa.Numeric(18, 2), nullable=True, server_default="0"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "wifi_devices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("employee_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("device_name", sa.String(255), nullable=True),
        sa.Column("device_mac", sa.String(64), nullable=False, index=True),
        sa.Column("device_type", sa.String(50), nullable=True),
        sa.Column("last_connected", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=True, server_default="active", index=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("employee_id", "device_mac", name="uq_employee_device_mac"),
    )

    op.create_table(
        "wifi_auth_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("employee_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("username", sa.String(255), nullable=True, index=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("device_mac", sa.String(64), nullable=True),
        sa.Column("authentication_result", sa.String(20), nullable=True, index=True),
        sa.Column("failure_reason", sa.String(255), nullable=True),
        sa.Column("timestamp", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), index=True),
    )


def downgrade():
    op.drop_table("wifi_auth_logs")
    op.drop_table("wifi_devices")
    op.drop_table("wifi_sessions")
    op.drop_table("wifi_settings")
