import io
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)

from app.api.sop_library import get_sop_templates
from app.db import get_session
from app.models import SOPExecutionLog, SOPTemplate
from app.schemas import (
    SOPApprovalUpdate,
    SOPDeviationUpdate,
    SOPExecutionCreate,
    SOPExecutionSchema,
    SOPTemplateSchema,
)

router = APIRouter(prefix="/api/sop", tags=["sop"])


async def _ensure_templates_seeded(session: AsyncSession) -> None:
    templates = get_sop_templates()
    existing = await session.execute(select(SOPTemplate.sop_code))
    existing_codes = {row[0] for row in existing.all()}

    changed = False
    for t in templates:
        if t["sop_code"] not in existing_codes:
            session.add(
                SOPTemplate(
                    sop_code=t["sop_code"],
                    title=t["title"],
                    sop_number=t["sop_number"],
                    version=t["version"],
                    effective_date=datetime.fromisoformat(t["effective_date"]).date(),
                    document=t["document"],
                    form_schema=t["form_schema"],
                    db_table_structure=t["db_table_structure"],
                    validation_rules=t["validation_rules"],
                    is_active=True,
                )
            )
            changed = True

    if changed:
        await session.commit()


def _validate_execution_payload(template: SOPTemplate, payload: SOPExecutionCreate) -> List[str]:
    errors: List[str] = []
    form_schema = template.form_schema or {}
    form_data = payload.form_data or {}

    fields = form_schema.get("fields", [])
    for f in fields:
        if f.get("required") and not form_data.get(f.get("name")):
            errors.append(f"{f.get('name')} is required")

    checklist_items = form_schema.get("checklist", [])
    checklist_data = form_data.get("checklist", {})
    for item in checklist_items:
        if item.get("required") and checklist_data.get(item.get("name")) is not True:
            errors.append(f"Checklist item '{item.get('name')}' must be checked")

    numeric_items = form_schema.get("numeric_fields", [])
    numeric_data = form_data.get("numeric_inputs", {})
    for nf in numeric_items:
        name = nf.get("name")
        raw = numeric_data.get(name)
        if nf.get("required") and raw in (None, ""):
            errors.append(f"Numeric field '{name}' is required")
            continue
        if raw in (None, ""):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            errors.append(f"Numeric field '{name}' must be a number")
            continue

        if nf.get("min") is not None and value < float(nf["min"]):
            errors.append(f"Numeric field '{name}' must be >= {nf['min']}")
        if nf.get("max") is not None and value > float(nf["max"]):
            errors.append(f"Numeric field '{name}' must be <= {nf['max']}")

    if payload.deviation and len(payload.deviation.strip()) < 10:
        errors.append("deviation must be at least 10 characters when provided")

    return errors


@router.get("/templates", response_model=List[SOPTemplateSchema])
async def list_sop_templates(session: AsyncSession = Depends(get_session)):
    await _ensure_templates_seeded(session)
    q = select(SOPTemplate).where(SOPTemplate.is_active == True).order_by(SOPTemplate.sop_number)
    result = await session.execute(q)
    return result.scalars().all()


@router.get("/templates/{sop_code}", response_model=SOPTemplateSchema)
async def get_sop_template(sop_code: str, session: AsyncSession = Depends(get_session)):
    await _ensure_templates_seeded(session)
    q = select(SOPTemplate).where(SOPTemplate.sop_code == sop_code)
    result = await session.execute(q)
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="SOP template not found")
    return template


