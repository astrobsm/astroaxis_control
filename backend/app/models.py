import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import uuid
from sqlalchemy import func

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = sa.Column(sa.String(255), unique=True, nullable=False, index=True)
    full_name = sa.Column(sa.String(255), nullable=False)
    hashed_password = sa.Column(sa.String(255), nullable=False)
    role = sa.Column(sa.String(50), nullable=False, default='customer_care')  # admin, sales_staff, marketer, customer_care, production_staff
    is_active = sa.Column(sa.Boolean, default=True)
    is_locked = sa.Column(sa.Boolean, default=False)
    failed_login_attempts = sa.Column(sa.Integer, default=0)
    last_login = sa.Column(sa.TIMESTAMP(timezone=True))
    two_factor_enabled = sa.Column(sa.Boolean, default=False)
    two_factor_secret = sa.Column(sa.String(255))
    phone = sa.Column(sa.String(20))
    department = sa.Column(sa.String(100))
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = sa.Column(sa.TIMESTAMP(timezone=True), onupdate=func.now())

class Permission(Base):
    __tablename__ = 'permissions'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = sa.Column(sa.String(100), unique=True, nullable=False)
    display_name = sa.Column(sa.String(255), nullable=False)
    description = sa.Column(sa.Text)
    module = sa.Column(sa.String(50), nullable=False)  # staff, sales, production, inventory, etc.
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())
    
    # Relationships
    user_permissions = relationship("UserPermission", back_populates="permission")

class UserPermission(Base):
    __tablename__ = 'user_permissions'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    permission_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('permissions.id', ondelete='CASCADE'), nullable=False)
    granted_by = sa.Column(UUID(as_uuid=True), sa.ForeignKey('users.id'))
    granted_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())
    
    # Relationships - explicitly specify foreign_keys to avoid ambiguity
    user = relationship("User", foreign_keys=[user_id])
    permission = relationship("Permission", back_populates="user_permissions")
    granter = relationship("User", foreign_keys=[granted_by])

class Product(Base):
    __tablename__ = 'products'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sku = sa.Column(sa.String(64), unique=True, nullable=False, index=True)
    name = sa.Column(sa.String(255), nullable=False)
    description = sa.Column(sa.Text)
    manufacturer = sa.Column(sa.String(255))
    unit = sa.Column(sa.String(32), nullable=False, default='each')
    reorder_level = sa.Column(sa.Numeric(18,6), default=0)
    cost_price = sa.Column(sa.Numeric(18,2), default=0)
    selling_price = sa.Column(sa.Numeric(18,2), default=0)
    retail_price = sa.Column(sa.Numeric(18,2))
    wholesale_price = sa.Column(sa.Numeric(18,2))
    lead_time_days = sa.Column(sa.Integer, default=0)
    minimum_order_quantity = sa.Column(sa.Numeric(18,6), default=1)
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())

class RawMaterial(Base):
    __tablename__ = 'raw_materials'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sku = sa.Column(sa.String(64), unique=True, nullable=False, index=True)
    name = sa.Column(sa.String(255), nullable=False)
    manufacturer = sa.Column(sa.String(255))
    unit = sa.Column(sa.String(50), default='kg')
    reorder_level = sa.Column(sa.Numeric(18,6), default=0)
    unit_cost = sa.Column(sa.Numeric(18,2), nullable=False, default=0)
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())

class BOM(Base):
    __tablename__ = 'boms'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('products.id'), nullable=False, index=True)
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())
    
    # Relationships
    lines = relationship("BOMLine", back_populates="bom")

class BOMLine(Base):
    __tablename__ = 'bom_lines'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bom_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('boms.id'), nullable=False, index=True)
    raw_material_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('raw_materials.id'), nullable=False)
    qty_per_unit = sa.Column(sa.Numeric(18,6), nullable=False)
    
    # Relationships
    bom = relationship("BOM", back_populates="lines")

class ProductCost(Base):
    __tablename__ = 'product_costs'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('products.id'), nullable=False, index=True)
    unit_cost = sa.Column(sa.Numeric(18,6), nullable=False)
    effective_date = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())

