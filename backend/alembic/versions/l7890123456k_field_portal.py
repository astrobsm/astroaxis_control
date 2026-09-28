"""The field portal: distributor marketers get a login of their own.

Revision ID: l7890123456k
Revises: k6789012345j
Create Date: 2026-09-28

WHAT THIS CHANGES, AND WHAT IT DELIBERATELY DOES NOT
====================================================
`distributor_marketers` (c8901234567b) carries a comment that this migration
has to answer directly:

    They work for the distributor, not for Bonnesante. No users row, no login,
    no access to anything.

That was right, and it stays right. These people are employed by Penacea and
Tripleluminance, not by Bonnesante Medicals, and giving them rows in `users`
would put a competitor's staff inside the system that holds this company's
payroll, formulations, margins and customer book. One forgotten WHERE clause
in any of sixty API modules would be the whole business.

So they do NOT get a users row. They get an account in a table of its own,
which authenticates against a DIFFERENT secret, issues a token of a different
type, and is accepted by exactly one router -- the field portal. There is no
route from a field token into the ERP, in the same way there is none from a
distributor ordering link or a meeting guest pass.

THE TENANCY BOUNDARY IS THE TOKEN, NOT THE REQUEST
==================================================
Every row a marketer can see is reached through `distributor_id`, and that
value is read from their signed token -- never from a path, a query string or
a body. A marketer cannot ask for another distributor's catalogue because
there is nowhere in the API to say which distributor they mean.

THE PRICE LIST IS ALSO THE PRODUCT RANGE
========================================
Rather than a separate "which products may this distributor sell" table that
would immediately drift out of step, a distributor's range IS the set of
products they have priced. If it is on their price list they carry it; if it
is not, their marketers cannot see it. One table, one truth, and no way for
the two answers to disagree.

LOCATION DATA IS PERSONAL DATA
==============================
`field_marketer_locations` tracks where a named individual was, minute by
minute. Under the NDPA 2023 that is personal data being processed, and three
things follow, all enforced here rather than left to a policy document:

  * `consented_at` on the account. No consent, no pings accepted -- checked in
    the service, and the column exists so the date can be produced if anyone
    ever asks when it was given.
  * `captured_while_working` on every ping, so a trail recorded outside a
    marketer's own working hours is identifiable and can be excluded or
    deleted without guessing.
  * `purge_after` on every ping, set when it is written. A retention limit
    that lives on the row cannot be forgotten by a later maintainer who did
    not read the policy.
"""
from alembic import op

revision = 'l7890123456k'
down_revision = 'k6789012345j'
branch_labels = None
depends_on = None


