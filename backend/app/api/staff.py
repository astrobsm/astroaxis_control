from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from typing import List, Optional
from uuid import UUID
from datetime import datetime, date
import os

from app.db import get_session
from app.models import Employee, Department, WorkLog, User, ProductionOrder
from app.schemas import (
    EmployeeSchema, EmployeeCreate, EmployeeUpdate,
    DepartmentSchema, DepartmentCreate, DepartmentUpdate,
    WorkLogSchema, WorkLogCreate,
    PaginatedResponse, ApiResponse
)
from sqlalchemy import func

# Additional imports for staff payroll and attendance
from app.models import Staff, PayrollEntry, Attendance
from app.schemas import StaffSchema, StaffCreate, StaffUpdate, PayrollEntrySchema, PayrollEntryCreate, AttendanceSchema
from fastapi.responses import StreamingResponse
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from decimal import Decimal
from datetime import timedelta
import random
import string

router = APIRouter(prefix='/api/staff')

# Helper function to generate unique 4-digit PIN
async def generate_unique_pin(session: AsyncSession) -> str:
    """Generate a unique 4-digit PIN for staff attendance"""
    max_attempts = 1000  # Prevent infinite loop
    for _ in range(max_attempts):
        pin = ''.join(random.choices(string.digits, k=4))
        # Check if PIN already exists
        existing = await session.execute(select(Staff).where(Staff.clock_pin == pin))
        if not existing.scalars().first():
            return pin
    raise HTTPException(status_code=500, detail="Unable to generate unique PIN")

# Helper function to generate unique employee ID (BSM + 4 digits)
async def generate_unique_employee_id(session: AsyncSession) -> str:
    """Generate a unique employee ID in format BSM####"""
    max_attempts = 1000  # Prevent infinite loop
    for _ in range(max_attempts):
        # Generate 4 random digits
        four_digits = ''.join(random.choices(string.digits, k=4))
        employee_id = f"BSM{four_digits}"
        # Check if employee_id already exists
        existing = await session.execute(select(Staff).where(Staff.employee_id == employee_id))
        if not existing.scalars().first():
            return employee_id
    raise HTTPException(status_code=500, detail="Unable to generate unique employee ID")

# Department endpoints
@router.post('/departments', response_model=DepartmentSchema)
async def create_department(
    department_data: DepartmentCreate,
    session: AsyncSession = Depends(get_session)
):
    """Create a new department"""
    try:
        # Verify manager exists if provided
        if department_data.manager_id:
            user_result = await session.execute(select(User).where(User.id == department_data.manager_id))
            user = user_result.scalars().first()
            if not user:
                raise HTTPException(status_code=404, detail="Manager user not found")
        
        department = Department(**department_data.dict())
        session.add(department)
        await session.commit()
        await session.refresh(department)
        return department
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error creating department: {str(e)}")

