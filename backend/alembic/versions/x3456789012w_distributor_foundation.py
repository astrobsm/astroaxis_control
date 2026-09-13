"""Distributor foundation: geography, territories, and distributor identity.

Revision ID: x3456789012w
Revises: w2345678901v
Create Date: 2026-09-13

THE DECISION THIS SCHEMA ENCODES
--------------------------------
A distributor is a CUSTOMER plus a WAREHOUSE plus a profile. Not a second
accounting system, not a second inventory.

  * `distributors.customer_id` -> the existing customers table. To accounting a
    distributor simply IS a customer, so invoices, payments, AR ageing and GL
    posting need no new financial code and cannot disagree with a second copy.
  * `distributors.warehouse_id` -> the existing warehouses table. Distributor
    stock is a real stock_levels balance, and shipping to a distributor is
    transfer_stock(main -> distributor): an existing, audited, balance-checked
    operation rather than a number decremented somewhere else.
  * `distributors.user_id` -> the portal login.

Both links are UNIQUE in both directions. One distributor cannot hold two
accounting identities, and one customer account cannot back two distributors --
which is the duplicate-identity failure that makes reconciliation impossible
later.

ADMINISTRATIVE GEOGRAPHY IS NOT COMMERCIAL GEOGRAPHY
----------------------------------------------------
States and LGAs are administrative facts. A territory is a commercial decision,
and the two are deliberately separate tables joined by `territory_lgas`. A
territory may cover part of one LGA or several whole ones, and management can
re-cut territories without anyone pretending the map of Nigeria changed.

HISTORY IS NEVER OVERWRITTEN
---------------------------
Two tables exist solely so that history survives a change:

  * `territory_targets` -- a target is never updated. A new row supersedes the
    old one with an effective date and a reason, so a report for August still
    uses August's target after October's was raised.
  * `territory_assignments` -- a reassignment closes the old row and opens a
    new one. Historical sales stay attached to the distributor who made them.

Exclusivity is enforced by a partial unique index rather than by application
code, because "this territory already belongs to someone" is exactly the check
that gets forgotten on the one code path nobody tested.
"""
from alembic import op
import sqlalchemy as sa

revision = 'x3456789012w'
down_revision = 'w2345678901v'
branch_labels = None
depends_on = None


# 36 states + the Federal Capital Territory, with capitals and geopolitical
# zones. Seeded because they are stable administrative facts, not configuration.
NIGERIAN_STATES = [
    # code, name, capital, geopolitical zone
    ("AB", "Abia", "Umuahia", "South East"),
    ("AD", "Adamawa", "Yola", "North East"),
    ("AK", "Akwa Ibom", "Uyo", "South South"),
    ("AN", "Anambra", "Awka", "South East"),
    ("BA", "Bauchi", "Bauchi", "North East"),
    ("BY", "Bayelsa", "Yenagoa", "South South"),
    ("BE", "Benue", "Makurdi", "North Central"),
    ("BO", "Borno", "Maiduguri", "North East"),
    ("CR", "Cross River", "Calabar", "South South"),
    ("DE", "Delta", "Asaba", "South South"),
    ("EB", "Ebonyi", "Abakaliki", "South East"),
    ("ED", "Edo", "Benin City", "South South"),
    ("EK", "Ekiti", "Ado-Ekiti", "South West"),
    ("EN", "Enugu", "Enugu", "South East"),
    ("FC", "Federal Capital Territory", "Abuja", "North Central"),
    ("GO", "Gombe", "Gombe", "North East"),
    ("IM", "Imo", "Owerri", "South East"),
    ("JI", "Jigawa", "Dutse", "North West"),
    ("KD", "Kaduna", "Kaduna", "North West"),
    ("KN", "Kano", "Kano", "North West"),
    ("KT", "Katsina", "Katsina", "North West"),
    ("KE", "Kebbi", "Birnin Kebbi", "North West"),
    ("KO", "Kogi", "Lokoja", "North Central"),
    ("KW", "Kwara", "Ilorin", "North Central"),
    ("LA", "Lagos", "Ikeja", "South West"),
    ("NA", "Nasarawa", "Lafia", "North Central"),
    ("NI", "Niger", "Minna", "North Central"),
    ("OG", "Ogun", "Abeokuta", "South West"),
    ("ON", "Ondo", "Akure", "South West"),
    ("OS", "Osun", "Osogbo", "South West"),
    ("OY", "Oyo", "Ibadan", "South West"),
    ("PL", "Plateau", "Jos", "North Central"),
    ("RI", "Rivers", "Port Harcourt", "South South"),
    ("SO", "Sokoto", "Sokoto", "North West"),
    ("TA", "Taraba", "Jalingo", "North East"),
    ("YO", "Yobe", "Damaturu", "North East"),
    ("ZA", "Zamfara", "Gusau", "North West"),
]

