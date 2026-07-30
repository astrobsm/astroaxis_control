"""No module may define a function with the same name as one it imports.

THE BUG THIS EXISTS TO PREVENT
------------------------------
`app/api/payment_tracking.py` imported the service function `record_payment`
and also defined a FastAPI route handler called `record_payment`. A module-level
def shadows an import of the same name, so this call inside the handler:

    result = await record_payment(session, invoice_id=invoice_id, ...)

resolved to the HANDLER, not the service. The handler's first parameter is
`invoice_id`, so `session` bound to it positionally and `invoice_id=` was then
supplied again by keyword:

    TypeError: record_payment() got multiple values for argument 'invoice_id'

Every payment recorded through the Payment Tracking screen failed with that
message. Nothing caught it: the module imports fine, the app starts, the route
registers, and the error only appears when a user actually records money.

The same file had already aliased `delete_payment as delete_payment_svc` to
avoid this exact collision with its own `delete_payment` handler -- so the
pattern was known and simply missed once. That is precisely the kind of mistake
a static check should catch instead of a customer.

This test needs no database and runs in milliseconds.
"""
from __future__ import annotations

import ast
import pathlib

APP_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"


def _shadowed_names(path: pathlib.Path) -> list[tuple[str, str]]:
    """(name, source_module) for each import shadowed by a def in this file.

    Only `from x import y` without an alias can be shadowed silently -- an
    aliased import (`import y as y_svc`) is the fix, and `import x.y` is
    accessed through the module object so a same-named def cannot hide it.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []

    imported: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.asname is None and alias.name != "*":
                    imported[alias.name] = node.module or "?"

    # Only top-level defs shadow a module-level import; a nested function is
    # scoped to its parent and cannot.
    defined = {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    return sorted((name, imported[name]) for name in imported.keys() & defined)


def test_no_module_shadows_its_own_imports():
    offenders = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        for name, module in _shadowed_names(path):
            offenders.append(
                f"{path.relative_to(APP_ROOT.parent)}: def {name}() shadows "
                f"`from {module} import {name}` -- calls to {name}() inside this "
                f"module hit the local def, not the import. Alias the import "
                f"(e.g. `{name} as {name}_svc`) or rename the def."
            )

    assert not offenders, (
        "Shadowed imports found. Each one silently redirects every call in that "
        "module:\n  " + "\n  ".join(offenders)
    )


def test_the_detector_actually_detects(tmp_path):
    """Guard the guard.

    A checker that silently matches nothing passes forever and protects
    nothing, so prove it fires on the exact shape of the original bug.
    """
    sample = tmp_path / "shadowed.py"
    sample.write_text(
        "from app.services.receivables import record_payment\n"
        "\n"
        "async def record_payment(invoice_id, payment_data):\n"
        "    return await record_payment(None, invoice_id=invoice_id)\n",
        encoding="utf-8",
    )
    assert _shadowed_names(sample) == [
        ("record_payment", "app.services.receivables")]

    clean = tmp_path / "aliased.py"
    clean.write_text(
        "from app.services.receivables import record_payment as record_payment_svc\n"
        "\n"
        "async def record_payment(invoice_id, payment_data):\n"
        "    return await record_payment_svc(None, invoice_id=invoice_id)\n",
        encoding="utf-8",
    )
    assert _shadowed_names(clean) == []


def test_nested_defs_are_not_flagged(tmp_path):
    """A def inside a function does not shadow a module-level import."""
    sample = tmp_path / "nested.py"
    sample.write_text(
        "from app.services.receivables import record_payment\n"
        "\n"
        "def outer():\n"
        "    def record_payment():\n"
        "        pass\n"
        "    return record_payment\n",
        encoding="utf-8",
    )
    assert _shadowed_names(sample) == []