@router.post("/records", response_model=SOPExecutionSchema, status_code=201)
async def create_sop_record(payload: SOPExecutionCreate, session: AsyncSession = Depends(get_session)):
    await _ensure_templates_seeded(session)
    q = select(SOPTemplate).where(SOPTemplate.sop_code == payload.sop_code, SOPTemplate.is_active == True)
    result = await session.execute(q)
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="SOP template not found")

    errors = _validate_execution_payload(template, payload)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    form_data = payload.form_data or {}
    record = SOPExecutionLog(
        template_id=template.id,
        sop_code=payload.sop_code,
        operator_name=form_data.get("operator_name"),
        supervisor_name=form_data.get("supervisor_name"),
        executed_at=datetime.fromisoformat(form_data.get("executed_at").replace("Z", "+00:00")),
        batch_number=form_data.get("batch_number"),
        material_equipment_used={"text": form_data.get("material_equipment_used", "")},
        checklist=form_data.get("checklist", {}),
        numeric_inputs=form_data.get("numeric_inputs", {}),
        operator_signature=form_data.get("operator_signature"),
        supervisor_signature=form_data.get("supervisor_signature"),
        comments=payload.comments or form_data.get("comments"),
        deviation=payload.deviation or form_data.get("deviation"),
        status="submitted" if not (payload.deviation or form_data.get("deviation")) else "deviation_raised",
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


@router.get("/records", response_model=List[SOPExecutionSchema])
async def list_sop_records(
    sop_code: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    q = select(SOPExecutionLog)
    if sop_code:
        q = q.where(SOPExecutionLog.sop_code == sop_code)
    if status:
        q = q.where(SOPExecutionLog.status == status)
    q = q.order_by(desc(SOPExecutionLog.created_at)).limit(limit)
    result = await session.execute(q)
    return result.scalars().all()


@router.patch("/records/{record_id}/approve", response_model=SOPExecutionSchema)
async def approve_sop_record(record_id: UUID, payload: SOPApprovalUpdate, session: AsyncSession = Depends(get_session)):
    q = select(SOPExecutionLog).where(SOPExecutionLog.id == record_id)
    result = await session.execute(q)
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="SOP record not found")

    record.status = "approved"
    record.approved_by = payload.approved_by
    record.approved_at = datetime.utcnow()
    if payload.comments:
        record.comments = (record.comments or "") + f"\nApproval note: {payload.comments}"

    await session.commit()
    await session.refresh(record)
    return record


@router.patch("/records/{record_id}/deviation", response_model=SOPExecutionSchema)
async def raise_deviation(record_id: UUID, payload: SOPDeviationUpdate, session: AsyncSession = Depends(get_session)):
    q = select(SOPExecutionLog).where(SOPExecutionLog.id == record_id)
    result = await session.execute(q)
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="SOP record not found")

    record.status = "deviation_raised"
    record.deviation = payload.deviation
    if payload.comments:
        record.comments = (record.comments or "") + f"\nDeviation note: {payload.comments}"

    await session.commit()
    await session.refresh(record)
    return record


