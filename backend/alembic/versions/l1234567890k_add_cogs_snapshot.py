"""Snapshot cost of goods sold onto sale lines.

Revision ID: l1234567890k
Revises: k0123456789j
Create Date: 2026-07-20

COGS was derived at query time by joining `product_pricing`, so changing a
product's cost price restated the profit of every period already closed and
paid out. Cost is now captured on the line when the sale happens and never
recomputed.

BACKFILL HONESTY: the historical cost of an already-shipped sale is not
recoverable -- the price list has been overwritten since (products.py deletes
and recreates all pricing rows on update), and stock movements before this
point may carry no unit_cost. Existing rows are therefore backfilled with a
best-effort estimate and explicitly marked `cost_source = 'backfill_estimate'`
so no reader mistakes them for recorded fact. Reports can exclude or flag
them; the accounting module must not treat them as audited figures.
"""
from alembic import op
import sqlalchemy as sa

revision = 'l1234567890k'
down_revision = 'k0123456789j'
branch_labels = None
depends_on = None


def upgrade():
    for table in ('sales_order_lines', 'invoice_lines'):
        op.execute(f"""
            ALTER TABLE {table}
                ADD COLUMN IF NOT EXISTS unit_cost NUMERIC(18,6),
                ADD COLUMN IF NOT EXISTS cost_total NUMERIC(18,2),
                ADD COLUMN IF NOT EXISTS cost_source VARCHAR(32)
        """)

    # ------------------------------------------------------------------
    # Backfill, best available source first. Each pass only touches rows
    # still lacking a cost, so earlier (better) sources win.
    # ------------------------------------------------------------------

    # 1. Weighted average of costed inbound movements -- the closest thing to
    #    a real historical cost, since it comes from the movement ledger.
    op.execute("""
        WITH wac AS (
            SELECT product_id,
                   SUM(quantity * unit_cost) / NULLIF(SUM(quantity), 0) AS cost
              FROM stock_movements
             WHERE product_id IS NOT NULL
               AND movement_type IN ('IN','PRODUCTION_IN','TRANSFER_IN',
                                     'DAMAGE_TRANSFER_IN')
               AND unit_cost IS NOT NULL AND unit_cost > 0 AND quantity > 0
             GROUP BY product_id
        )
        UPDATE sales_order_lines sol
           SET unit_cost = wac.cost,
               cost_total = ROUND(sol.quantity * wac.cost, 2),
               cost_source = 'backfill_estimate'
          FROM wac
         WHERE wac.product_id = sol.product_id
           AND sol.unit_cost IS NULL
           AND wac.cost > 0
    """)

    # 2. Price list, matching the line's unit. DISTINCT ON guards against the
    #    duplicate (product_id, unit) rows that made the live join fan out and
    #    double-count COGS.
    op.execute("""
        WITH pp AS (
            SELECT DISTINCT ON (product_id, LOWER(unit))
                   product_id, LOWER(unit) AS unit, cost_price
              FROM product_pricing
             WHERE cost_price > 0
             ORDER BY product_id, LOWER(unit), created_at DESC
        )
        UPDATE sales_order_lines sol
           SET unit_cost = pp.cost_price,
               cost_total = ROUND(sol.quantity * pp.cost_price, 2),
               cost_source = 'backfill_estimate'
          FROM pp
         WHERE pp.product_id = sol.product_id
           AND pp.unit = LOWER(COALESCE(sol.unit, ''))
           AND sol.unit_cost IS NULL
    """)

    # 3. Product-level cost price.
    op.execute("""
        UPDATE sales_order_lines sol
           SET unit_cost = p.cost_price,
               cost_total = ROUND(sol.quantity * p.cost_price, 2),
               cost_source = 'backfill_estimate'
          FROM products p
         WHERE p.id = sol.product_id
           AND sol.unit_cost IS NULL
           AND p.cost_price > 0
    """)

    # 4. Anything still uncosted is marked 'unknown', NOT zero-with-a-number.
    #    A zero cost silently reports 100% margin; 'unknown' lets reports
    #    exclude the line and say so.
    op.execute("""
        UPDATE sales_order_lines
           SET unit_cost = 0, cost_total = 0, cost_source = 'unknown'
         WHERE unit_cost IS NULL
    """)

    # Invoice lines inherit from their order line where one exists, so the
    # invoice and the order agree; otherwise they get the same fallbacks.
    op.execute("""
        UPDATE invoice_lines il
           SET unit_cost = sol.unit_cost,
               cost_total = ROUND(il.quantity * sol.unit_cost, 2),
               cost_source = sol.cost_source
          FROM invoices i
          JOIN sales_order_lines sol ON sol.sales_order_id = i.sales_order_id
         WHERE il.invoice_id = i.id
           AND sol.product_id = il.product_id
           AND il.unit_cost IS NULL
    """)
    op.execute("""
        UPDATE invoice_lines il
           SET unit_cost = p.cost_price,
               cost_total = ROUND(il.quantity * p.cost_price, 2),
               cost_source = 'backfill_estimate'
          FROM products p
         WHERE p.id = il.product_id
           AND il.unit_cost IS NULL
           AND p.cost_price > 0
    """)
    op.execute("""
        UPDATE invoice_lines
           SET unit_cost = 0, cost_total = 0, cost_source = 'unknown'
         WHERE unit_cost IS NULL
    """)

    # Cost must never be negative, whatever wrote it.
    for table in ('sales_order_lines', 'invoice_lines'):
        op.execute(f"""
            ALTER TABLE {table}
                ADD CONSTRAINT ck_{table}_cost_non_negative
                CHECK (unit_cost IS NULL OR unit_cost >= 0)
        """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_sales_order_lines_cost_source
            ON sales_order_lines (cost_source)
    """)

    # Close the fan-out hole that let duplicate pricing rows double COGS.
    # Deduplicate first, keeping the most recent row per (product, unit).
    op.execute("""
        DELETE FROM product_pricing pp
         WHERE pp.id NOT IN (
             SELECT DISTINCT ON (product_id, LOWER(unit)) id
               FROM product_pricing
              ORDER BY product_id, LOWER(unit), created_at DESC
         )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_product_pricing_product_unit
            ON product_pricing (product_id, LOWER(unit))
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS uq_product_pricing_product_unit")
    op.execute("DROP INDEX IF EXISTS ix_sales_order_lines_cost_source")
    for table in ('sales_order_lines', 'invoice_lines'):
        op.execute(f"ALTER TABLE {table} "
                   f"DROP CONSTRAINT IF EXISTS ck_{table}_cost_non_negative")
        op.execute(f"""
            ALTER TABLE {table}
                DROP COLUMN IF EXISTS unit_cost,
                DROP COLUMN IF EXISTS cost_total,
                DROP COLUMN IF EXISTS cost_source
        """)