def upgrade():
    # ---- the login -------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS field_marketer_accounts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            marketer_id UUID NOT NULL UNIQUE
                REFERENCES distributor_marketers(id) ON DELETE CASCADE,
            -- Denormalised from the marketer on purpose: it is the tenancy
            -- key, it is read on every single request, and a marketer moving
            -- between distributors is an ending and a new engagement, not an
            -- update.
            distributor_id UUID NOT NULL REFERENCES distributors(id),

            -- They sign in with a phone number. Field staff know their number;
            -- many do not have a work email.
            login_phone VARCHAR(40) NOT NULL UNIQUE,
            email VARCHAR(255),
            password_hash VARCHAR(255) NOT NULL,

            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            is_locked BOOLEAN NOT NULL DEFAULT FALSE,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            last_login_at TIMESTAMPTZ,

            -- Consent to being located. Null means never given, and the
            -- service refuses to store a position without it.
            consented_at TIMESTAMPTZ,
            consent_text TEXT,

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_field_accounts_distributor "
               "ON field_marketer_accounts (distributor_id) WHERE is_active")

    # ---- how an account comes into existence -----------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS field_marketer_invites (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            distributor_id UUID NOT NULL REFERENCES distributors(id)
                ON DELETE CASCADE,
            -- Null until the invite is used: the marketer record is created
            -- from what they enter, so one link can onboard a whole team.
            marketer_id UUID REFERENCES distributor_marketers(id),
            label VARCHAR(160) NOT NULL,

            -- Only the hash, as every other shareable link in this system.
            token_sha256 VARCHAR(64) UNIQUE NOT NULL,
            token_hint VARCHAR(12),
            expires_at TIMESTAMPTZ NOT NULL,
            max_uses INTEGER,
            use_count INTEGER NOT NULL DEFAULT 0,
            revoked_at TIMESTAMPTZ,
            revoke_reason TEXT,

            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_field_invites_live "
               "ON field_marketer_invites (distributor_id) "
               "WHERE revoked_at IS NULL")

    # ---- what the distributor sells, and for how much ---------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS distributor_price_list (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            distributor_id UUID NOT NULL REFERENCES distributors(id)
                ON DELETE CASCADE,
            product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            unit VARCHAR(32) NOT NULL DEFAULT 'unit',
            price NUMERIC(18,2) NOT NULL,

            -- A price has a life. Changing one ends the old row and opens a
            -- new one, so a visit logged last month can still be read against
            -- the price that was quoted at the time.
            effective_from DATE NOT NULL DEFAULT CURRENT_DATE,
            effective_to DATE,

            set_by UUID REFERENCES users(id),
            note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_dpl_price CHECK (price >= 0),
            CONSTRAINT ck_dpl_dates CHECK (
                effective_to IS NULL OR effective_to >= effective_from)
        )
    """)
    # One live price per product per unit per distributor. Two live prices is
    # two answers to "what does this cost", which is the question the whole
    # table exists to answer.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_dpl_live
            ON distributor_price_list (distributor_id, product_id, lower(unit))
         WHERE effective_to IS NULL
    """)

    # ---- who the marketer brought in --------------------------------------
    # Tagging columns on the EXISTING customers table rather than a parallel
    # customer list. A customer registered by a distributor's marketer is a
    # customer of Bonnesante Medicals, in the same table as every other, or
    # the company ends up with two answers to who it sells to.
    op.execute("""
        ALTER TABLE customers
            ADD COLUMN IF NOT EXISTS registered_by_marketer_id UUID
                REFERENCES distributor_marketers(id)
    """)
    op.execute("""
        ALTER TABLE customers
            ADD COLUMN IF NOT EXISTS introduced_by_distributor_id UUID
                REFERENCES distributors(id)
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_customers_by_marketer "
               "ON customers (registered_by_marketer_id) "
               "WHERE registered_by_marketer_id IS NOT NULL")

    # ---- what the marketer did --------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS field_visits (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            marketer_id UUID NOT NULL REFERENCES distributor_marketers(id)
                ON DELETE CASCADE,
            distributor_id UUID NOT NULL REFERENCES distributors(id),

            -- Whichever of these is known. A cold call on a pharmacy that is
            -- not yet in any list is still a visit worth recording.
            customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
            outlet_id UUID REFERENCES distributor_outlets(id) ON DELETE SET NULL,
            place_name VARCHAR(255) NOT NULL,

            purpose VARCHAR(32) NOT NULL DEFAULT 'VISIT',
            outcome TEXT,
            products_discussed TEXT,
            order_value NUMERIC(18,2),
            follow_up_on DATE,

            -- Where the phone said it was when the visit was logged.
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            accuracy_m DOUBLE PRECISION,
            address TEXT,

            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ended_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_fv_purpose CHECK (purpose IN
                ('VISIT','CALL','DELIVERY','COLLECTION','PROSPECTING','OTHER')),
            CONSTRAINT ck_fv_interval CHECK (
                ended_at IS NULL OR ended_at >= started_at)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_field_visits_marketer "
               "ON field_visits (marketer_id, started_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_field_visits_distributor "
               "ON field_visits (distributor_id, started_at DESC)")

    # ---- where the marketer was -------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS field_marketer_locations (
            id BIGSERIAL PRIMARY KEY,
            marketer_id UUID NOT NULL REFERENCES distributor_marketers(id)
                ON DELETE CASCADE,
            distributor_id UUID NOT NULL REFERENCES distributors(id),

            latitude DOUBLE PRECISION NOT NULL,
            longitude DOUBLE PRECISION NOT NULL,
            accuracy_m DOUBLE PRECISION,
            speed_mps DOUBLE PRECISION,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            -- Whether the marketer had started their day when this was taken.
            -- A trail from a Sunday afternoon is identifiable and removable
            -- without anybody having to guess which rows they were.
            captured_while_working BOOLEAN NOT NULL DEFAULT TRUE,

            -- Set on write, not applied by a policy somebody has to remember.
            purge_after DATE NOT NULL,

            CONSTRAINT ck_fml_lat CHECK (latitude BETWEEN -90 AND 90),
            CONSTRAINT ck_fml_lng CHECK (longitude BETWEEN -180 AND 180)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_fml_trail "
               "ON field_marketer_locations (marketer_id, recorded_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_fml_purge "
               "ON field_marketer_locations (purge_after)")

    # ---- what was done in the portal --------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS field_audit_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            distributor_id UUID REFERENCES distributors(id) ON DELETE SET NULL,
            marketer_id UUID REFERENCES distributor_marketers(id)
                ON DELETE SET NULL,
            event_type VARCHAR(48) NOT NULL,
            detail JSONB,
            ip_address VARCHAR(64),
            user_agent VARCHAR(500),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_field_audit "
               "ON field_audit_logs (distributor_id, created_at DESC)")


def downgrade():
    for table in ("field_audit_logs", "field_marketer_locations",
                  "field_visits", "distributor_price_list",
                  "field_marketer_invites", "field_marketer_accounts"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    # The tagging columns stay. They carry which marketer introduced a
    # customer, and dropping them would destroy that attribution for good --
    # a downgrade should undo a schema, not erase a commercial fact.