def _build_sop_pdf(template: SOPTemplate) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
        title=f"{template.sop_number} - {template.title}",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('SOPTitle', parent=styles['Title'],
                                 fontSize=16, textColor=colors.HexColor('#1e293b'),
                                 spaceAfter=10, alignment=1)
    h2 = ParagraphStyle('SOPH2', parent=styles['Heading2'],
                        fontSize=12, textColor=colors.HexColor('#4f46e5'),
                        spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle('SOPBody', parent=styles['BodyText'],
                          fontSize=10, leading=13, textColor=colors.HexColor('#0f172a'))
    small = ParagraphStyle('SOPSmall', parent=styles['BodyText'],
                           fontSize=9, leading=11, textColor=colors.HexColor('#475569'))

    story = []
    story.append(Paragraph("ASTRO-BSM PHARMACEUTICALS — STANDARD OPERATING PROCEDURE", small))
    story.append(Paragraph(template.title, title_style))

    meta_data = [
        ['SOP Number', template.sop_number, 'Version', template.version],
        ['SOP Code', template.sop_code, 'Effective Date', str(template.effective_date)],
    ]
    meta = Table(meta_data, colWidths=[3.2*cm, 5.5*cm, 3.2*cm, 5.0*cm])
    meta.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#cbd5e1')),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f1f5f9')),
        ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#f1f5f9')),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(meta)
    story.append(Spacer(1, 8))

    def esc(s: str) -> str:
        return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))

    def render_value(value, indent: int = 0) -> None:
        prefix = '&nbsp;' * (indent * 4)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    for k, v in item.items():
                        if isinstance(v, (list, dict)):
                            story.append(Paragraph(
                                f"{prefix}• <b>{esc(k.replace('_', ' ').title())}:</b>", body))
                            render_value(v, indent + 1)
                        else:
                            story.append(Paragraph(
                                f"{prefix}• <b>{esc(k.replace('_', ' ').title())}:</b> {esc(v)}",
                                body))
                else:
                    story.append(Paragraph(f"{prefix}• {esc(item)}", body))
        elif isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, (list, dict)):
                    story.append(Paragraph(
                        f"{prefix}<b>{esc(k.replace('_', ' ').title())}:</b>", body))
                    render_value(v, indent + 1)
                else:
                    story.append(Paragraph(
                        f"{prefix}<b>{esc(k.replace('_', ' ').title())}:</b> {esc(v)}", body))
        else:
            story.append(Paragraph(f"{prefix}{esc(value)}", body))

    document = template.document or {}
    for section_key, section_value in document.items():
        story.append(Paragraph(esc(section_key.replace('_', ' ').title()), h2))
        render_value(section_value)
        story.append(Spacer(1, 4))

    rules = template.validation_rules or []
    if rules:
        story.append(Paragraph("Validation Rules", h2))
        for r in rules:
            story.append(Paragraph(f"• {esc(r)}", body))

    story.append(Spacer(1, 14))
    sig_data = [
        ['Prepared By', 'Reviewed By', 'Approved By'],
        ['', '', ''],
        ['Sign / Date', 'Sign / Date', 'Sign / Date'],
    ]
    sig = Table(sig_data, colWidths=[5.5*cm, 5.5*cm, 5.5*cm], rowHeights=[0.7*cm, 1.6*cm, 0.7*cm])
    sig.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#cbd5e1')),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f8fafc')),
        ('BACKGROUND', (0, 2), (-1, 2), colors.HexColor('#f8fafc')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(sig)

    footer = ParagraphStyle('SOPFooter', parent=small, alignment=1, fontSize=8,
                            textColor=colors.HexColor('#94a3b8'))
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        f"Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} • "
        f"NAFDAC / WHO GMP compliant • Confidential — Internal Use Only",
        footer))

    doc.build(story)
    return buf.getvalue()


@router.get("/templates/{sop_code}/pdf")
async def download_sop_pdf(sop_code: str, session: AsyncSession = Depends(get_session)):
    await _ensure_templates_seeded(session)
    q = select(SOPTemplate).where(SOPTemplate.sop_code == sop_code)
    result = await session.execute(q)
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="SOP template not found")

    pdf_bytes = _build_sop_pdf(template)
    filename = f"{template.sop_number}_{template.title}".replace(' ', '_').replace('/', '-')
    filename = ''.join(c for c in filename if c.isalnum() or c in ('_', '-')) + '.pdf'

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


