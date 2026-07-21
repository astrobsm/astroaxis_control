"""Enforce stock ledger integrity invariants.

Revision ID: k0123456789j
Revises: j9012345678i
Create Date: 2026-07-20

`stock_levels` is the authoritative on-hand balance, but nothing stopped the
table from holding several rows for the same (warehouse, item): every writer
did SELECT-then-INSERT-or-UPDATE with no constraint to conflict against, so
two concurrent first-time writes each created a row. Reads then used
`.first()` with no ORDER BY, meaning reads and writes could land on different
rows and the balance would silently diverge.

`product_id` and `raw_material_id` were also both nullable with no CHECK, so a
row could reference neither item (invisible to every report but still counted
in totals) or both (the same quantity attributed twice).

This migration:
  1. quarantines rows that cannot be repaired automatically,
  2. merges duplicate balance rows by summing them,
  3. adds partial unique indexes so ON CONFLICT upserts become possible,
  4. adds the CHECK constraint that makes the discriminator unambiguous.

Nothing is deleted outright -- unrepairable rows are copied to
`stock_levels_quarantine` first so inventory staff can reconcile them by hand.
"""
from alembic import op
import sqlalchemy as sa

revision = 'k0123456789j'
down_revision = 'j9012345678i'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # 1. Quarantine rows that violate the discriminator rule.
    #    "Both null" is meaningless; "both set" is ambiguous. Neither can be
    #    repaired without a human deciding what the row was meant to be.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS stock_levels_quarantine (
            LIKE stock_levels INCLUDING ALL
        )
    """)
    op.execute("""
        ALTER TABLE stock_levels_quarantine
            ADD COLUMN IF NOT EXISTS quarantined_at TIMESTAMPTZ DEFAULT NOW(),
            ADD COLUMN IF NOT EXISTS quarantine_reason TEXT
    """)

    for reason, predicate in [
        ('both product_id and raw_material_id are NULL',
         'product_id IS NULL AND raw_material_id IS NULL'),
        ('both product_id and raw_material_id are set',
         'product_id IS NOT NULL AND raw_material_id IS NOT NULL'),
    ]:
        op.execute(sa.text(f"""
            INSERT INTO stock_levels_quarantine
            SELECT sl.*, NOW(), :reason FROM stock_levels sl WHERE {predicate}
        """).bindparams(reason=reason))
        op.execute(f"DELETE FROM stock_levels WHERE {predicate}")

    # ------------------------------------------------------------------
    # 2. Merge duplicate balance rows.
    #    Summing is the only defensible merge: each row represents stock that
    #    was really added, so the total is the true on-hand quantity. The
    #    survivor is the most recently updated row, which keeps its min/max
    #    thresholds.
    # ------------------------------------------------------------------
    for item_col in ('product_id', 'raw_material_id'):
        # Fold the duplicates' quantities into the survivor.
        op.execute(f"""
            WITH agg AS (
                SELECT warehouse_id, {item_col} AS item_id,
                       SUM(current_stock)                AS tot_stock,
                       SUM(COALESCE(reserved_stock, 0))  AS tot_reserved,
                       COUNT(*)                          AS n
                FROM stock_levels
                WHERE {item_col} IS NOT NULL
                GROUP BY warehouse_id, {item_col}
                HAVING COUNT(*) > 1
            ),
            survivor AS (
                SELECT DISTINCT ON (sl.warehouse_id, sl.{item_col})
                       sl.id, sl.warehouse_id, sl.{item_col} AS item_id
                FROM stock_levels sl
                JOIN agg a ON a.warehouse_id = sl.warehouse_id
                          AND a.item_id = sl.{item_col}
                ORDER BY sl.warehouse_id, sl.{item_col},
                         sl.updated_at DESC NULLS LAST, sl.id
            )
            UPDATE stock_levels sl
               SET current_stock  = a.tot_stock,
                   reserved_stock = a.tot_reserved,
                   updated_at     = NOW()
              FROM survivor s
              JOIN agg a ON a.warehouse_id = s.warehouse_id
                        AND a.item_id = s.item_id
             WHERE sl.id = s.id
        """)
        # Remove the now-folded duplicates.
        op.execute(f"""
            DELETE FROM stock_levels sl
             WHERE sl.{item_col} IS NOT NULL
               AND sl.id <> (
                   SELECT s2.id FROM stock_levels s2
                    WHERE s2.warehouse_id = sl.warehouse_id
                      AND s2.{item_col} = sl.{item_col}
                    ORDER BY s2.updated_at DESC NULLS LAST, s2.id
                    LIMIT 1
               )
        """)

    # ------------------------------------------------------------------
    # 3. Partial unique indexes.
    #    Partial rather than plain: the unused discriminator column is NULL,
    #    and NULLs never compare equal, so a plain UNIQUE would not stop
    #    duplicates. These are also valid ON CONFLICT targets.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_levels_wh_product
            ON stock_levels (warehouse_id, product_id)
         WHERE product_id IS NOT NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_levels_wh_raw_material
            ON stock_levels (warehouse_id, raw_material_id)
         WHERE raw_material_id IS NOT NULL
    """)

    # ------------------------------------------------------------------
    # 4. Discriminator CHECK: exactly one of the two must be set.
    # ------------------------------------------------------------------
    op.execute("""
        ALTER TABLE stock_levels
            ADD CONSTRAINT ck_stock_levels_one_item_type
            CHECK ((product_id IS NULL) <> (raw_material_id IS NULL))
    """)

    # stock_movements gets the same discriminator rule. It is an append-only
    # ledger, so a row referencing neither item can never be reconciled.
    op.execute("""
        CREATE TABLE IF NOT EXISTS stock_movements_quarantine (
            LIKE stock_movements INCLUDING ALL
        )
    """)
    op.execute("""
        ALTER TABLE stock_movements_quarantine
            ADD COLUMN IF NOT EXISTS quarantined_at TIMESTAMPTZ DEFAULT NOW()
    """)
    op.execute("""
        INSERT INTO stock_movements_quarantine
        SELECT sm.*, NOW() FROM stock_movements sm
         WHERE (sm.product_id IS NULL AND sm.raw_material_id IS NULL)
            OR (sm.product_id IS NOT NULL AND sm.raw_material_id IS NOT NULL)
    """)
    op.execute("""
        DELETE FROM stock_movements
         WHERE (product_id IS NULL AND raw_material_id IS NULL)
            OR (product_id IS NOT NULL AND raw_material_id IS NOT NULL)
    """)
    op.execute("""
        ALTER TABLE stock_movements
            ADD CONSTRAINT ck_stock_movements_one_item_type
            CHECK ((product_id IS NULL) <> (raw_material_id IS NULL))
    """)

    # Movements record a positive magnitude; direction lives in movement_type.
    # One writer stored OUT as a negative number, which broke every SUM().
    op.execute("""
        UPDATE stock_movements SET quantity = ABS(quantity) WHERE quantity < 0
    """)
    op.execute("""
        ALTER TABLE stock_movements
            ADD CONSTRAINT ck_stock_movements_positive_qty
            CHECK (quantity >= 0)
    """)

    # Index the columns the ledger-replay and valuation queries filter on.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_stock_movements_type_created
            ON stock_movements (movement_type, created_at)
    """)


def downgrade():
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT IF EXISTS ck_stock_movements_positive_qty")
    op.execute("ALTER TABLE stock_movements DROP CONSTRAINT IF EXISTS ck_stock_movements_one_item_type")
    op.execute("ALTER TABLE stock_levels DROP CONSTRAINT IF EXISTS ck_stock_levels_one_item_type")
    op.execute("DROP INDEX IF EXISTS ix_stock_movements_type_created")
    op.execute("DROP INDEX IF EXISTS uq_stock_levels_wh_raw_material")
    op.execute("DROP INDEX IF EXISTS uq_stock_levels_wh_product")
    # Quarantined rows are intentionally left in place; they are the only
    # record of data this migration removed.
