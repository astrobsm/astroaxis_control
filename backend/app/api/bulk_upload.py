"""
Bulk Upload API - Excel template generation and bulk data import
Supports: Staff, Products, Raw Materials, Stock Intake, Warehouses, Damaged Items, Returns, BOM
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db import get_session
from app.models import Staff, Product, RawMaterial, StockLevel, StockMovement, Warehouse, DamagedStock, BOM, BOMLine
from datetime import datetime, timezone
from decimal import Decimal
import uuid
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from io import BytesIO
import secrets
import string
import hashlib

router = APIRouter(prefix="/api/bulk-upload", tags=["bulk-upload"])

def generate_clock_pin():
    """Generate 4-digit clock PIN"""
    return ''.join(secrets.choice(string.digits) for _ in range(4))

def generate_password(length=12):
    """Generate random password"""
    chars = string.ascii_letters + string.digits + "!@#$%"
    return ''.join(secrets.choice(chars) for _ in range(length))

def hash_password(password: str) -> str:
    """Hash password for storage"""
    return hashlib.sha256(password.encode()).hexdigest()

# ==================== TEMPLATE DOWNLOADS ====================

@router.get("/template/staff")
async def download_staff_template():
    """Download Excel template for staff bulk upload"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Staff Template"
    
    # Header styling
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    
    headers = ["First Name*", "Last Name*", "Phone", "Date of Birth (YYYY-MM-DD)", 
               "Position*", "Payment Mode*", "Bank Name", "Account Number", 
               "Account Name", "Bank Currency"]
    
    for col, header in enumerate(headers, 1):
        cell = ws.cell(1, col, header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')
    
    # Sample data
    ws.append(["John", "Doe", "+2348012345678", "1990-01-15", "Sales Manager", 
               "bank_transfer", "Access Bank", "1234567890", "John Doe", "NGN"])
    ws.append(["Jane", "Smith", "+2347098765432", "1988-05-20", "Pharmacist", 
               "cash", "", "", "", ""])
    
    # Instructions sheet
    ws2 = wb.create_sheet("Instructions")
    ws2.append(["STAFF BULK UPLOAD INSTRUCTIONS"])
    ws2.append([])
    ws2.append(["Required Fields (marked with *):"])
    ws2.append(["- First Name, Last Name, Position, Payment Mode"])
    ws2.append([])
    ws2.append(["Payment Mode Options:"])
    ws2.append(["- cash, bank_transfer, mobile_money"])
    ws2.append([])
    ws2.append(["Notes:"])
    ws2.append(["- Employee ID, Clock PIN, and Login details will be auto-generated"])
    ws2.append(["- You will receive a summary with login credentials after upload"])
    ws2.append(["- Date format must be YYYY-MM-DD (e.g., 1990-01-15)"])
    
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=staff_bulk_upload_template.xlsx"}
    )


@router.get("/template/products")
async def download_products_template():
    """Download Excel template for products bulk upload"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Products Template"
    
    header_fill = PatternFill(start_color="70AD47", end_color="70AD47", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    
    headers = ["SKU*", "Name*", "Unit*", "Description", "Manufacturer", 
               "Reorder Level", "Cost Price", "Retail Price", "Wholesale Price",
               "Lead Time Days", "Min Order Qty"]
    
    for col, header in enumerate(headers, 1):
        cell = ws.cell(1, col, header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')
    
    ws.append(["PROD-001", "Paracetamol 500mg", "tablet", "Pain reliever", 
               "GSK", "100", "50", "100", "80", "7", "50"])
    ws.append(["PROD-002", "Amoxicillin 250mg", "capsule", "Antibiotic", 
               "Pfizer", "50", "150", "250", "200", "14", "20"])
    
    # Instructions
    ws2 = wb.create_sheet("Instructions")
    ws2.append(["PRODUCTS BULK UPLOAD INSTRUCTIONS"])
    ws2.append([])
    ws2.append(["Required Fields (marked with *):"])
    ws2.append(["- SKU, Name, Unit"])
    ws2.append([])
    ws2.append(["Unit Options:"])
    ws2.append(["- tablet, capsule, bottle, box, tube, sachet, vial, ampoule, kg, liter, piece, each"])
    ws2.append([])
    ws2.append(["Notes:"])
    ws2.append(["- SKU must be unique"])
    ws2.append(["- Prices should be in Naira without currency symbol"])
    
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=products_bulk_upload_template.xlsx"}
    )


@router.get("/template/raw-materials")
async def download_raw_materials_template():
    """Download Excel template for raw materials bulk upload"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Raw Materials Template"
    
    header_fill = PatternFill(start_color="FFC000", end_color="FFC000", fill_type="solid")
    header_font = Font(color="000000", bold=True)
    
    headers = ["SKU*", "Name*", "Unit*", "Manufacturer", "Reorder Level", "Unit Cost"]
    
    for col, header in enumerate(headers, 1):
        cell = ws.cell(1, col, header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')
    
    ws.append(["HONEY-001", "Pure Honey", "kg", "Local Farm", "10", "6000"])
    ws.append(["GLY-002", "Glycerine", "kg", "ChemCorp", "5", "2000"])
    
    ws2 = wb.create_sheet("Instructions")
    ws2.append(["RAW MATERIALS BULK UPLOAD INSTRUCTIONS"])
    ws2.append([])
    ws2.append(["Required Fields (marked with *):"])
    ws2.append(["- SKU, Name, Unit"])
    ws2.append([])
    ws2.append(["Unit Options:"])
    ws2.append(["- kg, liter, gram, ml, piece, each"])
    
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=raw_materials_bulk_upload_template.xlsx"}
    )


