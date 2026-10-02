"""Payslips and the payment report, as PDFs people can file.

TWO DOCUMENTS, TWO AUDIENCES
============================
A payslip goes to one member of staff and has to be checkable BY THEM: every
earning and every deduction itemised, with the rate that produced it, because
"trust me, this is your tax" is not an answer anybody should have to accept.
The real payroll engine already stores that breakdown in `payslip_components`;
until now nothing rendered it, and the only payslip PDF in this system was
built on the retired calculator that deducted nothing at all.

The payment report goes to whoever signs the transfers and to whoever audits
them afterwards. Its job is to state one number -- the total to be paid for
the period -- without letting it be misread, which means showing the employer's
own contributions separately (the company pays them; staff never see them) and
showing what is still outstanding.

ON THE NAIRA SIGN
=================
ReportLab's Helvetica is a Latin-1 font with no glyph for the naira sign, so a
bare symbol prints as a black box. These documents use the ISO code, which is
also what a bank statement shows.
"""
from __future__ import annotations

import io
import os
from datetime import date, datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

NAIRA = 'NGN '

NAVY = colors.HexColor('#0B1F4A')
ROYAL = colors.HexColor('#1F4E9E')
GREY = colors.HexColor('#64748B')
LINE = colors.HexColor('#D8DEE9')
GREEN = colors.HexColor('#16A34A')
AMBER = colors.HexColor('#B45309')

COMPANY = 'BONNESANTE MEDICALS'
PAGE_W, PAGE_H = A4


def naira(value) -> str:
    return f'{NAIRA}{float(value or 0):,.2f}'


def _logo_path():
    for p in ('/app/company-logo.png', '/app/frontend/build/company-logo.png'):
        if os.path.exists(p):
            return p
    return None


def _letterhead(c, title: str, subtitle: str = '') -> float:
    """Draw the heading and return the y to start writing at.

    The logo sits small and top-right, in line with the text rather than
    above it, which is the arrangement adopted for invoices.
    """
    y = PAGE_H - 18 * mm

    logo = _logo_path()
    if logo:
        try:
            c.drawImage(logo, PAGE_W - 20 * mm - 22 * mm, y - 6 * mm,
                        width=22 * mm, height=22 * mm,
                        preserveAspectRatio=True, mask='auto',
                        anchor='ne')
        except Exception:
            pass  # a missing logo must never cost somebody their payslip

    c.setFillColor(NAVY)
    c.setFont('Helvetica-Bold', 15)
    c.drawString(20 * mm, y, COMPANY)

    c.setFillColor(ROYAL)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(20 * mm, y - 6.5 * mm, title)

    if subtitle:
        c.setFillColor(GREY)
        c.setFont('Helvetica', 8.5)
        c.drawString(20 * mm, y - 11.5 * mm, subtitle)

    c.setStrokeColor(LINE)
    c.setLineWidth(0.8)
    c.line(20 * mm, y - 15 * mm, PAGE_W - 20 * mm, y - 15 * mm)
    return y - 22 * mm


def _footer(c, note: str = ''):
    c.setFillColor(GREY)
    c.setFont('Helvetica', 7)
    c.drawString(20 * mm, 12 * mm,
                 f'Generated {datetime.now().strftime("%d %b %Y %H:%M")}'
                 f' · {COMPANY}')
    if note:
        c.drawRightString(PAGE_W - 20 * mm, 12 * mm, note)


# ---------------------------------------------------------------------------
# Payslips
# ---------------------------------------------------------------------------

