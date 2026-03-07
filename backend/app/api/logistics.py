"""
Logistics & Delivery Tracking Module - Batch Manifest Workflow
- Deliveries are batched on Mondays and Thursdays
- A Delivery Manifest groups multiple customers & their items in one trip
- At delivery point, receiver signs; logistics staff records receiver details + physical invoice number
- Printable PDF manifest with signature lines
- Dashboard + analytics
"""
from uuid import UUID
import uuid
from datetime import datetime, timezone, date as date_type
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.db import get_session
import io, os

router = APIRouter(prefix="/api/logistics", tags=["Logistics"])

TABLE_STATEMENTS = [
    # Keep legacy tables alive so old data is not lost
    """CREATE TABLE IF NOT EXISTS logistics_deliveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    delivery_number VARCHAR(64) UNIQUE NOT NULL,
    sales_order_id UUID,
    sales_officer VARCHAR(255) NOT NULL,
    staff_id UUID,
    customer_name VARCHAR(255) NOT NULL,
    customer_phone VARCHAR(50),
    delivery_address TEXT NOT NULL,
    city VARCHAR(100),
    state VARCHAR(100),
    landmark TEXT,
    delivery_date DATE NOT NULL DEFAULT CURRENT_DATE,
    departure_time TIME,
    arrival_time TIME,
    transport_mode VARCHAR(50) DEFAULT 'vehicle',
    vehicle_details VARCHAR(255),
    driver_name VARCHAR(255),
    driver_phone VARCHAR(50),
    transport_cost NUMERIC(18,2) NOT NULL DEFAULT 0,
    additional_charges NUMERIC(18,2) DEFAULT 0,
    total_cost NUMERIC(18,2) NOT NULL DEFAULT 0,
    payment_method VARCHAR(50),
    payment_reference VARCHAR(255),
    status VARCHAR(30) NOT NULL DEFAULT 'pending',
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)""",
    """CREATE TABLE IF NOT EXISTS logistics_delivery_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    delivery_id UUID NOT NULL REFERENCES logistics_deliveries(id) ON DELETE CASCADE,
    product_id UUID,
    product_name VARCHAR(255) NOT NULL,
    sku VARCHAR(100),
    quantity NUMERIC(18,6) NOT NULL DEFAULT 1,
    unit VARCHAR(50) DEFAULT 'each',
    weight_kg NUMERIC(10,3),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
)""",
    "CREATE INDEX IF NOT EXISTS idx_ld_officer ON logistics_deliveries(sales_officer)",
    "CREATE INDEX IF NOT EXISTS idx_ld_date ON logistics_deliveries(delivery_date)",
    "CREATE INDEX IF NOT EXISTS idx_ld_status ON logistics_deliveries(status)",
    "CREATE INDEX IF NOT EXISTS idx_ld_customer ON logistics_deliveries(customer_name)",
    # New batch manifest tables
    """CREATE TABLE IF NOT EXISTS delivery_manifests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manifest_number VARCHAR(64) UNIQUE NOT NULL,
    delivery_date DATE NOT NULL DEFAULT CURRENT_DATE,
    logistics_officer VARCHAR(255) NOT NULL,
    vehicle_details VARCHAR(255),
    driver_name VARCHAR(255),
    driver_phone VARCHAR(50),
    transport_mode VARCHAR(50) DEFAULT 'vehicle',
    transport_cost NUMERIC(18,2) NOT NULL DEFAULT 0,
    additional_charges NUMERIC(18,2) DEFAULT 0,
    total_cost NUMERIC(18,2) NOT NULL DEFAULT 0,
    status VARCHAR(30) NOT NULL DEFAULT 'preparing',
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)""",
    """CREATE TABLE IF NOT EXISTS manifest_customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manifest_id UUID NOT NULL REFERENCES delivery_manifests(id) ON DELETE CASCADE,
    customer_id UUID,
    customer_name VARCHAR(255) NOT NULL,
    customer_phone VARCHAR(100),
    delivery_address TEXT NOT NULL,
    city VARCHAR(100),
    state VARCHAR(100),
    receiver_name VARCHAR(255),
    receiver_phone VARCHAR(100),
    physical_invoice_number VARCHAR(100),
    delivery_time TIMESTAMPTZ,
    signature_collected BOOLEAN DEFAULT FALSE,
    delivery_notes TEXT,
    status VARCHAR(30) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW()
)""",
    """CREATE TABLE IF NOT EXISTS manifest_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manifest_customer_id UUID NOT NULL REFERENCES manifest_customers(id) ON DELETE CASCADE,
    product_id UUID,
    product_name VARCHAR(255) NOT NULL,
    sku VARCHAR(100),
    quantity NUMERIC(18,6) NOT NULL DEFAULT 1,
    unit VARCHAR(50) DEFAULT 'each',
    created_at TIMESTAMPTZ DEFAULT NOW()
)""",
    "CREATE INDEX IF NOT EXISTS idx_dm_date ON delivery_manifests(delivery_date)",
    "CREATE INDEX IF NOT EXISTS idx_dm_status ON delivery_manifests(status)",
    "CREATE INDEX IF NOT EXISTS idx_mc_manifest ON manifest_customers(manifest_id)",
    "CREATE INDEX IF NOT EXISTS idx_mi_mc ON manifest_items(manifest_customer_id)",
]


