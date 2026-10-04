"""Quotations: a price promise with an expiry date.

Revision ID: r0123456789q
Revises: q0123456789p
Create Date: 2026-10-04

WHY A QUOTATION LINE STORES ITS PRICE
=====================================
The obvious build joins a quotation line to `product_pricing` and reads the
price when the quote is displayed. It is also the single worst mistake
available here.

A quotation is a promise. A customer holding a quote for 180,000 naira, dated
and valid until the end of the month, must see 180,000 naira when they open
it -- on the day the price list changes, and on the day after. A quote that
silently re-prices itself is not a quote; it is a trap, and the customer finds
out at the moment they try to accept it.

So every line stores `unit_price` as quoted, and `product_name` as it was
written. The product may later be renamed or withdrawn; the piece of paper the
customer is holding does not change.

VALIDITY IS NOT OPTIONAL
========================
`valid_until` is NOT NULL. A quotation with no expiry is a price promise with
no end, and this company sells into a currency that has moved a long way in a
year. The default is set by the service rather than the schema, because how
long a quote should stand is a commercial decision somebody may want to
change, not a database constant.

Expiry is DERIVED, not stored. Nothing flips a quote to EXPIRED on a schedule,
because a nightly job that fails leaves quotes looking live when they are not.
`valid_until < today` is the whole test, computed every time anybody looks.

A QUOTE CONVERTS ONCE
=====================
`converted_order_id` is uniquely indexed and a CHECK ties it to the CONVERTED
status. Converting twice would produce two sales orders for one agreement,
which is the kind of error that is found by the customer, in writing, after
they have been invoiced twice.

WHAT THIS DOES NOT DO
=====================
It does not touch stock. A quotation reserves nothing: the customer has not
agreed to anything yet, and holding stock against every outstanding quote
would empty the warehouse on paper while it was full in fact. Reservation
belongs with the order, in the stage after this one.
"""
from alembic import op

