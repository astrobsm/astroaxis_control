from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db import get_session
from app.models import SystemSettings, RolePermission, CustomField, User, AuditLog
from pydantic import BaseModel
from typing import Optional, List
import uuid
from datetime import datetime

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Pydantic schemas
class SystemSettingsSchema(BaseModel):
    # General
    company_name: Optional[str] = None
    company_logo_url: Optional[str] = None
    company_slogan: Optional[str] = None
    business_address: Optional[str] = None
    business_email: Optional[str] = None
    business_phone: Optional[str] = None
    business_website: Optional[str] = None
    
    # Localization
    currency_code: Optional[str] = None
    currency_symbol: Optional[str] = None
    timezone: Optional[str] = None
    date_format: Optional[str] = None
    time_format: Optional[str] = None
    number_format: Optional[str] = None
    default_language: Optional[str] = None
    
    # Tax & Currency
    default_tax_rate: Optional[float] = None
    enable_currency_conversion: Optional[bool] = None
    currency_api_key: Optional[str] = None
    
    # Theme
    theme_mode: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    
    # Inventory
    stock_valuation_method: Optional[str] = None
    default_warehouse_id: Optional[str] = None
    auto_generate_sku: Optional[bool] = None
    sku_prefix: Optional[str] = None
    low_stock_alert_threshold: Optional[float] = None
    enable_barcode: Optional[bool] = None
    barcode_type: Optional[str] = None
    
    # Procurement
    default_payment_terms: Optional[str] = None
    po_number_prefix: Optional[str] = None
    po_auto_numbering: Optional[bool] = None
    default_lead_time_days: Optional[int] = None
    
    # Sales
    invoice_prefix: Optional[str] = None
    invoice_auto_numbering: Optional[bool] = None
    sales_tax_enabled: Optional[bool] = None
    default_discount_type: Optional[str] = None
    default_sales_channel: Optional[str] = None
    
    # Accounting
    fiscal_year_start_month: Optional[int] = None
    auto_post_invoices: Optional[bool] = None
    auto_post_purchases: Optional[bool] = None
    auto_post_payroll: Optional[bool] = None
    
    # Security
    password_min_length: Optional[int] = None
    password_require_uppercase: Optional[bool] = None
    password_require_lowercase: Optional[bool] = None
    password_require_numbers: Optional[bool] = None
    password_require_special: Optional[bool] = None
    session_timeout_minutes: Optional[int] = None
    max_login_attempts: Optional[int] = None
    enable_2fa: Optional[bool] = None
    enable_ip_whitelist: Optional[bool] = None
    
    # Notifications
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: Optional[bool] = None
    sms_gateway_url: Optional[str] = None
    sms_api_key: Optional[str] = None
    enable_email_notifications: Optional[bool] = None
    enable_sms_notifications: Optional[bool] = None
    
    # Backup
    auto_backup_enabled: Optional[bool] = None
    backup_frequency: Optional[str] = None
    backup_time: Optional[str] = None
    backup_retention_days: Optional[int] = None
    
    # Modules
    module_hr_enabled: Optional[bool] = None
    module_payroll_enabled: Optional[bool] = None
    module_inventory_enabled: Optional[bool] = None
    module_production_enabled: Optional[bool] = None
    module_sales_enabled: Optional[bool] = None
    module_accounting_enabled: Optional[bool] = None
    module_crm_enabled: Optional[bool] = None

class RolePermissionSchema(BaseModel):
    role: str
    module: str
    can_view: bool = False
    can_create: bool = False
    can_edit: bool = False
    can_delete: bool = False
    can_approve: bool = False

class CustomFieldSchema(BaseModel):
    module: str
    field_name: str
    field_label: str
    field_type: str
    field_options: Optional[str] = None
    is_required: bool = False
    is_active: bool = True
    display_order: int = 0

