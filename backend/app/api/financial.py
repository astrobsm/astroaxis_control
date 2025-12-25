"""
Financial reporting API - Admin only access to company financial status
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, and_, or_
from typing import Dict, Any
from decimal import Decimal
from datetime import datetime, timedelta, timezone
import io

from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, 
    Spacer, Image, PageBreak, KeepTogether
)
import os

from app.db import get_session
from app.models import (
    SalesOrder, Product, RawMaterial, StockMovement,
    Staff, Attendance, ProductionOrder, Customer, Warehouse
)
from app.schemas import ApiResponse

router = APIRouter(prefix='/api/financial')


async def calculate_financial_metrics(session: AsyncSession) -> Dict[str, Any]:
    """Calculate comprehensive financial metrics for the company"""
    
    # 1. REVENUE ANALYSIS
    # Total revenue from paid sales orders
    revenue_query = select(func.coalesce(func.sum(SalesOrder.total_amount), 0)).where(
        SalesOrder.payment_status == 'paid'
    )
    revenue_result = await session.execute(revenue_query)
    total_revenue = float(revenue_result.scalar())
    
    # Outstanding payments (unpaid orders)
    outstanding_query = select(func.coalesce(func.sum(SalesOrder.total_amount), 0)).where(
        SalesOrder.payment_status == 'unpaid'
    )
    outstanding_result = await session.execute(outstanding_query)
    outstanding_payments = float(outstanding_result.scalar())
    
    # Revenue by month (last 12 months)
    revenue_by_month = []
    for i in range(12):
        month_start = (datetime.now(timezone.utc) - timedelta(days=30*i)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(seconds=1)
        
        monthly_query = select(func.coalesce(func.sum(SalesOrder.total_amount), 0)).where(
            and_(
                SalesOrder.payment_status == 'paid',
                SalesOrder.payment_date >= month_start,
                SalesOrder.payment_date <= month_end
            )
        )
        monthly_result = await session.execute(monthly_query)
        monthly_revenue = float(monthly_result.scalar())
        
        revenue_by_month.append({
            'month': month_start.strftime('%b %Y'),
            'revenue': monthly_revenue
        })
    
    # 2. INVENTORY VALUATION
    # Product inventory value - calculate from stock movements
    # Get sum of all stock movements (in - out) per product
    from sqlalchemy import case
    
    stock_query = select(
        StockMovement.product_id,
        func.sum(
            case(
                (StockMovement.movement_type == 'in', StockMovement.quantity),
                else_=-StockMovement.quantity
            )
        ).label('current_stock')
    ).group_by(StockMovement.product_id)
    
    stock_result = await session.execute(stock_query)
    product_stocks = {row.product_id: float(row.current_stock or 0) for row in stock_result}
    
    # Get products with cost price
    products_query = select(Product)
    products_result = await session.execute(products_query)
    products = products_result.scalars().all()
    
    product_inventory_value = sum(
        product_stocks.get(p.id, 0) * float(p.cost_price or 0) 
        for p in products
    )
    
    # Raw materials inventory value - set to 0 (no current_stock field in model)
    # TODO: Implement raw material stock tracking in future update
    raw_materials_value = 0.0
    
    total_inventory_value = product_inventory_value + raw_materials_value
    
    # 3. PAYROLL EXPENSES
    # Calculate monthly payroll from attendance records
    current_month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # Get all staff with attendance this month
    staff_query = select(Staff)
    staff_result = await session.execute(staff_query)
    all_staff = staff_result.scalars().all()
    
    total_payroll = 0
    for staff_member in all_staff:
        # Get attendance records for current month
        attendance_query = select(Attendance).where(
            and_(
                Attendance.staff_id == staff_member.id,
                Attendance.clock_in >= current_month_start
            )
        )
        attendance_result = await session.execute(attendance_query)
        attendance_records = attendance_result.scalars().all()
        
        # Calculate hours worked
        total_hours = 0
        for record in attendance_records:
            if record.clock_out:
                hours = (record.clock_out - record.clock_in).total_seconds() / 3600
                total_hours += hours
        
        staff_pay = total_hours * float(staff_member.hourly_rate)
        total_payroll += staff_pay
    
    # 4. PRODUCTION COSTS
    # Estimate production costs from completed production orders
    completed_production_query = select(ProductionOrder).where(
        ProductionOrder.status == 'completed'
    )
    completed_production_result = await session.execute(completed_production_query)
    completed_orders = completed_production_result.scalars().all()
    
    production_costs = 0
    for prod_order in completed_orders:
        # Simplified: estimate 70% of revenue as production cost
        production_costs += float(prod_order.quantity_produced) * 0.7
    
    # 5. ASSETS & LIABILITIES
    # Assets: Inventory + Outstanding Payments
    total_assets = total_inventory_value + outstanding_payments
    
    # Liabilities: Estimated (simplified - would normally include debts, loans, etc.)
    total_liabilities = total_payroll  # Current month payroll liability
    
    # Net Worth: Assets - Liabilities
    net_worth = total_assets - total_liabilities
    
    # 6. PROFITABILITY
    total_expenses = total_payroll + production_costs
    net_profit = total_revenue - total_expenses
    profit_margin = (net_profit / total_revenue * 100) if total_revenue > 0 else 0
    
    # 7. SALES STATISTICS
    sales_count_query = select(func.count(SalesOrder.id))
    sales_count_result = await session.execute(sales_count_query)
    total_sales = sales_count_result.scalar()
    
    paid_sales_query = select(func.count(SalesOrder.id)).where(SalesOrder.payment_status == 'paid')
    paid_sales_result = await session.execute(paid_sales_query)
    paid_sales = paid_sales_result.scalar()
    
    # 8. CUSTOMER ANALYTICS
    customers_query = select(func.count(Customer.id))
    customers_result = await session.execute(customers_query)
    total_customers = customers_result.scalar()
    
    # 9. INVENTORY COUNTS
    products_count_query = select(func.count(Product.id))
    products_count_result = await session.execute(products_count_query)
    total_products = products_count_result.scalar()
    
    raw_materials_count_query = select(func.count(RawMaterial.id))
    raw_materials_count_result = await session.execute(raw_materials_count_query)
    total_raw_materials = raw_materials_count_result.scalar()
    
    # 10. WORKFORCE
    staff_count_query = select(func.count(Staff.id))
    staff_count_result = await session.execute(staff_count_query)
    total_staff = staff_count_result.scalar()
    
    return {
        # Revenue Metrics
        'total_revenue': total_revenue,
        'outstanding_payments': outstanding_payments,
        'revenue_by_month': list(reversed(revenue_by_month)),  # Oldest to newest
        
        # Inventory Metrics
        'product_inventory_value': product_inventory_value,
        'raw_materials_value': raw_materials_value,
        'total_inventory_value': total_inventory_value,
        
        # Expense Metrics
        'monthly_payroll': total_payroll,
        'production_costs': production_costs,
        'total_expenses': total_expenses,
        
        # Financial Position
        'total_assets': total_assets,
        'total_liabilities': total_liabilities,
        'net_worth': net_worth,
        
        # Profitability
        'net_profit': net_profit,
        'profit_margin': profit_margin,
        
        # Operational Metrics
        'total_sales': total_sales,
        'paid_sales': paid_sales,
        'total_customers': total_customers,
        'total_products': total_products,
        'total_raw_materials': total_raw_materials,
        'total_staff': total_staff,
        
        # Timestamp
        'generated_at': datetime.now(timezone.utc).isoformat()
    }


@router.get('/company-status', response_model=ApiResponse)
async def get_company_financial_status(
    session: AsyncSession = Depends(get_session)
):
    """
    Get comprehensive company financial status
    Admin only access - displays complete financial overview
    """
    try:
        metrics = await calculate_financial_metrics(session)
        
        return ApiResponse(
            message="Company financial status retrieved successfully",
            data=metrics
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving financial status: {str(e)}"
        )


@router.get('/company-status/export')
async def export_financial_report(
    session: AsyncSession = Depends(get_session)
):
    """
    Export company financial status as PDF
    Admin only access
    """
    try:
        metrics = await calculate_financial_metrics(session)
        
        # Create PDF in memory
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=50,
            leftMargin=50,
            topMargin=50,
            bottomMargin=30
        )
        
        # Container for PDF elements
        elements = []
        styles = getSampleStyleSheet()
        
        # Custom styles
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#667eea'),
            spaceAfter=30,
            alignment=TA_CENTER
        )
        
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=14,
            textColor=colors.HexColor('#667eea'),
            spaceAfter=12,
            spaceBefore=20
        )
        
        # Add company logo if exists
        logo_path = '/app/frontend/build/company-logo.png'
        if os.path.exists(logo_path):
            img = Image(logo_path, width=2*inch, height=2*inch)
            img.hAlign = 'CENTER'
            elements.append(img)
            elements.append(Spacer(1, 20))
        
        # Title
        title = Paragraph('COMPANY FINANCIAL STATUS REPORT', title_style)
        elements.append(title)
        
        # Generation date
        gen_date = Paragraph(
            f'<i>Generated on: {datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M")}</i>',
            styles['Normal']
        )
        gen_date.hAlign = 'CENTER'
        elements.append(gen_date)
        elements.append(Spacer(1, 30))
        
        # EXECUTIVE SUMMARY
        elements.append(Paragraph('EXECUTIVE SUMMARY', heading_style))
        
        summary_data = [
            ['Metric', 'Value'],
            ['Total Revenue (Paid)', f"₦{metrics['total_revenue']:,.2f}"],
            ['Outstanding Payments', f"₦{metrics['outstanding_payments']:,.2f}"],
            ['Total Assets', f"₦{metrics['total_assets']:,.2f}"],
            ['Total Liabilities', f"₦{metrics['total_liabilities']:,.2f}"],
            ['Net Worth', f"₦{metrics['net_worth']:,.2f}"],
            ['Net Profit', f"₦{metrics['net_profit']:,.2f}"],
            ['Profit Margin', f"{metrics['profit_margin']:.2f}%"],
        ]
        
        summary_table = Table(summary_data, colWidths=[3.5*inch, 2.5*inch])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('FONTNAME', (0, -1), (1, -1), 'Helvetica-Bold'),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#d1fae5'))
        ]))
        elements.append(summary_table)
        elements.append(Spacer(1, 30))
        
        # REVENUE BREAKDOWN
        elements.append(Paragraph('REVENUE ANALYSIS', heading_style))
        
        revenue_data = [
            ['Description', 'Amount'],
            ['Total Sales Orders', f"{metrics['total_sales']}"],
            ['Paid Sales Orders', f"{metrics['paid_sales']}"],
            ['Total Revenue Collected', f"₦{metrics['total_revenue']:,.2f}"],
            ['Outstanding Payments', f"₦{metrics['outstanding_payments']:,.2f}"],
        ]
        
        revenue_table = Table(revenue_data, colWidths=[3.5*inch, 2.5*inch])
        revenue_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#11998e')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(revenue_table)
        elements.append(Spacer(1, 30))
        
        # INVENTORY VALUATION
        elements.append(Paragraph('INVENTORY VALUATION', heading_style))
        
        inventory_data = [
            ['Category', 'Count', 'Value'],
            ['Products', f"{metrics['total_products']}", f"₦{metrics['product_inventory_value']:,.2f}"],
            ['Raw Materials', f"{metrics['total_raw_materials']}", f"₦{metrics['raw_materials_value']:,.2f}"],
            ['TOTAL INVENTORY', '', f"₦{metrics['total_inventory_value']:,.2f}"],
        ]
        
        inventory_table = Table(inventory_data, colWidths=[2.5*inch, 1.5*inch, 2*inch])
        inventory_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#ff6b6b')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -2), colors.beige),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#feca57')),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(inventory_table)
        elements.append(Spacer(1, 30))
        
        # EXPENSES
        elements.append(Paragraph('EXPENSE BREAKDOWN', heading_style))
        
        expense_data = [
            ['Category', 'Amount'],
            ['Monthly Payroll', f"₦{metrics['monthly_payroll']:,.2f}"],
            ['Production Costs', f"₦{metrics['production_costs']:,.2f}"],
            ['TOTAL EXPENSES', f"₦{metrics['total_expenses']:,.2f}"],
        ]
        
        expense_table = Table(expense_data, colWidths=[3.5*inch, 2.5*inch])
        expense_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e74c3c')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -2), colors.beige),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#fee2e2')),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(expense_table)
        elements.append(Spacer(1, 30))
        
        # OPERATIONAL METRICS
        elements.append(Paragraph('OPERATIONAL METRICS', heading_style))
        
        ops_data = [
            ['Metric', 'Count'],
            ['Total Customers', f"{metrics['total_customers']}"],
            ['Total Staff', f"{metrics['total_staff']}"],
            ['Product SKUs', f"{metrics['total_products']}"],
            ['Raw Material Types', f"{metrics['total_raw_materials']}"],
        ]
        
        ops_table = Table(ops_data, colWidths=[3.5*inch, 2.5*inch])
        ops_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4776e6')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(ops_table)
        
        # Footer
        elements.append(Spacer(1, 40))
        footer_text = '''
        <i>This is a confidential financial report generated by ASTRO-ASIX ERP System.<br/>
        For internal use only. Unauthorized distribution is prohibited.</i>
        '''
        footer = Paragraph(footer_text, styles['Normal'])
        footer.hAlign = 'CENTER'
        elements.append(footer)
        
        # Build PDF
        doc.build(elements)
        buffer.seek(0)
        
        filename = f"financial_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.pdf"
        
        return StreamingResponse(
            buffer,
            media_type='application/pdf',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'}
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error generating financial report: {str(e)}"
        )
