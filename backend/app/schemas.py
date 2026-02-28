from pydantic import BaseModel, Field, validator, ConfigDict
from typing import Optional, List, Generic, TypeVar, Any
from decimal import Decimal
from datetime import datetime, date
from uuid import UUID
import uuid

T = TypeVar('T')

# Product Pricing Schemas
class ProductPricingBase(BaseModel):
    unit: str = Field(..., min_length=1, max_length=50)
    cost_price: Decimal = Field(default=0, ge=0)
    retail_price: Decimal = Field(..., ge=0)
    wholesale_price: Decimal = Field(..., ge=0)

class ProductPricingCreate(ProductPricingBase):
    pass

class ProductPricingSchema(ProductPricingBase):
    id: uuid.UUID
    product_id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True

# Product Schemas
class ProductBase(BaseModel):
    sku: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    manufacturer: Optional[str] = Field(None, max_length=255)
    unit: str = Field(default="each", max_length=32)
    reorder_level: Optional[Decimal] = Field(default=0, ge=0)
    cost_price: Optional[Decimal] = Field(default=0, ge=0)
    selling_price: Optional[Decimal] = Field(default=0, ge=0)
    retail_price: Optional[Decimal] = Field(None, ge=0)
    wholesale_price: Optional[Decimal] = Field(None, ge=0)
    lead_time_days: Optional[int] = Field(default=0, ge=0)
    minimum_order_quantity: Optional[Decimal] = Field(default=1, gt=0)

class ProductCreate(ProductBase):
    pricing: Optional[List[ProductPricingCreate]] = Field(default_factory=list)

class ProductUpdate(BaseModel):
    sku: Optional[str] = Field(None, min_length=1, max_length=64)
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    manufacturer: Optional[str] = Field(None, max_length=255)
    unit: Optional[str] = Field(None, max_length=32)
    reorder_level: Optional[Decimal] = Field(None, ge=0)
    cost_price: Optional[Decimal] = Field(None, ge=0)
    selling_price: Optional[Decimal] = Field(None, ge=0)
    retail_price: Optional[Decimal] = Field(None, ge=0)
    wholesale_price: Optional[Decimal] = Field(None, ge=0)
    lead_time_days: Optional[int] = Field(None, ge=0)
    minimum_order_quantity: Optional[Decimal] = Field(None, gt=0)
    pricing: Optional[List[ProductPricingCreate]] = None

class ProductSchema(ProductBase):
    id: uuid.UUID
    created_at: datetime
    pricing: List[ProductPricingSchema] = Field(default_factory=list)

    class Config:
        from_attributes = True

# Raw Material Schemas
class RawMaterialBase(BaseModel):
    sku: Optional[str] = Field(None, max_length=64)
    name: str = Field(..., min_length=1, max_length=255)
    unit_cost: Decimal = Field(..., ge=0)
    manufacturer: Optional[str] = Field(None, max_length=255)
    unit: Optional[str] = Field(default="kg", max_length=32)
    reorder_level: Optional[Decimal] = Field(default=0, ge=0)
    category: Optional[str] = Field(default="General", max_length=255)
    source: Optional[str] = Field(default="Local", max_length=255)

class RawMaterialCreate(RawMaterialBase):
    opening_stock: Optional[Decimal] = Field(default=0, ge=0)

class RawMaterialUpdate(BaseModel):
    sku: Optional[str] = Field(None, min_length=1, max_length=64)
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    unit_cost: Optional[Decimal] = Field(None, ge=0)
    manufacturer: Optional[str] = Field(None, max_length=255)
    unit: Optional[str] = Field(None, max_length=32)
    reorder_level: Optional[Decimal] = Field(None, ge=0)
    category: Optional[str] = Field(None, max_length=255)
    source: Optional[str] = Field(None, max_length=255)

class RawMaterialSchema(RawMaterialBase):
    id: uuid.UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# Warehouse Schemas
