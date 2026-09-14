"""A shareable registration link, and linking an applicant to who they already are.

TWO DIFFERENT KINDS OF PUBLIC LINK
==================================
The ordering link (a6789012345z) is issued to ONE named distributor and places
orders on THEIR account. A registration link is the opposite: issued once and
shared widely -- printed, forwarded, put in a WhatsApp group -- so that anyone
who wants to apply can. It is not a credential for an account, because there is
no account yet.

That changes what it has to defend against. An ordering token identifies a
distributor; a registration token identifies nothing, so every submission
arriving through it is an unverified claim from a stranger. It therefore lands
in `distributor_registrations` and NOT in `distributors`.

WHY UNVETTED SUBMISSIONS DO NOT GO STRAIGHT INTO THE REGISTER
============================================================
`distributors` is the company's own record of who it trades with. Letting a
public form write rows into it means anybody with the link can put a name in the
register, burn distributor codes, and appear in duplicate checks against real
applicants. A DRAFT row would be inert but still noise, and noise in a register
is what makes people stop trusting it.

So a registration is a separate thing: an unverified claim, awaiting triage.
It is not a duplicate of a distributor -- a distributor is somebody the company
has decided to trade with, and a registration is somebody who asked. Approving
one CREATES the distributor through the ordinary `create_distributor` path, so
there is exactly one way a distributor comes into existence.

WHAT THE APPLICANT IS NOT SHOWN
===============================
`claimed_customer_id` lets an applicant say "you already sell to me". It is set
from a CONFIRMATION, never from a browsable list: an unauthenticated form that
suggests customer names as you type hands the company's whole customer base to
anyone who has the link. The public endpoint accepts a full phone number and
answers about at most one account; staff see the full candidate list at review
time, where they are authenticated and can judge.

The claim is also just a claim. `claimed_customer_id` is what the applicant
said; `linked_customer_id` is what a reviewer decided, and they are separate
columns for the same reason REPORTED and VERIFIED are separate in
distributor_sales.

THERE IS NOTHING TO IMPORT
==========================
"Import their past transactions" sounds like copying rows. It must not be: a
distributor IS a customer plus a warehouse, so an approved applicant linked to an
existing customer ALREADY has their whole history in `sales_orders`. Copying it
would double-count revenue and create a second answer to "what did they buy".

Linking therefore ATTRIBUTES rather than imports -- it sets `distributor_id` on
the orders that were always theirs. `sales_channel` is deliberately left alone:
those were direct sales at the time, and rewriting them as distributor sales
would be falsifying history to make a report look tidy.
"""
from alembic import op
import sqlalchemy as sa

revision = 'h3456789012g'
down_revision = 'g2345678901f'
branch_labels = None
depends_on = None


