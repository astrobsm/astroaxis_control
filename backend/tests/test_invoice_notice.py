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
from pathlib import Path

from reportlab.lib.styles import getSampleStyleSheet

from app.api.sales import (
    COMPANY_ACCOUNTS,
    PAYMENT_NOTICE_EVIDENCE,
    PAYMENT_NOTICE_LEAD,
    PAYMENT_NOTICE_LINES,
    PAYMENT_NOTICE_TITLE,
    _payment_notice_pdf_elements,
    _payment_notice_thermal_html,
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
    """The text of every Paragraph in a list of flowables, before layout."""
    words = []
    for element in elements:
        for row in getattr(element, "_cellvalues", []):
            for cell in row:
                if hasattr(cell, "text"):
                    words.append(cell.text)
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

    # Every flowable has to survive a wrap, or it raises at PDF build time --
    # on a live invoice, after the customer has asked for it.
    for element in elements:
        element.wrap(6.1 * 72, 11 * 72)


def test_the_thermal_block_is_self_contained_html():
    html = _payment_notice_thermal_html()
    assert PAYMENT_NOTICE_TITLE in html
    assert html.count("<div") == html.count("</div>")
    assert "<ul" in html and html.count("<li") == len(PAYMENT_NOTICE_LINES)
    # Thermal paper is monochrome: the emphasis has to be the border and the
    # type size, not a colour that prints as nothing.
    assert "border" in html
    assert "font-size:15px" in html, "the account number is not set large"
