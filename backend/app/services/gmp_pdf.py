"""GMP-compliant PDF generator for the Regulatory Compliance module.

Renders BONNESANTE MEDICALS / ASTROBSM-branded documents with:
  - diagonal watermark on every page
  - header with company name + controlled-copy stamp
  - footer with document number, version, page x/y, effective date
  - QR code on the cover for online verification
"""

from __future__ import annotations

import io
import os
from datetime import date, datetime
from typing import Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

try:
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderPDF
    _HAS_QR = True
except Exception:  # pragma: no cover
    _HAS_QR = False


COMPANY_NAME = "BONNESANTE MEDICALS"
TRADEMARK = "ASTROBSM"
WATERMARK_TEXT = f"{COMPANY_NAME}  |  {TRADEMARK}  |  CONTROLLED COPY"

# Locate the company logo (best-effort — falls back to text only)
def _locate_logo() -> Optional[str]:
    candidates = [
        "/app/frontend/build/company-logo.png",
        "/app/frontend/public/company-logo.png",
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "frontend", "public", "company-logo.png"),
    ]
    for p in candidates:
        try:
            if p and os.path.isfile(p):
                return p
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Page decorators (watermark + header + footer)
# ---------------------------------------------------------------------------

def _make_page_decorator(meta: Dict[str, str]):
    logo_path = _locate_logo()

    def _decorate(canv: canvas.Canvas, doc: BaseDocTemplate):
        canv.saveState()
        w, h = A4

        # --- diagonal watermark ---------------------------------------------
        canv.setFont("Helvetica-Bold", 48)
        canv.setFillColor(colors.HexColor("#1d4ed8"))
        try:
            canv.setFillAlpha(0.08)
        except Exception:
            pass
        canv.translate(w / 2, h / 2)
        canv.rotate(35)
        canv.drawCentredString(0, 0, WATERMARK_TEXT)
        canv.restoreState()

        # --- header ---------------------------------------------------------
        canv.saveState()
        canv.setStrokeColor(colors.HexColor("#1d4ed8"))
        canv.setLineWidth(0.8)
        canv.line(15 * mm, h - 22 * mm, w - 15 * mm, h - 22 * mm)

        if logo_path:
            try:
                canv.drawImage(logo_path, 15 * mm, h - 20 * mm, width=18 * mm, height=14 * mm,
                               preserveAspectRatio=True, mask='auto')
            except Exception:
                pass
        canv.setFont("Helvetica-Bold", 10)
        canv.setFillColor(colors.HexColor("#0f172a"))
        canv.drawString(36 * mm, h - 12 * mm, f"{COMPANY_NAME}  |  {TRADEMARK}")
        canv.setFont("Helvetica", 8)
        canv.setFillColor(colors.HexColor("#475569"))
        canv.drawString(36 * mm, h - 17 * mm, "Wound-Care Pharmaceutical Manufacturing — GMP Controlled Document")

        # Controlled-copy stamp top-right
        canv.setStrokeColor(colors.HexColor("#dc2626"))
        canv.setFillColor(colors.HexColor("#dc2626"))
        canv.setLineWidth(1.2)
        stamp = "CONTROLLED COPY"
        canv.rect(w - 60 * mm, h - 20 * mm, 45 * mm, 10 * mm, stroke=1, fill=0)
        canv.setFont("Helvetica-Bold", 9)
        canv.drawCentredString(w - 37.5 * mm, h - 16 * mm, stamp)
        canv.restoreState()

        # --- footer ---------------------------------------------------------
        canv.saveState()
        canv.setStrokeColor(colors.HexColor("#94a3b8"))
        canv.setLineWidth(0.4)
        canv.line(15 * mm, 18 * mm, w - 15 * mm, 18 * mm)
        canv.setFont("Helvetica", 7.5)
        canv.setFillColor(colors.HexColor("#475569"))
        doc_no = meta.get("doc_number", "—")
        version = meta.get("version", "1.0")
        eff = meta.get("effective_date", "—")
        left = f"Doc No: {doc_no}    Version: {version}    Effective: {eff}"
        canv.drawString(15 * mm, 13 * mm, left)
        canv.drawRightString(w - 15 * mm, 13 * mm, f"Page {canv.getPageNumber()}")
        canv.drawCentredString(w / 2, 8 * mm, f"{COMPANY_NAME}  |  {TRADEMARK}   —   Uncontrolled when printed without QA stamp")
        canv.restoreState()

    return _decorate


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