class WarehouseBase(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    name: str = Field(..., min_length=1, max_length=255)
    location: Optional[str] = Field(None, max_length=255)
    manager_id: Optional[UUID] = None

class WarehouseCreate(WarehouseBase):
    pass

class WarehouseUpdate(BaseModel):
    code: Optional[str] = Field(None, min_length=1, max_length=32)
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    location: Optional[str] = Field(None, max_length=255)
    manager_id: Optional[UUID] = None
    is_active: Optional[bool] = None

class WarehouseSchema(WarehouseBase):
    id: uuid.UUID
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

# Stock Movement Schemas
class StockMovementBase(BaseModel):
    warehouse_id: uuid.UUID
    movement_type: str = Field(..., pattern="^(IN|OUT|RETURN|DAMAGE|TRANSFER)$")
    quantity: Decimal = Field(..., gt=0)
    unit_cost: Optional[Decimal] = Field(None, ge=0)
    reference: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None

class StockMovementCreateProduct(StockMovementBase):
    product_id: uuid.UUID

class StockMovementCreateRawMaterial(StockMovementBase):
    raw_material_id: uuid.UUID

class StockMovementSchema(StockMovementBase):
    id: uuid.UUID
    product_id: Optional[uuid.UUID]
    raw_material_id: Optional[uuid.UUID]
    created_by: Optional[uuid.UUID]
    created_at: datetime

    class Config:
        from_attributes = True

# Stock Intake Schema
class StockIntakeCreate(BaseModel):
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: Decimal = Field(..., gt=0)
    unit_cost: Optional[Decimal] = Field(None, ge=0)
    supplier: Optional[str] = Field(None, max_length=255)
    batch_number: Optional[str] = Field(None, max_length=100)
    expiry_date: Optional[str] = None
    notes: Optional[str] = None

# Raw Material Stock Intake Schema
class RawMaterialStockIntakeCreate(BaseModel):
    raw_material_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: Decimal = Field(..., gt=0)
    unit_cost: Optional[Decimal] = Field(None, ge=0)
    supplier: Optional[str] = Field(None, max_length=255)
    batch_number: Optional[str] = Field(None, max_length=100)
    expiry_date: Optional[str] = None
    notes: Optional[str] = None

# Stock Level Schemas
class StockLevelSchema(BaseModel):
    id: uuid.UUID
    warehouse_id: uuid.UUID
    product_id: Optional[uuid.UUID]
    raw_material_id: Optional[uuid.UUID]
    current_stock: Decimal
    reserved_stock: Decimal
    min_stock: Decimal
    max_stock: Decimal
    updated_at: datetime

    class Config:
        from_attributes = True

# Response Schemas
class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    total: int
    page: int = 1
    size: int = 50
    pages: int

class ApiResponse(BaseModel):
    success: bool = True
    message: str = "Operation completed successfully"
    data: Optional[Any] = None

# Sales Schemas
class CustomerBase(BaseModel):
    customer_code: str = Field(..., max_length=32)
    name: str = Field(..., max_length=255)
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    address: Optional[str] = None
    credit_limit: Optional[Decimal] = Field(default=0, ge=0)

class CustomerCreate(CustomerBase):
    pass

class CustomerUpdate(BaseModel):
    customer_code: Optional[str] = Field(None, max_length=32)
    name: Optional[str] = Field(None, max_length=255)
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=50)
    address: Optional[str] = None
    credit_limit: Optional[Decimal] = Field(None, ge=0)
    is_active: Optional[bool] = None

class CustomerSchema(CustomerBase):
    id: UUID
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True

class SalesOrderLineBase(BaseModel):
    product_id: UUID
    unit: Optional[str] = Field(None, max_length=50)  # Unit of measure for pricing
    quantity: Decimal = Field(..., gt=0)
    unit_price: Decimal = Field(..., ge=0)

class SalesOrderLineCreate(SalesOrderLineBase):
    pass

class SalesOrderLineSchema(SalesOrderLineBase):
    id: UUID
    line_total: Decimal
    
    class Config:
        from_attributes = True

