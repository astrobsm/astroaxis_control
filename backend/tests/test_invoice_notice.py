"""The payment-channel notice, and the test that keeps it on every invoice.

WHY THIS EXISTS
---------------
Customers were paying cash to marketers and the money was not reaching the
company. Both halves of that hurt: the loss, and a customer who believes --
honestly -- that they have settled their invoice.

The notice is the company's answer, so it has to be on every invoice the system
can produce. There are three formats (two A4 PDFs and an 80mm thermal print),
and a warning that appears on two of them is a warning that whoever is
collecting cash will simply print around.

WHAT THESE TESTS CHECK, AND WHAT THEY DO NOT
--------------------------------------------
`test_every_invoice_format_carries_the_notice` reads the source of
`app/api/sales.py` and asserts that each function producing an invoice calls one
of the two renderers. That is a structural check: it catches a FOURTH invoice
format added later without the notice, which is the realistic way this gets
lost. It does not prove the block is visible on the paper -- nothing short of
looking at a printed invoice does -- so the rendering tests below assert the
wording and that the flowables build without error.

They need no database, which is deliberate: a test that only runs when Postgres
is up is a test that stops running.
"""
import ast
import io
from datetime import datetime, timezone
from pathlib import Path

import pytest
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

import app.api.sales as sales_module
from app.api.sales import (
    COMPANY_ACCOUNTS,
    FIT_SCALES,
    PAYMENT_NOTICE_EVIDENCE,
    PAYMENT_NOTICE_LEAD,
    PAYMENT_NOTICE_LINES,
    PAYMENT_NOTICE_TITLE,
    _bill_to_elements,
    _invoice_header_elements,
    _logo_flowable,
    _naira,
    _payment_notice_pdf_elements,
    _payment_notice_thermal_html,
    _render_fitted_pdf,
)

SALES = Path(__file__).resolve().parents[1] / "app" / "api" / "sales.py"

# The functions in sales.py that hand a customer an invoice. Named explicitly so
# that adding a format means adding it here and being told what is missing.
INVOICE_FUNCTIONS = {
    "generate_invoice_pdf": "_payment_notice_pdf_elements",
    "generate_invoice": "_payment_notice_pdf_elements",
    "thermal_invoice": "_payment_notice_thermal_html",
}


def _calls_in(func: ast.AST) -> set:
    return {node.func.id for node in ast.walk(func)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}


def test_every_invoice_format_carries_the_notice():
    tree = ast.parse(SALES.read_text(encoding="utf-8"))
    functions = {node.name: node for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

    missing = []
    for name, renderer in INVOICE_FUNCTIONS.items():
        assert name in functions, f"{name} has been renamed or removed"
        if renderer not in _calls_in(functions[name]):
            missing.append(f"{name} does not call {renderer}")

    assert missing == [], (
        "an invoice format would print without the payment notice:\n  "
        + "\n  ".join(missing))


def test_a_new_invoice_endpoint_is_not_quietly_missed():
    """If sales.py grows a fourth invoice format, this test names it.

    The check above only looks at the three formats that exist. This one looks
    the other way round -- every function whose name says it produces an
    invoice must be one this file knows about.
    """
    tree = ast.parse(SALES.read_text(encoding="utf-8"))
    produced = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "invoice" in node.name.lower()
        and not node.name.startswith("_")
        # invoice_statement builds the previous-balance section, it does not
        # produce an invoice of its own.
        and node.name != "invoice_statement"
    }
    unknown = produced - set(INVOICE_FUNCTIONS)
    assert unknown == set(), (
        f"new invoice format(s) {sorted(unknown)} -- add the payment notice, "
        f"then list them in INVOICE_FUNCTIONS")


def test_the_notice_says_what_it_has_to_say():
    """Vague warnings get ignored. These points are the whole point."""
    text = " ".join(
        (PAYMENT_NOTICE_LEAD,) + PAYMENT_NOTICE_LINES + (PAYMENT_NOTICE_EVIDENCE,)
    ).lower()

    # Who may not be paid, by role rather than by a euphemism.
    for role in ("marketer", "sales representative", "driver", "staff"):
        assert role in text, f"the notice does not mention {role}"

    # That cash is refused, and that a personal transfer is the same thing.
    assert "cash" in text
    assert "personal account" in text

    # The consequence the customer actually cares about.
    assert "would remain unpaid" in text
    assert "risk" in text

    # Reporting a member of staff who offers to collect cash, with a number to
    # call. Asked for explicitly: without it the notice tells a customer what
    # not to do and nothing about what to do instead.
    assert "report" in text
    assert "+234 707 679 3866" in text
    assert "confidence" in text, (
        "somebody reporting a member of staff needs to be told it is "
        "confidential, or they will not report it")