@router.get("/template/product-stock-intake")
async def download_product_stock_intake_template(session: AsyncSession = Depends(get_session)):
    """Download Excel template for product stock intake"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Product Stock Intake"
    
    header_fill = PatternFill(start_color="5B9BD5", end_color="5B9BD5", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    
    headers = ["Product SKU*", "Warehouse Code*", "Quantity*", "Unit Cost*", 
               "Supplier", "Batch Number", "Notes"]
    
    for col, header in enumerate(headers, 1):
        cell = ws.cell(1, col, header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')
    
    ws.append(["PROD-001", "WH-001", "100", "50", "ABC Suppliers", "BATCH-2024-001", "Initial stock"])
    
    # Get reference data
    products_result = await session.execute(select(Product).limit(10))
    products = products_result.scalars().all()
    
    warehouses_result = await session.execute(select(Warehouse).limit(10))
    warehouses = warehouses_result.scalars().all()
    
    ws2 = wb.create_sheet("Instructions")
    ws2.append(["PRODUCT STOCK INTAKE BULK UPLOAD INSTRUCTIONS"])
    ws2.append([])
    ws2.append(["Required Fields (marked with *):"])
    ws2.append(["- Product SKU, Warehouse Code, Quantity, Unit Cost"])
    ws2.append([])
    ws2.append(["Available Products (SKU):"])
    for p in products:
        ws2.append([f"- {p.sku}: {p.name}"])
    ws2.append([])
    ws2.append(["Available Warehouses (Code):"])
    for w in warehouses:
        ws2.append([f"- {w.code}: {w.name}"])
    
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=product_stock_intake_template.xlsx"}
    )


@router.get("/template/raw-material-stock-intake")
async def download_raw_material_stock_intake_template(session: AsyncSession = Depends(get_session)):
    """Download Excel template for raw material stock intake"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Raw Material Stock Intake"
    
    header_fill = PatternFill(start_color="E7E6E6", end_color="E7E6E6", fill_type="solid")
    header_font = Font(color="000000", bold=True)
    
    headers = ["Raw Material SKU*", "Warehouse Code*", "Quantity*", "Unit Cost*", 
               "Supplier", "Batch Number", "Notes"]
    
    for col, header in enumerate(headers, 1):
        cell = ws.cell(1, col, header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')
    
    ws.append(["HONEY-001", "WH-001", "50", "6000", "Local Farm Co", "HN-2024-001", ""])
    
    raw_materials_result = await session.execute(select(RawMaterial).limit(10))
    raw_materials = raw_materials_result.scalars().all()
    
    warehouses_result = await session.execute(select(Warehouse).limit(10))
    warehouses = warehouses_result.scalars().all()
    
    ws2 = wb.create_sheet("Instructions")
    ws2.append(["RAW MATERIAL STOCK INTAKE BULK UPLOAD"])
    ws2.append([])
    ws2.append(["Available Raw Materials (SKU):"])
    for rm in raw_materials:
        ws2.append([f"- {rm.sku}: {rm.name}"])
    ws2.append([])
    ws2.append(["Available Warehouses (Code):"])
    for w in warehouses:
        ws2.append([f"- {w.code}: {w.name}"])
    
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=raw_material_stock_intake_template.xlsx"}
    )


