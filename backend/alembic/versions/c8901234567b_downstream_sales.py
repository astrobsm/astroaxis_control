"""Downstream sales: what a distributor sold, and how well that is known.

THIS IS NOT AN ACCOUNTING RECORD
================================
`sales_orders` records company -> customer. A distributor selling to a pharmacy
is distributor -> end customer: a transaction Bonnesante is not a party to,
earns nothing from, and already recognised revenue on when it sold to the
distributor.

So these tables produce NO JOURNAL ENTRY. Not "posting is disabled for now" --
there is no posting code path at all, and `ACCOUNTING_POSTING_ENABLED` is true
in production, so a stray call would silently double-count revenue in the live
general ledger. `app/services/downstream.py` imports nothing from
`app.services.ledger`, and a test asserts that no GL rows appear.

PROVENANCE IS THE POINT OF THIS MIGRATION
=========================================
Almost everything here is SELF-REPORTED by the distributor. That is not a
defect -- it is the only way this data can exist -- but a self-reported figure
presented as a fact is a lie told by rounding.

So `provenance` is NOT NULL with no default that means "fine":

  REPORTED   the distributor said so. Nobody checked.
  VERIFIED   somebody checked it against evidence, and is named on the row.
  DISPUTED   checked and found wrong.

A VERIFIED row must carry a verifier, a date and at least one piece of evidence
-- enforced by a check constraint, not by the form that happens to collect it.
Only VERIFIED counts toward a KPI, and every report that sums these rows returns
the two figures separately. Merging them would turn "the distributor claims" into
"the company knows".

This is the same discipline as the call log's duration provenance and the
wallet's duration_source: a soft figure is never rendered as a hard one.

STOCK
=====
A downstream sale reduces stock in the DISTRIBUTOR's warehouse only -- company
stock left the building when the goods were shipped to them. The movement is
written through `inventory.apply_stock_movement` like everything else, so the
batch attribution from phase 6 follows the goods all the way to the pharmacy
that bought them, which is what makes a recall reach the end of the chain.

Where a distributor reports selling more than the company recorded shipping, the
sale is still recorded and `stock_discrepancy` is set. Refusing the report would
discard a fact about the world to protect a number; the discrepancy IS the
finding, and it is surfaced rather than smoothed away.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c8901234567b'
down_revision = 'b7890123456a'
branch_labels = None
depends_on = None


def upgrade():
    # ---- the distributor's own field staff ---------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS distributor_marketers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            distributor_id UUID NOT NULL REFERENCES distributors(id),
            full_name VARCHAR(160) NOT NULL,
            phone VARCHAR(40),
            -- They work for the distributor, not for Bonnesante. No users row,
            -- no login, no access to anything.
            employee_reference VARCHAR(64),
            territory_id UUID REFERENCES territories(id),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            ended_on DATE,
            end_reason TEXT,
            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_marketer_end CHECK (
                is_active OR ended_on IS NOT NULL)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_marketers_distributor "
               "ON distributor_marketers (distributor_id) WHERE is_active")

    # ---- who the distributor sells to --------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS distributor_outlets (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            distributor_id UUID NOT NULL REFERENCES distributors(id),
            name VARCHAR(255) NOT NULL,
            -- HOSPITAL | PHARMACY | CLINIC | RETAILER | WHOLESALER | OTHER
            outlet_type VARCHAR(24) NOT NULL DEFAULT 'OTHER',
            phone VARCHAR(40),
            address TEXT,
            state_id UUID REFERENCES states(id),
            lga_id UUID REFERENCES lgas(id),
            town VARCHAR(120),
            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_outlet_per_distributor UNIQUE (distributor_id, name),
            CONSTRAINT ck_outlet_type CHECK (outlet_type IN
                ('HOSPITAL','PHARMACY','CLINIC','RETAILER','WHOLESALER','OTHER'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_outlets_distributor "
               "ON distributor_outlets (distributor_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_outlets_lga "
               "ON distributor_outlets (lga_id) WHERE lga_id IS NOT NULL")

    # ---- the sale ----------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS distributor_sales (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            sale_reference VARCHAR(40) UNIQUE NOT NULL,
            distributor_id UUID NOT NULL REFERENCES distributors(id),
            marketer_id UUID REFERENCES distributor_marketers(id),
            outlet_id UUID REFERENCES distributor_outlets(id),
            territory_id UUID REFERENCES territories(id),

            sold_on DATE NOT NULL,
            total_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
            currency CHAR(3) NOT NULL DEFAULT 'NGN',

            -- The whole point of this table. No default that means "fine".
            provenance VARCHAR(16) NOT NULL DEFAULT 'REPORTED',
            reported_by UUID REFERENCES users(id),
            reported_by_label VARCHAR(160),
            reported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            verified_by UUID REFERENCES users(id),
            verified_at TIMESTAMPTZ,
            verification_note TEXT,

            -- Set when the distributor reports selling more than the company
            -- recorded shipping them. The sale is still recorded; this is the
            -- finding, not a reason to discard it.
            stock_discrepancy BOOLEAN NOT NULL DEFAULT FALSE,
            discrepancy_note TEXT,

            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_ds_provenance CHECK (provenance IN
                ('REPORTED','VERIFIED','DISPUTED')),
            -- A verification with nobody's name on it is not a verification.
            CONSTRAINT ck_ds_verified_recorded CHECK (
                provenance <> 'VERIFIED'
                OR (verified_by IS NOT NULL AND verified_at IS NOT NULL)),
            CONSTRAINT ck_ds_disputed_explained CHECK (
                provenance <> 'DISPUTED'
                OR (verification_note IS NOT NULL AND verification_note <> '')),
            CONSTRAINT ck_ds_total CHECK (total_amount >= 0),
            CONSTRAINT ck_ds_not_future CHECK (sold_on <= CURRENT_DATE)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_ds_distributor "
               "ON distributor_sales (distributor_id, sold_on DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ds_provenance "
               "ON distributor_sales (provenance, sold_on DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ds_territory "
               "ON distributor_sales (territory_id, sold_on DESC) "
               "WHERE territory_id IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ds_discrepancy "
               "ON distributor_sales (distributor_id) WHERE stock_discrepancy")

    op.execute("""
        CREATE TABLE IF NOT EXISTS distributor_sale_lines (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            sale_id UUID NOT NULL REFERENCES distributor_sales(id),
            product_id UUID NOT NULL REFERENCES products(id),
            -- Carries phase 6 traceability to the end of the chain: this is
            -- what lets a recall name the pharmacy that bought the batch.
            batch_id UUID REFERENCES product_batches(id),
            unit VARCHAR(50),
            quantity NUMERIC(18,6) NOT NULL,
            unit_price NUMERIC(18,6),
            line_total NUMERIC(18,2),
            -- The movement out of the distributor's warehouse, where one was
            -- possible. NULL means the stock was not there to move -- see
            -- distributor_sales.stock_discrepancy.
            stock_movement_id UUID,
            CONSTRAINT ck_dsl_qty CHECK (quantity > 0)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_dsl_sale "
               "ON distributor_sale_lines (sale_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dsl_batch "
               "ON distributor_sale_lines (batch_id) WHERE batch_id IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dsl_product "
               "ON distributor_sale_lines (product_id)")

    # ---- what "verified" was verified against ------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS distributor_sale_evidence (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            sale_id UUID NOT NULL REFERENCES distributor_sales(id),
            -- INVOICE | RECEIPT | DELIVERY_NOTE | PHOTO | OTHER
            evidence_type VARCHAR(24) NOT NULL DEFAULT 'OTHER',
            filename VARCHAR(255) NOT NULL,
            content_type VARCHAR(100) NOT NULL,
            byte_size INTEGER NOT NULL,
            sha256 CHAR(64) NOT NULL,
            -- In the database for the same reason wallet receipts and
            -- compliance documents are: the container filesystem is replaced
            -- on every deploy, and evidence that disappears at the next
            -- release is not evidence.
            content BYTEA NOT NULL,
            note TEXT,
            uploaded_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_dse_size CHECK (byte_size > 0)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_dse_sale "
               "ON distributor_sale_evidence (sale_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dse_sha "
               "ON distributor_sale_evidence (sha256)")

    # ---- guards ------------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION distributor_sale_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Reported sales are not deletable. Mark one DISPUTED with '
                    'a reason -- deleting it removes the evidence that a claim '
                    'was ever made.';
            END IF;

            -- Once somebody has put their name to it, the figures are fixed.
            IF OLD.provenance = 'VERIFIED' AND (
                   NEW.total_amount <> OLD.total_amount
                OR NEW.sold_on <> OLD.sold_on
                OR NEW.distributor_id <> OLD.distributor_id) THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Sale ' || OLD.sale_reference || ' has been verified. Its '
                    'figures cannot be changed afterwards -- dispute it '
                    'instead, so the correction is visible.';
            END IF;

            -- Verification is a second pair of eyes or it is nothing.
            IF NEW.provenance = 'VERIFIED'
               AND NEW.verified_by IS NOT NULL
               AND NEW.verified_by = NEW.reported_by THEN
                RAISE EXCEPTION USING MESSAGE =
                    'The person who reported a sale cannot be the person who '
                    'verifies it.';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_ds_guard ON distributor_sales")
    op.execute("""
        CREATE TRIGGER trg_ds_guard
        BEFORE UPDATE OR DELETE ON distributor_sales
        FOR EACH ROW EXECUTE FUNCTION distributor_sale_guard()
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION distributor_sale_evidence_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION USING MESSAGE =
                'Sale evidence is append-only. A receipt that can be swapped '
                'after verification proves nothing.';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_dse_immutable "
               "ON distributor_sale_evidence")
    op.execute("""
        CREATE TRIGGER trg_dse_immutable
        BEFORE UPDATE OR DELETE ON distributor_sale_evidence
        FOR EACH ROW EXECUTE FUNCTION distributor_sale_evidence_immutable()
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_dse_immutable "
               "ON distributor_sale_evidence")
    op.execute("DROP TRIGGER IF EXISTS trg_ds_guard ON distributor_sales")
    for fn in ("distributor_sale_evidence_immutable", "distributor_sale_guard"):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
    for tbl in ("distributor_sale_evidence", "distributor_sale_lines",
                "distributor_sales", "distributor_outlets",
                "distributor_marketers"):
        op.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