def upgrade():
    # ---- the shareable link ------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS distributor_registration_links (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- Only the hash, as with ordering links. A dump of this table
            -- hands out no working link.
            token_sha256 CHAR(64) UNIQUE NOT NULL,
            token_hint VARCHAR(12) NOT NULL,

            -- Where it was published: "Trade fair, Aba, March" tells you what
            -- to revoke when the campaign ends.
            label VARCHAR(160) NOT NULL,
            campaign VARCHAR(160),

            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ,
            revoked_by UUID REFERENCES users(id),
            revoke_reason TEXT,

            -- A registration link is shared widely, so it is the one that gets
            -- scraped and spammed. NULL means no ceiling was set.
            max_submissions INTEGER,
            submission_count INTEGER NOT NULL DEFAULT 0,
            view_count INTEGER NOT NULL DEFAULT 0,
            last_used_at TIMESTAMPTZ,

            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_reglink_expiry CHECK (expires_at > created_at),
            CONSTRAINT ck_reglink_max CHECK (
                max_submissions IS NULL OR max_submissions > 0),
            CONSTRAINT ck_reglink_revocation CHECK (
                revoked_at IS NULL
                OR (revoke_reason IS NOT NULL AND revoke_reason <> ''))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_reglink_live "
               "ON distributor_registration_links (expires_at) "
               "WHERE revoked_at IS NULL")

    # ---- what a stranger submitted -----------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS distributor_registrations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            registration_reference VARCHAR(40) UNIQUE NOT NULL,
            link_id UUID REFERENCES distributor_registration_links(id),

            -- Everything below this line is UNVERIFIED. It is what somebody
            -- typed into a public form.
            legal_name VARCHAR(255) NOT NULL,
            trading_name VARCHAR(255),
            entity_type VARCHAR(20) NOT NULL DEFAULT 'COMPANY',
            contact_name VARCHAR(160),
            phone VARCHAR(40) NOT NULL,
            whatsapp VARCHAR(40),
            email VARCHAR(255),
            business_address TEXT,
            state_id UUID REFERENCES states(id),
            lga_id UUID REFERENCES lgas(id),
            town VARCHAR(120),
            cac_number VARCHAR(64),
            tin VARCHAR(64),
            years_in_operation INTEGER,
            business_type VARCHAR(120),
            employee_count INTEGER,
            marketer_count INTEGER,
            storage_description TEXT,
            products_of_interest TEXT,
            applicant_note TEXT,

            -- What the APPLICANT claimed: "you already sell to me".
            claims_existing_customer BOOLEAN NOT NULL DEFAULT FALSE,
            claimed_customer_id UUID REFERENCES customers(id),
            -- What a REVIEWER decided. Separate column on purpose: a claim and
            -- a confirmed fact are different things, exactly as REPORTED and
            -- VERIFIED are in distributor_sales.
            linked_customer_id UUID REFERENCES customers(id),

            status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
            reviewed_by UUID REFERENCES users(id),
            reviewed_at TIMESTAMPTZ,
            review_note TEXT,
            -- What approving it created. Without this, an approved
            -- registration and the distributor it produced are two unrelated
            -- rows.
            distributor_id UUID REFERENCES distributors(id),

            -- How many historical orders the link attributed. Recorded because
            -- "we brought their history across" needs a number somebody can
            -- check.
            orders_attributed INTEGER,

            submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ip_address VARCHAR(64),
            user_agent VARCHAR(500),

            CONSTRAINT ck_reg_status CHECK (status IN
                ('PENDING','REVIEWING','APPROVED','REJECTED','DUPLICATE')),
            CONSTRAINT ck_reg_entity CHECK (entity_type IN
                ('COMPANY','INDIVIDUAL','PARTNERSHIP','COOPERATIVE')),
            CONSTRAINT ck_reg_name CHECK (length(trim(legal_name)) >= 2),
            CONSTRAINT ck_reg_phone CHECK (length(trim(phone)) >= 7),
            -- A decision has to have somebody's name and a reason on it.
            CONSTRAINT ck_reg_decided CHECK (
                status NOT IN ('APPROVED','REJECTED','DUPLICATE')
                OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL
                    AND review_note IS NOT NULL AND review_note <> '')),
            -- An approved registration must have produced a distributor.
            CONSTRAINT ck_reg_approved_created CHECK (
                status <> 'APPROVED' OR distributor_id IS NOT NULL)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_reg_pending "
               "ON distributor_registrations (submitted_at DESC) "
               "WHERE status IN ('PENDING','REVIEWING')")
    op.execute("CREATE INDEX IF NOT EXISTS ix_reg_link "
               "ON distributor_registrations (link_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_reg_phone "
               "ON distributor_registrations (phone)")

    op.execute("""
        CREATE OR REPLACE FUNCTION distributor_registration_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Registrations are not deletable. Reject one with a reason '
                    -- Somebody applied. That they applied, and what happened,
                    -- is the record; a deleted application looks identical to
                    -- one that was never made.
                    'so the fact that they applied stays on the record.';
            END IF;

            -- What the applicant typed is evidence of what they claimed. The
            -- review is recorded alongside it, never over it.
            IF NEW.legal_name IS DISTINCT FROM OLD.legal_name
               OR NEW.phone IS DISTINCT FROM OLD.phone
               OR NEW.cac_number IS DISTINCT FROM OLD.cac_number
               OR NEW.claims_existing_customer IS DISTINCT FROM
                  OLD.claims_existing_customer
               OR NEW.claimed_customer_id IS DISTINCT FROM
                  OLD.claimed_customer_id THEN
                RAISE EXCEPTION USING MESSAGE =
                    'What the applicant submitted cannot be edited. Record the '
                    'review and the decision alongside it.';
            END IF;

            IF OLD.status IN ('APPROVED','REJECTED','DUPLICATE')
               AND NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Registration ' || OLD.registration_reference ||
                    ' was already ' || lower(OLD.status) ||
                    '. Raise a new registration rather than reopening this one.';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_registration_guard "
               "ON distributor_registrations")
    op.execute("""
        CREATE TRIGGER trg_registration_guard
        BEFORE UPDATE OR DELETE ON distributor_registrations
        FOR EACH ROW EXECUTE FUNCTION distributor_registration_guard()
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION registration_link_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Registration links are not deletable. Revoke one so the '
                    'registrations it produced keep something to point at.';
            END IF;
            IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS NULL THEN
                RAISE EXCEPTION USING MESSAGE =
                    'A revoked link cannot be reinstated. Issue a new one -- '
                    'the old token may be on a poster somebody still has.';
            END IF;
            IF NEW.token_sha256 <> OLD.token_sha256 THEN
                RAISE EXCEPTION USING MESSAGE =
                    'A link''s token cannot be changed.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_reglink_guard "
               "ON distributor_registration_links")
    op.execute("""
        CREATE TRIGGER trg_reglink_guard
        BEFORE UPDATE OR DELETE ON distributor_registration_links
        FOR EACH ROW EXECUTE FUNCTION registration_link_guard()
    """)

    # ---- where an attributed order came from -------------------------------
    #
    # Linking sets sales_orders.distributor_id on orders that were placed before
    # the distributor existed. This column records that the attribution was made
    # by a link rather than by the order being placed as a distributor order --
    # otherwise the two are indistinguishable, and nobody can tell which of a
    # distributor's orders they actually placed as a distributor.
    op.execute("ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS "
               "distributor_attributed_at TIMESTAMPTZ")
    op.execute("CREATE INDEX IF NOT EXISTS ix_sales_orders_attributed "
               "ON sales_orders (distributor_id) "
               "WHERE distributor_attributed_at IS NOT NULL")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_sales_orders_attributed")
    op.execute("ALTER TABLE sales_orders "
               "DROP COLUMN IF EXISTS distributor_attributed_at")
    op.execute("DROP TRIGGER IF EXISTS trg_reglink_guard "
               "ON distributor_registration_links")
    op.execute("DROP TRIGGER IF EXISTS trg_registration_guard "
               "ON distributor_registrations")
    for fn in ("registration_link_guard", "distributor_registration_guard"):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
    op.execute("DROP TABLE IF EXISTS distributor_registrations CASCADE")
    op.execute("DROP TABLE IF EXISTS distributor_registration_links CASCADE")