class Warehouse(Base):
    __tablename__ = 'warehouses'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = sa.Column(sa.String(32), unique=True, nullable=False, index=True)
    name = sa.Column(sa.String(255), nullable=False)
    location = sa.Column(sa.String(255))
    manager_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('staff.id'), nullable=True)
    is_active = sa.Column(sa.Boolean, default=True)
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())

class StockMovement(Base):
    __tablename__ = 'stock_movements'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    warehouse_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('warehouses.id'), nullable=False, index=True)
    product_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('products.id'), index=True)
    raw_material_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('raw_materials.id'), index=True)
    movement_type = sa.Column(sa.String(32), nullable=False)  # IN, OUT, RETURN, DAMAGE, TRANSFER
    quantity = sa.Column(sa.Numeric(18,6), nullable=False)
    unit_cost = sa.Column(sa.Numeric(18,6))
    reference = sa.Column(sa.String(255))  # Order number, invoice, etc.
    notes = sa.Column(sa.Text)
    created_by = sa.Column(UUID(as_uuid=True), sa.ForeignKey('users.id'))
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())

class StockLevel(Base):
    __tablename__ = 'stock_levels'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    warehouse_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('warehouses.id'), nullable=False, index=True)
    product_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('products.id'), index=True)
    raw_material_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('raw_materials.id'), index=True)
    current_stock = sa.Column(sa.Numeric(18,6), nullable=False, default=0)
    reserved_stock = sa.Column(sa.Numeric(18,6), default=0)
    min_stock = sa.Column(sa.Numeric(18,6), default=0)
    max_stock = sa.Column(sa.Numeric(18,6), default=0)
    updated_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())

# Damaged and Returned Stock Models
class DamagedStock(Base):
    __tablename__ = 'damaged_stock'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    warehouse_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('warehouses.id'), nullable=False)
    product_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('products.id'))
    raw_material_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('raw_materials.id'))
    quantity = sa.Column(sa.Numeric(18,6), nullable=False)
    damage_type = sa.Column(sa.String(100), nullable=False)
    damage_reason = sa.Column(sa.Text)
    damage_date = sa.Column(sa.Date, nullable=False, server_default=func.current_date())
    reported_by = sa.Column(UUID(as_uuid=True), sa.ForeignKey('staff.id'))
    disposal_status = sa.Column(sa.String(50), nullable=False, default='pending')
    disposal_date = sa.Column(sa.Date)
    notes = sa.Column(sa.Text)
    created_at = sa.Column(sa.DateTime, server_default=func.now())

class ReturnedStock(Base):
    __tablename__ = 'returned_stock'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    warehouse_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('warehouses.id'), nullable=False)
    product_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('products.id'), nullable=False)
    sales_order_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('sales_orders.id'))
    customer_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('customers.id'))
    quantity = sa.Column(sa.Numeric(18,6), nullable=False)
    return_reason = sa.Column(sa.Text, nullable=False)
    return_condition = sa.Column(sa.String(50), nullable=False)
    return_date = sa.Column(sa.Date, nullable=False, server_default=func.current_date())
    refund_status = sa.Column(sa.String(50), nullable=False, default='pending')
    refund_amount = sa.Column(sa.Numeric(18,2))
    processed_by = sa.Column(UUID(as_uuid=True), sa.ForeignKey('staff.id'))
    notes = sa.Column(sa.Text)
    created_at = sa.Column(sa.DateTime, server_default=func.now())

# Sales Models
class Customer(Base):
    __tablename__ = 'customers'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_code = sa.Column(sa.String(32), unique=True, nullable=False, index=True)
    name = sa.Column(sa.String(255), nullable=False)
    email = sa.Column(sa.String(255), index=True)
    phone = sa.Column(sa.String(50))
    address = sa.Column(sa.Text)
    credit_limit = sa.Column(sa.Numeric(12,2), default=0)
    is_active = sa.Column(sa.Boolean, default=True)
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())