# Get system settings
@router.get("/")
async def get_settings(db: AsyncSession = Depends(get_session)):
    result = await db.execute(select(SystemSettings).limit(1))
    settings = result.scalar_one_or_none()
    
    if not settings:
        # Create default settings
        settings = SystemSettings(id=uuid.uuid4())
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    
    return {
        "id": str(settings.id),
        "company_name": settings.company_name,
        "company_logo_url": settings.company_logo_url,
        "company_slogan": settings.company_slogan,
        "business_address": settings.business_address,
        "business_email": settings.business_email,
        "business_phone": settings.business_phone,
        "business_website": settings.business_website,
        "currency_code": settings.currency_code,
        "currency_symbol": settings.currency_symbol,
        "timezone": settings.timezone,
        "date_format": settings.date_format,
        "time_format": settings.time_format,
        "number_format": settings.number_format,
        "default_language": settings.default_language,
        "default_tax_rate": float(settings.default_tax_rate) if settings.default_tax_rate else 0,
        "enable_currency_conversion": settings.enable_currency_conversion,
        "theme_mode": settings.theme_mode,
        "primary_color": settings.primary_color,
        "secondary_color": settings.secondary_color,
        "stock_valuation_method": settings.stock_valuation_method,
        "default_warehouse_id": str(settings.default_warehouse_id) if settings.default_warehouse_id else None,
        "auto_generate_sku": settings.auto_generate_sku,
        "sku_prefix": settings.sku_prefix,
        "low_stock_alert_threshold": float(settings.low_stock_alert_threshold) if settings.low_stock_alert_threshold else 0,
        "enable_barcode": settings.enable_barcode,
        "barcode_type": settings.barcode_type,
        "default_payment_terms": settings.default_payment_terms,
        "po_number_prefix": settings.po_number_prefix,
        "po_auto_numbering": settings.po_auto_numbering,
        "default_lead_time_days": settings.default_lead_time_days,
        "invoice_prefix": settings.invoice_prefix,
        "invoice_auto_numbering": settings.invoice_auto_numbering,
        "sales_tax_enabled": settings.sales_tax_enabled,
        "default_discount_type": settings.default_discount_type,
        "default_sales_channel": settings.default_sales_channel,
        "fiscal_year_start_month": settings.fiscal_year_start_month,
        "auto_post_invoices": settings.auto_post_invoices,
        "auto_post_purchases": settings.auto_post_purchases,
        "auto_post_payroll": settings.auto_post_payroll,
        "password_min_length": settings.password_min_length,
        "password_require_uppercase": settings.password_require_uppercase,
        "password_require_lowercase": settings.password_require_lowercase,
        "password_require_numbers": settings.password_require_numbers,
        "password_require_special": settings.password_require_special,
        "session_timeout_minutes": settings.session_timeout_minutes,
        "max_login_attempts": settings.max_login_attempts,
        "enable_2fa": settings.enable_2fa,
        "enable_ip_whitelist": settings.enable_ip_whitelist,
        "smtp_host": settings.smtp_host,
        "smtp_port": settings.smtp_port,
        "smtp_username": settings.smtp_username,
        "smtp_use_tls": settings.smtp_use_tls,
        "sms_gateway_url": settings.sms_gateway_url,
        "enable_email_notifications": settings.enable_email_notifications,
        "enable_sms_notifications": settings.enable_sms_notifications,
        "auto_backup_enabled": settings.auto_backup_enabled,
        "backup_frequency": settings.backup_frequency,
        "backup_time": settings.backup_time,
        "backup_retention_days": settings.backup_retention_days,
        "module_hr_enabled": settings.module_hr_enabled,
        "module_payroll_enabled": settings.module_payroll_enabled,
        "module_inventory_enabled": settings.module_inventory_enabled,
        "module_production_enabled": settings.module_production_enabled,
        "module_sales_enabled": settings.module_sales_enabled,
        "module_accounting_enabled": settings.module_accounting_enabled,
        "module_crm_enabled": settings.module_crm_enabled
    }

# Update system settings
@router.put("/")
async def update_settings(data: SystemSettingsSchema, db: AsyncSession = Depends(get_session)):
    result = await db.execute(select(SystemSettings).limit(1))
    settings = result.scalar_one_or_none()
    
    if not settings:
        settings = SystemSettings(id=uuid.uuid4())
        db.add(settings)
    
    # Update all provided fields
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if hasattr(settings, key):
            setattr(settings, key, value)
    
    await db.commit()
    await db.refresh(settings)
    
    return {"success": True, "message": "Settings updated successfully"}

# Role Permissions CRUD
@router.get("/permissions")
async def get_permissions(db: AsyncSession = Depends(get_session)):
    result = await db.execute(select(RolePermission))
    permissions = result.scalars().all()
    
    return [{
        "id": str(p.id),
        "role": p.role,
        "module": p.module,
        "can_view": p.can_view,
        "can_create": p.can_create,
        "can_edit": p.can_edit,
        "can_delete": p.can_delete,
        "can_approve": p.can_approve
    } for p in permissions]

@router.post("/permissions")
async def create_permission(data: RolePermissionSchema, db: AsyncSession = Depends(get_session)):
    permission = RolePermission(
        id=uuid.uuid4(),
        role=data.role,
        module=data.module,
        can_view=data.can_view,
        can_create=data.can_create,
        can_edit=data.can_edit,
        can_delete=data.can_delete,
        can_approve=data.can_approve
    )
    db.add(permission)
    await db.commit()
    return {"success": True, "id": str(permission.id)}

@router.get("/permissions/{role}")
async def get_role_permissions(role: str, db: AsyncSession = Depends(get_session)):
    result = await db.execute(select(RolePermission).where(RolePermission.role == role))
    permissions = result.scalars().all()
    
    return [{
        "id": str(p.id),
        "role": p.role,
        "module": p.module,
        "can_view": p.can_view,
        "can_create": p.can_create,
        "can_edit": p.can_edit,
        "can_delete": p.can_delete,
        "can_approve": p.can_approve
    } for p in permissions]