@router.on_event("startup")
async def init_logistics_tables():
    from app.db import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        for stmt in TABLE_STATEMENTS:
            await session.execute(text(stmt))
        await session.commit()
        print("Logistics tables ready")


@router.post('/manifests')
async def create_manifest(data: dict, session: AsyncSession = Depends(get_session)):
    """Create a new delivery manifest with multiple customers and items."""
    try:
        mf_num = f"MF-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
        transport = float(data.get('transport_cost', 0))
        additional = float(data.get('additional_charges', 0))
        total = transport + additional

        sql = text("""
            INSERT INTO delivery_manifests
                (manifest_number, delivery_date, logistics_officer,
                 vehicle_details, driver_name, driver_phone, transport_mode,
                 transport_cost, additional_charges, total_cost, status, notes)
            VALUES
                (:mn, :dd, :lo, :vd, :dn, :dp, :tm, :tc, :ac, :tot, :status, :notes)
            RETURNING id, manifest_number
        """)
        # Convert delivery_date string to date object for asyncpg
        dd_raw = data.get('delivery_date', '')
        if isinstance(dd_raw, str) and dd_raw:
            dd_val = date_type.fromisoformat(dd_raw)
        elif isinstance(dd_raw, date_type):
            dd_val = dd_raw
        else:
            dd_val = datetime.now(timezone.utc).date()

        result = await session.execute(sql, {
            'mn': mf_num,
            'dd': dd_val,
            'lo': data.get('logistics_officer', ''),
            'vd': data.get('vehicle_details', ''),
            'dn': data.get('driver_name', ''),
            'dp': data.get('driver_phone', ''),
            'tm': data.get('transport_mode', 'vehicle'),
            'tc': transport, 'ac': additional, 'tot': total,
            'status': 'preparing',
            'notes': data.get('notes', ''),
        })
        row = result.fetchone()
        manifest_id = str(row.id)

        customers = data.get('customers', [])
        for cust in customers:
            cust_sql = text("""
                INSERT INTO manifest_customers
                    (manifest_id, customer_id, customer_name, customer_phone,
                     delivery_address, city, state, status)
                VALUES (:mid, :cid, :cn, :cp, :addr, :city, :state, 'pending')
                RETURNING id
            """)
            cust_result = await session.execute(cust_sql, {
                'mid': manifest_id,
                'cid': cust.get('customer_id') or None,
                'cn': cust.get('customer_name', ''),
                'cp': cust.get('customer_phone', ''),
                'addr': cust.get('delivery_address', ''),
                'city': cust.get('city', ''),
                'state': cust.get('state', ''),
            })
            mc_id = str(cust_result.fetchone().id)

            for item in cust.get('items', []):
                await session.execute(text("""
                    INSERT INTO manifest_items
                        (manifest_customer_id, product_id, product_name, sku, quantity, unit)
                    VALUES (:mcid, :pid, :pn, :sku, :qty, :unit)
                """), {
                    'mcid': mc_id,
                    'pid': item.get('product_id') or None,
                    'pn': item.get('product_name', ''),
                    'sku': item.get('sku', ''),
                    'qty': float(item.get('quantity', 1)),
                    'unit': item.get('unit', 'each'),
                })

        await session.commit()
        return {
            "message": f"Delivery Manifest {mf_num} created with {len(customers)} customer(s)",
            "manifest_number": mf_num,
            "id": manifest_id,
            "total_cost": total,
        }
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/manifests')
async def list_manifests(
    status: str = None,
    date_from: str = None,
    date_to: str = None,
    session: AsyncSession = Depends(get_session)
):
    """List all delivery manifests."""
    try:
        where, params = [], {}
        if status:
            where.append("dm.status = :status"); params['status'] = status
        if date_from:
            where.append("dm.delivery_date >= :df"); params['df'] = date_from
        if date_to:
            where.append("dm.delivery_date <= :dt"); params['dt'] = date_to
        wc = " AND ".join(where) if where else "1=1"

        sql = text(f"""
            SELECT dm.*,
                (SELECT COUNT(*) FROM manifest_customers WHERE manifest_id = dm.id) as customer_count,
                (SELECT COUNT(*) FROM manifest_items mi
                   JOIN manifest_customers mc ON mc.id = mi.manifest_customer_id
                   WHERE mc.manifest_id = dm.id) as total_items,
                (SELECT COUNT(*) FROM manifest_customers WHERE manifest_id = dm.id AND status = 'delivered') as delivered_count
            FROM delivery_manifests dm
            WHERE {wc}
            ORDER BY dm.delivery_date DESC, dm.created_at DESC
        """)
        result = await session.execute(sql, params)
        items = []
        for r in result.fetchall():
            items.append({
                "id": str(r.id), "manifest_number": r.manifest_number,
                "delivery_date": str(r.delivery_date) if r.delivery_date else None,
                "logistics_officer": r.logistics_officer,
                "vehicle_details": r.vehicle_details or '',
                "driver_name": r.driver_name or '',
                "transport_mode": r.transport_mode or 'vehicle',
                "transport_cost": float(r.transport_cost or 0),
                "additional_charges": float(r.additional_charges or 0),
                "total_cost": float(r.total_cost or 0),
                "status": r.status,
                "customer_count": r.customer_count,
                "total_items": r.total_items,
                "delivered_count": r.delivered_count,
                "notes": r.notes or '',
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        return {"items": items, "total": len(items)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/manifests/{manifest_id}')
async def get_manifest(manifest_id: UUID, session: AsyncSession = Depends(get_session)):
    """Get full manifest detail with all customers and their items."""
    try:
        sql = text("SELECT * FROM delivery_manifests WHERE id = :id")
        result = await session.execute(sql, {"id": str(manifest_id)})
        r = result.fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Manifest not found")

        cust_sql = text("SELECT * FROM manifest_customers WHERE manifest_id = :mid ORDER BY created_at")
        custs = []
        for c in (await session.execute(cust_sql, {"mid": str(manifest_id)})).fetchall():
            item_sql = text("SELECT * FROM manifest_items WHERE manifest_customer_id = :mcid ORDER BY created_at")
            items = []
            for it in (await session.execute(item_sql, {"mcid": str(c.id)})).fetchall():
                items.append({
                    "id": str(it.id),
                    "product_id": str(it.product_id) if it.product_id else None,
                    "product_name": it.product_name,
                    "sku": it.sku or '',
                    "quantity": float(it.quantity),
                    "unit": it.unit or 'each',
                })
            custs.append({
                "id": str(c.id),
                "customer_id": str(c.customer_id) if c.customer_id else None,
                "customer_name": c.customer_name,
                "customer_phone": c.customer_phone or '',
                "delivery_address": c.delivery_address or '',
                "city": c.city or '',
                "state": c.state or '',
                "receiver_name": c.receiver_name or '',
                "receiver_phone": c.receiver_phone or '',
                "physical_invoice_number": c.physical_invoice_number or '',
                "delivery_time": c.delivery_time.isoformat() if c.delivery_time else None,
                "signature_collected": c.signature_collected or False,
                "delivery_notes": c.delivery_notes or '',
                "status": c.status,
                "items": items,
            })

        return {
            "id": str(r.id), "manifest_number": r.manifest_number,
            "delivery_date": str(r.delivery_date) if r.delivery_date else None,
            "logistics_officer": r.logistics_officer,
            "vehicle_details": r.vehicle_details or '',
            "driver_name": r.driver_name or '',
            "driver_phone": r.driver_phone or '',
            "transport_mode": r.transport_mode or 'vehicle',
            "transport_cost": float(r.transport_cost or 0),
            "additional_charges": float(r.additional_charges or 0),
            "total_cost": float(r.total_cost or 0),
            "status": r.status,
            "notes": r.notes or '',
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "customers": custs,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put('/manifests/customer/{mc_id}/confirm')
async def confirm_customer_delivery(mc_id: UUID, data: dict, session: AsyncSession = Depends(get_session)):
    """At delivery point: record receiver details, physical invoice number, mark delivered."""
    try:
        sql = text("""
            UPDATE manifest_customers SET
                receiver_name = :rn,
                receiver_phone = :rp,
                physical_invoice_number = :pin,
                delivery_time = :dt,
                signature_collected = :sc,
                delivery_notes = :dn,
                status = 'delivered'
            WHERE id = :id
            RETURNING id, manifest_id
        """)
        result = await session.execute(sql, {
            'id': str(mc_id),
            'rn': data.get('receiver_name', ''),
            'rp': data.get('receiver_phone', ''),
            'pin': data.get('physical_invoice_number', ''),
            'dt': datetime.now(timezone.utc),
            'sc': data.get('signature_collected', True),
            'dn': data.get('delivery_notes', ''),
        })
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Customer delivery entry not found")

        manifest_id = str(row.manifest_id)
        check = await session.execute(text("""
            SELECT COUNT(*) as total, COUNT(*) FILTER (WHERE status = 'delivered') as done
            FROM manifest_customers WHERE manifest_id = :mid
        """), {"mid": manifest_id})
        cr = check.fetchone()
        if cr.total == cr.done:
            await session.execute(text(
                "UPDATE delivery_manifests SET status = 'completed', updated_at = NOW() WHERE id = :mid"
            ), {"mid": manifest_id})
        else:
            await session.execute(text(
                "UPDATE delivery_manifests SET status = 'in_transit', updated_at = NOW() WHERE id = :mid"
            ), {"mid": manifest_id})

        await session.commit()
        return {"message": "Delivery confirmed for customer", "all_delivered": cr.total == cr.done}
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.put('/manifests/{manifest_id}/status')
async def update_manifest_status(manifest_id: UUID, data: dict, session: AsyncSession = Depends(get_session)):
    """Update manifest status: preparing, dispatched, in_transit, completed, cancelled."""
    try:
        new_status = data.get('status', 'dispatched')
        sql = text("""
            UPDATE delivery_manifests SET status = :status, updated_at = NOW()
            WHERE id = :id RETURNING id, manifest_number
        """)
        result = await session.execute(sql, {"status": new_status, "id": str(manifest_id)})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Manifest not found")
        await session.commit()
        return {"message": f"Manifest {row.manifest_number} updated to {new_status}"}
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.put('/manifests/{manifest_id}/cost')
async def update_manifest_cost(manifest_id: UUID, data: dict, session: AsyncSession = Depends(get_session)):
    """Update the transport cost for a manifest."""
    try:
        transport = float(data.get('transport_cost', 0))
        additional = float(data.get('additional_charges', 0))
        total = transport + additional
        sql = text("""
            UPDATE delivery_manifests SET
                transport_cost = :tc, additional_charges = :ac, total_cost = :tot,
                updated_at = NOW()
            WHERE id = :id RETURNING id, manifest_number
        """)
        result = await session.execute(sql, {
            'tc': transport, 'ac': additional, 'tot': total,
            'id': str(manifest_id)
        })
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Manifest not found")
        await session.commit()
        return {"message": f"Cost updated for manifest {row.manifest_number}", "total_cost": total}
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/manifests/{manifest_id}/pdf')
@router.get('/manifests/{manifest_id}/download')
@router.get('/manifests/{manifest_id}/printout')
@router.post('/manifests/{manifest_id}/generate-doc')
async def generate_manifest_pdf(manifest_id: UUID, session: AsyncSession = Depends(get_session)):
    """Generate a printable PDF delivery manifest with signature lines."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image

        r = (await session.execute(
            text("SELECT * FROM delivery_manifests WHERE id = :id"),
            {"id": str(manifest_id)}
        )).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Manifest not found")

        custs = (await session.execute(
            text("SELECT * FROM manifest_customers WHERE manifest_id = :mid ORDER BY created_at"),
            {"mid": str(manifest_id)}
        )).fetchall()

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=30)
        styles = getSampleStyleSheet()
        story = []

        logo_paths = ['/app/company-logo.png', '/app/frontend/build/company-logo.png',
                      os.path.join(os.path.dirname(__file__), '..', '..', 'company-logo.png')]
        for path in logo_paths:
            if os.path.exists(path):
                story.append(Image(path, width=1.2*inch, height=1.2*inch))
                story.append(Spacer(1, 6))
                break

        story.append(Paragraph("BONNESANTE MEDICALS", styles['Title']))
        story.append(Paragraph(
            "NO 6B PEACE AVENUE/17A ISUOFIA STREET, FEDERAL HOUSING ESTATE TRANS EKULU, ENUGU<br/>"
            "Phone: +234 707 679 3866, +234 901 283 5413 | Email: astrobsm@gmail.com",
            styles['Normal']
        ))
        story.append(Spacer(1, 12))
        story.append(Paragraph(f"<b>DELIVERY MANIFEST: {r.manifest_number}</b>", styles['Heading2']))
        story.append(Spacer(1, 8))

        info_data = [
            ['Delivery Date:', str(r.delivery_date), 'Manifest #:', r.manifest_number],
            ['Logistics Officer:', r.logistics_officer or 'N/A', 'Vehicle:', r.vehicle_details or 'N/A'],
            ['Driver:', f"{r.driver_name or 'N/A'} ({r.driver_phone or 'N/A'})", 'Transport Mode:', (r.transport_mode or 'vehicle').title()],
            ['Transport Cost:', f"NGN {float(r.transport_cost or 0):,.2f}", 'Status:', r.status.upper()],
        ]
        info_table = Table(info_data, colWidths=[1.4*inch, 2.3*inch, 1.2*inch, 2.3*inch])
        info_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(info_table)
        story.append(Spacer(1, 16))

        for idx, c in enumerate(custs, 1):
            story.append(Paragraph(f"<b>Customer {idx}: {c.customer_name}</b>", styles['Heading3']))
            cust_info = f"Phone: {c.customer_phone or 'N/A'} | Address: {c.delivery_address or 'N/A'}"
            if c.city:
                cust_info += f", {c.city}"
            if c.state:
                cust_info += f", {c.state}"
            story.append(Paragraph(cust_info, styles['Normal']))
            story.append(Spacer(1, 4))

            items = (await session.execute(
                text("SELECT * FROM manifest_items WHERE manifest_customer_id = :mcid ORDER BY created_at"),
                {"mcid": str(c.id)}
            )).fetchall()

            item_data = [['#', 'Product', 'SKU', 'Qty', 'Unit']]
            for i, it in enumerate(items, 1):
                item_data.append([str(i), it.product_name, it.sku or '-', str(float(it.quantity)), it.unit or 'each'])

            if items:
                it_table = Table(item_data, colWidths=[0.4*inch, 3*inch, 1.2*inch, 0.8*inch, 0.8*inch])
                it_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, -1), 9),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('ALIGN', (1, 0), (1, -1), 'LEFT'),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                    ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8f9fa')),
                    ('TOPPADDING', (0, 0), (-1, -1), 4),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ]))
                story.append(it_table)

            story.append(Spacer(1, 8))

            if c.status == 'delivered' and c.receiver_name:
                conf_text = (
                    f"<b>Delivered</b> | Receiver: {c.receiver_name} "
                    f"({c.receiver_phone or 'N/A'}) | "
                    f"Invoice #: {c.physical_invoice_number or 'N/A'} | "
                    f"Time: {c.delivery_time.strftime('%Y-%m-%d %H:%M') if c.delivery_time else 'N/A'}"
                )
                story.append(Paragraph(conf_text, styles['Normal']))
            else:
                sig_data = [
                    ['Physical Invoice #:', '______________________', 'Date/Time:', '______________________'],
                    ['Receiver Name:', '______________________', 'Receiver Phone:', '______________________'],
                    ["Receiver's Signature:", '______________________', 'Officer Signature:', '______________________'],
                ]
                sig_table = Table(sig_data, colWidths=[1.4*inch, 2.1*inch, 1.3*inch, 2.1*inch])
                sig_table.setStyle(TableStyle([
                    ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                    ('FONTSIZE', (0, 0), (-1, -1), 9),
                    ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                    ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
                    ('TOPPADDING', (0, 0), (-1, -1), 6),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ]))
                story.append(sig_table)

            story.append(Spacer(1, 14))
            story.append(Table([['']],
                colWidths=[7.2*inch],
                style=[('LINEBELOW', (0, 0), (-1, -1), 1, colors.HexColor('#ddd'))]))
            story.append(Spacer(1, 8))

        story.append(Spacer(1, 10))
        story.append(Paragraph(
            "<i>This is a computer-generated delivery manifest from AstroBSM - Bonnesante Medicals.</i>",
            styles['Normal']
        ))

        doc.build(story)
        buffer.seek(0)

        return StreamingResponse(
            io.BytesIO(buffer.read()),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=Manifest-{r.manifest_number}.pdf"}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating manifest PDF: {str(e)}")


@router.get('/dashboard')
async def logistics_dashboard(session: AsyncSession = Depends(get_session)):
    """Get logistics summary dashboard using manifests."""
    try:
        stats = {}

        dq = await session.execute(text("""
            SELECT
                COUNT(*) as total_manifests,
                COUNT(*) FILTER (WHERE status = 'completed') as completed,
                COUNT(*) FILTER (WHERE status = 'preparing') as preparing,
                COUNT(*) FILTER (WHERE status IN ('dispatched','in_transit')) as in_transit,
                COUNT(*) FILTER (WHERE status = 'cancelled') as cancelled,
                COALESCE(SUM(total_cost), 0) as total_logistics_cost,
                COALESCE(AVG(total_cost), 0) as avg_manifest_cost
            FROM delivery_manifests
        """))
        dr = dq.fetchone()
        stats['summary'] = {
            "total_manifests": dr.total_manifests,
            "completed": dr.completed, "preparing": dr.preparing,
            "in_transit": dr.in_transit, "cancelled": dr.cancelled,
            "total_logistics_cost": float(dr.total_logistics_cost),
            "avg_manifest_cost": round(float(dr.avg_manifest_cost), 2),
        }

        cq = await session.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM manifest_customers) as total_drops,
                (SELECT COUNT(*) FROM manifest_customers WHERE status = 'delivered') as delivered_drops,
                (SELECT COALESCE(SUM(mi.quantity), 0) FROM manifest_items mi
                    JOIN manifest_customers mc ON mc.id = mi.manifest_customer_id
                    WHERE mc.status = 'delivered') as items_delivered
        """))
        cr = cq.fetchone()
        stats['delivery_stats'] = {
            "total_customer_drops": cr.total_drops,
            "delivered_drops": cr.delivered_drops,
            "items_delivered": float(cr.items_delivered),
        }

        oq = await session.execute(text("""
            SELECT logistics_officer,
                COUNT(*) as manifests,
                COALESCE(SUM(total_cost), 0) as total_cost
            FROM delivery_manifests
            GROUP BY logistics_officer ORDER BY manifests DESC LIMIT 10
        """))
        stats['by_officer'] = [
            {"officer": r.logistics_officer, "manifests": r.manifests,
             "total_cost": float(r.total_cost)}
            for r in oq.fetchall()
        ]

        mq = await session.execute(text("""
            SELECT TO_CHAR(delivery_date, 'YYYY-MM') as month,
                COUNT(*) as manifests,
                COALESCE(SUM(total_cost), 0) as total_cost
            FROM delivery_manifests
            WHERE delivery_date >= CURRENT_DATE - INTERVAL '6 months'
            GROUP BY TO_CHAR(delivery_date, 'YYYY-MM')
            ORDER BY month DESC
        """))
        stats['monthly_trend'] = [
            {"month": r.month, "manifests": r.manifests,
             "total_cost": float(r.total_cost)}
            for r in mq.fetchall()
        ]

        rq = await session.execute(text("""
            SELECT dm.id, dm.manifest_number, dm.logistics_officer, dm.delivery_date,
                dm.total_cost, dm.status,
                (SELECT COUNT(*) FROM manifest_customers WHERE manifest_id = dm.id) as customer_count
            FROM delivery_manifests dm ORDER BY dm.created_at DESC LIMIT 5
        """))
        stats['recent_manifests'] = [
            {
                "id": str(r.id),
                "manifest_number": r.manifest_number,
                "logistics_officer": r.logistics_officer,
                "delivery_date": str(r.delivery_date) if r.delivery_date else None,
                "total_cost": float(r.total_cost or 0),
                "status": r.status,
                "customer_count": r.customer_count,
            }
            for r in rq.fetchall()
        ]

        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/analytics')
async def logistics_analytics(
    date_from: str = None,
    date_to: str = None,
    session: AsyncSession = Depends(get_session)
):
    """Cost analytics for manifests."""
    try:
        where, params = [], {}
        if date_from:
            where.append("delivery_date >= :df"); params['df'] = date_from
        if date_to:
            where.append("delivery_date <= :dt"); params['dt'] = date_to
        wc = " AND ".join(where) if where else "1=1"

        tmq = await session.execute(text(f"""
            SELECT transport_mode,
                COUNT(*) as count,
                COALESCE(SUM(total_cost), 0) as total,
                COALESCE(AVG(total_cost), 0) as avg_cost
            FROM delivery_manifests
            WHERE {wc}
            GROUP BY transport_mode ORDER BY total DESC
        """), params)
        by_mode = [
            {"mode": r.transport_mode, "count": r.count,
             "total": float(r.total), "avg_cost": round(float(r.avg_cost), 2)}
            for r in tmq.fetchall()
        ]

        cq = await session.execute(text(f"""
            SELECT mc.customer_name, COUNT(DISTINCT mc.manifest_id) as manifests,
                COUNT(*) as drops
            FROM manifest_customers mc
            JOIN delivery_manifests dm ON dm.id = mc.manifest_id
            WHERE {wc.replace('delivery_date', 'dm.delivery_date')}
            GROUP BY mc.customer_name ORDER BY drops DESC LIMIT 10
        """), params)
        top_customers = [
            {"customer": r.customer_name, "manifests": r.manifests, "drops": r.drops}
            for r in cq.fetchall()
        ]

        dq = await session.execute(text(f"""
            SELECT TO_CHAR(delivery_date, 'Day') as day_name,
                EXTRACT(DOW FROM delivery_date) as dow,
                COUNT(*) as count,
                COALESCE(SUM(total_cost), 0) as total
            FROM delivery_manifests
            WHERE {wc}
            GROUP BY TO_CHAR(delivery_date, 'Day'), EXTRACT(DOW FROM delivery_date)
            ORDER BY dow
        """), params)
        by_day = [
            {"day": r.day_name.strip(), "count": r.count, "total": float(r.total)}
            for r in dq.fetchall()
        ]

        return {
            "by_transport_mode": by_mode,
            "top_customers": top_customers,
            "by_day_of_week": by_day,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
