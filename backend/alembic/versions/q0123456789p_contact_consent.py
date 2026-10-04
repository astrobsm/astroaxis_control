"""How to reach a customer, whether we may, and an outbox that cannot send yet.

Revision ID: q0123456789p
Revises: p0123456789o
Create Date: 2026-10-04

WHAT THIS IS FOR
================
Everything downstream of here -- WhatsApp, campaigns, reorder reminders,
birthday greetings -- needs three things that do not exist yet:

  * a way to reach a customer that resolves to exactly ONE customer;
  * a record of whether they have agreed to be contacted, and when;
  * a single place every outbound message passes through.

This migration adds those and nothing else. In particular it adds no sender.
The outbox can be filled and nothing will come out of it, because the decision
about whether this company sends automated commercial messages to customers'
personal phones has not been taken. See the audit document.

THE CONSENT DEFAULT IS 'UNKNOWN', AND UNKNOWN MEANS NO
======================================================
A new customer has not agreed to anything. The temptation is to default to
opted-in because almost everybody is fine with it and asking is friction; the
problem is that the one person who was not fine with it has no way to tell
you so until after you have messaged them.

So the column defaults to UNKNOWN, and UNKNOWN is treated as refusal for
anything promotional. Transactional messages -- an invoice, a delivery update,
an answer to a question the customer asked -- are a different thing and are
not governed by this flag, because a customer who places an order has asked
to hear about it.

CONSENT IS A STATE AND A HISTORY, AND IT NEEDS BOTH
==================================================
The current state lives on `customers` because every send has to check it and
a join on every send is a join too many. The history lives in
`customer_consent_events`, append-only, because "when did this customer opt
out, and how did we come to message them afterwards" is a question with legal
weight, and a mutable column cannot answer it.

THE OUTBOX EXISTS SO THAT SENDING IS NEVER ACCIDENTAL
=====================================================
Every outbound message is a row before it is a message. That gives four
things no direct-send ever has:

  * **An idempotency key.** A retried job, a double-clicked button and a
    redelivered webhook cannot produce two messages to the same person.
  * **A reason.** Each row records WHY it was created -- which opportunity,
    which template, which person asked for it. A message nobody can explain
    afterwards is a message that should not have been sent.
  * **A kill switch that works.** One setting stops everything, and because
    nothing sends except by reading this table, there is no second path that
    keeps running.
  * **A record of what was attempted**, including what failed and why, which
    is the only honest basis for telling a customer what they were sent.

A STATUS OF 'BLOCKED' IS NOT A FAILURE
======================================
When a message is refused because the customer opted out, or because the
frequency cap was reached, the row is kept with status BLOCKED and the reason
recorded. Deleting it would erase the evidence that the rules worked, which is
exactly the evidence anyone auditing this would want to see.
"""
from alembic import op

revision = 'q0123456789p'
down_revision = 'p0123456789o'
branch_labels = None
depends_on = None