def test_the_notice_is_courteous():
    """The customer has done nothing wrong, and most staff never touch cash.

    A notice that reads as an accusation is resented and ignored, and it would
    be unfair to the staff it describes. So it is checked for the courtesies
    rather than left to whoever edits the wording next.
    """
    text = " ".join(
        (PAYMENT_NOTICE_LEAD,) + PAYMENT_NOTICE_LINES + (PAYMENT_NOTICE_EVIDENCE,)
    ).lower()

    assert "kindly" in text or "please" in text
    assert "for your protection" in text
    assert "grateful" in text

    # Words that accuse the reader or the staff rather than stating the policy.
    for word in ("fraud", "steal", "stealing", "thief", "dishonest",
                 "not acceptable", "forbidden"):
        assert word not in text, f"{word!r} accuses rather than informs"


def test_the_accounts_and_the_policy_are_one_block():
    """Split them and the customer stops reading at the account number.

    That is the exact failure this is meant to prevent: account details read,
    instruction not read, cash handed to whoever brought the invoice.
    """
    assert COMPANY_ACCOUNTS, "no accounts to pay into"
    for bank, number, name in COMPANY_ACCOUNTS:
        assert bank and number and name

    html = _payment_notice_thermal_html()
    for _, number, _ in COMPANY_ACCOUNTS:
        assert number in html, f"{number} is not in the thermal block"
    assert "report" in html.lower()

    styles = getSampleStyleSheet()
    rendered = _rendered_text(_payment_notice_pdf_elements(styles))
    for _, number, _ in COMPANY_ACCOUNTS:
        assert number in rendered, f"{number} is not in the PDF block"
    assert "report" in rendered.lower()


def test_an_account_number_is_written_down_once():
    """Two copies of an account number is how one of them goes stale.

    They used to be typed into each invoice format separately. A bank change
    would have been applied to two of the three, and the third would have been
    quietly collecting payments into a closed account.
    """
    source = SALES.read_text(encoding="utf-8")
    for _, number, _ in COMPANY_ACCOUNTS:
        assert source.count(number) == 1, (
            f"account number {number} appears {source.count(number)} times in "
            f"sales.py -- it belongs only in COMPANY_ACCOUNTS")


def _rendered_text(elements) -> str:
    """The text of every Paragraph in a list of flowables, before layout.

    Descends through KeepTogether, which the notice is wrapped in so that the
    accounts and the policy cannot land on separate pages.
    """
    words = []

    def visit(items):
        for element in items:
            inner = getattr(element, "_content", None)
            if inner:
                visit(inner)
            for row in getattr(element, "_cellvalues", []):
                for cell in row:
                    if hasattr(cell, "text"):
                        words.append(cell.text)

    visit(elements)
    return " ".join(words)


def test_the_pdf_block_builds_and_carries_the_words():
    styles = getSampleStyleSheet()
    elements = _payment_notice_pdf_elements(styles)
    assert elements, "the notice produced nothing"

    # Read the cells before wrapping: wrap() rebuilds them into laid-out
    # fragments and the original Paragraph text is no longer reachable.
    rendered = _rendered_text(elements)
    assert PAYMENT_NOTICE_TITLE in rendered
    assert "marketer" in rendered

    # Actually build it. A flowable that raises during layout raises on a live
    # invoice, after the customer has asked for it -- and KeepTogether cannot
    # be laid out outside a real build, so wrap() alone would not have caught
    # it anyway.
    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=A4).build(
        _payment_notice_pdf_elements(getSampleStyleSheet()))
    assert out.getvalue().startswith(b"%PDF")


def test_the_thermal_block_is_self_contained_html():
    html = _payment_notice_thermal_html()
    assert PAYMENT_NOTICE_TITLE in html
    assert html.count("<div") == html.count("</div>")
    assert "<ul" in html and html.count("<li") == len(PAYMENT_NOTICE_LINES)
    # Thermal paper is monochrome: the emphasis has to be the border and the
    # type size, not a colour that prints as nothing.
    assert "border" in html
    assert "font-size:15px" in html, "the account number is not set large"


# ---------------------------------------------------------------------------
# Layout: one page where it fits, and money that actually prints
# ---------------------------------------------------------------------------


class _Fake:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _fake_order(n_lines):
    lines = [
        _Fake(product=_Fake(name=f"WOUND CARE GAUZE PAD {i + 1} sterile 10x10cm"),
              product_id=i, quantity=float(i + 1) * 3, unit="carton",
              unit_price=4250.0 + i * 100,
              line_total=(4250.0 + i * 100) * (i + 1) * 3)
        for i in range(n_lines)
    ]
    return _Fake(
        order_number="SO-2026-00417",
        order_date=datetime(2026, 9, 15, tzinfo=timezone.utc),
        required_date=datetime(2026, 9, 29, tzinfo=timezone.utc),
        payment_status="unpaid", status="confirmed",
        total_amount=sum(l.line_total for l in lines),
        customer=_Fake(name="DIVIDEND PHARMACY LIMITED",
                       email="dividend@example.com", phone="+234 803 123 4567",
                       address="12 Ogui Road, Enugu"),
        lines=lines)