class SalesOrderBase(BaseModel):
    customer_id: UUID
    warehouse_id: Optional[UUID] = None
    required_date: Optional[datetime] = None
    notes: Optional[str] = None

class SalesOrderCreate(SalesOrderBase):
    lines: List[SalesOrderLineCreate] = []

class SalesOrderUpdate(BaseModel):
    status: Optional[str] = Field(None, pattern="^(pending|confirmed|production|shipped|delivered|cancelled)$")
    payment_status: Optional[str] = Field(None, pattern="^(paid|unpaid|partial)$")
    required_date: Optional[datetime] = None
    notes: Optional[str] = None

class SalesOrderSchema(SalesOrderBase):
    id: UUID
    order_number: str
    status: str
    payment_status: str
    payment_date: Optional[datetime] = None
    order_date: datetime
    total_amount: Decimal
    lines: List[SalesOrderLineSchema] = []
    created_at: datetime
    
    class Config:
        from_attributes = True

# Production Schemas
class ProductionOrderBase(BaseModel):
    product_id: UUID
    quantity_planned: Decimal = Field(..., gt=0)
    scheduled_start_date: Optional[datetime] = None
    scheduled_end_date: Optional[datetime] = None
    priority: Optional[int] = Field(default=5, ge=1, le=10)
    notes: Optional[str] = None
    sales_order_id: Optional[UUID] = None

class ProductionOrderCreate(ProductionOrderBase):
    pass

class ProductionOrderUpdate(BaseModel):
    quantity_planned: Optional[Decimal] = Field(None, gt=0)
    quantity_produced: Optional[Decimal] = Field(None, ge=0)
    status: Optional[str] = Field(None, pattern="^(planned|in_progress|completed|cancelled)$")
    scheduled_start_date: Optional[datetime] = None
    actual_start_date: Optional[datetime] = None
    scheduled_end_date: Optional[datetime] = None
    actual_end_date: Optional[datetime] = None
    priority: Optional[int] = Field(None, ge=1, le=10)
    notes: Optional[str] = None

class ProductionOrderSchema(ProductionOrderBase):
    id: UUID
    order_number: str
    quantity_produced: Decimal
    status: str
    actual_start_date: Optional[datetime]
    actual_end_date: Optional[datetime]
    created_at: datetime
    
    class Config:
        from_attributes = True

# Staff Schemas
class DepartmentBase(BaseModel):
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    manager_id: Optional[UUID] = None

class DepartmentCreate(DepartmentBase):
    pass

class DepartmentUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    manager_id: Optional[UUID] = None
    is_active: Optional[bool] = None

class DepartmentSchema(DepartmentBase):
    id: UUID
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True

class EmployeeBase(BaseModel):
    employee_number: str = Field(..., max_length=32)
    first_name: str = Field(..., max_length=128)
    last_name: str = Field(..., max_length=128)
    position: Optional[str] = Field(None, max_length=128)
    department_id: Optional[UUID] = None
    salary: Optional[Decimal] = Field(None, ge=0)
    hire_date: Optional[date] = None
    phone: Optional[str] = Field(None, max_length=32)
    address: Optional[str] = None
    user_id: Optional[UUID] = None

class EmployeeCreate(EmployeeBase):
    pass

class EmployeeUpdate(BaseModel):
    first_name: Optional[str] = Field(None, max_length=128)
    last_name: Optional[str] = Field(None, max_length=128)
    position: Optional[str] = Field(None, max_length=128)
    department_id: Optional[UUID] = None
    salary: Optional[Decimal] = Field(None, ge=0)
    phone: Optional[str] = Field(None, max_length=32)
    address: Optional[str] = None
    is_active: Optional[bool] = None

class EmployeeSchema(EmployeeBase):
    id: UUID
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True

class WorkLogBase(BaseModel):
    employee_id: UUID
    production_order_id: Optional[UUID] = None
    work_date: date
    hours_worked: Decimal = Field(..., gt=0, le=24)
    description: Optional[str] = None

class WorkLogCreate(WorkLogBase):
    pass