def _styles():
    ss = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontName="Helvetica-Bold",
                              fontSize=16, textColor=colors.HexColor("#0f172a"),
                              spaceAfter=8, alignment=1),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                              fontSize=11, textColor=colors.HexColor("#1d4ed8"),
                              spaceBefore=8, spaceAfter=4),
        "body": ParagraphStyle("body", parent=ss["BodyText"], fontName="Helvetica",
                                fontSize=9.5, leading=13, textColor=colors.HexColor("#0f172a"),
                                spaceAfter=4),
        "meta_label": ParagraphStyle("meta_label", parent=ss["BodyText"], fontName="Helvetica-Bold",
                                      fontSize=9, textColor=colors.HexColor("#475569")),
        "meta_value": ParagraphStyle("meta_value", parent=ss["BodyText"], fontName="Helvetica",
                                      fontSize=9, textColor=colors.HexColor("#0f172a")),
    }


def _qr_flowable(text: str, size_mm: float = 22):
    if not _HAS_QR or not text:
        return Spacer(1, 1)
    qr = QrCodeWidget(text)
    b = qr.getBounds()
    w = b[2] - b[0]
    h = b[3] - b[1]
    side = size_mm * mm
    d = Drawing(side, side, transform=[side / w, 0, 0, side / h, 0, 0])
    d.add(qr)
    return d


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------

def build_document_pdf(doc_meta: Dict, content: Dict, signatures: Optional[List[Dict]] = None,
                       verify_url: Optional[str] = None) -> bytes:
    """Build the PDF and return its bytes.

    doc_meta keys: doc_number, doc_type, title, version, status, category,
                   effective_date, review_date, author, owner.
    content     : {'title': str, 'sections': [{'heading','body'}, ...]}
    signatures  : [{'role','name','signed_at','meaning'}]
    """
    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=28 * mm, bottomMargin=22 * mm,
        title=f"{doc_meta.get('doc_number','')} - {doc_meta.get('title','')}",
        author=COMPANY_NAME,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin,
                  doc.width, doc.height, id="main")
    decorate = _make_page_decorator({
        "doc_number": doc_meta.get("doc_number", "—"),
        "version": str(doc_meta.get("version", "1.0")),
        "effective_date": str(doc_meta.get("effective_date") or "—"),
    })
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=decorate)])

    S = _styles()
    story = []

    # ---- Cover block --------------------------------------------------------
    story.append(Paragraph(doc_meta.get("title") or content.get("title") or "Controlled Document",
                            S["h1"]))
    subtitle = f"{doc_meta.get('doc_type','SOP')} — {doc_meta.get('category','')}"
    story.append(Paragraph(subtitle, ParagraphStyle("sub", parent=S["body"],
                                                     alignment=1, fontSize=10,
                                                     textColor=colors.HexColor("#475569"))))
    story.append(Spacer(1, 8))

    # Meta table (left) + QR (right)
    meta_rows = [
        ["Document No.", doc_meta.get("doc_number", "—"),
         "Version", str(doc_meta.get("version", "1.0"))],
        ["Status", doc_meta.get("status", "DRAFT"),
         "Category", doc_meta.get("category", "—")],
        ["Effective Date", str(doc_meta.get("effective_date") or "—"),
         "Review Date", str(doc_meta.get("review_date") or "—")],
        ["Owner", doc_meta.get("owner") or "—",
         "Author", doc_meta.get("author") or "—"],
    ]
    meta_table = Table(meta_rows, colWidths=[28 * mm, 50 * mm, 25 * mm, 35 * mm])
    meta_table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
        ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9),
        ("FONT", (2, 0), (2, -1), "Helvetica-Bold", 9),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#475569")),
        ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#475569")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    qr = _qr_flowable(verify_url or doc_meta.get("doc_number", ""), size_mm=26)
    cover_grid = Table([[meta_table, qr]], colWidths=[138 * mm, 36 * mm])
    cover_grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(cover_grid)
    story.append(Spacer(1, 10))

    # ---- Sections -----------------------------------------------------------
    for s in content.get("sections", []):
        heading = s.get("heading", "")
        body = (s.get("body") or "").strip()
        if heading:
            story.append(Paragraph(heading, S["h2"]))
        if body:
            for para in body.split("\n"):
                if para.strip():
                    story.append(Paragraph(para.replace(" ", "&nbsp;").replace("&nbsp;", " ", 1)
                                            if para.startswith(" ") else para,
                                            S["body"]))
                else:
                    story.append(Spacer(1, 2))

    # ---- Approvals / E-signatures -------------------------------------------
    sigs = signatures or []
    if sigs:
        story.append(Spacer(1, 10))
        story.append(Paragraph("APPROVALS / ELECTRONIC SIGNATURES", S["h2"]))
        sig_rows = [["Role", "Name", "Meaning", "Date / Time (UTC)"]]
        for s in sigs:
            sig_rows.append([
                s.get("role", ""),
                s.get("name", ""),
                s.get("meaning", "Approved"),
                str(s.get("signed_at") or ""),
            ])
        sig_table = Table(sig_rows, colWidths=[35 * mm, 50 * mm, 40 * mm, 49 * mm])
        sig_table.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1d4ed8")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#94a3b8")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(sig_table)

    doc.build(story)
    return buf.getvalue()