@router.get("/template/warehouses")
async def download_warehouses_template():
    """Download Excel template for warehouses bulk upload"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Warehouses Template"
    
    header_fill = PatternFill(start_color="C65911", end_color="C65911", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    
    headers = ["Code*", "Name*", "Location*", "Manager Name"]
    
    for col, header in enumerate(headers, 1):
        cell = ws.cell(1, col, header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')
    
    ws.append(["WH-001", "Main Warehouse", "Lagos VGC", "John Manager"])
    ws.append(["WH-002", "Production Warehouse", "Ikeja Industrial", "Jane Supervisor"])
    
    ws2 = wb.create_sheet("Instructions")
    ws2.append(["WAREHOUSES BULK UPLOAD INSTRUCTIONS"])
    ws2.append([])
    ws2.append(["Required Fields (marked with *):"])
    ws2.append(["- Code, Name, Location"])
    ws2.append([])
    ws2.append(["Notes:"])
    ws2.append(["- Code must be unique"])
    ws2.append(["- Manager Name is optional"])
    
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=warehouses_bulk_upload_template.xlsx"}
    )


@router.get("/template/damaged-products")
async def download_damaged_products_template(session: AsyncSession = Depends(get_session)):
    """Download Excel template for damaged products"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Damaged Products"
    
    header_fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    
    headers = ["Warehouse Code*", "Product SKU*", "Quantity*", "Damage Type*", 
               "Damage Reason", "Notes"]
    
    for col, header in enumerate(headers, 1):
        cell = ws.cell(1, col, header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')
    
    ws.append(["WH-001", "PROD-001", "5", "expired", "Past expiry date", "Dispose immediately"])
    
    ws2 = wb.create_sheet("Instructions")
    ws2.append(["DAMAGED PRODUCTS BULK UPLOAD"])
    ws2.append([])
    ws2.append(["Damage Type Options:"])
    ws2.append(["- expired, damaged, defective, contaminated, other"])
    
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=damaged_products_template.xlsx"}
    )


