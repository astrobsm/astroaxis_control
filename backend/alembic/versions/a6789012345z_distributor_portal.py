"""Shareable ordering links for distributors.

WHAT A LINK IS
==============
A distributor opens a URL and places an order without logging in. That makes the
token in the URL a CREDENTIAL, and it is treated like one:

* **Only a hash of it is stored.** `token_sha256` is what lives in the database.
  A dump of this table -- a backup on a laptop, a support export, a leaked
  read-replica -- hands out no working links. The token exists exactly once, in
  the response that created it, and the app cannot show it again.
* **It expires.** `expires_at` is NOT NULL and has no "never" value. A link with
  no expiry is a permanent unauthenticated entry point that outlives the
  relationship it was issued for, and nobody ever remembers to revoke it.
* **It is revocable, and revocation is recorded**, not a deletion. Who revoked
  it and why is exactly what is wanted after a phone is lost.
* **Every use is logged** in an append-only table. If a link is misused, the
  question asked afterwards is "what was done with it and from where", and that
  cannot be answered from a `last_used_at` column that gets overwritten.

WHAT THE LINK DOES *NOT* GRANT
==============================
It places orders for ONE distributor and does nothing else. It cannot read
invoices, balances, other distributors, or anything about the company. The
catalogue it serves carries no prices -- see app/services/portal.py, where that
is enforced by never selecting the price columns rather than by filtering them
out of a response, because a filter is one refactor away from leaking.

THE ORDER IT PRODUCES IS A REAL SALES ORDER
===========================================
There is no separate distributor order table and no queue to reconcile. The
portal writes `sales_orders` + `sales_order_lines` through the same shape the
existing public order path uses, with `sales_channel = 'DISTRIBUTOR'` and
`distributor_id` set -- columns that already exist from the phase 1 migration.
`order_link_id` records WHICH link placed it, so if a link is later found to
have leaked, the orders it produced can be listed rather than guessed at.
"""
from alembic import op
import sqlalchemy as sa

revision = 'a6789012345z'
down_revision = 'z5678901234y'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS distributor_order_links (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            distributor_id UUID NOT NULL REFERENCES distributors(id),

            -- SHA-256 of the token. The token itself is never stored; see the
            -- module docstring.
            token_sha256 CHAR(64) UNIQUE NOT NULL,
            -- The last few characters, so a person can tell two links apart in
            -- a list without the app being able to reconstruct either.
            token_hint VARCHAR(12) NOT NULL,

            -- Who it was issued to, in the issuer's words: "Chinedu, Aba depot".
            label VARCHAR(160) NOT NULL,
            recipient_name VARCHAR(160),
            recipient_phone VARCHAR(40),

            -- NOT NULL on purpose. There is no "never expires".
            expires_at TIMESTAMPTZ NOT NULL,

            revoked_at TIMESTAMPTZ,
            revoked_by UUID REFERENCES users(id),
            revoke_reason TEXT,

            last_used_at TIMESTAMPTZ,
            use_count INTEGER NOT NULL DEFAULT 0,
            order_count INTEGER NOT NULL DEFAULT 0,

            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_link_expiry_after_issue CHECK (expires_at > created_at),
            CONSTRAINT ck_link_revocation_recorded CHECK (
                revoked_at IS NULL
                OR (revoke_reason IS NOT NULL AND revoke_reason <> ''))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_dol_distributor "
               "ON distributor_order_links (distributor_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dol_live "
               "ON distributor_order_links (expires_at) "
               "WHERE revoked_at IS NULL")

    # ---- every use of a public credential, append-only ----------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS distributor_order_link_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            link_id UUID REFERENCES distributor_order_links(id),
            -- Kept even when the link row is gone and for tokens that matched
            -- nothing, which is what a probe looks like.
            token_hint VARCHAR(12),
            event_type VARCHAR(24) NOT NULL,
            detail TEXT,
            sales_order_id UUID REFERENCES sales_orders(id),
            ip_address VARCHAR(64),
            user_agent VARCHAR(500),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_dole_type CHECK (event_type IN
                ('OPENED','QUOTED','ORDERED','REJECTED','EXPIRED','REVOKED',
                 'NOT_FOUND'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_dole_link "
               "ON distributor_order_link_events (link_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dole_recent "
               "ON distributor_order_link_events (created_at DESC)")

    op.execute("""
        CREATE OR REPLACE FUNCTION distributor_link_event_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION USING MESSAGE =
                'Link usage events are append-only. A record of who used a '
                'credential is worth nothing if it can be edited afterwards.';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_dole_immutable "
               "ON distributor_order_link_events")
    op.execute("""
        CREATE TRIGGER trg_dole_immutable
        BEFORE UPDATE OR DELETE ON distributor_order_link_events
        FOR EACH ROW EXECUTE FUNCTION distributor_link_event_immutable()
    """)

    # A revoked link stays revoked. Un-revoking would make the audit trail lie
    # about the window in which a leaked credential worked.
    op.execute("""
        CREATE OR REPLACE FUNCTION distributor_link_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Order links are not deletable. Revoke one instead, so the '
                    'orders it placed keep something to point at.';
            END IF;
            IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS NULL THEN
                RAISE EXCEPTION USING MESSAGE =
                    'A revoked link cannot be reinstated. Issue a new one -- '
                    'the old token may be in someone else''s hands, and the '
                    'record must keep saying when it stopped working.';
            END IF;
            IF NEW.token_sha256 <> OLD.token_sha256 THEN
                RAISE EXCEPTION USING MESSAGE =
                    'A link''s token cannot be changed. Issue a new link.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_dol_guard "
               "ON distributor_order_links")
    op.execute("""
        CREATE TRIGGER trg_dol_guard
        BEFORE UPDATE OR DELETE ON distributor_order_links
        FOR EACH ROW EXECUTE FUNCTION distributor_link_guard()
    """)

    # ---- which link placed an order ----------------------------------------
    op.execute("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS "
               "order_link_id UUID REFERENCES distributor_order_links(id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_sales_orders_link "
               "ON sales_orders (order_link_id) "
               "WHERE order_link_id IS NOT NULL")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_sales_orders_link")
    op.execute("ALTER TABLE sales_orders DROP COLUMN IF EXISTS order_link_id")
    op.execute("DROP TRIGGER IF EXISTS trg_dol_guard ON distributor_order_links")
    op.execute("DROP TRIGGER IF EXISTS trg_dole_immutable "
               "ON distributor_order_link_events")
    for fn in ("distributor_link_guard", "distributor_link_event_immutable"):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
    op.execute("DROP TABLE IF EXISTS distributor_order_link_events CASCADE")
    op.execute("DROP TABLE IF EXISTS distributor_order_links CASCADE")