revision = 'r0123456789q'
down_revision = 'q0123456789p'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS quotations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            quotation_number VARCHAR(64) UNIQUE NOT NULL,
            customer_id UUID NOT NULL REFERENCES customers(id),

            status VARCHAR(16) NOT NULL DEFAULT 'DRAFT',

            -- Which price list this was quoted from. Stored because the
            -- customer may change category later and the quote must not.
            customer_type VARCHAR(16) NOT NULL DEFAULT 'retail',

            -- NOT NULL on purpose. See the module note: a quote with no end
            -- date is a price promise with no end.
            valid_until DATE NOT NULL,

            subtotal NUMERIC(18,2) NOT NULL DEFAULT 0,
            discount_percent NUMERIC(5,2) NOT NULL DEFAULT 0,
            discount_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
            delivery_charge NUMERIC(18,2) NOT NULL DEFAULT 0,
            total_amount NUMERIC(18,2) NOT NULL DEFAULT 0,

            terms TEXT,
            notes TEXT,

            -- Who stands behind the price.
            prepared_by UUID REFERENCES users(id),
            prepared_by_name VARCHAR(255),
            -- Set only where the discount exceeded what a salesperson may
            -- give on their own. Null means nobody needed to approve it.
            discount_approved_by UUID REFERENCES users(id),
            discount_approved_by_name VARCHAR(255),

            sent_at TIMESTAMPTZ,
            decided_at TIMESTAMPTZ,
            decision_note TEXT,

            converted_order_id UUID REFERENCES sales_orders(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_quotation_status CHECK (status IN
                ('DRAFT','SENT','ACCEPTED','DECLINED','EXPIRED',
                 'CONVERTED','CANCELLED')),
            CONSTRAINT ck_quotation_type CHECK (customer_type IN
                ('retail','wholesale')),
            CONSTRAINT ck_quotation_amounts CHECK (
                subtotal >= 0 AND discount_amount >= 0
                AND delivery_charge >= 0 AND total_amount >= 0),
            CONSTRAINT ck_quotation_discount CHECK (
                discount_percent >= 0 AND discount_percent <= 100),
            -- Converted means there is an order. One without the other is a
            -- quote nobody can trace to a sale, or a sale nobody can trace to
            -- a quote.
            CONSTRAINT ck_quotation_converted CHECK (
                (status = 'CONVERTED') = (converted_order_id IS NOT NULL))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_quotations_customer "
               "ON quotations (customer_id, created_at DESC)")
    # "What is outstanding" is the question this table is opened for.
    op.execute("CREATE INDEX IF NOT EXISTS ix_quotations_open "
               "ON quotations (valid_until) WHERE status IN ('SENT','ACCEPTED')")
    # One quote, one order.
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_quotation_order "
               "ON quotations (converted_order_id) "
               "WHERE converted_order_id IS NOT NULL")

    op.execute("""
        CREATE TABLE IF NOT EXISTS quotation_lines (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            quotation_id UUID NOT NULL
                REFERENCES quotations(id) ON DELETE CASCADE,
            product_id UUID REFERENCES products(id),

            -- Snapshots, not lookups. The product may be renamed or withdrawn
            -- after the quote is sent; the customer's copy does not change.
            product_name VARCHAR(255) NOT NULL,
            unit VARCHAR(32) NOT NULL,
            quantity NUMERIC(18,2) NOT NULL,
            unit_price NUMERIC(18,2) NOT NULL,
            line_total NUMERIC(18,2) NOT NULL,
            line_note TEXT,
            sequence INTEGER NOT NULL DEFAULT 0,

            CONSTRAINT ck_quotation_line_qty CHECK (quantity > 0),
            CONSTRAINT ck_quotation_line_price CHECK (unit_price >= 0)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_quotation_lines "
               "ON quotation_lines (quotation_id, sequence)")

    # Every change of state, and who made it. A quote is a commercial
    # commitment; "who sent this, who accepted it, and when" is asked
    # whenever one is disputed.
    op.execute("""
        CREATE TABLE IF NOT EXISTS quotation_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            quotation_id UUID NOT NULL
                REFERENCES quotations(id) ON DELETE CASCADE,
            from_status VARCHAR(16),
            to_status VARCHAR(16) NOT NULL,
            note TEXT,
            actor_id UUID REFERENCES users(id),
            actor_name VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_quotation_events "
               "ON quotation_events (quotation_id, created_at DESC)")

    op.execute("""
        CREATE OR REPLACE FUNCTION trg_quotation_event_append_only()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'quotation_events is append-only: it is the record of who '
                'promised what price, and who accepted it';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS no_edit_quotation_events "
               "ON quotation_events")
    op.execute("""
        CREATE TRIGGER no_edit_quotation_events
        BEFORE UPDATE OR DELETE ON quotation_events
        FOR EACH ROW EXECUTE FUNCTION trg_quotation_event_append_only()
    """)

    # The quote the order came from, so the link reads both ways.
    op.execute("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS "
               "quotation_id UUID REFERENCES quotations(id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_sales_orders_quotation "
               "ON sales_orders (quotation_id) WHERE quotation_id IS NOT NULL")

    op.execute("""
        INSERT INTO app_settings (key, value, value_type, description) VALUES
          ('QUOTATION_VALID_DAYS', '14', 'INTEGER',
           'How long a new quotation stands by default, in days.'),
          ('QUOTATION_MAX_DISCOUNT_PERCENT', '10', 'INTEGER',
           'The largest discount a salesperson may give without an '
           'administrator approving it.')
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_sales_orders_quotation")
    op.execute("ALTER TABLE sales_orders DROP COLUMN IF EXISTS quotation_id")
    op.execute("DROP TRIGGER IF EXISTS no_edit_quotation_events "
               "ON quotation_events")
    op.execute("DROP TABLE IF EXISTS quotation_events CASCADE")
    op.execute("DROP FUNCTION IF EXISTS trg_quotation_event_append_only()")
    op.execute("DROP TABLE IF EXISTS quotation_lines CASCADE")
    op.execute("DROP TABLE IF EXISTS quotations CASCADE")
    op.execute("DELETE FROM app_settings WHERE key IN "
               "('QUOTATION_VALID_DAYS','QUOTATION_MAX_DISCOUNT_PERCENT')")