@router.get('/departments', response_model=PaginatedResponse[DepartmentSchema])
async def list_departments(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    active_only: bool = Query(True),
    session: AsyncSession = Depends(get_session)
):
    """List departments with pagination"""
    query = select(Department)
    
    if active_only:
        query = query.where(Department.is_active == True)
    
    # Get total count
    count_result = await session.execute(select(func.count(Department.id)).select_from(query.subquery()))
    total = count_result.scalar_one()
    
    # Calculate page from skip/limit
    page = (skip // limit) + 1 if limit > 0 else 1
    size = limit
    
    # Get paginated results
    query = query.offset(skip).limit(limit).order_by(Department.name)
    result = await session.execute(query)
    departments = result.scalars().all()
    
    return PaginatedResponse(
        items=departments,
        total=total,
        page=page,
        size=size,
        pages=(total + size - 1) // size if size > 0 else 1
    )

@router.get('/departments/{department_id}', response_model=DepartmentSchema)
async def get_department(
    department_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Get department by ID"""
    result = await session.execute(select(Department).where(Department.id == department_id))
    department = result.scalars().first()
    
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")
    
    return department

@router.put('/departments/{department_id}', response_model=DepartmentSchema)
async def update_department(
    department_id: UUID,
    department_data: DepartmentUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Update department"""
    result = await session.execute(select(Department).where(Department.id == department_id))
    department = result.scalars().first()
    
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")
    
    try:
        # Verify manager exists if provided
        if department_data.manager_id:
            user_result = await session.execute(select(User).where(User.id == department_data.manager_id))
            user = user_result.scalars().first()
            if not user:
                raise HTTPException(status_code=404, detail="Manager user not found")
        
        update_data = department_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(department, field, value)
        
        await session.commit()
        await session.refresh(department)
        return department
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error updating department: {str(e)}")

@router.delete('/departments/{department_id}', response_model=ApiResponse)
async def delete_department(
    department_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Soft delete department (set inactive)"""
    result = await session.execute(select(Department).where(Department.id == department_id))
    department = result.scalars().first()
    
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")
    
    try:
        department.is_active = False
        await session.commit()
        return ApiResponse(message="Department deactivated successfully")
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error deactivating department: {str(e)}")

# Employee endpoints
@router.post('/employees', response_model=EmployeeSchema)
async def create_employee(
    employee_data: EmployeeCreate,
    session: AsyncSession = Depends(get_session)
):
    """Create a new employee"""
    try:
        # Check if employee number already exists
        existing_result = await session.execute(
            select(Employee).where(Employee.employee_number == employee_data.employee_number)
        )
        if existing_result.scalars().first():
            raise HTTPException(status_code=400, detail="Employee number already exists")
        
        # Verify department exists if provided
        if employee_data.department_id:
            dept_result = await session.execute(select(Department).where(Department.id == employee_data.department_id))
            department = dept_result.scalars().first()
            if not department:
                raise HTTPException(status_code=404, detail="Department not found")
        
        # Verify user exists if provided
        if employee_data.user_id:
            user_result = await session.execute(select(User).where(User.id == employee_data.user_id))
            user = user_result.scalars().first()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
        
        employee = Employee(**employee_data.dict())
        session.add(employee)
        await session.commit()
        await session.refresh(employee)
        return employee
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error creating employee: {str(e)}")

@router.get('/employees', response_model=PaginatedResponse[EmployeeSchema])
async def list_employees(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    active_only: bool = Query(True),
    department_id: Optional[UUID] = Query(None),
    search: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session)
):
    """List employees with pagination, search and filters"""
    query = select(Employee)
    
    if active_only:
        query = query.where(Employee.is_active == True)
    
    if department_id:
        query = query.where(Employee.department_id == department_id)
    
    if search:
        search_filter = (
            Employee.first_name.ilike(f'%{search}%') |
            Employee.last_name.ilike(f'%{search}%') |
            Employee.employee_number.ilike(f'%{search}%')
        )
        query = query.where(search_filter)
    
    # Get total count
    count_query = select(func.count(Employee.id))
    if active_only:
        count_query = count_query.where(Employee.is_active == True)
    if department_id:
        count_query = count_query.where(Employee.department_id == department_id)
    if search:
        search_filter = (
            Employee.first_name.ilike(f'%{search}%') |
            Employee.last_name.ilike(f'%{search}%') |
            Employee.employee_number.ilike(f'%{search}%')
        )
        count_query = count_query.where(search_filter)
    
    count_result = await session.execute(count_query)
    total = count_result.scalar_one()
    
    # Calculate page from skip/limit
    page = (skip // limit) + 1 if limit > 0 else 1
    size = limit
    
    # Get paginated results
    query = query.offset(skip).limit(limit).order_by(Employee.first_name, Employee.last_name)
    result = await session.execute(query)
    employees = result.scalars().all()
    
    return PaginatedResponse(
        items=employees,
        total=total,
        page=page,
        size=size,
        pages=(total + size - 1) // size if size > 0 else 1
    )

@router.get('/employees/{employee_id}', response_model=EmployeeSchema)
async def get_employee(
    employee_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Get employee by ID"""
    result = await session.execute(select(Employee).where(Employee.id == employee_id))
    employee = result.scalars().first()
    
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    return employee

@router.put('/employees/{employee_id}', response_model=EmployeeSchema)
async def update_employee(
    employee_id: UUID,
    employee_data: EmployeeUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Update employee"""
    result = await session.execute(select(Employee).where(Employee.id == employee_id))
    employee = result.scalars().first()
    
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    try:
        # Verify department exists if provided
        if employee_data.department_id:
            dept_result = await session.execute(select(Department).where(Department.id == employee_data.department_id))
            department = dept_result.scalars().first()
            if not department:
                raise HTTPException(status_code=404, detail="Department not found")
        
        update_data = employee_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(employee, field, value)
        
        await session.commit()
        await session.refresh(employee)
        return employee
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error updating employee: {str(e)}")

@router.delete('/employees/{employee_id}', response_model=ApiResponse)
async def delete_employee(
    employee_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Soft delete employee (set inactive)"""
    result = await session.execute(select(Employee).where(Employee.id == employee_id))
    employee = result.scalars().first()
    
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    try:
        employee.is_active = False
        await session.commit()
        return ApiResponse(message="Employee deactivated successfully")
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error deactivating employee: {str(e)}")

# Work Log endpoints
@router.post('/work-logs', response_model=WorkLogSchema)
async def create_work_log(
    work_log_data: WorkLogCreate,
    session: AsyncSession = Depends(get_session)
):
    """Create a new work log entry"""
    try:
        # Verify employee exists
        employee_result = await session.execute(select(Employee).where(Employee.id == work_log_data.employee_id))
        employee = employee_result.scalars().first()
        if not employee:
            raise HTTPException(status_code=404, detail="Employee not found")
        
        # Verify production order exists if provided
        if work_log_data.production_order_id:
            po_result = await session.execute(
                select(ProductionOrder).where(ProductionOrder.id == work_log_data.production_order_id)
            )
            production_order = po_result.scalars().first()
            if not production_order:
                raise HTTPException(status_code=404, detail="Production order not found")
        
        work_log = WorkLog(**work_log_data.dict())
        session.add(work_log)
        await session.commit()
        await session.refresh(work_log)
        return work_log
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error creating work log: {str(e)}")

@router.get('/work-logs', response_model=PaginatedResponse[WorkLogSchema])
async def list_work_logs(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    employee_id: Optional[UUID] = Query(None),
    production_order_id: Optional[UUID] = Query(None),
    work_date: Optional[date] = Query(None),
    session: AsyncSession = Depends(get_session)
):
    """List work logs with pagination and filters"""
    query = select(WorkLog)
    
    if employee_id:
        query = query.where(WorkLog.employee_id == employee_id)
    
    if production_order_id:
        query = query.where(WorkLog.production_order_id == production_order_id)
    
    if work_date:
        query = query.where(WorkLog.work_date == work_date)
    
    # Get total count
    count_query = select(func.count(WorkLog.id))
    if employee_id:
        count_query = count_query.where(WorkLog.employee_id == employee_id)
    if production_order_id:
        count_query = count_query.where(WorkLog.production_order_id == production_order_id)
    if work_date:
        count_query = count_query.where(WorkLog.work_date == work_date)
    
    count_result = await session.execute(count_query)
    total = count_result.scalar_one()
    
    # Calculate page from skip/limit
    page = (skip // limit) + 1 if limit > 0 else 1
    size = limit
    
    # Get paginated results
    query = query.offset(skip).limit(limit).order_by(WorkLog.work_date.desc(), WorkLog.created_at.desc())
    result = await session.execute(query)
    work_logs = result.scalars().all()
    
    return PaginatedResponse(
        items=work_logs,
        total=total,
        page=page,
        size=size,
        pages=(total + size - 1) // size if size > 0 else 1
    )

@router.get('/work-logs/{work_log_id}', response_model=WorkLogSchema)
async def get_work_log(
    work_log_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Get work log by ID"""
    result = await session.execute(select(WorkLog).where(WorkLog.id == work_log_id))
    work_log = result.scalars().first()
    
    if not work_log:
        raise HTTPException(status_code=404, detail="Work log not found")
    
    return work_log

@router.get('/dashboard/stats')
async def get_staff_dashboard_stats(
    session: AsyncSession = Depends(get_session)
):
    """Get staff dashboard statistics"""
    try:
        # Count employees by department
        dept_counts = await session.execute(
            select(Department.name, func.count(Employee.id))
            .outerjoin(Employee, Employee.department_id == Department.id)
            .where(Employee.is_active == True)
            .group_by(Department.name)
        )
        
        # Total employees
        total_employees_result = await session.execute(
            select(func.count(Employee.id)).where(Employee.is_active == True)
        )
        total_employees = total_employees_result.scalar_one()
        
        # Total departments
        total_departments_result = await session.execute(
            select(func.count(Department.id)).where(Department.is_active == True)
        )
        total_departments = total_departments_result.scalar_one()
        
        return {
            'total_employees': total_employees,
            'total_departments': total_departments,
            'employees_by_department': dict(dept_counts.all())
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting dashboard stats: {str(e)}")


# --- New Staff endpoints (staff table separate from employee) ---
@router.post('/staffs', response_model=StaffSchema)
async def create_staff(staff_data: StaffCreate, session: AsyncSession = Depends(get_session)):
    """Create a staff record with auto-generated employee ID (BSM####), bank and payroll details"""
    try:
        # Generate unique employee ID (BSM + 4 digits)
        employee_id = await generate_unique_employee_id(session)
        
        # Generate unique PIN for the staff member
        clock_pin = await generate_unique_pin(session)
        
        # Create staff with generated employee_id and PIN
        staff_dict = staff_data.dict()
        staff_dict['employee_id'] = employee_id
        staff_dict['clock_pin'] = clock_pin
        staff = Staff(**staff_dict)
        
        session.add(staff)
        await session.commit()
        await session.refresh(staff)
        return staff
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error creating staff: {str(e)}")


@router.get('/staffs', response_model=PaginatedResponse[StaffSchema])
async def list_staffs(skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500), session: AsyncSession = Depends(get_session)):
    query = select(Staff)
    count_result = await session.execute(select(func.count(Staff.id)))
    total = count_result.scalar_one()
    result = await session.execute(query.offset(skip).limit(limit).order_by(Staff.first_name))
    items = result.scalars().all()
    
    # Calculate page from skip/limit
    page = (skip // limit) + 1 if limit > 0 else 1
    size = limit
    
    return PaginatedResponse(items=items, total=total, page=page, size=size, pages=(total + size - 1) // size if size > 0 else 1)


@router.get('/staffs/{staff_id}', response_model=StaffSchema)
async def get_staff(staff_id: UUID, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Staff).where(Staff.id == staff_id))
    staff = result.scalars().first()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff not found")
    return staff


@router.put('/staffs/{staff_id}', response_model=StaffSchema)
async def update_staff(staff_id: UUID, staff_data: StaffUpdate, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Staff).where(Staff.id == staff_id))
    staff = result.scalars().first()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff not found")
    try:
        update_data = staff_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(staff, field, value)
        await session.commit()
        await session.refresh(staff)
        return staff
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error updating staff: {str(e)}")


@router.delete('/staffs/{staff_id}', response_model=ApiResponse)
async def delete_staff(staff_id: UUID, session: AsyncSession = Depends(get_session)):
    """Delete a staff member. Removes the record permanently."""
    result = await session.execute(select(Staff).where(Staff.id == staff_id))
    staff = result.scalars().first()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff not found")
    try:
        await session.delete(staff)
        await session.commit()
        return ApiResponse(message=f"Staff {staff.employee_id} ({staff.first_name} {staff.last_name}) deleted successfully")
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error deleting staff: {str(e)}")


# Payroll calculation endpoint
@router.post('/payroll/calculate', response_model=PayrollEntrySchema)
async def calculate_payroll(pay_data: PayrollEntryCreate, session: AsyncSession = Depends(get_session)):
    """Calculate payroll for a staff member over a pay period based on attendance records"""
    try:
        # verify staff
        staff_result = await session.execute(select(Staff).where(Staff.id == pay_data.staff_id))
        staff = staff_result.scalars().first()
        if not staff:
            raise HTTPException(status_code=404, detail="Staff not found")

        # sum attendance hours in period
        att_q = select(func.coalesce(func.sum(Attendance.hours_worked), 0)).where(
            Attendance.staff_id == pay_data.staff_id,
            func.date(Attendance.clock_in) >= pay_data.pay_period_start,
            func.date(Attendance.clock_in) <= pay_data.pay_period_end,
            Attendance.status == 'completed'
        )
        att_res = await session.execute(att_q)
        total_hours = att_res.scalar_one() or 0

        # payroll rules: monthly standard hours 160 (40h/week * 4)
        standard_hours = Decimal('160')
        regular_rate = Decimal('425')  # ₦425 per hour
        overtime_rate = Decimal('450')  # ₦450 per hour
        total_hours_dec = Decimal(str(total_hours))
        regular_hours = min(total_hours_dec, standard_hours)
        overtime_hours = max(Decimal('0'), total_hours_dec - standard_hours)
        gross_pay = (regular_hours * regular_rate) + (overtime_hours * overtime_rate)
        deductions = Decimal('0')
        net_pay = gross_pay - deductions

        payroll = PayrollEntry(
            staff_id=pay_data.staff_id,
            pay_period_start=pay_data.pay_period_start,
            pay_period_end=pay_data.pay_period_end,
            regular_hours=regular_hours,
            overtime_hours=overtime_hours,
            gross_pay=gross_pay,
            deductions=deductions,
            net_pay=net_pay,
            status='draft'
        )
        session.add(payroll)
        await session.commit()
        await session.refresh(payroll)

        return payroll
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error calculating payroll: {str(e)}")


# Payslip PDF generation
@router.get('/payslip/{payroll_id}/pdf')
async def generate_payslip_pdf(payroll_id: UUID, session: AsyncSession = Depends(get_session)):
    """Generate a PDF payslip for a payroll entry and return as streaming response"""
    try:
        result = await session.execute(select(PayrollEntry).where(PayrollEntry.id == payroll_id))
        payroll = result.scalars().first()
        if not payroll:
            raise HTTPException(status_code=404, detail="Payroll entry not found")

        staff_result = await session.execute(select(Staff).where(Staff.id == payroll.staff_id))
        staff = staff_result.scalars().first()

        # build PDF
        buffer = BytesIO()
        p = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4

        # Add Company Logo - try multiple locations
        logo_paths = [
            '/app/company-logo.png',
            '/app/frontend/build/company-logo.png',
            os.path.join(os.path.dirname(__file__), '..', '..', 'company-logo.png')
        ]
        logo_path = None
        for path in logo_paths:
            if os.path.exists(path):
                logo_path = path
                break
        if logo_path:
            p.drawImage(logo_path, 40, height - 80, width=80, height=80, preserveAspectRatio=True)

        # Header
        p.setFont('Helvetica-Bold', 14)
        p.drawString(130, height - 50, 'BONNESANTE MEDICALS - Payslip')
        p.setFont('Helvetica', 10)
        p.drawString(40, height - 100, f'Employee: {staff.first_name} {staff.last_name} (#{staff.employee_id})')
        p.drawString(40, height - 115, f'Position: {staff.position or ""}')
        p.drawString(40, height - 130, f'Pay Period: {payroll.pay_period_start} to {payroll.pay_period_end}')

        # Payroll details
        y = height - 170
        p.setFont('Helvetica-Bold', 11)
        p.drawString(40, y, 'Earnings')
        p.setFont('Helvetica', 10)
        y -= 20
        p.drawString(60, y, f'Regular hours: {payroll.regular_hours} @ ₦425/hr')
        p.drawString(300, y, f'₦{payroll.regular_hours * Decimal("425"):.2f}')
        y -= 18
        p.drawString(60, y, f'Overtime hours: {payroll.overtime_hours} @ ₦450/hr')
        p.drawString(300, y, f'₦{payroll.overtime_hours * Decimal("450"):.2f}')
        y -= 18
        p.setFont('Helvetica-Bold', 10)
        p.drawString(60, y, f'Gross Pay:')
        p.drawString(300, y, f'₦{payroll.gross_pay:.2f}')
        y -= 24
        p.setFont('Helvetica', 10)
        p.drawString(60, y, f'Deductions:')
        p.drawString(300, y, f'₦{payroll.deductions:.2f}')
        y -= 18
        p.setFont('Helvetica-Bold', 12)
        p.drawString(60, y, f'Net Pay:')
        p.drawString(300, y, f'₦{payroll.net_pay:.2f}')

        # Bank details
        y -= 40
        p.setFont('Helvetica', 10)
        p.drawString(40, y, 'Bank Details:')
        y -= 18
        p.drawString(60, y, f'Bank: {staff.bank_name or ""}')
        y -= 16
        p.drawString(60, y, f'Account Name: {staff.bank_account_name or ""}')
        y -= 16
        p.drawString(60, y, f'Account Number: {staff.bank_account_number or ""}')

        p.showPage()
        p.save()
        buffer.seek(0)

        return StreamingResponse(buffer, media_type='application/pdf', headers={
            'Content-Disposition': f'attachment; filename="payslip_{payroll.id}.pdf"'
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating payslip PDF: {str(e)}")


# ==================== SALARY & PAYROLL MODULE ENDPOINTS ====================

@router.get('/payroll/dashboard')
async def payroll_dashboard(
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    session: AsyncSession = Depends(get_session)
):
    """
    Get salary dashboard: all active staff with their pay configuration,
    attendance hours for the selected period, and calculated due amounts.
    Defaults to current calendar month if no period specified.
    """
    try:
        today = date.today()
        if not period_start:
            period_start = today.replace(day=1)
        if not period_end:
            # last day of current month
            next_month = today.replace(day=28) + timedelta(days=4)
            period_end = next_month - timedelta(days=next_month.day)

        # Get all active staff
        result = await session.execute(select(Staff).where(Staff.is_active == True))
        all_staff = result.scalars().all()

        dashboard = []
        total_due = Decimal('0')
        total_hours = Decimal('0')

        for staff in all_staff:
            # Sum attendance hours in period
            att_q = select(func.coalesce(func.sum(Attendance.hours_worked), 0)).where(
                Attendance.staff_id == staff.id,
                func.date(Attendance.clock_in) >= period_start,
                func.date(Attendance.clock_in) <= period_end,
                Attendance.status == 'completed'
            )
            att_res = await session.execute(att_q)
            hours_worked = Decimal(str(att_res.scalar_one() or 0))

            # Count attendance days
            days_q = select(func.count(Attendance.id)).where(
                Attendance.staff_id == staff.id,
                func.date(Attendance.clock_in) >= period_start,
                func.date(Attendance.clock_in) <= period_end,
                Attendance.status == 'completed'
            )
            days_res = await session.execute(days_q)
            days_worked = days_res.scalar_one() or 0

            # Calculate pay based on staff payment mode
            payment_mode = staff.payment_mode or 'monthly'
            hourly_rate = Decimal(str(staff.hourly_rate or 0))
            monthly_salary = Decimal(str(staff.monthly_salary or 0))

            standard_hours = Decimal('160')  # 40h/week * 4 weeks
            overtime_rate_multiplier = Decimal('1.5')

            if payment_mode == 'hourly':
                regular_hours = min(hours_worked, standard_hours)
                overtime_hours = max(Decimal('0'), hours_worked - standard_hours)
                regular_pay = regular_hours * hourly_rate
                overtime_pay = overtime_hours * (hourly_rate * overtime_rate_multiplier)
                gross_pay = regular_pay + overtime_pay
            else:
                # Monthly salary: pro-rate if needed, add overtime
                gross_pay = monthly_salary
                regular_hours = min(hours_worked, standard_hours)
                overtime_hours = max(Decimal('0'), hours_worked - standard_hours)
                # For monthly staff, overtime uses equivalent hourly rate
                if monthly_salary > 0 and standard_hours > 0:
                    equiv_hourly = monthly_salary / standard_hours
                    overtime_pay = overtime_hours * (equiv_hourly * overtime_rate_multiplier)
                    gross_pay = monthly_salary + overtime_pay
                else:
                    overtime_pay = Decimal('0')
                regular_pay = gross_pay - (overtime_hours * (monthly_salary / standard_hours * overtime_rate_multiplier) if monthly_salary > 0 else Decimal('0'))

            # Check if payroll already processed for this period
            existing_q = select(PayrollEntry).where(
                PayrollEntry.staff_id == staff.id,
                PayrollEntry.pay_period_start == period_start,
                PayrollEntry.pay_period_end == period_end,
            )
            existing_res = await session.execute(existing_q)
            existing_payroll = existing_res.scalars().first()

            payroll_status = existing_payroll.status if existing_payroll else 'not_processed'
            payroll_id = str(existing_payroll.id) if existing_payroll else None

            total_due += gross_pay
            total_hours += hours_worked

            dashboard.append({
                'staff_id': str(staff.id),
                'employee_id': staff.employee_id,
                'first_name': staff.first_name,
                'last_name': staff.last_name,
                'position': staff.position or '',
                'payment_mode': payment_mode,
                'hourly_rate': float(hourly_rate),
                'monthly_salary': float(monthly_salary),
                'hours_worked': float(hours_worked),
                'days_worked': days_worked,
                'regular_hours': float(regular_hours),
                'overtime_hours': float(overtime_hours),
                'gross_pay': float(gross_pay),
                'net_pay': float(gross_pay),  # No deductions for now
                'payroll_status': payroll_status,
                'payroll_id': payroll_id,
                'bank_name': staff.bank_name or '',
                'bank_account_number': staff.bank_account_number or '',
                'bank_account_name': staff.bank_account_name or '',
            })

        return {
            'success': True,
            'period_start': period_start.isoformat(),
            'period_end': period_end.isoformat(),
            'total_staff': len(dashboard),
            'total_due': float(total_due),
            'total_hours': float(total_hours),
            'staff': dashboard
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching payroll dashboard: {str(e)}")


@router.get('/payroll/entries')
async def list_payroll_entries(
    status: Optional[str] = None,
    session: AsyncSession = Depends(get_session)
):
    """List all payroll entries with staff details, optionally filtered by status"""
    try:
        q = select(PayrollEntry).order_by(PayrollEntry.created_at.desc())
        if status:
            q = q.where(PayrollEntry.status == status)
        result = await session.execute(q)
        entries = result.scalars().all()

        entries_list = []
        for entry in entries:
            staff_res = await session.execute(select(Staff).where(Staff.id == entry.staff_id))
            staff = staff_res.scalars().first()
            entries_list.append({
                'id': str(entry.id),
                'staff_id': str(entry.staff_id),
                'employee_id': staff.employee_id if staff else '',
                'staff_name': f"{staff.first_name} {staff.last_name}" if staff else 'Unknown',
                'position': staff.position if staff else '',
                'pay_period_start': entry.pay_period_start.isoformat(),
                'pay_period_end': entry.pay_period_end.isoformat(),
                'regular_hours': float(entry.regular_hours),
                'overtime_hours': float(entry.overtime_hours),
                'gross_pay': float(entry.gross_pay),
                'deductions': float(entry.deductions),
                'net_pay': float(entry.net_pay),
                'status': entry.status,
                'created_at': entry.created_at.isoformat(),
            })
        return {'success': True, 'entries': entries_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching payroll entries: {str(e)}")


@router.post('/payroll/bulk-calculate')
async def bulk_calculate_payroll(
    period_start: date = Query(...),
    period_end: date = Query(...),
    session: AsyncSession = Depends(get_session)
):
    """Calculate payroll for ALL active staff for a given period. Uses each staff member's actual rates."""
    try:
        result = await session.execute(select(Staff).where(Staff.is_active == True))
        all_staff = result.scalars().all()

        processed = []
        skipped = []
        standard_hours = Decimal('160')
        overtime_multiplier = Decimal('1.5')

        for staff in all_staff:
            # Check if already processed
            existing_q = select(PayrollEntry).where(
                PayrollEntry.staff_id == staff.id,
                PayrollEntry.pay_period_start == period_start,
                PayrollEntry.pay_period_end == period_end,
            )
            existing_res = await session.execute(existing_q)
            if existing_res.scalars().first():
                skipped.append({
                    'staff_id': str(staff.id),
                    'employee_id': staff.employee_id,
                    'name': f"{staff.first_name} {staff.last_name}",
                    'reason': 'Already processed for this period'
                })
                continue

            # Sum attendance
            att_q = select(func.coalesce(func.sum(Attendance.hours_worked), 0)).where(
                Attendance.staff_id == staff.id,
                func.date(Attendance.clock_in) >= period_start,
                func.date(Attendance.clock_in) <= period_end,
                Attendance.status == 'completed'
            )
            att_res = await session.execute(att_q)
            total_hours_dec = Decimal(str(att_res.scalar_one() or 0))

            regular_hours = min(total_hours_dec, standard_hours)
            overtime_hours = max(Decimal('0'), total_hours_dec - standard_hours)

            payment_mode = staff.payment_mode or 'monthly'
            hourly_rate = Decimal(str(staff.hourly_rate or 0))
            monthly_salary = Decimal(str(staff.monthly_salary or 0))

            if payment_mode == 'hourly':
                gross_pay = (regular_hours * hourly_rate) + (overtime_hours * hourly_rate * overtime_multiplier)
            else:
                if monthly_salary > 0 and standard_hours > 0:
                    equiv_hourly = monthly_salary / standard_hours
                    gross_pay = monthly_salary + (overtime_hours * equiv_hourly * overtime_multiplier)
                else:
                    gross_pay = monthly_salary

            deductions = Decimal('0')
            net_pay = gross_pay - deductions

            payroll = PayrollEntry(
                staff_id=staff.id,
                pay_period_start=period_start,
                pay_period_end=period_end,
                regular_hours=regular_hours,
                overtime_hours=overtime_hours,
                gross_pay=gross_pay,
                deductions=deductions,
                net_pay=net_pay,
                status='draft'
            )
            session.add(payroll)
            await session.flush()

            processed.append({
                'payroll_id': str(payroll.id),
                'staff_id': str(staff.id),
                'employee_id': staff.employee_id,
                'name': f"{staff.first_name} {staff.last_name}",
                'gross_pay': float(gross_pay),
                'net_pay': float(net_pay),
            })

        await session.commit()
        return {
            'success': True,
            'period_start': period_start.isoformat(),
            'period_end': period_end.isoformat(),
            'processed_count': len(processed),
            'skipped_count': len(skipped),
            'processed': processed,
            'skipped': skipped,
        }
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Error bulk calculating payroll: {str(e)}")


@router.put('/payroll/entries/{payroll_id}/status')
async def update_payroll_status(
    payroll_id: UUID,
    new_status: str = Query(..., regex='^(draft|approved|paid)$'),
    session: AsyncSession = Depends(get_session)
):
    """Update the status of a payroll entry (draft -> approved -> paid)"""
    try:
        result = await session.execute(select(PayrollEntry).where(PayrollEntry.id == payroll_id))
        entry = result.scalars().first()
        if not entry:
            raise HTTPException(status_code=404, detail="Payroll entry not found")
        entry.status = new_status
        await session.commit()
        await session.refresh(entry)
        return {
            'success': True,
            'message': f'Payroll entry status updated to {new_status}',
            'id': str(entry.id),
            'status': entry.status,
        }
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error updating status: {str(e)}")


# Also update the existing calculate endpoint to use staff's actual rates
@router.post('/payroll/calculate-v2', response_model=PayrollEntrySchema)
async def calculate_payroll_v2(pay_data: PayrollEntryCreate, session: AsyncSession = Depends(get_session)):
    """Calculate payroll for a single staff member using their actual configured rates"""
    try:
        staff_result = await session.execute(select(Staff).where(Staff.id == pay_data.staff_id))
        staff = staff_result.scalars().first()
        if not staff:
            raise HTTPException(status_code=404, detail="Staff not found")

        # Sum attendance hours
        att_q = select(func.coalesce(func.sum(Attendance.hours_worked), 0)).where(
            Attendance.staff_id == pay_data.staff_id,
            func.date(Attendance.clock_in) >= pay_data.pay_period_start,
            func.date(Attendance.clock_in) <= pay_data.pay_period_end,
            Attendance.status == 'completed'
        )
        att_res = await session.execute(att_q)
        total_hours = Decimal(str(att_res.scalar_one() or 0))

        standard_hours = Decimal('160')
        overtime_multiplier = Decimal('1.5')
        regular_hours = min(total_hours, standard_hours)
        overtime_hours = max(Decimal('0'), total_hours - standard_hours)

        payment_mode = staff.payment_mode or 'monthly'
        hourly_rate = Decimal(str(staff.hourly_rate or 0))
        monthly_salary = Decimal(str(staff.monthly_salary or 0))

        if payment_mode == 'hourly':
            gross_pay = (regular_hours * hourly_rate) + (overtime_hours * hourly_rate * overtime_multiplier)
        else:
            if monthly_salary > 0 and standard_hours > 0:
                equiv_hourly = monthly_salary / standard_hours
                gross_pay = monthly_salary + (overtime_hours * equiv_hourly * overtime_multiplier)
            else:
                gross_pay = monthly_salary

        deductions = Decimal('0')
        net_pay = gross_pay - deductions

        payroll = PayrollEntry(
            staff_id=pay_data.staff_id,
            pay_period_start=pay_data.pay_period_start,
            pay_period_end=pay_data.pay_period_end,
            regular_hours=regular_hours,
            overtime_hours=overtime_hours,
            gross_pay=gross_pay,
            deductions=deductions,
            net_pay=net_pay,
            status='draft'
        )
        session.add(payroll)
        await session.commit()
        await session.refresh(payroll)
        return payroll
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error calculating payroll: {str(e)}")


# Enhanced payslip PDF with actual staff rates
@router.get('/payslip/{payroll_id}/pdf-v2')
async def generate_enhanced_payslip_pdf(payroll_id: UUID, session: AsyncSession = Depends(get_session)):
    """Generate an enhanced PDF payslip that uses the staff's actual configured rates"""
    try:
        result = await session.execute(select(PayrollEntry).where(PayrollEntry.id == payroll_id))
        payroll = result.scalars().first()
        if not payroll:
            raise HTTPException(status_code=404, detail="Payroll entry not found")

        staff_result = await session.execute(select(Staff).where(Staff.id == payroll.staff_id))
        staff = staff_result.scalars().first()

        payment_mode = staff.payment_mode or 'monthly'
        hourly_rate = Decimal(str(staff.hourly_rate or 0))
        monthly_salary = Decimal(str(staff.monthly_salary or 0))
        standard_hours = Decimal('160')
        overtime_multiplier = Decimal('1.5')

        # Build PDF
        buffer = BytesIO()
        p = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4

        # Company Logo
        logo_paths = [
            '/app/company-logo.png',
            '/app/frontend/build/company-logo.png',
            os.path.join(os.path.dirname(__file__), '..', '..', 'company-logo.png')
        ]
        logo_path = None
        for path in logo_paths:
            if os.path.exists(path):
                logo_path = path
                break
        if logo_path:
            p.drawImage(logo_path, 40, height - 90, width=80, height=80, preserveAspectRatio=True)

        # Header
        p.setFont('Helvetica-Bold', 16)
        p.drawString(140, height - 50, 'BONNESANTE MEDICALS')
        p.setFont('Helvetica', 10)
        p.drawString(140, height - 65, 'AstroBSM StockMaster ERP - Payslip')

        # Divider line
        y = height - 100
        p.line(40, y, width - 40, y)

        # Employee Info section
        y -= 25
        p.setFont('Helvetica-Bold', 11)
        p.drawString(40, y, 'EMPLOYEE DETAILS')
        y -= 20
        p.setFont('Helvetica', 10)
        p.drawString(60, y, f'Name: {staff.first_name} {staff.last_name}')
        p.drawString(300, y, f'Employee ID: {staff.employee_id}')
        y -= 16
        p.drawString(60, y, f'Position: {staff.position or "N/A"}')
        p.drawString(300, y, f'Payment Mode: {payment_mode.title()}')
        y -= 16
        p.drawString(60, y, f'Pay Period: {payroll.pay_period_start} to {payroll.pay_period_end}')
        if payment_mode == 'hourly':
            p.drawString(300, y, f'Rate: N{hourly_rate:,.2f}/hr')
        else:
            p.drawString(300, y, f'Monthly Salary: N{monthly_salary:,.2f}')

        # Divider
        y -= 15
        p.line(40, y, width - 40, y)

        # Earnings section
        y -= 25
        p.setFont('Helvetica-Bold', 11)
        p.drawString(40, y, 'EARNINGS')

        # Table header
        y -= 20
        p.setFont('Helvetica-Bold', 9)
        p.drawString(60, y, 'Description')
        p.drawString(260, y, 'Hours')
        p.drawString(340, y, 'Rate')
        p.drawString(430, y, 'Amount (N)')
        y -= 3
        p.line(60, y, width - 40, y)

        p.setFont('Helvetica', 10)
        y -= 18

        if payment_mode == 'hourly':
            p.drawString(60, y, 'Regular Hours')
            p.drawString(260, y, f'{payroll.regular_hours:.1f}')
            p.drawString(340, y, f'N{hourly_rate:,.2f}')
            regular_amount = payroll.regular_hours * hourly_rate
            p.drawString(430, y, f'N{regular_amount:,.2f}')
            y -= 18
            ot_rate = hourly_rate * overtime_multiplier
            p.drawString(60, y, 'Overtime Hours (1.5x)')
            p.drawString(260, y, f'{payroll.overtime_hours:.1f}')
            p.drawString(340, y, f'N{ot_rate:,.2f}')
            overtime_amount = payroll.overtime_hours * ot_rate
            p.drawString(430, y, f'N{overtime_amount:,.2f}')
        else:
            p.drawString(60, y, 'Monthly Salary')
            p.drawString(260, y, '-')
            p.drawString(340, y, '-')
            p.drawString(430, y, f'N{monthly_salary:,.2f}')
            y -= 18
            if payroll.overtime_hours > 0 and monthly_salary > 0:
                equiv_hourly = monthly_salary / standard_hours
                ot_rate = equiv_hourly * overtime_multiplier
                p.drawString(60, y, 'Overtime Hours (1.5x)')
                p.drawString(260, y, f'{payroll.overtime_hours:.1f}')
                p.drawString(340, y, f'N{ot_rate:,.2f}')
                overtime_amount = payroll.overtime_hours * ot_rate
                p.drawString(430, y, f'N{overtime_amount:,.2f}')

        # Totals
        y -= 10
        p.line(60, y, width - 40, y)
        y -= 18
        p.setFont('Helvetica-Bold', 10)
        p.drawString(60, y, 'Gross Pay')
        p.drawString(430, y, f'N{payroll.gross_pay:,.2f}')
        y -= 18
        p.setFont('Helvetica', 10)
        p.drawString(60, y, 'Deductions')
        p.drawString(430, y, f'N{payroll.deductions:,.2f}')
        y -= 5
        p.line(60, y, width - 40, y)
        y -= 20
        p.setFont('Helvetica-Bold', 13)
        p.drawString(60, y, 'NET PAY')
        p.drawString(420, y, f'N{payroll.net_pay:,.2f}')

        # Bank Details section
        y -= 40
        p.line(40, y + 10, width - 40, y + 10)
        p.setFont('Helvetica-Bold', 11)
        p.drawString(40, y, 'BANK DETAILS')
        y -= 20
        p.setFont('Helvetica', 10)
        p.drawString(60, y, f'Bank: {staff.bank_name or "N/A"}')
        y -= 16
        p.drawString(60, y, f'Account Name: {staff.bank_account_name or "N/A"}')
        y -= 16
        p.drawString(60, y, f'Account Number: {staff.bank_account_number or "N/A"}')

        # Footer
        y -= 40
        p.line(40, y + 10, width - 40, y + 10)
        p.setFont('Helvetica', 8)
        p.drawString(40, y, f'Generated on {datetime.now().strftime("%Y-%m-%d %H:%M")} | Status: {payroll.status.upper()} | Payroll ID: {payroll.id}')
        p.drawString(40, y - 12, 'This is a computer-generated document. No signature required.')

        p.showPage()
        p.save()
        buffer.seek(0)

        filename = f"payslip_{staff.employee_id}_{payroll.pay_period_start}_{payroll.pay_period_end}.pdf"
        return StreamingResponse(buffer, media_type='application/pdf', headers={
            'Content-Disposition': f'attachment; filename="{filename}"'
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating payslip PDF: {str(e)}")


# Birthday notification endpoint
@router.get('/birthdays/upcoming')
async def get_upcoming_birthdays(
    days_ahead: int = Query(30, ge=1, le=365, description="Number of days to look ahead (up to 365)"),
    session: AsyncSession = Depends(get_session)
):
    """Get staff with birthdays in the next N days (up to 365)"""
    try:
        today = date.today()
        
        # Get all active staff with birthdays
        result = await session.execute(
            select(Staff).where(
                Staff.is_active == True,
                Staff.date_of_birth.isnot(None)
            )
        )
        all_staff = result.scalars().all()
        
        upcoming_birthdays = []
        
        for staff in all_staff:
            if staff.date_of_birth:
                # Calculate this year's birthday
                this_year_birthday = staff.date_of_birth.replace(year=today.year)
                
                # If birthday already passed this year, check next year
                if this_year_birthday < today:
                    this_year_birthday = staff.date_of_birth.replace(year=today.year + 1)
                
                # Calculate days until birthday
                days_until = (this_year_birthday - today).days
                
                # Include if within the specified days
                if 0 <= days_until <= days_ahead:
                    upcoming_birthdays.append({
                        'id': str(staff.id),
                        'employee_id': staff.employee_id,
                        'first_name': staff.first_name,
                        'last_name': staff.last_name,
                        'date_of_birth': staff.date_of_birth.isoformat(),
                        'birthday_this_year': this_year_birthday.isoformat(),
                        'days_until': days_until,
                        'age_turning': today.year - staff.date_of_birth.year if this_year_birthday.year == today.year else today.year + 1 - staff.date_of_birth.year,
                        'is_today': days_until == 0
                    })
        
        # Sort by days until birthday
        upcoming_birthdays.sort(key=lambda x: x['days_until'])
        
        return {
            'success': True,
            'days_ahead': days_ahead,
            'count': len(upcoming_birthdays),
            'birthdays': upcoming_birthdays
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching birthdays: {str(e)}")