# Nigeria has 774 LGAs. Only the two sets seeded here are reproduced from
# memory with confidence; the rest must be imported from an authoritative
# source (NBS/INEC) through the LGA importer rather than guessed at, because a
# misspelt or missing LGA silently corrupts every territory report built on it.
# `POST /api/geography/lgas/import` exists for exactly that.
LAGOS_LGAS = [
    "Agege", "Ajeromi-Ifelodun", "Alimosho", "Amuwo-Odofin", "Apapa",
    "Badagry", "Epe", "Eti-Osa", "Ifako-Ijaiye", "Ikeja", "Ikorodu",
    "Kosofe", "Lagos Island", "Lagos Mainland", "Mushin", "Ojo",
    "Oshodi-Isolo", "Shomolu", "Surulere", "Ibeju-Lekki",
]

FCT_AREA_COUNCILS = [
    "Abaji", "Abuja Municipal", "Bwari", "Gwagwalada", "Kuje", "Kwali",
]


def upgrade():
    # ---- administrative geography --------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS countries (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            iso2 CHAR(2) UNIQUE NOT NULL,
            iso3 CHAR(3),
            name VARCHAR(128) NOT NULL,
            currency_code CHAR(3) NOT NULL DEFAULT 'NGN',
            phone_prefix VARCHAR(8),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS states (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            country_id UUID NOT NULL REFERENCES countries(id),
            code VARCHAR(8) NOT NULL,
            name VARCHAR(128) NOT NULL,
            capital VARCHAR(128),
            -- Nigeria's six geopolitical zones. A reporting dimension, not an
            -- administrative tier -- hence a column rather than a table.
            geopolitical_zone VARCHAR(32),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_states_country_code UNIQUE (country_id, code),
            CONSTRAINT uq_states_country_name UNIQUE (country_id, name)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_states_country "
               "ON states (country_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS lgas (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            state_id UUID NOT NULL REFERENCES states(id),
            code VARCHAR(16),
            name VARCHAR(128) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_lgas_state_name UNIQUE (state_id, name)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_lgas_state ON lgas (state_id)")

    # ---- commercial geography ------------------------------------------
    #
    # A territory is a commercial decision. It belongs to a state for reporting
    # and rolls up to a region, but its actual coverage is the set of LGAs in
    # territory_lgas -- so a state can be cut into six zones, or two, or
    # recut next year, without anything pretending the administrative map moved.
    op.execute("""
        CREATE TABLE IF NOT EXISTS regions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code VARCHAR(16) UNIQUE NOT NULL,
            name VARCHAR(128) NOT NULL,
            country_id UUID REFERENCES countries(id),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS territories (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code VARCHAR(32) UNIQUE NOT NULL,
            name VARCHAR(160) NOT NULL,
            state_id UUID NOT NULL REFERENCES states(id),
            region_id UUID REFERENCES regions(id),
            description TEXT,
            boundary_note TEXT,
            -- Descriptive; the authoritative coverage is territory_lgas.
            towns TEXT,
            -- AVAILABLE   no distributor holds it
            -- RESERVED    held for a named applicant, not yet assigned
            -- ASSIGNED    an active assignment exists
            -- SUSPENDED   trading paused
            -- UNDER_REVIEW performance or compliance review in progress
            -- RETIRED     no longer traded; history preserved
            status VARCHAR(16) NOT NULL DEFAULT 'AVAILABLE',
            is_exclusive BOOLEAN NOT NULL DEFAULT TRUE,
            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_territory_status CHECK (status IN
                ('AVAILABLE','RESERVED','ASSIGNED','SUSPENDED',
                 'UNDER_REVIEW','RETIRED'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_territories_state "
               "ON territories (state_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_territories_status "
               "ON territories (status)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS territory_lgas (
            territory_id UUID NOT NULL
                REFERENCES territories(id) ON DELETE CASCADE,
            lga_id UUID NOT NULL REFERENCES lgas(id),
            -- FALSE where a territory covers only part of an LGA, so a later
            -- split does not have to guess whether coverage was complete.
            is_whole_lga BOOLEAN NOT NULL DEFAULT TRUE,
            PRIMARY KEY (territory_id, lga_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_territory_lgas_lga "
               "ON territory_lgas (lga_id)")

    # ---- targets, versioned ---------------------------------------------
    #
    # A target is never UPDATEd. Raising one inserts a new row and closes the
    # old, so August's report keeps August's target after October's was raised.
    op.execute("""
        CREATE TABLE IF NOT EXISTS territory_targets (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            territory_id UUID NOT NULL REFERENCES territories(id),
            monthly_target NUMERIC(18,2) NOT NULL,
            currency CHAR(3) NOT NULL DEFAULT 'NGN',
            effective_from DATE NOT NULL,
            -- NULL = still in force. Closed when superseded.
            effective_to DATE,
            reason TEXT,
            approved_by UUID REFERENCES users(id),
            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_target_positive CHECK (monthly_target >= 0),
            CONSTRAINT ck_target_period CHECK (
                effective_to IS NULL OR effective_to >= effective_from)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_territory_targets_lookup "
               "ON territory_targets (territory_id, effective_from DESC)")
    # One open target per territory. Two would make "the current target"
    # ambiguous, and every downstream percentage meaningless.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_territory_target_open
            ON territory_targets (territory_id)
         WHERE effective_to IS NULL
    """)

    # ---- distributor identity -------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS distributors (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            distributor_code VARCHAR(32) UNIQUE NOT NULL,

            legal_name VARCHAR(255) NOT NULL,
            trading_name VARCHAR(255),
            entity_type VARCHAR(16) NOT NULL DEFAULT 'COMPANY',

            -- The three identities. See the module docstring: these ARE the
            -- integration, and each is unique in both directions so a
            -- distributor can never acquire a second accounting identity.
            customer_id UUID UNIQUE REFERENCES customers(id),
            warehouse_id UUID UNIQUE REFERENCES warehouses(id),
            user_id UUID UNIQUE REFERENCES users(id),

            phone VARCHAR(32),
            whatsapp VARCHAR(32),
            email VARCHAR(255),
            business_address TEXT,
            state_id UUID REFERENCES states(id),
            lga_id UUID REFERENCES lgas(id),
            town VARCHAR(128),

            cac_number VARCHAR(64),
            tin VARCHAR(64),
            years_in_operation INTEGER,
            business_type VARCHAR(128),
            employee_count INTEGER,
            marketer_count INTEGER,

            status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
            tier VARCHAR(16) NOT NULL DEFAULT 'STANDARD',

            approved_at TIMESTAMPTZ,
            approved_by UUID REFERENCES users(id),
            activated_at TIMESTAMPTZ,
            suspended_at TIMESTAMPTZ,
            terminated_at TIMESTAMPTZ,
            status_reason TEXT,

            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_dist_entity CHECK (entity_type IN
                ('INDIVIDUAL','COMPANY')),
            CONSTRAINT ck_dist_status CHECK (status IN
                ('DRAFT','APPLIED','UNDER_REVIEW','APPROVED','ACTIVE',
                 'SUSPENDED','TERMINATED','REJECTED')),
            CONSTRAINT ck_dist_tier CHECK (tier IN
                ('STANDARD','PREFERRED','PREMIUM','STRATEGIC')),
            -- An ACTIVE distributor trades, so it must have somewhere for its
            -- money and its stock to go. Without this, an activation could
            -- half-succeed and only surface at the first order.
            CONSTRAINT ck_dist_active_provisioned CHECK (
                status <> 'ACTIVE'
                OR (customer_id IS NOT NULL AND warehouse_id IS NOT NULL))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_distributors_status "
               "ON distributors (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_distributors_state "
               "ON distributors (state_id)")
    # Duplicate-identity guards. Partial, because these fields are optional --
    # an individual distributor has no CAC number -- but where present they
    # must be unique, since two distributors sharing a CAC number is either a
    # data-entry error or the same business applying twice.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_distributors_cac
            ON distributors (lower(cac_number))
         WHERE cac_number IS NOT NULL AND cac_number <> ''
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_distributors_tin
            ON distributors (lower(tin))
         WHERE tin IS NOT NULL AND tin <> ''
    """)

    # ---- territory assignment, with history ------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS territory_assignments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            territory_id UUID NOT NULL REFERENCES territories(id),
            distributor_id UUID NOT NULL REFERENCES distributors(id),
            assigned_from DATE NOT NULL,
            -- NULL = current holder. Closed on reassignment; the row stays so
            -- historical sales remain attached to whoever made them.
            assigned_to DATE,
            is_exclusive BOOLEAN NOT NULL DEFAULT TRUE,
            status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
            assigned_by UUID REFERENCES users(id),
            ended_by UUID REFERENCES users(id),
            reason TEXT,
            end_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_ta_status CHECK (status IN
                ('ACTIVE','ENDED','SUSPENDED')),
            CONSTRAINT ck_ta_period CHECK (
                assigned_to IS NULL OR assigned_to >= assigned_from)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_ta_territory "
               "ON territory_assignments (territory_id, assigned_from DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ta_distributor "
               "ON territory_assignments (distributor_id)")
    # THE exclusivity guarantee. One live exclusive holder per territory,
    # enforced by the database rather than by an application check that one
    # untested code path can skip.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_territory_one_active_holder
            ON territory_assignments (territory_id)
         WHERE assigned_to IS NULL AND status = 'ACTIVE' AND is_exclusive
    """)

    # ---- applications -----------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS distributor_applications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            application_number VARCHAR(40) UNIQUE NOT NULL,
            distributor_id UUID NOT NULL REFERENCES distributors(id),
            submitted_at TIMESTAMPTZ,
            status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
            -- Scores are a snapshot at decision time. Recomputing them later
            -- against changed weights would rewrite why a decision was made.
            eligibility_score NUMERIC(5,2),
            eligibility_band VARCHAR(24),
            score_breakdown JSONB,
            decided_by UUID REFERENCES users(id),
            decided_at TIMESTAMPTZ,
            decision_note TEXT,
            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_app_status CHECK (status IN
                ('DRAFT','SUBMITTED','UNDER_REVIEW','APPROVED','REJECTED',
                 'WITHDRAWN'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_dist_app_status "
               "ON distributor_applications (status, submitted_at)")

    # ---- documents --------------------------------------------------------
    #
    # Stored in the database, like wallet receipts and for the same reason: the
    # container filesystem is replaced on every deploy, so a compliance
    # document written to disk is evidence with an expiry date.
    op.execute("""
        CREATE TABLE IF NOT EXISTS distributor_documents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            distributor_id UUID NOT NULL REFERENCES distributors(id),
            doc_type VARCHAR(48) NOT NULL,
            title VARCHAR(255),
            filename VARCHAR(255) NOT NULL,
            content_type VARCHAR(100) NOT NULL,
            byte_size INTEGER NOT NULL,
            sha256 CHAR(64) NOT NULL,
            content BYTEA NOT NULL,
            issue_date DATE,
            expiry_date DATE,
            verification_status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
            verified_by UUID REFERENCES users(id),
            verified_at TIMESTAMPTZ,
            verification_note TEXT,
            uploaded_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_doc_size CHECK (byte_size > 0),
            CONSTRAINT ck_doc_verification CHECK (verification_status IN
                ('PENDING','VERIFIED','REJECTED','EXPIRED'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_dist_doc_distributor "
               "ON distributor_documents (distributor_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dist_doc_expiry "
               "ON distributor_documents (expiry_date) "
               "WHERE expiry_date IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dist_doc_sha "
               "ON distributor_documents (sha256)")

    # ---- qualifications ---------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS distributor_qualifications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            distributor_id UUID NOT NULL REFERENCES distributors(id),
            qualification_type VARCHAR(96) NOT NULL,
            holder_name VARCHAR(255),
            institution VARCHAR(255),
            certificate_number VARCHAR(96),
            registration_number VARCHAR(96),
            issuing_authority VARCHAR(255),
            issue_date DATE,
            expiry_date DATE,
            document_id UUID REFERENCES distributor_documents(id),
            verification_status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
            verified_by UUID REFERENCES users(id),
            verified_at TIMESTAMPTZ,
            verification_note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_qual_verification CHECK (verification_status IN
                ('PENDING','VERIFIED','REJECTED','EXPIRED'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_dist_qual_distributor "
               "ON distributor_qualifications (distributor_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dist_qual_expiry "
               "ON distributor_qualifications (expiry_date) "
               "WHERE expiry_date IS NOT NULL")

    # ---- append-only audit for this domain --------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS distributor_audit_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            event_type VARCHAR(64) NOT NULL,
            entity_type VARCHAR(48),
            entity_id UUID,
            distributor_id UUID,
            territory_id UUID,
            actor_user_id UUID,
            actor_label VARCHAR(255),
            old_value JSONB,
            new_value JSONB,
            reason TEXT,
            ip_address VARCHAR(64),
            user_agent VARCHAR(500),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_dist_audit_distributor "
               "ON distributor_audit_logs (distributor_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dist_audit_territory "
               "ON distributor_audit_logs (territory_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dist_audit_event "
               "ON distributor_audit_logs (event_type, created_at DESC)")

    # ---- extend existing tables (additive only) ---------------------------
    #
    # sales_orders is the busiest table in the application, so it is touched
    # ONCE, here, rather than again when ordering is built. All three columns
    # are nullable with a default, no backfill runs, and every existing row
    # keeps behaving exactly as it does today.
    for ddl in (
        "ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS "
        "distributor_id UUID REFERENCES distributors(id)",
        "ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS "
        "territory_id UUID REFERENCES territories(id)",
        "ALTER TABLE sales_orders ADD COLUMN IF NOT EXISTS "
        "sales_channel VARCHAR(24) NOT NULL DEFAULT 'DIRECT'",

        # The reverse link, so a customer row can say it belongs to a
        # distributor without anyone having to search the distributor table.
        "ALTER TABLE customers ADD COLUMN IF NOT EXISTS "
        "distributor_id UUID REFERENCES distributors(id)",
        "ALTER TABLE customers ADD COLUMN IF NOT EXISTS "
        "cac_number VARCHAR(64)",
        "ALTER TABLE customers ADD COLUMN IF NOT EXISTS tin VARCHAR(64)",

        # Marks a warehouse as distributor-held. Existing warehouses default to
        # COMPANY, so every current stock query is unaffected.
        "ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS "
        "warehouse_kind VARCHAR(24) NOT NULL DEFAULT 'COMPANY'",
        "ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS "
        "distributor_id UUID REFERENCES distributors(id)",
    ):
        op.execute(ddl)

    op.execute("CREATE INDEX IF NOT EXISTS ix_sales_orders_distributor "
               "ON sales_orders (distributor_id) "
               "WHERE distributor_id IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_sales_orders_territory "
               "ON sales_orders (territory_id) "
               "WHERE territory_id IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_sales_orders_channel "
               "ON sales_orders (sales_channel)")
    op.execute("ALTER TABLE sales_orders DROP CONSTRAINT IF EXISTS "
               "ck_sales_orders_channel")
    op.execute("""
        ALTER TABLE sales_orders ADD CONSTRAINT ck_sales_orders_channel
            CHECK (sales_channel IN ('DIRECT','DISTRIBUTOR','PUBLIC_ORDER'))
    """)
    op.execute("ALTER TABLE warehouses DROP CONSTRAINT IF EXISTS "
               "ck_warehouse_kind")
    op.execute("""
        ALTER TABLE warehouses ADD CONSTRAINT ck_warehouse_kind
            CHECK (warehouse_kind IN ('COMPANY','DISTRIBUTOR','QUARANTINE'))
    """)

    # ---- immutability -----------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION distributor_audit_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION USING MESSAGE =
                'distributor_audit_logs is append-only. Territory targets, '
                'assignments and distributor status changes are recorded '
                'here; rewriting them is what the table exists to prevent.';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_dist_audit_immutable "
               "ON distributor_audit_logs")
    op.execute("""
        CREATE TRIGGER trg_dist_audit_immutable
        BEFORE UPDATE OR DELETE ON distributor_audit_logs
        FOR EACH ROW EXECUTE FUNCTION distributor_audit_immutable()
    """)

    # A closed target is history. Reopening or re-pricing one would restate a
    # period that has already been reported and possibly paid commission on.
    op.execute("""
        CREATE OR REPLACE FUNCTION territory_target_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Territory targets are not deletable. Supersede a target '
                    'with a new one so the period it governed keeps its own.';
            END IF;
            IF OLD.effective_to IS NOT NULL AND (
                   NEW.monthly_target <> OLD.monthly_target
                OR NEW.effective_from <> OLD.effective_from
                OR NEW.effective_to IS DISTINCT FROM OLD.effective_to) THEN
                RAISE EXCEPTION USING MESSAGE =
                    'This target has already been superseded; the period it '
                    'governed cannot be restated.';
            END IF;
            IF NEW.territory_id <> OLD.territory_id THEN
                RAISE EXCEPTION USING MESSAGE =
                    'A target cannot be moved to a different territory.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_territory_target_guard "
               "ON territory_targets")
    op.execute("""
        CREATE TRIGGER trg_territory_target_guard
        BEFORE UPDATE OR DELETE ON territory_targets
        FOR EACH ROW EXECUTE FUNCTION territory_target_guard()
    """)

    # An assignment that has ended is history: historical sales are attached to
    # whoever held the territory at the time.
    op.execute("""
        CREATE OR REPLACE FUNCTION territory_assignment_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Territory assignments are not deletable. End an '
                    'assignment so the record of who held the territory, and '
                    'when, survives the reassignment.';
            END IF;
            IF NEW.territory_id <> OLD.territory_id
               OR NEW.distributor_id <> OLD.distributor_id
               OR NEW.assigned_from <> OLD.assigned_from THEN
                RAISE EXCEPTION USING MESSAGE =
                    'An assignment is immutable in territory, distributor and '
                    'start date. End it and create a new one.';
            END IF;
            IF OLD.assigned_to IS NOT NULL
               AND NEW.assigned_to IS DISTINCT FROM OLD.assigned_to THEN
                RAISE EXCEPTION USING MESSAGE =
                    'This assignment has already ended; its end date cannot '
                    'be changed.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_territory_assignment_guard "
               "ON territory_assignments")
    op.execute("""
        CREATE TRIGGER trg_territory_assignment_guard
        BEFORE UPDATE OR DELETE ON territory_assignments
        FOR EACH ROW EXECUTE FUNCTION territory_assignment_guard()
    """)

    # ---- seed -------------------------------------------------------------
    op.execute(sa.text("""
        INSERT INTO countries (id, iso2, iso3, name, currency_code, phone_prefix)
        VALUES (gen_random_uuid(), 'NG', 'NGA', 'Nigeria', 'NGN', '+234')
        ON CONFLICT (iso2) DO NOTHING
    """))

    for code, name, capital, zone in NIGERIAN_STATES:
        op.execute(sa.text("""
            INSERT INTO states
                (id, country_id, code, name, capital, geopolitical_zone)
            SELECT gen_random_uuid(), c.id, :code, :name, :cap, :zone
              FROM countries c WHERE c.iso2 = 'NG'
            ON CONFLICT (country_id, code) DO NOTHING
        """).bindparams(code=code, name=name, cap=capital, zone=zone))

    for lga in LAGOS_LGAS:
        op.execute(sa.text("""
            INSERT INTO lgas (id, state_id, name)
            SELECT gen_random_uuid(), s.id, :name
              FROM states s JOIN countries c ON c.id = s.country_id
             WHERE c.iso2 = 'NG' AND s.code = 'LA'
            ON CONFLICT (state_id, name) DO NOTHING
        """).bindparams(name=lga))

    for lga in FCT_AREA_COUNCILS:
        op.execute(sa.text("""
            INSERT INTO lgas (id, state_id, name)
            SELECT gen_random_uuid(), s.id, :name
              FROM states s JOIN countries c ON c.id = s.country_id
             WHERE c.iso2 = 'NG' AND s.code = 'FC'
            ON CONFLICT (state_id, name) DO NOTHING
        """).bindparams(name=lga))

    for code, name in (("SW", "South West"), ("SE", "South East"),
                       ("SS", "South South"), ("NC", "North Central"),
                       ("NE", "North East"), ("NW", "North West")):
        op.execute(sa.text("""
            INSERT INTO regions (id, code, name, country_id)
            SELECT gen_random_uuid(), :code, :name, c.id
              FROM countries c WHERE c.iso2 = 'NG'
            ON CONFLICT (code) DO NOTHING
        """).bindparams(code=code, name=name))