def _invoice_story(order, scale):
    """The parts every A4 invoice is made of, at one scale."""
    styles = getSampleStyleSheet()
    story = _invoice_header_elements(order, styles, scale)
    story.extend(_bill_to_elements(order, styles, scale))
    rows = [["Item", "Qty", "Unit Price", "Total"]]
    for line in order.lines:
        rows.append([line.product.name, f"{line.quantity:g}",
                     _naira(line.unit_price), _naira(line.line_total)])
    table = Table(rows, colWidths=[3.5 * inch, 0.8 * inch, 1.3 * inch,
                                   1.3 * inch], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), round(8.5 * scale, 1)),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    story.append(table)
    story.extend(_payment_notice_pdf_elements(styles, scale))
    return story


def _page_count(buffer):
    """Pages in a PDF, counted from its object types.

    Read from the bytes rather than with a PDF library, so this test suite does
    not grow a dependency for one assertion. "/Type /Pages" is the single tree
    node and is subtracted; what is left is one per page. Cross-checked against
    pdfinfo on the same documents.
    """
    data = buffer.getvalue()
    return data.count(b"/Type /Page") - data.count(b"/Type /Pages")


def test_an_ordinary_invoice_fits_on_one_page():
    """The old header alone -- a 1.5in logo on its own line, then a title, then
    the address, each followed by a half-inch spacer -- pushed a three-line
    invoice onto a second page. That is what this asserts has not come back.
    """
    buffer = _render_fitted_pdf(lambda scale: _invoice_story(_fake_order(3), scale))
    assert _page_count(buffer) == 1


def test_a_normal_length_invoice_still_fits():
    """Twelve lines is an ordinary order here, not an exceptional one."""
    buffer = _render_fitted_pdf(lambda scale: _invoice_story(_fake_order(12), scale))
    assert _page_count(buffer) == 1


def test_it_tries_progressively_tighter_before_giving_up():
    seen = []

    def build(scale):
        seen.append(scale)
        return _invoice_story(_fake_order(60), scale)

    _render_fitted_pdf(build)
    assert seen == list(FIT_SCALES), (
        "every scale must be tried before a second page is accepted")


def test_what_cannot_fit_is_returned_readable_rather_than_shrunk():
    """Once a second page is unavoidable, shrinking the type buys nothing.

    Sixty lines cannot go on one page at any sane size, so the reader should
    get the full-size version across two pages -- not 6pt type across two.
    """
    order = _fake_order(60)
    fitted = _render_fitted_pdf(lambda scale: _invoice_story(order, scale))

    full_size = io.BytesIO()
    doc = SimpleDocTemplate(
        full_size, pagesize=A4, leftMargin=0.55 * inch, rightMargin=0.55 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch)
    doc.build(_invoice_story(order, 1.0))
    full_size.seek(0)

    assert _page_count(fitted) > 1
    assert _page_count(fitted) == _page_count(full_size), (
        "the document handed back should be the full-size one, so it must have "
        "the same page count as a plain full-size build")


def test_the_smallest_scale_is_still_readable():
    """A floor, so 'fit one page' can never mean 'unreadable'."""
    assert min(FIT_SCALES) >= 0.75
    assert max(FIT_SCALES) == 1.0
    assert list(FIT_SCALES) == sorted(FIT_SCALES, reverse=True)


def test_money_on_a_pdf_uses_no_glyph_the_font_lacks():
    """Every naira sign on every PDF invoice printed as a filled black box.

    ReportLab's built-in Helvetica is Latin-1 and has no glyph for it, so the
    amounts -- the one thing on an invoice that must be unambiguous -- came out
    as squares. PDF amounts now carry the ISO code, which always renders.
    """
    rendered = _naira(1234.5)
    assert rendered == "NGN 1,234.50"
    rendered.encode("ascii")           # raises if a non-Latin-1 glyph crept back

    source = SALES.read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if "₦" not in line:
            continue
        assert stripped.startswith("#") or '"""' in line or "'" not in line, (
            f"a naira sign reached PDF-building code: {stripped[:70]}")


def test_a_missing_logo_does_not_cost_the_customer_their_invoice():
    """An invoice without a logo is a working invoice. No logo at all is not."""
    original = sales_module.LOGO_PATHS
    try:
        sales_module.LOGO_PATHS = ('/nowhere/company-logo.png',)
        assert _logo_flowable(0.6 * inch) is None
        story = _invoice_header_elements(_fake_order(2), getSampleStyleSheet())
        assert story, "the header must still be produced without a logo"
    finally:
        sales_module.LOGO_PATHS = original


def test_the_logo_keeps_its_own_proportions():
    """It used to be forced into a fixed square, which distorts any logo."""
    logo = _logo_flowable(0.6 * inch)
    if logo is None:
        pytest.skip("logo file is not on this machine")
    assert abs(logo.drawHeight - 0.6 * inch) < 0.01
    assert logo.drawWidth > 0