@router.get("/template/damaged-raw-materials")
async def download_damaged_raw_materials_template():
    """Download Excel template for damaged raw materials"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Damaged Raw Materials"
    
    header_fill = PatternFill(start_color="C00000", end_color="C00000", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    
    headers = ["Warehouse Code*", "Raw Material SKU*", "Quantity*", "Damage Type*", 
               "Damage Reason", "Notes"]
    
    for col, header in enumerate(headers, 1):
        cell = ws.cell(1, col, header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')
    
    ws.append(["WH-001", "HONEY-001", "2", "contaminated", "Foreign particles found", ""])
    
    ws2 = wb.create_sheet("Instructions")
    ws2.append(["DAMAGED RAW MATERIALS BULK UPLOAD"])
    ws2.append([])
    ws2.append(["Damage Type Options:"])
    ws2.append(["- expired, damaged, defective, contaminated, other"])
    
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=damaged_raw_materials_template.xlsx"}
    )


@router.get("/template/product-returns")
async def download_product_returns_template():
    """Download Excel template for product returns"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Product Returns"
    
    header_fill = PatternFill(start_color="FF6600", end_color="FF6600", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    
    headers = ["Warehouse Code*", "Product SKU*", "Quantity*", "Return Reason*", 
               "Return Condition*", "Customer Name", "Refund Amount", "Notes"]
    
    for col, header in enumerate(headers, 1):
        cell = ws.cell(1, col, header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')
    
    ws.append(["WH-001", "PROD-001", "2", "defective", "damaged", "John Customer", "200", "Refund approved"])
    
    ws2 = wb.create_sheet("Instructions")
    ws2.append(["PRODUCT RETURNS BULK UPLOAD"])
    ws2.append([])
    ws2.append(["Return Condition Options:"])
    ws2.append(["- good, damaged, expired, defective"])
    
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=product_returns_template.xlsx"}
    )


@router.get("/template/bom")
async def download_bom_template(session: AsyncSession = Depends(get_session)):
    """Download Excel template for BOM (Bill of Materials)"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BOM Template"
    
    header_fill = PatternFill(start_color="9933FF", end_color="9933FF", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    
    headers = ["Product SKU*", "Raw Material SKU*", "Quantity Per Unit*", "Unit*"]
    
    for col, header in enumerate(headers, 1):
        cell = ws.cell(1, col, header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')
    
    ws.append(["PROD-001", "HONEY-001", "66", "kg"])
    ws.append(["PROD-001", "GLY-002", "67", "kg"])
    ws.append(["PROD-002", "HONEY-001", "40", "kg"])
    
    products_result = await session.execute(select(Product).limit(10))
    products = products_result.scalars().all()
    
    raw_materials_result = await session.execute(select(RawMaterial).limit(10))
    raw_materials = raw_materials_result.scalars().all()
    
    ws2 = wb.create_sheet("Instructions")
    ws2.append(["BOM (BILL OF MATERIALS) BULK UPLOAD"])
    ws2.append([])
    ws2.append(["Format:"])
    ws2.append(["- Each row represents one raw material required for a product"])
    ws2.append(["- Multiple rows with same Product SKU = multiple materials for that product"])
    ws2.append([])
    ws2.append(["Available Products (SKU):"])
    for p in products:
        ws2.append([f"- {p.sku}: {p.name}"])
    ws2.append([])
    ws2.append(["Available Raw Materials (SKU):"])
    for rm in raw_materials:
        ws2.append([f"- {rm.sku}: {rm.name} ({rm.unit})"])
    
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=bom_bulk_upload_template.xlsx"}
    )


# ==================== BULK UPLOAD ENDPOINTS ====================

@router.post("/staff")
async def bulk_upload_staff(file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    """Bulk upload staff from Excel file with auto-generated credentials"""
    try:
        content = await file.read()
        wb = openpyxl.load_workbook(BytesIO(content))
        ws = wb.active
        
        created_staff = []
        errors = []
        
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            try:
                if not row[0]:  # Skip empty rows
                    continue
                
                first_name, last_name, phone, dob, position, payment_mode, bank_name, account_number, account_name, bank_currency = row[:10]
                
                # Validation
                if not all([first_name, last_name, position, payment_mode]):
                    errors.append(f"Row {row_idx}: Missing required fields")
                    continue
                
                # Auto-generate credentials
                employee_id = f"BSM{str(uuid.uuid4().int)[:4]}"
                clock_pin = generate_clock_pin()
                password = generate_password()
                
                # Create staff
                staff = Staff(
                    id=uuid.uuid4(),
                    employee_id=employee_id,
                    first_name=str(first_name).strip(),
                    last_name=str(last_name).strip(),
                    phone=str(phone).strip() if phone else None,
                    date_of_birth=dob if dob else None,
                    position=str(position).strip(),
                    hourly_rate=Decimal('425'),  # Default rate
                    payment_mode=str(payment_mode).strip(),
                    bank_name=str(bank_name).strip() if bank_name else None,
                    bank_account_number=str(account_number).strip() if account_number else None,
                    bank_account_name=str(account_name).strip() if account_name else None,
                    bank_currency=str(bank_currency).strip() if bank_currency else 'NGN',
                    clock_pin=clock_pin
                )
                
                session.add(staff)
                await session.flush()
                
                created_staff.append({
                    "row": row_idx,
                    "name": f"{first_name} {last_name}",
                    "employee_id": employee_id,
                    "clock_pin": clock_pin,
                    "position": position
                })
                
            except Exception as e:
                errors.append(f"Row {row_idx}: {str(e)}")
        
        await session.commit()
        
        return {
            "success": True,
            "message": f"Successfully uploaded {len(created_staff)} staff members",
            "created": created_staff,
            "errors": errors
        }
        
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.post("/products")
async def bulk_upload_products(file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    """Bulk upload products from Excel file"""
    try:
        content = await file.read()
        wb = openpyxl.load_workbook(BytesIO(content))
        ws = wb.active
        
        created_count = 0
        errors = []
        
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            try:
                if not row[0]:
                    continue
                
                sku, name, unit, description, manufacturer, reorder_level, cost_price, retail_price, wholesale_price, lead_time, min_order = row[:11]
                
                if not all([sku, name, unit]):
                    errors.append(f"Row {row_idx}: Missing required fields")
                    continue
                
                product = Product(
                    id=uuid.uuid4(),
                    sku=str(sku).strip(),
                    name=str(name).strip(),
                    unit=str(unit).strip(),
                    description=str(description).strip() if description else None,
                    manufacturer=str(manufacturer).strip() if manufacturer else None,
                    reorder_level=Decimal(str(reorder_level)) if reorder_level else Decimal('0'),
                    cost_price=Decimal(str(cost_price)) if cost_price else None,
                    selling_price=Decimal(str(retail_price)) if retail_price else None,
                    retail_price=Decimal(str(retail_price)) if retail_price else None,
                    wholesale_price=Decimal(str(wholesale_price)) if wholesale_price else None,
                    lead_time_days=int(lead_time) if lead_time else None,
                    minimum_order_quantity=Decimal(str(min_order)) if min_order else Decimal('1')
                )
                
                session.add(product)
                created_count += 1
                
            except Exception as e:
                errors.append(f"Row {row_idx}: {str(e)}")
        
        await session.commit()
        
        return {
            "success": True,
            "message": f"Successfully uploaded {created_count} products",
            "created_count": created_count,
            "errors": errors
        }
        
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.post("/raw-materials")
async def bulk_upload_raw_materials(file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    """Bulk upload raw materials from Excel file"""
    try:
        content = await file.read()
        wb = openpyxl.load_workbook(BytesIO(content))
        ws = wb.active
        
        created_count = 0
        errors = []
        
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            try:
                if not row[0]:
                    continue
                
                sku, name, unit, manufacturer, reorder_level, unit_cost = row[:6]
                
                if not all([sku, name, unit]):
                    errors.append(f"Row {row_idx}: Missing required fields")
                    continue
                
                raw_material = RawMaterial(
                    id=uuid.uuid4(),
                    sku=str(sku).strip(),
                    name=str(name).strip(),
                    unit=str(unit).strip(),
                    manufacturer=str(manufacturer).strip() if manufacturer else None,
                    reorder_level=Decimal(str(reorder_level)) if reorder_level else Decimal('0'),
                    unit_cost=Decimal(str(unit_cost)) if unit_cost else Decimal('0')
                )
                
                session.add(raw_material)
                created_count += 1
                
            except Exception as e:
                errors.append(f"Row {row_idx}: {str(e)}")
        
        await session.commit()
        
        return {
            "success": True,
            "message": f"Successfully uploaded {created_count} raw materials",
            "created_count": created_count,
            "errors": errors
        }
        
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.post("/product-stock-intake")
async def bulk_upload_product_stock_intake(file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    """Bulk upload product stock intake from Excel file"""
    try:
        content = await file.read()
        wb = openpyxl.load_workbook(BytesIO(content))
        ws = wb.active
        
        created_count = 0
        errors = []
        
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            try:
                if not row[0]:
                    continue
                
                product_sku, warehouse_code, quantity, unit_cost, supplier, batch_number, notes = row[:7]
                
                # Get product by SKU
                product_result = await session.execute(select(Product).where(Product.sku == product_sku))
                product = product_result.scalars().first()
                
                if not product:
                    errors.append(f"Row {row_idx}: Product SKU '{product_sku}' not found")
                    continue
                
                # Get warehouse by code
                warehouse_result = await session.execute(select(Warehouse).where(Warehouse.code == warehouse_code))
                warehouse = warehouse_result.scalars().first()
                
                if not warehouse:
                    errors.append(f"Row {row_idx}: Warehouse code '{warehouse_code}' not found")
                    continue
                
                # Update or create stock level
                stock_result = await session.execute(
                    select(StockLevel).where(
                        StockLevel.warehouse_id == warehouse.id,
                        StockLevel.product_id == product.id
                    )
                )
                stock_level = stock_result.scalars().first()
                
                qty = Decimal(str(quantity))
                
                if stock_level:
                    stock_level.current_stock += qty
                    stock_level.updated_at = datetime.now(timezone.utc)
                else:
                    stock_level = StockLevel(
                        id=uuid.uuid4(),
                        warehouse_id=warehouse.id,
                        product_id=product.id,
                        current_stock=qty,
                        reserved_stock=Decimal('0')
                    )
                    session.add(stock_level)
                
                # Create stock movement
                movement = StockMovement(
                    id=uuid.uuid4(),
                    warehouse_id=warehouse.id,
                    product_id=product.id,
                    movement_type='INTAKE',
                    quantity=qty,
                    unit_cost=Decimal(str(unit_cost)),
                    reference=f"Bulk Upload - {batch_number or 'N/A'}",
                    notes=str(notes) if notes else f"Supplier: {supplier or 'N/A'}"
                )
                session.add(movement)
                
                created_count += 1
                
            except Exception as e:
                errors.append(f"Row {row_idx}: {str(e)}")
        
        await session.commit()
        
        return {
            "success": True,
            "message": f"Successfully processed {created_count} stock intake records",
            "created_count": created_count,
            "errors": errors
        }
        
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.post("/raw-material-stock-intake")
async def bulk_upload_raw_material_stock_intake(file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    """Bulk upload raw material stock intake from Excel file"""
    try:
        content = await file.read()
        wb = openpyxl.load_workbook(BytesIO(content))
        ws = wb.active
        
        created_count = 0
        errors = []
        
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            try:
                if not row[0]:
                    continue
                
                rm_sku, warehouse_code, quantity, unit_cost, supplier, batch_number, notes = row[:7]
                
                # Get raw material by SKU
                rm_result = await session.execute(select(RawMaterial).where(RawMaterial.sku == rm_sku))
                raw_material = rm_result.scalars().first()
                
                if not raw_material:
                    errors.append(f"Row {row_idx}: Raw Material SKU '{rm_sku}' not found")
                    continue
                
                # Get warehouse by code
                warehouse_result = await session.execute(select(Warehouse).where(Warehouse.code == warehouse_code))
                warehouse = warehouse_result.scalars().first()
                
                if not warehouse:
                    errors.append(f"Row {row_idx}: Warehouse code '{warehouse_code}' not found")
                    continue
                
                # Update or create stock level
                stock_result = await session.execute(
                    select(StockLevel).where(
                        StockLevel.warehouse_id == warehouse.id,
                        StockLevel.raw_material_id == raw_material.id
                    )
                )
                stock_level = stock_result.scalars().first()
                
                qty = Decimal(str(quantity))
                
                if stock_level:
                    stock_level.current_stock += qty
                    stock_level.updated_at = datetime.now(timezone.utc)
                else:
                    stock_level = StockLevel(
                        id=uuid.uuid4(),
                        warehouse_id=warehouse.id,
                        raw_material_id=raw_material.id,
                        current_stock=qty,
                        reserved_stock=Decimal('0')
                    )
                    session.add(stock_level)
                
                # Create stock movement
                movement = StockMovement(
                    id=uuid.uuid4(),
                    warehouse_id=warehouse.id,
                    raw_material_id=raw_material.id,
                    movement_type='INTAKE',
                    quantity=qty,
                    unit_cost=Decimal(str(unit_cost)),
                    reference=f"Bulk Upload - {batch_number or 'N/A'}",
                    notes=str(notes) if notes else f"Supplier: {supplier or 'N/A'}"
                )
                session.add(movement)
                
                created_count += 1
                
            except Exception as e:
                errors.append(f"Row {row_idx}: {str(e)}")
        
        await session.commit()
        
        return {
            "success": True,
            "message": f"Successfully processed {created_count} raw material intake records",
            "created_count": created_count,
            "errors": errors
        }
        
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.post("/warehouses")
async def bulk_upload_warehouses(file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    """Bulk upload warehouses from Excel file"""
    try:
        content = await file.read()
        wb = openpyxl.load_workbook(BytesIO(content))
        ws = wb.active
        
        created_count = 0
        errors = []
        
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            try:
                if not row[0]:
                    continue
                
                code, name, location, manager_name = row[:4]
                
                if not all([code, name, location]):
                    errors.append(f"Row {row_idx}: Missing required fields")
                    continue
                
                warehouse = Warehouse(
                    id=uuid.uuid4(),
                    code=str(code).strip(),
                    name=str(name).strip(),
                    location=str(location).strip(),
                    manager_id=None  # Set manually later if needed
                )
                
                session.add(warehouse)
                created_count += 1
                
            except Exception as e:
                errors.append(f"Row {row_idx}: {str(e)}")
        
        await session.commit()
        
        return {
            "success": True,
            "message": f"Successfully uploaded {created_count} warehouses",
            "created_count": created_count,
            "errors": errors
        }
        
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.post("/damaged-products")
async def bulk_upload_damaged_products(file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    """Bulk upload damaged products from Excel file"""
    try:
        content = await file.read()
        wb = openpyxl.load_workbook(BytesIO(content))
        ws = wb.active
        
        created_count = 0
        errors = []
        
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            try:
                if not row[0]:
                    continue
                
                warehouse_code, product_sku, quantity, damage_type, damage_reason, notes = row[:6]
                
                # Get warehouse and product
                warehouse_result = await session.execute(select(Warehouse).where(Warehouse.code == warehouse_code))
                warehouse = warehouse_result.scalars().first()
                
                product_result = await session.execute(select(Product).where(Product.sku == product_sku))
                product = product_result.scalars().first()
                
                if not warehouse or not product:
                    errors.append(f"Row {row_idx}: Warehouse or Product not found")
                    continue
                
                # Update stock level
                stock_result = await session.execute(
                    select(StockLevel).where(
                        StockLevel.warehouse_id == warehouse.id,
                        StockLevel.product_id == product.id
                    )
                )
                stock_level = stock_result.scalars().first()
                
                if stock_level:
                    stock_level.current_stock -= Decimal(str(quantity))
                    stock_level.updated_at = datetime.now(timezone.utc)
                
                # Create damaged product record
                damaged = DamagedProduct(
                    id=uuid.uuid4(),
                    warehouse_id=warehouse.id,
                    product_id=product.id,
                    quantity=Decimal(str(quantity)),
                    damage_type=str(damage_type).strip(),
                    damage_reason=str(damage_reason).strip() if damage_reason else None,
                    notes=str(notes).strip() if notes else None
                )
                session.add(damaged)
                
                # Create stock movement
                movement = StockMovement(
                    id=uuid.uuid4(),
                    warehouse_id=warehouse.id,
                    product_id=product.id,
                    movement_type='DAMAGED',
                    quantity=Decimal(str(quantity)),
                    reference=f"Bulk Upload - Damaged: {damage_type}",
                    notes=str(notes) if notes else str(damage_reason)
                )
                session.add(movement)
                
                created_count += 1
                
            except Exception as e:
                errors.append(f"Row {row_idx}: {str(e)}")
        
        await session.commit()
        
        return {
            "success": True,
            "message": f"Successfully processed {created_count} damaged product records",
            "created_count": created_count,
            "errors": errors
        }
        
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.post("/damaged-raw-materials")
async def bulk_upload_damaged_raw_materials(file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    """Bulk upload damaged raw materials from Excel file"""
    try:
        content = await file.read()
        wb = openpyxl.load_workbook(BytesIO(content))
        ws = wb.active
        
        created_count = 0
        errors = []
        
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            try:
                if not row[0]:
                    continue
                
                warehouse_code, rm_sku, quantity, damage_type, damage_reason, notes = row[:6]
                
                warehouse_result = await session.execute(select(Warehouse).where(Warehouse.code == warehouse_code))
                warehouse = warehouse_result.scalars().first()
                
                rm_result = await session.execute(select(RawMaterial).where(RawMaterial.sku == rm_sku))
                raw_material = rm_result.scalars().first()
                
                if not warehouse or not raw_material:
                    errors.append(f"Row {row_idx}: Warehouse or Raw Material not found")
                    continue
                
                stock_result = await session.execute(
                    select(StockLevel).where(
                        StockLevel.warehouse_id == warehouse.id,
                        StockLevel.raw_material_id == raw_material.id
                    )
                )
                stock_level = stock_result.scalars().first()
                
                if stock_level:
                    stock_level.current_stock -= Decimal(str(quantity))
                    stock_level.updated_at = datetime.now(timezone.utc)
                
                damaged = DamagedRawMaterial(
                    id=uuid.uuid4(),
                    warehouse_id=warehouse.id,
                    raw_material_id=raw_material.id,
                    quantity=Decimal(str(quantity)),
                    damage_type=str(damage_type).strip(),
                    damage_reason=str(damage_reason).strip() if damage_reason else None,
                    notes=str(notes).strip() if notes else None
                )
                session.add(damaged)
                
                movement = StockMovement(
                    id=uuid.uuid4(),
                    warehouse_id=warehouse.id,
                    raw_material_id=raw_material.id,
                    movement_type='DAMAGED',
                    quantity=Decimal(str(quantity)),
                    reference=f"Bulk Upload - Damaged: {damage_type}",
                    notes=str(notes) if notes else str(damage_reason)
                )
                session.add(movement)
                
                created_count += 1
                
            except Exception as e:
                errors.append(f"Row {row_idx}: {str(e)}")
        
        await session.commit()
        
        return {
            "success": True,
            "message": f"Successfully processed {created_count} damaged raw material records",
            "created_count": created_count,
            "errors": errors
        }
        
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.post("/product-returns")
async def bulk_upload_product_returns(file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    """Bulk upload product returns from Excel file"""
    try:
        content = await file.read()
        wb = openpyxl.load_workbook(BytesIO(content))
        ws = wb.active
        
        created_count = 0
        errors = []
        
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            try:
                if not row[0]:
                    continue
                
                warehouse_code, product_sku, quantity, return_reason, return_condition, customer_name, refund_amount, notes = row[:8]
                
                warehouse_result = await session.execute(select(Warehouse).where(Warehouse.code == warehouse_code))
                warehouse = warehouse_result.scalars().first()
                
                product_result = await session.execute(select(Product).where(Product.sku == product_sku))
                product = product_result.scalars().first()
                
                if not warehouse or not product:
                    errors.append(f"Row {row_idx}: Warehouse or Product not found")
                    continue
                
                # Update stock level (add back to stock if condition is good)
                if str(return_condition).lower() == 'good':
                    stock_result = await session.execute(
                        select(StockLevel).where(
                            StockLevel.warehouse_id == warehouse.id,
                            StockLevel.product_id == product.id
                        )
                    )
                    stock_level = stock_result.scalars().first()
                    
                    if stock_level:
                        stock_level.current_stock += Decimal(str(quantity))
                        stock_level.updated_at = datetime.now(timezone.utc)
                
                product_return = ProductReturn(
                    id=uuid.uuid4(),
                    warehouse_id=warehouse.id,
                    product_id=product.id,
                    quantity=Decimal(str(quantity)),
                    return_reason=str(return_reason).strip(),
                    return_condition=str(return_condition).strip(),
                    customer_name=str(customer_name).strip() if customer_name else None,
                    refund_amount=Decimal(str(refund_amount)) if refund_amount else Decimal('0'),
                    notes=str(notes).strip() if notes else None
                )
                session.add(product_return)
                
                movement = StockMovement(
                    id=uuid.uuid4(),
                    warehouse_id=warehouse.id,
                    product_id=product.id,
                    movement_type='RETURN',
                    quantity=Decimal(str(quantity)),
                    reference=f"Bulk Upload - Return: {return_reason}",
                    notes=f"Customer: {customer_name or 'N/A'}, Condition: {return_condition}"
                )
                session.add(movement)
                
                created_count += 1
                
            except Exception as e:
                errors.append(f"Row {row_idx}: {str(e)}")
        
        await session.commit()
        
        return {
            "success": True,
            "message": f"Successfully processed {created_count} product return records",
            "created_count": created_count,
            "errors": errors
        }
        
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.post("/bom")
async def bulk_upload_bom(file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    """Bulk upload BOM (Bill of Materials) from Excel file"""
    try:
        content = await file.read()
        wb = openpyxl.load_workbook(BytesIO(content))
        ws = wb.active
        
        bom_map = {}  # product_sku -> list of lines
        errors = []
        
        # Group BOM lines by product
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            try:
                if not row[0]:
                    continue
                
                product_sku, rm_sku, qty_per_unit, unit = row[:4]
                
                if not all([product_sku, rm_sku, qty_per_unit, unit]):
                    errors.append(f"Row {row_idx}: Missing required fields")
                    continue
                
                if product_sku not in bom_map:
                    bom_map[product_sku] = []
                
                bom_map[product_sku].append({
                    "rm_sku": str(rm_sku).strip(),
                    "qty_per_unit": Decimal(str(qty_per_unit)),
                    "unit": str(unit).strip(),
                    "row": row_idx
                })
                
            except Exception as e:
                errors.append(f"Row {row_idx}: {str(e)}")
        
        # Create BOMs
        created_count = 0
        for product_sku, lines in bom_map.items():
            try:
                # Get product
                product_result = await session.execute(select(Product).where(Product.sku == product_sku))
                product = product_result.scalars().first()
                
                if not product:
                    errors.append(f"Product SKU '{product_sku}' not found")
                    continue
                
                # Delete existing BOM if any
                existing_bom_result = await session.execute(select(BOM).where(BOM.product_id == product.id))
                existing_bom = existing_bom_result.scalars().first()
                if existing_bom:
                    await session.delete(existing_bom)
                
                # Create new BOM
                bom = BOM(
                    id=uuid.uuid4(),
                    product_id=product.id
                )
                session.add(bom)
                await session.flush()
                
                # Create BOM lines
                for line_data in lines:
                    rm_result = await session.execute(
                        select(RawMaterial).where(RawMaterial.sku == line_data["rm_sku"])
                    )
                    raw_material = rm_result.scalars().first()
                    
                    if not raw_material:
                        errors.append(f"Raw Material SKU '{line_data['rm_sku']}' not found (row {line_data['row']})")
                        continue
                    
                    bom_line = BOMLine(
                        id=uuid.uuid4(),
                        bom_id=bom.id,
                        raw_material_id=raw_material.id,
                        qty_per_unit=line_data["qty_per_unit"]
                    )
                    session.add(bom_line)
                
                created_count += 1
                
            except Exception as e:
                errors.append(f"Product '{product_sku}': {str(e)}")
        
        await session.commit()
        
        return {
            "success": True,
            "message": f"Successfully created {created_count} BOMs",
            "created_count": created_count,
            "errors": errors
        }
        
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