# Custom Fields CRUD
@router.get("/custom-fields")
async def get_custom_fields(module: Optional[str] = None, db: AsyncSession = Depends(get_session)):
    query = select(CustomField)
    if module:
        query = query.where(CustomField.module == module)
    
    result = await db.execute(query.order_by(CustomField.display_order))
    fields = result.scalars().all()
    
    return [{
        "id": str(f.id),
        "module": f.module,
        "field_name": f.field_name,
        "field_label": f.field_label,
        "field_type": f.field_type,
        "field_options": f.field_options,
        "is_required": f.is_required,
        "is_active": f.is_active,
        "display_order": f.display_order
    } for f in fields]

@router.post("/custom-fields")
async def create_custom_field(data: CustomFieldSchema, db: AsyncSession = Depends(get_session)):
    field = CustomField(
        id=uuid.uuid4(),
        module=data.module,
        field_name=data.field_name,
        field_label=data.field_label,
        field_type=data.field_type,
        field_options=data.field_options,
        is_required=data.is_required,
        is_active=data.is_active,
        display_order=data.display_order
    )
    db.add(field)
    await db.commit()
    return {"success": True, "id": str(field.id)}

@router.delete("/custom-fields/{field_id}")
async def delete_custom_field(field_id: str, db: AsyncSession = Depends(get_session)):
    result = await db.execute(select(CustomField).where(CustomField.id == uuid.UUID(field_id)))
    field = result.scalar_one_or_none()
    
    if not field:
        raise HTTPException(status_code=404, detail="Custom field not found")
    
    await db.delete(field)
    await db.commit()
    return {"success": True}

# Initialize default permissions for all roles
@router.post("/init-permissions")
async def initialize_permissions(db: AsyncSession = Depends(get_session)):
    roles = ["admin", "sales_staff", "marketer", "customer_care", "production_staff"]
    modules = ["staff", "attendance", "products", "raw_materials", "inventory", "warehouses", 
               "production", "sales", "customers", "accounting", "reports", "settings"]
    
    # Admin has all permissions
    for module in modules:
        result = await db.execute(
            select(RolePermission).where(
                RolePermission.role == "admin",
                RolePermission.module == module
            )
        )
        if not result.scalar_one_or_none():
            perm = RolePermission(
                id=uuid.uuid4(),
                role="admin",
                module=module,
                can_view=True,
                can_create=True,
                can_edit=True,
                can_delete=True,
                can_approve=True
            )
            db.add(perm)
    
    # Sales staff permissions
    sales_modules = ["products", "sales", "customers", "inventory"]
    for module in sales_modules:
        result = await db.execute(
            select(RolePermission).where(
                RolePermission.role == "sales_staff",
                RolePermission.module == module
            )
        )
        if not result.scalar_one_or_none():
            perm = RolePermission(
                id=uuid.uuid4(),
                role="sales_staff",
                module=module,
                can_view=True,
                can_create=True,
                can_edit=True,
                can_delete=False,
                can_approve=False
            )
            db.add(perm)
    
    # Marketer permissions
    marketer_modules = ["products", "customers", "sales"]
    for module in marketer_modules:
        result = await db.execute(
            select(RolePermission).where(
                RolePermission.role == "marketer",
                RolePermission.module == module
            )
        )
        if not result.scalar_one_or_none():
            perm = RolePermission(
                id=uuid.uuid4(),
                role="marketer",
                module=module,
                can_view=True,
                can_create=False,
                can_edit=False,
                can_delete=False,
                can_approve=False
            )
            db.add(perm)
    
    # Customer care permissions
    care_modules = ["customers", "sales", "products"]
    for module in care_modules:
        result = await db.execute(
            select(RolePermission).where(
                RolePermission.role == "customer_care",
                RolePermission.module == module
            )
        )
        if not result.scalar_one_or_none():
            perm = RolePermission(
                id=uuid.uuid4(),
                role="customer_care",
                module=module,
                can_view=True,
                can_create=True,
                can_edit=True,
                can_delete=False,
                can_approve=False
            )
            db.add(perm)
    
    # Production staff permissions
    production_modules = ["production", "products", "raw_materials", "inventory", "warehouses"]
    for module in production_modules:
        result = await db.execute(
            select(RolePermission).where(
                RolePermission.role == "production_staff",
                RolePermission.module == module
            )
        )
        if not result.scalar_one_or_none():
            perm = RolePermission(
                id=uuid.uuid4(),
                role="production_staff",
                module=module,
                can_view=True,
                can_create=True,
                can_edit=True,
                can_delete=False,
                can_approve=False
            )
            db.add(perm)
    
    await db.commit()
    return {"success": True, "message": "Default permissions initialized"}