class SalesOrder(Base):
    __tablename__ = 'sales_orders'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_number = sa.Column(sa.String(64), unique=True, nullable=False, index=True)
    customer_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('customers.id'), nullable=False, index=True)
    status = sa.Column(sa.String(32), nullable=False, default='pending')  # pending, confirmed, production, shipped, delivered, cancelled
    order_date = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())
    required_date = sa.Column(sa.TIMESTAMP(timezone=True))
    total_amount = sa.Column(sa.Numeric(18,2), default=0)
    notes = sa.Column(sa.Text)
    created_by = sa.Column(UUID(as_uuid=True), sa.ForeignKey('users.id'))
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())
    
    # Relationships
    customer = relationship("Customer", foreign_keys=[customer_id])
    lines = relationship("SalesOrderLine", back_populates="sales_order")

class SalesOrderLine(Base):
    __tablename__ = 'sales_order_lines'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sales_order_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('sales_orders.id'), nullable=False, index=True)
    product_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('products.id'), nullable=False)
    quantity = sa.Column(sa.Numeric(18,6), nullable=False)
    unit_price = sa.Column(sa.Numeric(18,6), nullable=False)
    line_total = sa.Column(sa.Numeric(18,2), nullable=False)
    
    # Relationships
    sales_order = relationship("SalesOrder", back_populates="lines")
    product = relationship("Product", foreign_keys=[product_id])

# Production Models
class ProductionOrder(Base):
    __tablename__ = 'production_orders'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_number = sa.Column(sa.String(64), unique=True, nullable=False, index=True)
    product_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('products.id'), nullable=False, index=True)
    quantity_planned = sa.Column(sa.Numeric(18,6), nullable=False)
    quantity_produced = sa.Column(sa.Numeric(18,6), default=0)
    status = sa.Column(sa.String(20), nullable=False, default='planned')  # planned, in_progress, completed, cancelled
    scheduled_start_date = sa.Column(sa.TIMESTAMP(timezone=True))
    actual_start_date = sa.Column(sa.TIMESTAMP(timezone=True))
    scheduled_end_date = sa.Column(sa.TIMESTAMP(timezone=True))
    actual_end_date = sa.Column(sa.TIMESTAMP(timezone=True))
    priority = sa.Column(sa.Integer, default=5)
    notes = sa.Column(sa.Text)
    created_by = sa.Column(UUID(as_uuid=True), sa.ForeignKey('users.id'))
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())

class ProductionOrderMaterial(Base):
    __tablename__ = 'production_order_materials'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_order_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('production_orders.id'), nullable=False, index=True)
    raw_material_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('raw_materials.id'), nullable=False)
    quantity_required = sa.Column(sa.Numeric(18,6), nullable=False)
    quantity_consumed = sa.Column(sa.Numeric(18,6), default=0)
    warehouse_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('warehouses.id'))

# Staff/Employee Models
class Department(Base):
    __tablename__ = 'departments'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = sa.Column(sa.String(255), nullable=False)
    description = sa.Column(sa.Text)
    manager_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('users.id'))
    is_active = sa.Column(sa.Boolean, default=True)
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())

class Employee(Base):
    __tablename__ = 'employees'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_number = sa.Column(sa.String(32), unique=True, nullable=False, index=True)
    user_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('users.id'), index=True)
    first_name = sa.Column(sa.String(128), nullable=False)
    last_name = sa.Column(sa.String(128), nullable=False)
    position = sa.Column(sa.String(128))
    department_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('departments.id'), index=True)
    salary = sa.Column(sa.Numeric(18,2))
    hire_date = sa.Column(sa.Date)
    is_active = sa.Column(sa.Boolean, default=True)
    phone = sa.Column(sa.String(32))
    address = sa.Column(sa.Text)
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())

class WorkLog(Base):
    __tablename__ = 'work_logs'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('employees.id'), nullable=False, index=True)
    production_order_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('production_orders.id'), index=True)
    work_date = sa.Column(sa.Date, nullable=False)
    hours_worked = sa.Column(sa.Numeric(5,2), nullable=False)
    description = sa.Column(sa.Text)
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())

class AuditLog(Base):
    __tablename__ = 'audit_logs'
    __table_args__ = {'extend_existing': True}
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('users.id'), index=True)
    action = sa.Column(sa.String(255), nullable=False)  # login, logout, create, update, delete, etc.
    module = sa.Column(sa.String(100))
    record_id = sa.Column(sa.String(100))
    ip_address = sa.Column(sa.String(50))
    user_agent = sa.Column(sa.String(500))
    payload = sa.Column(sa.JSON)
    details = sa.Column(sa.Text)
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now(), index=True)

# Staff model to match the staff table in database
class Staff(Base):
    __tablename__ = 'staff'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id = sa.Column(sa.String(32), unique=True, nullable=False, index=True)
    first_name = sa.Column(sa.String(100), nullable=False)
    last_name = sa.Column(sa.String(100), nullable=False)
    phone = sa.Column(sa.String(20))
    date_of_birth = sa.Column(sa.Date)
    position = sa.Column(sa.String(100))
    payment_mode = sa.Column(sa.String(20))  # 'monthly' or 'hourly'
    hourly_rate = sa.Column(sa.Numeric(10,2), default=0)
    monthly_salary = sa.Column(sa.Numeric(10,2), default=0)  # Keep for backward compatibility
    # Clock-in PIN for attendance (4-digit auto-generated)
    clock_pin = sa.Column(sa.String(4), unique=True, nullable=False, index=True)
    # Bank / payroll account details
    bank_name = sa.Column(sa.String(128))
    bank_account_number = sa.Column(sa.String(64))
    bank_account_name = sa.Column(sa.String(128))
    bank_currency = sa.Column(sa.String(8), default='NGN')
    hire_date = sa.Column(sa.Date)
    is_active = sa.Column(sa.Boolean, default=True)
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())

# Attendance model for timed clock-in / clock-out
class Attendance(Base):
    __tablename__ = 'attendance'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    staff_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('staff.id'), nullable=False, index=True)
    clock_in = sa.Column(sa.TIMESTAMP(timezone=True), nullable=False)
    clock_out = sa.Column(sa.TIMESTAMP(timezone=True))
    hours_worked = sa.Column(sa.Numeric(6,2), default=0)
    status = sa.Column(sa.String(32), default='open')  # open, completed
    notes = sa.Column(sa.Text)
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())

# Additional models for existing database tables
class Invoice(Base):
    __tablename__ = 'invoices'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_number = sa.Column(sa.String(64), unique=True, nullable=False, index=True)
    customer_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('customers.id'), nullable=False)
    sales_order_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('sales_orders.id'))
    invoice_date = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())
    due_date = sa.Column(sa.TIMESTAMP(timezone=True))
    total_amount = sa.Column(sa.Numeric(18,2), default=0)
    paid_amount = sa.Column(sa.Numeric(18,2), default=0)
    status = sa.Column(sa.String(32), default='pending')  # pending, paid, overdue, cancelled
    notes = sa.Column(sa.Text)
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())

class InvoiceLine(Base):
    __tablename__ = 'invoice_lines'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('invoices.id'), nullable=False)
    product_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('products.id'), nullable=False)
    quantity = sa.Column(sa.Numeric(18,6), nullable=False)
    unit_price = sa.Column(sa.Numeric(18,6), nullable=False)
    line_total = sa.Column(sa.Numeric(18,2), nullable=False)

class Payment(Base):
    __tablename__ = 'payments'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('invoices.id'), nullable=False)
    payment_method = sa.Column(sa.String(50), nullable=False)  # cash, check, bank_transfer, etc.
    amount = sa.Column(sa.Numeric(18,2), nullable=False)
    payment_date = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())
    reference = sa.Column(sa.String(255))
    notes = sa.Column(sa.Text)
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())

class PayrollEntry(Base):
    __tablename__ = 'payroll_entries'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    staff_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('staff.id'), nullable=False)
    pay_period_start = sa.Column(sa.Date, nullable=False)
    pay_period_end = sa.Column(sa.Date, nullable=False)
    regular_hours = sa.Column(sa.Numeric(5,2), default=0)
    overtime_hours = sa.Column(sa.Numeric(5,2), default=0)
    gross_pay = sa.Column(sa.Numeric(18,2), nullable=False)
    deductions = sa.Column(sa.Numeric(18,2), default=0)
    net_pay = sa.Column(sa.Numeric(18,2), nullable=False)
    status = sa.Column(sa.String(32), default='draft')  # draft, approved, paid
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())

class ProductionLaborLog(Base):
    __tablename__ = 'production_labor_logs'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    production_order_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('production_orders.id'), nullable=False)
    staff_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('staff.id'), nullable=False)
    work_date = sa.Column(sa.Date, nullable=False)
    hours_worked = sa.Column(sa.Numeric(5,2), nullable=False)
    hourly_rate = sa.Column(sa.Numeric(10,2), nullable=False)
    total_cost = sa.Column(sa.Numeric(18,2), nullable=False)
    description = sa.Column(sa.Text)
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())

class SystemSettings(Base):
    __tablename__ = 'system_settings'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # General Settings
    company_name = sa.Column(sa.String(255), default='ASTRO-ASIX ERP')
    company_logo_url = sa.Column(sa.String(500))
    company_slogan = sa.Column(sa.String(500))
    business_address = sa.Column(sa.Text)
    business_email = sa.Column(sa.String(255))
    business_phone = sa.Column(sa.String(50))
    business_website = sa.Column(sa.String(255))
    
    # Localization
    currency_code = sa.Column(sa.String(10), default='NGN')
    currency_symbol = sa.Column(sa.String(10), default='₦')
    timezone = sa.Column(sa.String(100), default='Africa/Lagos')
    date_format = sa.Column(sa.String(50), default='DD/MM/YYYY')
    time_format = sa.Column(sa.String(50), default='HH:mm:ss')
    number_format = sa.Column(sa.String(50), default='1,234.56')
    default_language = sa.Column(sa.String(10), default='en')
    
    # Tax & Currency
    default_tax_rate = sa.Column(sa.Numeric(5,2), default=7.5)  # VAT in Nigeria
    enable_currency_conversion = sa.Column(sa.Boolean, default=False)
    currency_api_key = sa.Column(sa.String(255))
    
    # Theme
    theme_mode = sa.Column(sa.String(20), default='light')  # light, dark, auto
    primary_color = sa.Column(sa.String(20), default='#3b82f6')
    secondary_color = sa.Column(sa.String(20), default='#6366f1')
    
    # Inventory Settings
    stock_valuation_method = sa.Column(sa.String(50), default='FIFO')  # FIFO, LIFO, Weighted Average
    default_warehouse_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('warehouses.id'))
    auto_generate_sku = sa.Column(sa.Boolean, default=True)
    sku_prefix = sa.Column(sa.String(20), default='PRD')
    low_stock_alert_threshold = sa.Column(sa.Numeric(18,2), default=10)
    enable_barcode = sa.Column(sa.Boolean, default=True)
    barcode_type = sa.Column(sa.String(50), default='CODE128')
    
    # Procurement Settings
    default_payment_terms = sa.Column(sa.String(100), default='Net 30')
    po_number_prefix = sa.Column(sa.String(20), default='PO')
    po_auto_numbering = sa.Column(sa.Boolean, default=True)
    default_lead_time_days = sa.Column(sa.Integer, default=7)
    
    # Sales Settings
    invoice_prefix = sa.Column(sa.String(20), default='INV')
    invoice_auto_numbering = sa.Column(sa.Boolean, default=True)
    sales_tax_enabled = sa.Column(sa.Boolean, default=True)
    default_discount_type = sa.Column(sa.String(20), default='percentage')  # percentage, fixed
    default_sales_channel = sa.Column(sa.String(50), default='in_store')
    
    # Accounting Settings
    fiscal_year_start_month = sa.Column(sa.Integer, default=1)  # January
    auto_post_invoices = sa.Column(sa.Boolean, default=True)
    auto_post_purchases = sa.Column(sa.Boolean, default=True)
    auto_post_payroll = sa.Column(sa.Boolean, default=False)
    
    # Security Settings
    password_min_length = sa.Column(sa.Integer, default=8)
    password_require_uppercase = sa.Column(sa.Boolean, default=True)
    password_require_lowercase = sa.Column(sa.Boolean, default=True)
    password_require_numbers = sa.Column(sa.Boolean, default=True)
    password_require_special = sa.Column(sa.Boolean, default=True)
    session_timeout_minutes = sa.Column(sa.Integer, default=30)
    max_login_attempts = sa.Column(sa.Integer, default=5)
    enable_2fa = sa.Column(sa.Boolean, default=False)
    enable_ip_whitelist = sa.Column(sa.Boolean, default=False)
    
    # Notifications
    smtp_host = sa.Column(sa.String(255))
    smtp_port = sa.Column(sa.Integer, default=587)
    smtp_username = sa.Column(sa.String(255))
    smtp_password = sa.Column(sa.String(255))
    smtp_use_tls = sa.Column(sa.Boolean, default=True)
    sms_gateway_url = sa.Column(sa.String(500))
    sms_api_key = sa.Column(sa.String(255))
    enable_email_notifications = sa.Column(sa.Boolean, default=True)
    enable_sms_notifications = sa.Column(sa.Boolean, default=False)
    
    # Backup Settings
    auto_backup_enabled = sa.Column(sa.Boolean, default=False)
    backup_frequency = sa.Column(sa.String(50), default='daily')  # daily, weekly, monthly
    backup_time = sa.Column(sa.String(10), default='02:00')
    backup_retention_days = sa.Column(sa.Integer, default=30)
    
    # Module Toggles
    module_hr_enabled = sa.Column(sa.Boolean, default=True)
    module_payroll_enabled = sa.Column(sa.Boolean, default=True)
    module_inventory_enabled = sa.Column(sa.Boolean, default=True)
    module_production_enabled = sa.Column(sa.Boolean, default=True)
    module_sales_enabled = sa.Column(sa.Boolean, default=True)
    module_accounting_enabled = sa.Column(sa.Boolean, default=True)
    module_crm_enabled = sa.Column(sa.Boolean, default=False)
    
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = sa.Column(sa.TIMESTAMP(timezone=True), onupdate=func.now())