def _draw_payslip(c, slip: dict, period: dict):
    """One payslip, one page.

    One page each is deliberate even though two would fit: a payslip is handed
    to an individual, and a sheet carrying a colleague's pay on the other half
    cannot be handed to anybody.
    """
    y = _letterhead(
        c, 'PAYSLIP',
        f'Pay period {period["start"]} to {period["end"]}'
        f'  ·  {slip["payslip_number"]}')

    # --- who ---------------------------------------------------------------
    c.setFillColor(colors.HexColor('#F6F8FC'))
    c.rect(20 * mm, y - 20 * mm, PAGE_W - 40 * mm, 18 * mm, fill=1, stroke=0)

    c.setFillColor(NAVY)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(24 * mm, y - 7 * mm, slip['name'])
    c.setFillColor(GREY)
    c.setFont('Helvetica', 8.5)
    c.drawString(24 * mm, y - 12 * mm,
                 f'{slip["employee_id"]}  ·  {slip.get("position") or "Staff"}')
    c.drawString(24 * mm, y - 16.5 * mm,
                 f'{slip.get("bank_name") or "Bank not on record"}'
                 f'  ·  {slip.get("bank_account_number") or "-"}')

    if slip.get('paid_on'):
        c.setFillColor(GREEN)
        c.setFont('Helvetica-Bold', 9)
        c.drawRightString(PAGE_W - 24 * mm, y - 7 * mm, 'PAID')
        c.setFillColor(GREY)
        c.setFont('Helvetica', 8)
        c.drawRightString(PAGE_W - 24 * mm, y - 12 * mm,
                          f'{slip["paid_on"]} · {slip.get("method") or ""}')
        if slip.get('batch_number'):
            c.drawRightString(PAGE_W - 24 * mm, y - 16.5 * mm,
                              slip['batch_number'])
    else:
        c.setFillColor(AMBER)
        c.setFont('Helvetica-Bold', 9)
        c.drawRightString(PAGE_W - 24 * mm, y - 7 * mm, 'NOT YET PAID')

    y -= 28 * mm

    # --- the breakdown -----------------------------------------------------
    earnings = [x for x in slip.get('components', [])
                if x['type'] == 'EARNING']
    deductions = [x for x in slip.get('components', [])
                  if x['type'] == 'DEDUCTION']
    employer = [x for x in slip.get('components', [])
                if x['type'] == 'EMPLOYER_CONTRIBUTION']

    def section(title, rows, start_y, total_label, total_value):
        yy = start_y
        c.setFillColor(ROYAL)
        c.setFont('Helvetica-Bold', 9)
        c.drawString(20 * mm, yy, title.upper())
        yy -= 5 * mm
        c.setStrokeColor(LINE)
        c.setLineWidth(0.5)
        c.line(20 * mm, yy + 1.5 * mm, PAGE_W - 20 * mm, yy + 1.5 * mm)

        c.setFont('Helvetica', 9)
        if not rows:
            c.setFillColor(GREY)
            c.drawString(22 * mm, yy - 3 * mm, 'None')
            yy -= 8 * mm
        for r in rows:
            c.setFillColor(colors.black)
            c.drawString(22 * mm, yy - 3 * mm, r['label'])
            # The rate that produced the figure, where there is one. A
            # deduction with no stated basis cannot be checked by the person
            # it was taken from.
            if r.get('rate_applied'):
                c.setFillColor(GREY)
                c.setFont('Helvetica', 7.5)
                basis = (f' of {naira(r["basis_amount"])}'
                         if r.get('basis_amount') else '')
                c.drawString(22 * mm, yy - 7 * mm,
                             f'{float(r["rate_applied"]):g}%{basis}')
                c.setFont('Helvetica', 9)
                yy -= 4 * mm
            c.setFillColor(colors.black)
            c.drawRightString(PAGE_W - 22 * mm, yy - 3 * mm, naira(r['amount']))
            yy -= 6.5 * mm

        c.setStrokeColor(LINE)
        c.line(20 * mm, yy, PAGE_W - 20 * mm, yy)
        c.setFillColor(NAVY)
        c.setFont('Helvetica-Bold', 9.5)
        c.drawString(22 * mm, yy - 5.5 * mm, total_label)
        c.drawRightString(PAGE_W - 22 * mm, yy - 5.5 * mm, naira(total_value))
        return yy - 12 * mm

    if slip.get('regular_hours') or slip.get('overtime_hours'):
        c.setFillColor(GREY)
        c.setFont('Helvetica', 8.5)
        c.drawString(20 * mm, y,
                     f'Hours: {float(slip.get("regular_hours") or 0):.1f} regular'
                     f'  ·  {float(slip.get("overtime_hours") or 0):.1f} overtime')
        y -= 7 * mm

    y = section('Earnings', earnings, y, 'Gross pay', slip['gross_pay'])
    y = section('Deductions', deductions, y, 'Total deductions',
                slip['total_deductions'])

    # --- what they actually get -------------------------------------------
    c.setFillColor(NAVY)
    c.rect(20 * mm, y - 13 * mm, PAGE_W - 40 * mm, 13 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(24 * mm, y - 8.5 * mm, 'NET PAY')
    c.setFont('Helvetica-Bold', 13)
    c.drawRightString(PAGE_W - 24 * mm, y - 8.5 * mm, naira(slip['net_pay']))
    y -= 20 * mm

    if employer:
        y = section('Paid by the company on your behalf (not deducted from you)',
                    employer, y, 'Employer contributions',
                    slip.get('employer_contributions') or 0)

    c.setFillColor(GREY)
    c.setFont('Helvetica', 7.5)
    c.drawString(20 * mm, y,
                 'This payslip is computer generated. If any figure looks '
                 'wrong, raise it with HR before the next pay run.')

    _footer(c, slip['payslip_number'])


def payslip_pdf(slips: list, period: dict) -> bytes:
    """One PDF, one payslip per page, in employee-id order."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f'Payslips {period["start"]} to {period["end"]}')
    for slip in slips:
        _draw_payslip(c, slip, period)
        c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
# Payment report
# ---------------------------------------------------------------------------

def payment_report_pdf(report: dict) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f'Payroll payment report {report["period_start"]}')

    y = _letterhead(
        c, 'PAYROLL PAYMENT REPORT',
        f'Pay period {report["period_start"]} to {report["period_end"]}'
        f'  ·  {report["staff_count"]} staff processed')

    # --- the headline figures ---------------------------------------------
    tiles = [
        ('Net payable to staff', report['net_total'], NAVY),
        ('Already paid', report['paid_total'], GREEN),
        ('Still outstanding', report['outstanding_total'],
         AMBER if report['outstanding_total'] else GREY),
    ]
    tile_w = (PAGE_W - 40 * mm - 8 * mm) / 3
    for i, (label, value, col) in enumerate(tiles):
        x = 20 * mm + i * (tile_w + 4 * mm)
        c.setFillColor(colors.HexColor('#F6F8FC'))
        c.rect(x, y - 17 * mm, tile_w, 17 * mm, fill=1, stroke=0)
        c.setFillColor(col)
        c.setFont('Helvetica-Bold', 12)
        c.drawString(x + 4 * mm, y - 8 * mm, naira(value))
        c.setFillColor(GREY)
        c.setFont('Helvetica', 7.5)
        c.drawString(x + 4 * mm, y - 13 * mm, label)
    y -= 24 * mm

    # Gross, deductions and employer cost, stated so the net above cannot be
    # mistaken for what the month costs the company.
    c.setFillColor(GREY)
    c.setFont('Helvetica', 8.5)
    c.drawString(20 * mm, y,
                 f'Gross {naira(report["gross_total"])}'
                 f'   less deductions {naira(report["deductions_total"])}'
                 f'   = net {naira(report["net_total"])}')
    y -= 5 * mm
    c.drawString(20 * mm, y,
                 f'Employer contributions {naira(report["employer_cost_total"])}'
                 f' are paid by the company in addition and are not deducted '
                 f'from anyone.')
    y -= 5 * mm
    total_cost = (float(report['gross_total'])
                  + float(report['employer_cost_total']))
    c.setFillColor(NAVY)
    c.setFont('Helvetica-Bold', 9)
    c.drawString(20 * mm, y, f'Total cost of employment for the period: '
                             f'{naira(total_cost)}')
    y -= 10 * mm

    # --- deductions summary -----------------------------------------------
    if report.get('deduction_breakdown'):
        c.setFillColor(ROYAL)
        c.setFont('Helvetica-Bold', 9)
        c.drawString(20 * mm, y, 'WITHHELD AND OWED TO THIRD PARTIES')
        y -= 6 * mm
        c.setFont('Helvetica', 8.5)
        for b in report['deduction_breakdown']:
            c.setFillColor(colors.black)
            tag = ' (employer)' if b['type'] == 'EMPLOYER_CONTRIBUTION' else ''
            c.drawString(22 * mm, y, f'{b["label"]}{tag}')
            c.drawRightString(PAGE_W - 22 * mm, y, naira(b['total']))
            y -= 5 * mm
        y -= 4 * mm

    # --- per-person -------------------------------------------------------
    def header_row(yy):
        c.setFillColor(ROYAL)
        c.setFont('Helvetica-Bold', 7.5)
        c.drawString(20 * mm, yy, 'EMPLOYEE')
        c.drawString(62 * mm, yy, 'BANK / ACCOUNT')
        c.drawRightString(135 * mm, yy, 'GROSS')
        c.drawRightString(160 * mm, yy, 'DEDUCTIONS')
        c.drawRightString(182 * mm, yy, 'NET')
        c.drawRightString(PAGE_W - 20 * mm, yy, 'STATUS')
        c.setStrokeColor(LINE)
        c.setLineWidth(0.5)
        c.line(20 * mm, yy - 2 * mm, PAGE_W - 20 * mm, yy - 2 * mm)
        return yy - 6 * mm

    c.setFillColor(ROYAL)
    c.setFont('Helvetica-Bold', 9)
    c.drawString(20 * mm, y, 'PAYMENT SCHEDULE')
    y -= 7 * mm
    y = header_row(y)

    for line in report.get('lines', []):
        if y < 28 * mm:
            _footer(c)
            c.showPage()
            y = _letterhead(c, 'PAYROLL PAYMENT REPORT (continued)',
                            f'{report["period_start"]} to {report["period_end"]}')
            y = header_row(y)

        c.setFillColor(colors.black)
        c.setFont('Helvetica', 8)
        name = line['name'][:26]
        c.drawString(20 * mm, y, f'{line["employee_id"]} {name}')
        acct = (f'{line["bank_name"]} {line["bank_account_number"]}').strip()
        c.setFillColor(GREY if acct else AMBER)
        c.drawString(62 * mm, y, (acct or 'NO BANK DETAILS ON RECORD')[:34])
        c.setFillColor(colors.black)
        c.drawRightString(135 * mm, y, naira(line['gross_pay']))
        c.drawRightString(160 * mm, y, naira(line['total_deductions']))
        c.setFont('Helvetica-Bold', 8)
        c.drawRightString(182 * mm, y, naira(line['net_pay']))
        c.setFont('Helvetica-Bold', 7.5)
        c.setFillColor(GREEN if line['paid'] else AMBER)
        c.drawRightString(PAGE_W - 20 * mm, y,
                          (line['paid_on'] or 'PAID') if line['paid']
                          else 'UNPAID')
        y -= 5.2 * mm

    y -= 3 * mm
    c.setStrokeColor(LINE)
    c.line(20 * mm, y + 2 * mm, PAGE_W - 20 * mm, y + 2 * mm)
    c.setFillColor(NAVY)
    c.setFont('Helvetica-Bold', 9)
    c.drawString(20 * mm, y - 3 * mm,
                 f'{report["staff_count"]} staff'
                 f'  ·  {report["paid_count"]} paid'
                 f'  ·  {report["unpaid_count"]} outstanding')
    c.drawRightString(PAGE_W - 20 * mm, y - 3 * mm, naira(report['net_total']))
    y -= 12 * mm

    # --- payments made -----------------------------------------------------
    if report.get('batches'):
        if y < 45 * mm:
            _footer(c)
            c.showPage()
            y = _letterhead(c, 'PAYROLL PAYMENT REPORT (continued)',
                            f'{report["period_start"]} to {report["period_end"]}')
        c.setFillColor(ROYAL)
        c.setFont('Helvetica-Bold', 9)
        c.drawString(20 * mm, y, 'PAYMENTS RECORDED')
        y -= 6 * mm
        c.setFont('Helvetica', 8)
        for b in report['batches']:
            c.setFillColor(colors.black)
            c.drawString(22 * mm, y,
                         f'{b["paid_on"]}  {b["batch_number"]}  '
                         f'{b["method"].replace("_", " ").title()}'
                         f'  ·  {b["staff_count"]} staff')
            c.drawRightString(PAGE_W - 22 * mm, y, naira(b['total_net']))
            y -= 4.5 * mm
            if b.get('bank_reference') or b.get('paid_by_name'):
                c.setFillColor(GREY)
                c.setFont('Helvetica', 7)
                bits = []
                if b.get('bank_reference'):
                    bits.append(f'ref {b["bank_reference"]}')
                if b.get('paid_by_name'):
                    bits.append(f'recorded by {b["paid_by_name"]}')
                c.drawString(24 * mm, y, '  ·  '.join(bits))
                c.setFont('Helvetica', 8)
                y -= 4.5 * mm
        y -= 4 * mm

    c.setFillColor(GREY)
    c.setFont('Helvetica', 7.5)
    for i, chunk in enumerate(_wrap(report.get('note', ''), 115)):
        c.drawString(20 * mm, y - i * 4 * mm, chunk)

    _footer(c)
    c.save()
    buf.seek(0)
    return buf.read()


def _wrap(text: str, width: int) -> list:
    out, line = [], ''
    for word in (text or '').split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f'{line} {word}'.strip()
    if line:
        out.append(line)
    return out