class WorkLogSchema(WorkLogBase):
    id: UUID
    created_at: datetime
    
    class Config:
        from_attributes = True

# Staff Schemas (for the staff table)
class StaffBase(BaseModel):
    first_name: str = Field(..., max_length=100)
    last_name: str = Field(..., max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    date_of_birth: Optional[date] = None
    position: Optional[str] = Field(None, max_length=100)
    payment_mode: Optional[str] = Field(None, max_length=20)  # 'monthly' or 'hourly'
    hourly_rate: Optional[Decimal] = Field(default=0, ge=0)
    monthly_salary: Optional[Decimal] = Field(default=0, ge=0)  # Keep for backward compatibility
    hire_date: Optional[date] = None
    # Bank / payroll account details
    bank_name: Optional[str] = Field(None, max_length=128)
    bank_account_number: Optional[str] = Field(None, max_length=64)
    bank_account_name: Optional[str] = Field(None, max_length=128)
    bank_currency: Optional[str] = Field(default='NGN', max_length=8)

class StaffCreate(StaffBase):
    pass

class StaffUpdate(BaseModel):
    first_name: Optional[str] = Field(None, max_length=100)
    last_name: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    date_of_birth: Optional[date] = None
    position: Optional[str] = Field(None, max_length=100)
    payment_mode: Optional[str] = Field(None, max_length=20)
    hourly_rate: Optional[Decimal] = Field(None, ge=0)
    monthly_salary: Optional[Decimal] = Field(None, ge=0)
    hire_date: Optional[date] = None
    is_active: Optional[bool] = None
    bank_name: Optional[str] = Field(None, max_length=128)
    bank_account_number: Optional[str] = Field(None, max_length=64)
    bank_account_name: Optional[str] = Field(None, max_length=128)
    bank_currency: Optional[str] = Field(None, max_length=8)

class StaffSchema(StaffBase):
    id: UUID
    employee_id: str  # Auto-generated BSM + 4 digits
    clock_pin: str  # Include PIN in response for admin use
    is_active: bool
    created_at: datetime
    full_name: Optional[str] = None  # Computed field
    
    @validator('full_name', always=True, pre=False)
    def compute_full_name(cls, v, values):
        """Compute full_name from first_name and last_name"""
        if v:
            return v
        first = values.get('first_name', '')
        last = values.get('last_name', '')
        return f"{first} {last}".strip()
    
    class Config:
        from_attributes = True

# Payroll Schema
class PayrollEntryBase(BaseModel):
    staff_id: UUID
    pay_period_start: date
    pay_period_end: date

class PayrollEntryCreate(PayrollEntryBase):
    pass

class PayrollEntrySchema(BaseModel):
    id: UUID
    staff_id: UUID
    pay_period_start: date
    pay_period_end: date
    regular_hours: Decimal
    overtime_hours: Decimal
    gross_pay: Decimal
    deductions: Decimal
    net_pay: Decimal
    status: str
    created_at: datetime

    class Config:
        from_attributes = True

# Attendance Schemas
class AttendanceBase(BaseModel):
    staff_id: UUID
    clock_in: Optional[datetime] = None
    clock_out: Optional[datetime] = None
    hours_worked: Optional[Decimal] = Field(default=0)
    notes: Optional[str] = None

class AttendanceCreate(BaseModel):
    staff_id: UUID
    notes: Optional[str] = None

class AttendanceSchema(AttendanceBase):
    id: UUID
    status: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

# PIN-based Quick Attendance Schemas
class QuickAttendanceRequest(BaseModel):
    pin: str = Field(..., min_length=4, max_length=4, pattern="^[0-9]{4}$")
    action: str = Field(..., pattern="^(clock_in|clock_out)$")
    notes: Optional[str] = Field(None, max_length=255)

class QuickAttendanceResponse(BaseModel):
    success: bool
    message: str
    staff_name: Optional[str] = None
    action: Optional[str] = None
    timestamp: Optional[datetime] = None
    hours_worked: Optional[Decimal] = None