class RolePermission(Base):
    __tablename__ = 'role_permissions'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role = sa.Column(sa.String(50), nullable=False, index=True)  # admin, sales_staff, marketer, customer_care, production_staff
    module = sa.Column(sa.String(100), nullable=False)  # staff, products, sales, inventory, etc.
    can_view = sa.Column(sa.Boolean, default=False)
    can_create = sa.Column(sa.Boolean, default=False)
    can_edit = sa.Column(sa.Boolean, default=False)
    can_delete = sa.Column(sa.Boolean, default=False)
    can_approve = sa.Column(sa.Boolean, default=False)
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = sa.Column(sa.TIMESTAMP(timezone=True), onupdate=func.now())

class UserSession(Base):
    __tablename__ = 'user_sessions'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = sa.Column(UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False, index=True)
    token = sa.Column(sa.String(500), unique=True, nullable=False, index=True)
    ip_address = sa.Column(sa.String(50))
    user_agent = sa.Column(sa.String(500))
    expires_at = sa.Column(sa.TIMESTAMP(timezone=True), nullable=False)
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())

class CustomField(Base):
    __tablename__ = 'custom_fields'
    id = sa.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    module = sa.Column(sa.String(100), nullable=False, index=True)  # products, customers, orders, etc.
    field_name = sa.Column(sa.String(100), nullable=False)
    field_label = sa.Column(sa.String(200), nullable=False)
    field_type = sa.Column(sa.String(50), nullable=False)  # text, number, date, dropdown, checkbox, etc.
    field_options = sa.Column(sa.Text)  # JSON string for dropdown options
    is_required = sa.Column(sa.Boolean, default=False)
    is_active = sa.Column(sa.Boolean, default=True)
    display_order = sa.Column(sa.Integer, default=0)
    created_at = sa.Column(sa.TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = sa.Column(sa.TIMESTAMP(timezone=True), onupdate=func.now())