def _build_record_pdf(record: SOPExecutionLog, template: Optional[SOPTemplate]) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
        title=f"SOP Record {record.batch_number}",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('RecTitle', parent=styles['Title'],
                                 fontSize=15, textColor=colors.HexColor('#1e293b'),
                                 spaceAfter=8, alignment=1)
    h2 = ParagraphStyle('RecH2', parent=styles['Heading2'],
                        fontSize=12, textColor=colors.HexColor('#4f46e5'),
                        spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle('RecBody', parent=styles['BodyText'],
                          fontSize=10, leading=13, textColor=colors.HexColor('#0f172a'))
    small = ParagraphStyle('RecSmall', parent=styles['BodyText'],
                           fontSize=9, leading=11, textColor=colors.HexColor('#475569'))

    def esc(s) -> str:
        return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))

    story = []
    story.append(Paragraph("ASTRO-BSM PHARMACEUTICALS — SOP EXECUTION RECORD", small))
    title_text = template.title if template else record.sop_code
    story.append(Paragraph(esc(title_text), title_style))

    sop_number = template.sop_number if template else record.sop_code
    sop_version = template.version if template else '—'
    header_data = [
        ['SOP Number', esc(sop_number), 'SOP Code', esc(record.sop_code)],
        ['Version', esc(sop_version), 'Batch Number', esc(record.batch_number)],
        ['Executed At', esc(record.executed_at.strftime('%Y-%m-%d %H:%M')) if record.executed_at else '—',
         'Status', esc(record.status)],
        ['Operator', esc(record.operator_name), 'Supervisor', esc(record.supervisor_name)],
    ]
    header = Table(header_data, colWidths=[3.2*cm, 5.5*cm, 3.2*cm, 5.0*cm])
    header.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#cbd5e1')),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f1f5f9')),
        ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#f1f5f9')),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(header)
    story.append(Spacer(1, 8))

    me = record.material_equipment_used or {}
    me_text = me.get('text') if isinstance(me, dict) else str(me)
    if me_text:
        story.append(Paragraph("Materials / Equipment Used", h2))
        story.append(Paragraph(esc(me_text), body))

    checklist = record.checklist or {}
    if checklist:
        story.append(Paragraph("Checklist", h2))
        rows = [['Item', 'Status']]
        for k, v in checklist.items():
            rows.append([esc(k.replace('_', ' ').title()),
                         'Checked' if v is True else ('Not Checked' if v is False else esc(v))])
        ct = Table(rows, colWidths=[11*cm, 5.5*cm])
        ct.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#cbd5e1')),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eef2ff')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(ct)
        story.append(Spacer(1, 6))

    numerics = record.numeric_inputs or {}
    if numerics:
        story.append(Paragraph("Numeric Measurements", h2))
        rows = [['Field', 'Value']]
        for k, v in numerics.items():
            rows.append([esc(k.replace('_', ' ').title()), esc(v)])
        nt = Table(rows, colWidths=[11*cm, 5.5*cm])
        nt.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#cbd5e1')),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f0f9ff')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(nt)
        story.append(Spacer(1, 6))

    if record.comments:
        story.append(Paragraph("Comments", h2))
        story.append(Paragraph(esc(record.comments), body))

    if record.deviation:
        story.append(Paragraph("Deviation", h2))
        story.append(Paragraph(esc(record.deviation), body))

    story.append(Spacer(1, 14))
    sig_data = [
        ['Operator Signature', 'Supervisor Signature', 'QA / Approver'],
        [esc(record.operator_signature or ''),
         esc(record.supervisor_signature or ''),
         esc(record.approved_by or '')],
        ['',
         '',
         esc(record.approved_at.strftime('%Y-%m-%d %H:%M')) if record.approved_at else 'Pending'],
    ]
    sig = Table(sig_data, colWidths=[5.5*cm, 5.5*cm, 5.5*cm], rowHeights=[0.7*cm, 1.4*cm, 0.6*cm])
    sig.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#cbd5e1')),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f8fafc')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(sig)

    footer = ParagraphStyle('RecFooter', parent=small, alignment=1, fontSize=8,
                            textColor=colors.HexColor('#94a3b8'))
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        f"Record ID: {record.id} • Submitted {record.created_at.strftime('%Y-%m-%d %H:%M UTC') if record.created_at else '—'} • "
        f"Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} • NAFDAC / WHO GMP compliant",
        footer))

    doc.build(story)
    return buf.getvalue()


@router.get("/records/{record_id}/pdf")
async def download_sop_record_pdf(record_id: UUID, session: AsyncSession = Depends(get_session)):
    q = select(SOPExecutionLog).where(SOPExecutionLog.id == record_id)
    result = await session.execute(q)
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="SOP record not found")

    tq = select(SOPTemplate).where(SOPTemplate.id == record.template_id)
    tres = await session.execute(tq)
    template = tres.scalar_one_or_none()

    pdf_bytes = _build_record_pdf(record, template)
    base = f"{record.sop_code}_{record.batch_number}"
    filename = ''.join(c for c in base if c.isalnum() or c in ('_', '-')) + '.pdf'

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )




