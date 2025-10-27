import asyncio
import sys
import os

# Ensure we import the local app package
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.db import engine
from app.models import (
    Base, Product, RawMaterial, BOM, BOMLine, Warehouse, StockLevel, StockMovement,
    Customer, SalesOrder, SalesOrderLine, ProductionOrder, Department, Employee
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
import uuid

async def run():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with AsyncSession(engine) as session:
        # Clear existing data first to avoid conflicts
        print("🧹 Clearing existing data...")
        await session.execute(text("""
            TRUNCATE TABLE 
                production_order_materials, production_orders,
                sales_order_lines, sales_orders,
                work_logs, employees, departments,
                bom_lines, boms, stock_movements, stock_levels, 
                products, raw_materials, warehouses, customers 
            RESTART IDENTITY CASCADE
        """))
        await session.commit()
        
    async with AsyncSession(engine) as session:
        # Create warehouses
        wh1 = uuid.uuid4()
        wh2 = uuid.uuid4()
        await session.execute(insert(Warehouse).values(id=wh1, code='WH-001', name='Main Warehouse', location='Building A'))
        await session.execute(insert(Warehouse).values(id=wh2, code='WH-002', name='Production Warehouse', location='Building B'))
        
        # create products
        pid1 = uuid.uuid4()
        pid2 = uuid.uuid4()
        await session.execute(insert(Product).values(id=pid1, sku='PROD-001', name='Wound Dressing', unit='each'))
        await session.execute(insert(Product).values(id=pid2, sku='PROD-002', name='Bandage Roll', unit='roll'))
        
        # create raw materials
        rm1 = uuid.uuid4()
        rm2 = uuid.uuid4()
        rm3 = uuid.uuid4()
        await session.execute(insert(RawMaterial).values(id=rm1, sku='RM-001', name='Gauze', unit_cost='0.150000'))
        await session.execute(insert(RawMaterial).values(id=rm2, sku='RM-002', name='Adhesive', unit_cost='0.050000'))
        await session.execute(insert(RawMaterial).values(id=rm3, sku='RM-003', name='Cotton Fabric', unit_cost='0.300000'))
        
        # create BOMs
        bid1 = uuid.uuid4()
        bid2 = uuid.uuid4()
        await session.execute(insert(BOM).values(id=bid1, product_id=pid1))
        await session.execute(insert(BOM).values(id=bid2, product_id=pid2))
        
        # BOM lines
        await session.execute(insert(BOMLine).values(id=uuid.uuid4(), bom_id=bid1, raw_material_id=rm1, qty_per_unit='2'))
        await session.execute(insert(BOMLine).values(id=uuid.uuid4(), bom_id=bid1, raw_material_id=rm2, qty_per_unit='1'))
        await session.execute(insert(BOMLine).values(id=uuid.uuid4(), bom_id=bid2, raw_material_id=rm3, qty_per_unit='5'))
        
        # Create initial stock levels
        await session.execute(insert(StockLevel).values(
            id=uuid.uuid4(), warehouse_id=wh1, raw_material_id=rm1, 
            current_stock='100', min_stock='20', max_stock='500'
        ))
        await session.execute(insert(StockLevel).values(
            id=uuid.uuid4(), warehouse_id=wh1, raw_material_id=rm2, 
            current_stock='50', min_stock='10', max_stock='200'
        ))
        await session.execute(insert(StockLevel).values(
            id=uuid.uuid4(), warehouse_id=wh1, raw_material_id=rm3, 
            current_stock='200', min_stock='50', max_stock='1000'
        ))
        
        # Create sample stock movements
        await session.execute(insert(StockMovement).values(
            id=uuid.uuid4(), warehouse_id=wh1, raw_material_id=rm1,
            movement_type='IN', quantity='100', unit_cost='0.15', reference='PO-001', notes='Initial stock'
        ))
        await session.execute(insert(StockMovement).values(
            id=uuid.uuid4(), warehouse_id=wh1, raw_material_id=rm2,
            movement_type='IN', quantity='50', unit_cost='0.05', reference='PO-002', notes='Initial stock'
        ))
        
        await session.commit()
        
        # Create sample customers
        cid1, cid2, cid3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await session.execute(insert(Customer).values([
            {'id': cid1, 'customer_code': 'CUST001', 'name': 'ABC Manufacturing Co.', 'email': 'orders@abcmfg.com', 'phone': '+1-555-0101', 'credit_limit': 50000.00},
            {'id': cid2, 'customer_code': 'CUST002', 'name': 'XYZ Industries Ltd.', 'email': 'purchasing@xyzind.com', 'phone': '+1-555-0102', 'credit_limit': 75000.00},
            {'id': cid3, 'customer_code': 'CUST003', 'name': 'Tech Solutions Inc.', 'email': 'procurement@techsol.com', 'phone': '+1-555-0103', 'credit_limit': 100000.00}
        ]))
        
        # Create sample departments
        did1, did2, did3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await session.execute(insert(Department).values([
            {'id': did1, 'name': 'Production', 'description': 'Manufacturing and production operations'},
            {'id': did2, 'name': 'Sales', 'description': 'Sales and customer relations'},
            {'id': did3, 'name': 'Administration', 'description': 'Administrative and management functions'}
        ]))
        
        # Create sample employees
        eid1, eid2, eid3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await session.execute(insert(Employee).values([
            {'id': eid1, 'employee_number': 'EMP001', 'first_name': 'John', 'last_name': 'Doe', 'position': 'Production Manager', 'department_id': did1, 'salary': 75000.00},
            {'id': eid2, 'employee_number': 'EMP002', 'first_name': 'Jane', 'last_name': 'Smith', 'position': 'Sales Representative', 'department_id': did2, 'salary': 50000.00},
            {'id': eid3, 'employee_number': 'EMP003', 'first_name': 'Mike', 'last_name': 'Johnson', 'position': 'Operations Supervisor', 'department_id': did1, 'salary': 65000.00}
        ]))
        
        # Create sample sales orders
        soid1, soid2 = uuid.uuid4(), uuid.uuid4()
        await session.execute(insert(SalesOrder).values([
            {'id': soid1, 'order_number': 'SO-20251022-001', 'customer_id': cid1, 'status': 'confirmed', 'total_amount': 1250.00, 'notes': 'Urgent order for Q4 delivery'},
            {'id': soid2, 'order_number': 'SO-20251022-002', 'customer_id': cid2, 'status': 'pending', 'total_amount': 875.50, 'notes': 'Standard delivery terms'}
        ]))
        
        # Create sample sales order lines
        await session.execute(insert(SalesOrderLine).values([
            {'id': uuid.uuid4(), 'sales_order_id': soid1, 'product_id': pid1, 'quantity': 100, 'unit_price': 12.50, 'line_total': 1250.00},
            {'id': uuid.uuid4(), 'sales_order_id': soid2, 'product_id': pid2, 'quantity': 175, 'unit_price': 5.00, 'line_total': 875.00}
        ]))
        
        # Create sample production orders
        poid1, poid2 = uuid.uuid4(), uuid.uuid4()
        await session.execute(insert(ProductionOrder).values([
            {'id': poid1, 'order_number': 'PO-20251022-001', 'product_id': pid1, 'quantity_planned': 150, 'quantity_produced': 0, 'status': 'planned', 'priority': 3},
            {'id': poid2, 'order_number': 'PO-20251022-002', 'product_id': pid2, 'quantity_planned': 200, 'quantity_produced': 50, 'status': 'in_progress', 'priority': 1}
        ]))
        
        await session.commit()
        
    print('✅ Seeded sample data:')
    print(f'   📦 Products: {pid1} (Wound Dressing), {pid2} (Bandage Roll)')
    print(f'   🧱 Raw Materials: Gauze, Adhesive, Cotton Fabric')
    print(f'   🏭 Warehouses: Main Warehouse, Production Warehouse')
    print(f'   📋 BOMs: {bid1}, {bid2}')
    print('   📊 Stock levels and movements initialized')
    print(f'   👥 Customers: 3 customers (ABC Manufacturing, XYZ Industries, Tech Solutions)')
    print(f'   🏢 Departments: 3 departments (Production, Sales, Administration)')
    print(f'   👨‍💼 Employees: 3 employees (John Doe, Jane Smith, Mike Johnson)')
    print(f'   📋 Sales Orders: 2 orders with line items')
    print(f'   🏭 Production Orders: 2 orders (1 planned, 1 in progress)')

if __name__ == '__main__':
    asyncio.run(run())
