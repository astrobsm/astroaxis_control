"""Holding stock for an order without pretending it has left the warehouse.

Revision ID: t0123456789s
Revises: s0123456789r
Create Date: 2026-10-04

WHAT WAS ALREADY HERE, AND WHY IT WAS DANGEROUS
===============================================
`stock_levels.reserved_stock` has existed since the initial schema. Nothing
has ever written to it, so it is zero everywhere, and four modules already
compute `current_stock - reserved_stock` and get the right answer by accident.

Three others do not: `production.py` reads `current_stock` alone as
"available", and `raw_materials.py` and `stock_management.py` return a
hardcoded zero for reserved. Those were harmless while the column was always
zero. The moment anything reserves stock they become wrong, and the way they
become wrong is to promise stock that is already spoken for.

So this migration and the change that goes with it had to arrive together:
the column, the reservations behind it, and every reader corrected in one
step. A reservation system that half the application cannot see is worse than
none.

RESERVING DOES NOT MOVE STOCK
=============================
`current_stock` is untouched by a reservation. The goods are still in the
warehouse and still on the balance sheet; they are merely promised. Only
consuming a reservation moves stock, and that goes through
`apply_stock_movement` like every other movement, so the four invariants that
module guarantees still hold.

Two numbers, two meanings, and the application must never confuse them:

    current_stock   what is physically there
    reserved_stock  how much of it is already promised
    available       the difference, and the only one a salesperson should see

RESERVED_STOCK IS MAINTAINED, NOT DERIVED
=========================================
This is a deliberate exception to the rule that nothing is stored twice, and
it is the same exception `current_stock` already is. Summing live reservations
on every stock display would put a correlated subquery into a dozen hot
queries.

The safety comes from the same place it does for `current_stock`: the
aggregate is only ever changed inside the transaction that writes the
reservation row, under the row lock `_lock_or_create_level` already takes. A
reconciliation function proves the two agree, and a test runs it -- which is
the trial balance of inventory.

A RESERVATION THAT CANNOT EXPIRE IS A LEAK
==========================================
`expires_at` is NOT NULL. A customer who abandons a basket, a quotation nobody
answers, an order left pending while somebody goes on leave: each would hold
stock out of circulation forever. The warehouse would show empty while being
full, which is the same failure as a stock error and harder to diagnose
because every individual number looks right.

Expired reservations are released by a job, and the release is recorded with
a reason like any other.
"""
from alembic import op

revision = 't0123456789s'
down_revision = 's0123456789r'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS inventory_reservations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            warehouse_id UUID NOT NULL REFERENCES warehouses(id),
            product_id UUID REFERENCES products(id),
            raw_material_id UUID REFERENCES raw_materials(id),

            quantity NUMERIC(18,6) NOT NULL,
            status VARCHAR(12) NOT NULL DEFAULT 'HELD',

            -- What the stock is being held for. Without this a reservation
            -- cannot be released when its order is cancelled, because nobody
            -- can tell which reservation belonged to it.
            reference_type VARCHAR(24) NOT NULL,
            reference_id UUID,
            reference_label VARCHAR(120),

            -- NOT NULL: see the module note. A reservation with no end date
            -- takes stock out of circulation permanently.
            expires_at TIMESTAMPTZ NOT NULL,

            released_at TIMESTAMPTZ,
            release_reason TEXT,
            consumed_at TIMESTAMPTZ,

            created_by UUID REFERENCES users(id),
            created_by_name VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_reservation_qty CHECK (quantity > 0),
            CONSTRAINT ck_reservation_status CHECK (status IN
                ('HELD','RELEASED','CONSUMED')),
            CONSTRAINT ck_reservation_reference CHECK (reference_type IN
                ('SALES_ORDER','QUOTATION','PRODUCTION_ORDER','TRANSFER',
                 'MANUAL')),
            -- Exactly one kind of item, like stock_levels itself.
            CONSTRAINT ck_reservation_item CHECK (
                (product_id IS NOT NULL) <> (raw_material_id IS NOT NULL)),
            -- A released or consumed reservation says when. Without it the
            -- reconciliation cannot tell a live hold from a dead one.
            CONSTRAINT ck_reservation_closed CHECK (
                (status = 'HELD' AND released_at IS NULL AND consumed_at IS NULL)
                OR (status = 'RELEASED' AND released_at IS NOT NULL)
                OR (status = 'CONSUMED' AND consumed_at IS NOT NULL))
        )
    """)

    # The reconciliation sums live holds per (warehouse, item); the release
    # job finds expired ones. Both are partial on HELD, which is the small
    # half of this table in a healthy system.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_reservations_live
            ON inventory_reservations (warehouse_id, product_id,
                                       raw_material_id)
         WHERE status = 'HELD'
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_reservations_expiry "
               "ON inventory_reservations (expires_at) WHERE status = 'HELD'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_reservations_reference "
               "ON inventory_reservations (reference_type, reference_id)")

    op.execute("""
        INSERT INTO app_settings (key, value, value_type, description) VALUES
          ('RESERVATION_HOLD_HOURS', '72', 'INTEGER',
           'How long stock is held for an order before the reservation '
           'lapses and the stock returns to available.')
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS inventory_reservations CASCADE")
    op.execute("DELETE FROM app_settings WHERE key = 'RESERVATION_HOLD_HOURS'")
    # reserved_stock is left as it stands rather than zeroed. Zeroing it would
    # silently make every held quantity available again, which is the failure
    # this table exists to prevent.