def downgrade():
    for trg, tbl in (("trg_dist_audit_immutable", "distributor_audit_logs"),
                     ("trg_territory_target_guard", "territory_targets"),
                     ("trg_territory_assignment_guard",
                      "territory_assignments")):
        op.execute(f"DROP TRIGGER IF EXISTS {trg} ON {tbl}")
    for fn in ("distributor_audit_immutable", "territory_target_guard",
               "territory_assignment_guard"):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")

    for col in ("distributor_id", "territory_id", "sales_channel"):
        op.execute(f"ALTER TABLE sales_orders DROP COLUMN IF EXISTS {col}")
    op.execute("ALTER TABLE sales_orders DROP CONSTRAINT IF EXISTS "
               "ck_sales_orders_channel")
    for col in ("distributor_id", "cac_number", "tin"):
        op.execute(f"ALTER TABLE customers DROP COLUMN IF EXISTS {col}")
    for col in ("warehouse_kind", "distributor_id"):
        op.execute(f"ALTER TABLE warehouses DROP COLUMN IF EXISTS {col}")
    op.execute("ALTER TABLE warehouses DROP CONSTRAINT IF EXISTS "
               "ck_warehouse_kind")

    for tbl in ("distributor_audit_logs", "distributor_qualifications",
                "distributor_documents", "distributor_applications",
                "territory_assignments", "distributors", "territory_targets",
                "territory_lgas", "territories", "regions", "lgas", "states",
                "countries"):
        op.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
