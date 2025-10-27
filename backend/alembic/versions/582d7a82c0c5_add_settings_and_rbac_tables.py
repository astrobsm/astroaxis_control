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
    # Modify users table
    op.add_column('users', sa.Column('full_name', sa.String(255), nullable=True))
    op.add_column('users', sa.Column('is_active', sa.Boolean(), server_default='true'))
    op.add_column('users', sa.Column('is_locked', sa.Boolean(), server_default='false'))
    op.add_column('users', sa.Column('failed_login_attempts', sa.Integer(), server_default='0'))
    op.add_column('users', sa.Column('last_login', sa.TIMESTAMP(timezone=True)))
    op.add_column('users', sa.Column('two_factor_enabled', sa.Boolean(), server_default='false'))
    op.add_column('users', sa.Column('two_factor_secret', sa.String(255)))
    op.add_column('users', sa.Column('phone', sa.String(20)))
    op.add_column('users', sa.Column('department', sa.String(100)))
    op.add_column('users', sa.Column('updated_at', sa.TIMESTAMP(timezone=True)))
    
    # Update existing users with default full_name
    op.execute("UPDATE users SET full_name = email WHERE full_name IS NULL")
    op.alter_column('users', 'full_name', nullable=False)
    
    # Create system_settings table
    op.create_table('system_settings',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('company_name', sa.String(255), server_default='ASTRO-ASIX ERP'),
        sa.Column('company_logo_url', sa.String(500)),
        sa.Column('company_slogan', sa.String(500)),
        sa.Column('business_address', sa.Text()),
        sa.Column('business_email', sa.String(255)),
        sa.Column('business_phone', sa.String(50)),
        sa.Column('business_website', sa.String(255)),
        sa.Column('currency_code', sa.String(10), server_default='NGN'),
        sa.Column('currency_symbol', sa.String(10), server_default='₦'),
        sa.Column('timezone', sa.String(100), server_default='Africa/Lagos'),
        sa.Column('date_format', sa.String(50), server_default='DD/MM/YYYY'),
        sa.Column('time_format', sa.String(50), server_default='HH:mm:ss'),
        sa.Column('number_format', sa.String(50), server_default='1,234.56'),
        sa.Column('default_language', sa.String(10), server_default='en'),
        sa.Column('default_tax_rate', sa.Numeric(5,2), server_default='7.5'),
        sa.Column('enable_currency_conversion', sa.Boolean(), server_default='false'),
        sa.Column('currency_api_key', sa.String(255)),
        sa.Column('theme_mode', sa.String(20), server_default='light'),
        sa.Column('primary_color', sa.String(20), server_default='#3b82f6'),
        sa.Column('secondary_color', sa.String(20), server_default='#6366f1'),
        sa.Column('stock_valuation_method', sa.String(50), server_default='FIFO'),
        sa.Column('default_warehouse_id', UUID(as_uuid=True), sa.ForeignKey('warehouses.id')),
        sa.Column('auto_generate_sku', sa.Boolean(), server_default='true'),
        sa.Column('sku_prefix', sa.String(20), server_default='PRD'),
        sa.Column('low_stock_alert_threshold', sa.Numeric(18,2), server_default='10'),
        sa.Column('enable_barcode', sa.Boolean(), server_default='true'),
        sa.Column('barcode_type', sa.String(50), server_default='CODE128'),
        sa.Column('default_payment_terms', sa.String(100), server_default='Net 30'),
        sa.Column('po_number_prefix', sa.String(20), server_default='PO'),
        sa.Column('po_auto_numbering', sa.Boolean(), server_default='true'),
        sa.Column('default_lead_time_days', sa.Integer(), server_default='7'),
        sa.Column('invoice_prefix', sa.String(20), server_default='INV'),
        sa.Column('invoice_auto_numbering', sa.Boolean(), server_default='true'),
        sa.Column('sales_tax_enabled', sa.Boolean(), server_default='true'),
        sa.Column('default_discount_type', sa.String(20), server_default='percentage'),
        sa.Column('default_sales_channel', sa.String(50), server_default='in_store'),
        sa.Column('fiscal_year_start_month', sa.Integer(), server_default='1'),
        sa.Column('auto_post_invoices', sa.Boolean(), server_default='true'),
        sa.Column('auto_post_purchases', sa.Boolean(), server_default='true'),
        sa.Column('auto_post_payroll', sa.Boolean(), server_default='false'),
        sa.Column('password_min_length', sa.Integer(), server_default='8'),
        sa.Column('password_require_uppercase', sa.Boolean(), server_default='true'),
        sa.Column('password_require_lowercase', sa.Boolean(), server_default='true'),
        sa.Column('password_require_numbers', sa.Boolean(), server_default='true'),
        sa.Column('password_require_special', sa.Boolean(), server_default='true'),
        sa.Column('session_timeout_minutes', sa.Integer(), server_default='30'),
        sa.Column('max_login_attempts', sa.Integer(), server_default='5'),
        sa.Column('enable_2fa', sa.Boolean(), server_default='false'),
        sa.Column('enable_ip_whitelist', sa.Boolean(), server_default='false'),
        sa.Column('smtp_host', sa.String(255)),
        sa.Column('smtp_port', sa.Integer(), server_default='587'),
        sa.Column('smtp_username', sa.String(255)),
        sa.Column('smtp_password', sa.String(255)),
        sa.Column('smtp_use_tls', sa.Boolean(), server_default='true'),
        sa.Column('sms_gateway_url', sa.String(500)),
        sa.Column('sms_api_key', sa.String(255)),
        sa.Column('enable_email_notifications', sa.Boolean(), server_default='true'),
        sa.Column('enable_sms_notifications', sa.Boolean(), server_default='false'),
        sa.Column('auto_backup_enabled', sa.Boolean(), server_default='false'),
        sa.Column('backup_frequency', sa.String(50), server_default='daily'),
        sa.Column('backup_time', sa.String(10), server_default='02:00'),
        sa.Column('backup_retention_days', sa.Integer(), server_default='30'),
        sa.Column('module_hr_enabled', sa.Boolean(), server_default='true'),
        sa.Column('module_payroll_enabled', sa.Boolean(), server_default='true'),
        sa.Column('module_inventory_enabled', sa.Boolean(), server_default='true'),
        sa.Column('module_production_enabled', sa.Boolean(), server_default='true'),
        sa.Column('module_sales_enabled', sa.Boolean(), server_default='true'),
        sa.Column('module_accounting_enabled', sa.Boolean(), server_default='true'),
        sa.Column('module_crm_enabled', sa.Boolean(), server_default='false'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), onupdate=sa.func.now())
    )
    
    # Create role_permissions table
    op.create_table('role_permissions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('role', sa.String(50), nullable=False, index=True),
        sa.Column('module', sa.String(100), nullable=False),
        sa.Column('can_view', sa.Boolean(), server_default='false'),
        sa.Column('can_create', sa.Boolean(), server_default='false'),
        sa.Column('can_edit', sa.Boolean(), server_default='false'),
        sa.Column('can_delete', sa.Boolean(), server_default='false'),
        sa.Column('can_approve', sa.Boolean(), server_default='false'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), onupdate=sa.func.now())
    )
    
    # Create user_sessions table
    op.create_table('user_sessions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False, index=True),
        sa.Column('token', sa.String(500), unique=True, nullable=False, index=True),
        sa.Column('ip_address', sa.String(50)),
        sa.Column('user_agent', sa.String(500)),
        sa.Column('expires_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now())
    )
    
    # Update audit_logs table
    op.add_column('audit_logs', sa.Column('module', sa.String(100)))
    op.add_column('audit_logs', sa.Column('record_id', sa.String(100)))
    op.add_column('audit_logs', sa.Column('ip_address', sa.String(50)))
    op.add_column('audit_logs', sa.Column('user_agent', sa.String(500)))
    op.add_column('audit_logs', sa.Column('details', sa.Text()))
    
    # Create custom_fields table
    op.create_table('custom_fields',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('module', sa.String(100), nullable=False, index=True),
        sa.Column('field_name', sa.String(100), nullable=False),
        sa.Column('field_label', sa.String(200), nullable=False),
        sa.Column('field_type', sa.String(50), nullable=False),
        sa.Column('field_options', sa.Text()),
        sa.Column('is_required', sa.Boolean(), server_default='false'),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('display_order', sa.Integer(), server_default='0'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), onupdate=sa.func.now())
    )


def downgrade() -> None:
    op.drop_table('custom_fields')
    op.drop_column('audit_logs', 'details')
    op.drop_column('audit_logs', 'user_agent')
    op.drop_column('audit_logs', 'ip_address')
    op.drop_column('audit_logs', 'record_id')
    op.drop_column('audit_logs', 'module')
    op.drop_table('user_sessions')
    op.drop_table('role_permissions')
    op.drop_table('system_settings')
    op.drop_column('users', 'updated_at')
    op.drop_column('users', 'department')
    op.drop_column('users', 'phone')
    op.drop_column('users', 'two_factor_secret')
    op.drop_column('users', 'two_factor_enabled')
    op.drop_column('users', 'last_login')
    op.drop_column('users', 'failed_login_attempts')
    op.drop_column('users', 'is_locked')
    op.drop_column('users', 'is_active')
    op.drop_column('users', 'full_name')