def upgrade():
    # ---- how to reach them -------------------------------------------------
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS "
               "whatsapp_number VARCHAR(32)")
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS "
               "customer_type VARCHAR(24)")
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS "
               "preferred_channel VARCHAR(16)")
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS "
               "assigned_staff_id UUID REFERENCES staff(id)")

    op.execute("ALTER TABLE customers DROP CONSTRAINT IF EXISTS ck_customer_type")
    op.execute("""
        ALTER TABLE customers ADD CONSTRAINT ck_customer_type CHECK (
            customer_type IS NULL OR customer_type IN
            ('INDIVIDUAL','HOSPITAL','CLINIC','PHARMACY','DISTRIBUTOR',
             'CORPORATE','OTHER'))
    """)
    op.execute("ALTER TABLE customers DROP CONSTRAINT IF EXISTS ck_customer_channel")
    op.execute("""
        ALTER TABLE customers ADD CONSTRAINT ck_customer_channel CHECK (
            preferred_channel IS NULL OR preferred_channel IN
            ('WHATSAPP','SMS','EMAIL','PHONE'))
    """)

    # One number, one customer. This is the whole point: an inbound message
    # arrives from a number and has to resolve to exactly one record, which is
    # why the duplicate merge had to come first. Partial, because most
    # customers will not have one set for a while.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_customers_whatsapp
            ON customers (whatsapp_number)
         WHERE whatsapp_number IS NOT NULL AND merged_into_id IS NULL
    """)

    # ---- whether we may contact them --------------------------------------
    op.execute("""
        ALTER TABLE customers ADD COLUMN IF NOT EXISTS
            marketing_consent VARCHAR(12) NOT NULL DEFAULT 'UNKNOWN'
    """)
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS "
               "consent_at TIMESTAMPTZ")
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS "
               "consent_source VARCHAR(40)")
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS "
               "do_not_contact BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS "
               "do_not_contact_until DATE")

    op.execute("ALTER TABLE customers DROP CONSTRAINT IF EXISTS ck_customer_consent")
    op.execute("""
        ALTER TABLE customers ADD CONSTRAINT ck_customer_consent CHECK (
            marketing_consent IN ('OPTED_IN','OPTED_OUT','UNKNOWN'))
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_customers_contactable "
               "ON customers (marketing_consent) "
               "WHERE do_not_contact = FALSE AND merged_into_id IS NULL")

    op.execute("""
        CREATE TABLE IF NOT EXISTS customer_consent_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            consent VARCHAR(12) NOT NULL,
            -- How we came to believe it. 'CUSTOMER_REPLY' carries more weight
            -- than 'STAFF_ENTERED', and anyone auditing will want to know
            -- which it was.
            source VARCHAR(40) NOT NULL,
            channel VARCHAR(16),
            evidence TEXT,
            actor_id UUID REFERENCES users(id),
            actor_name VARCHAR(255),
            -- clock_timestamp(), not NOW(): NOW() is the TRANSACTION's start
            -- time, so two consent changes written in one transaction get
            -- identical timestamps and the history cannot be put in order.
            -- For an append-only log the real wall clock is the right answer.
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT ck_consent_event CHECK (
                consent IN ('OPTED_IN','OPTED_OUT','UNKNOWN'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_consent_events_customer "
               "ON customer_consent_events (customer_id, created_at DESC)")

    op.execute("""
        CREATE OR REPLACE FUNCTION trg_consent_event_append_only()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'customer_consent_events is append-only: it is the evidence '
                'that a customer agreed, or asked us to stop';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS no_edit_consent_events "
               "ON customer_consent_events")
    op.execute("""
        CREATE TRIGGER no_edit_consent_events
        BEFORE UPDATE OR DELETE ON customer_consent_events
        FOR EACH ROW EXECUTE FUNCTION trg_consent_event_append_only()
    """)

    # ---- dates worth remembering ------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS customer_important_dates (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            date_type VARCHAR(32) NOT NULL,
            -- The day and month are what matter; the year is kept where it was
            -- given because a tenth anniversary is worth more than a ninth,
            -- but it is never required and never shown as an age.
            day SMALLINT NOT NULL,
            month SMALLINT NOT NULL,
            year SMALLINT,
            description VARCHAR(255),
            -- Never inferred. If nobody can say where a date came from, it
            -- should not be used to message anybody.
            source VARCHAR(40) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_cid_type CHECK (date_type IN
                ('BIRTHDAY','WEDDING_ANNIVERSARY','COMPANY_ANNIVERSARY',
                 'RELATIONSHIP_ANNIVERSARY','OTHER')),
            CONSTRAINT ck_cid_day CHECK (day BETWEEN 1 AND 31),
            CONSTRAINT ck_cid_month CHECK (month BETWEEN 1 AND 12),
            CONSTRAINT ck_cid_year CHECK (year IS NULL OR year BETWEEN 1900 AND 2100)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_date_type
            ON customer_important_dates (customer_id, date_type)
         WHERE is_active AND date_type <> 'OTHER'
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_customer_dates_calendar "
               "ON customer_important_dates (month, day) WHERE is_active")

    # ---- the outbox --------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS outbound_messages (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- A retried job, a double-clicked button and a redelivered
            -- webhook must not produce two messages to one person.
            idempotency_key VARCHAR(160) UNIQUE NOT NULL,

            channel VARCHAR(16) NOT NULL,
            customer_id UUID REFERENCES customers(id) ON DELETE SET NULL,
            to_address VARCHAR(160) NOT NULL,

            -- TRANSACTIONAL is about something the customer already did and
            -- is not governed by marketing consent. PROMOTIONAL is.
            category VARCHAR(16) NOT NULL,
            template_code VARCHAR(64),
            body TEXT NOT NULL,

            -- Why this message exists. A message nobody can explain after the
            -- fact is a message that should not have been sent.
            reason TEXT NOT NULL,
            opportunity_key VARCHAR(160),

            status VARCHAR(16) NOT NULL DEFAULT 'QUEUED',
            blocked_reason TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            provider_message_id VARCHAR(160),

            scheduled_for TIMESTAMPTZ,
            sent_at TIMESTAMPTZ,
            created_by UUID REFERENCES users(id),
            created_by_name VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_outbound_channel CHECK (channel IN
                ('WHATSAPP','SMS','EMAIL')),
            CONSTRAINT ck_outbound_category CHECK (category IN
                ('TRANSACTIONAL','PROMOTIONAL','SERVICE')),
            CONSTRAINT ck_outbound_status CHECK (status IN
                ('QUEUED','SENDING','SENT','FAILED','CANCELLED','BLOCKED')),
            -- A blocked message says why. Blocking silently is the same as
            -- losing the message.
            CONSTRAINT ck_outbound_blocked CHECK (
                status <> 'BLOCKED' OR blocked_reason IS NOT NULL),
            CONSTRAINT ck_outbound_reason CHECK (length(trim(reason)) >= 3)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_outbound_due "
               "ON outbound_messages (scheduled_for) WHERE status = 'QUEUED'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_outbound_customer "
               "ON outbound_messages (customer_id, created_at DESC)")
    # Frequency capping asks "how many promotional messages has this customer
    # had recently", on every send.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_outbound_promotional
            ON outbound_messages (customer_id, sent_at DESC)
         WHERE category = 'PROMOTIONAL' AND status = 'SENT'
    """)

    # ---- settings, including the stop button -------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key VARCHAR(80) PRIMARY KEY,
            value TEXT,
            value_type VARCHAR(12) NOT NULL DEFAULT 'STRING',
            description TEXT,
            updated_by UUID REFERENCES users(id),
            updated_by_name VARCHAR(255),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_setting_type CHECK (value_type IN
                ('STRING','BOOLEAN','INTEGER','DECIMAL'))
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS app_setting_changes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            key VARCHAR(80) NOT NULL,
            old_value TEXT,
            new_value TEXT,
            actor_id UUID REFERENCES users(id),
            actor_name VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_setting_changes_key "
               "ON app_setting_changes (key, created_at DESC)")

    # Everything that could send is OFF. Each has to be turned on deliberately
    # by a named person, and the change is recorded.
    op.execute("""
        INSERT INTO app_settings (key, value, value_type, description) VALUES
          ('OUTBOUND_MESSAGING_ENABLED', 'false', 'BOOLEAN',
           'Master switch. When off, nothing is sent to any customer on any '
           'channel, whatever else is configured. Turning this off is the '
           'stop button.'),
          ('WHATSAPP_ENABLED', 'false', 'BOOLEAN',
           'Allow sending on WhatsApp. Requires a configured provider.'),
          ('PROMOTIONAL_MESSAGING_ENABLED', 'false', 'BOOLEAN',
           'Allow PROMOTIONAL messages. Transactional messages are governed '
           'separately -- a customer who places an order has asked to hear '
           'about it.'),
          ('PROMOTIONAL_MAX_PER_7_DAYS', '2', 'INTEGER',
           'Most promotional messages one customer may receive in 7 days.'),
          ('PROMOTIONAL_MAX_PER_30_DAYS', '4', 'INTEGER',
           'Most promotional messages one customer may receive in 30 days.'),
          ('PROMOTIONAL_MIN_HOURS_BETWEEN', '48', 'INTEGER',
           'Smallest gap between two promotional messages to one customer.')
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS app_setting_changes CASCADE")
    op.execute("DROP TABLE IF EXISTS app_settings CASCADE")
    op.execute("DROP TABLE IF EXISTS outbound_messages CASCADE")
    op.execute("DROP TABLE IF EXISTS customer_important_dates CASCADE")
    op.execute("DROP TRIGGER IF EXISTS no_edit_consent_events "
               "ON customer_consent_events")
    op.execute("DROP TABLE IF EXISTS customer_consent_events CASCADE")
    op.execute("DROP FUNCTION IF EXISTS trg_consent_event_append_only()")
    op.execute("DROP INDEX IF EXISTS uq_customers_whatsapp")
    op.execute("DROP INDEX IF EXISTS ix_customers_contactable")
    for c in ("ck_customer_type", "ck_customer_channel", "ck_customer_consent"):
        op.execute(f"ALTER TABLE customers DROP CONSTRAINT IF EXISTS {c}")
    for col in ("whatsapp_number", "customer_type", "preferred_channel",
                "assigned_staff_id", "consent_at", "consent_source",
                "do_not_contact_until"):
        op.execute(f"ALTER TABLE customers DROP COLUMN IF EXISTS {col}")
    # marketing_consent and do_not_contact stay. A customer who asked not to
    # be contacted must not become contactable again because somebody rolled
    # back a schema.
