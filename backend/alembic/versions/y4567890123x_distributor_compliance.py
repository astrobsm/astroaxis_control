"""Distributor compliance: storage facilities, assessments and agreements.

Revision ID: y4567890123x
Revises: x3456789012w
Create Date: 2026-09-13

THE DISTINCTION THIS SCHEMA REFUSES TO BLUR
-------------------------------------------
Specification section 3 is explicit: never represent a company policy as if it
were statutory law. So every checklist item carries `requirement_kind`:

    REGULATORY  imposed by law or a regulator -- `authority` must name which
    COMPANY     imposed by Bonnesante Medicals' own policy
    COMMERCIAL  an internal performance expectation

and a REGULATORY item without a named authority is refused by a check
constraint. The seeded checklist is therefore entirely COMPANY, with authority
NULL, because this migration does not know which Nigerian regulations apply to
which product classification -- and inventing that would be exactly the
misrepresentation section 3 prohibits. An administrator marks the regulatory
ones and names the authority.

CRITICAL ITEMS OUTRANK THE SCORE
--------------------------------
`is_critical` marks a requirement whose failure blocks approval regardless of
the weighted total, mirroring the eligibility rule: a score that can absorb a
failed quarantine area is a score that hides one.

AGREEMENTS REUSE WHAT ALREADY EXISTS, AND STOP WHERE IT STOPS
--------------------------------------------------------------
The agreement TEMPLATE is a controlled company document -- versioned, reviewed,
effective-dated -- which is precisely what `reg_documents` already is, so
templates live there and are referenced by id. No new template table.

The agreement INSTANCE does not: it is a contract with one counterparty, it is
not reviewed on the document schedule, and several hundred of them would swamp
a GMP register an auditor reads. So instances are their own table, and their
signatures mirror `reg_signatures` field for field -- signer, role, MEANING and
CONTENT HASH -- because that discipline is what makes a signature evidence
rather than a checkbox.

The template reference is deliberately NOT a foreign key: `reg_documents` is
created by a runtime bootstrap in app/api/regulatory.py rather than by a
migration, and a migration that fails because a bootstrap has not run yet is a
worse problem than a soft reference the service validates.
"""
from alembic import op
import sqlalchemy as sa

revision = 'y4567890123x'
down_revision = 'x3456789012w'
branch_labels = None
depends_on = None


# Section 7's checklist. Every item is seeded as a COMPANY requirement with no
# named authority -- see the module docstring. Weight and criticality reflect
# what a wound-care storage failure would actually cost, not a legal opinion.
CHECKLIST = [
    # code, section, requirement, weight, critical, evidence
    ("FLOOR_AREA", "Space", "Floor area is sufficient for expected stock volume", 2, False, False),
    ("CAPACITY", "Space", "Storage capacity is documented and not exceeded", 2, False, False),
    ("SHELVING", "Space", "Goods are stored on shelving, off the floor", 3, True, True),
    ("VENTILATION", "Environment", "The store is ventilated", 3, False, True),
    ("LIGHTING", "Environment", "Lighting is adequate to read labels and batch numbers", 2, False, False),
    ("CLEANLINESS", "Environment", "The store is clean and free of debris", 3, True, True),
    ("PEST_CONTROL", "Environment", "Pest control measures are in place", 3, True, True),
    ("PEST_DOCS", "Environment", "Pest control is documented with dated records", 2, False, True),
    ("TEMP_MONITOR", "Environment", "Temperature is monitored where the product requires it", 3, False, True),
    ("TEMP_LOGS", "Environment", "Temperature logs are kept and reviewed", 2, False, True),
    ("HUMIDITY", "Environment", "Humidity is monitored where the product requires it", 2, False, False),
    ("SECURITY", "Security", "The store is secure against unauthorised entry", 3, True, True),
    ("ACCESS_CONTROL", "Security", "Access is restricted to named, responsible persons", 2, True, False),
    ("FIRE", "Safety", "Fire protection equipment is present and serviced", 3, True, True),
    ("POWER", "Utilities", "Mains power supply is reliable", 2, False, False),
    ("BACKUP_POWER", "Utilities", "Backup power is available where the product requires it", 2, False, False),
    ("SEGREGATION", "Stock control", "Products are segregated by type and batch", 3, True, True),
    ("QUARANTINE", "Stock control", "A defined quarantine area exists and is used", 3, True, True),
    ("RETURNS_AREA", "Stock control", "A defined area exists for returned goods", 2, True, True),
    ("DAMAGED_AREA", "Stock control", "A defined area exists for damaged goods", 2, True, True),
    ("EXPIRED_AREA", "Stock control", "Expired goods are segregated and clearly marked", 3, True, True),
    ("CLEANING_PROC", "Procedures", "Written cleaning procedures exist", 2, False, True),
    ("CLEANING_SCHED", "Procedures", "A cleaning schedule is followed and recorded", 2, False, True),
    ("RESPONSIBLE", "Procedures", "A named person is responsible for the facility", 2, True, False),
    ("FIFO", "Stock control", "Stock is rotated first-expired-first-out", 3, False, False),
]


def upgrade():
    # ---- the configurable checklist --------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS facility_checklist_items (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code VARCHAR(48) UNIQUE NOT NULL,
            section VARCHAR(64) NOT NULL,
            requirement TEXT NOT NULL,
            -- Section 3: the system must never present company policy as law.
            requirement_kind VARCHAR(16) NOT NULL DEFAULT 'COMPANY',
            -- Who imposes it. Mandatory for a REGULATORY item.
            authority VARCHAR(128),
            -- Which products this applies to. NULL = all.
            product_category VARCHAR(64),
            weight INTEGER NOT NULL DEFAULT 1,
            -- A critical failure blocks approval whatever the total score is.
            is_critical BOOLEAN NOT NULL DEFAULT FALSE,
            requires_evidence BOOLEAN NOT NULL DEFAULT FALSE,
            sort_order INTEGER NOT NULL DEFAULT 100,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_chk_kind CHECK (requirement_kind IN
                ('REGULATORY','COMPANY','COMMERCIAL')),
            CONSTRAINT ck_chk_weight CHECK (weight > 0),
            -- A regulatory claim with nobody behind it is exactly the
            -- misrepresentation section 3 prohibits.
            CONSTRAINT ck_chk_regulatory_has_authority CHECK (
                requirement_kind <> 'REGULATORY'
                OR (authority IS NOT NULL AND authority <> ''))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_chk_active "
               "ON facility_checklist_items (is_active, sort_order)")

    # ---- the facility ------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS distributor_facilities (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            distributor_id UUID NOT NULL REFERENCES distributors(id),
            name VARCHAR(255) NOT NULL,
            address TEXT,
            state_id UUID REFERENCES states(id),
            lga_id UUID REFERENCES lgas(id),
            town VARCHAR(128),
            latitude NUMERIC(10,7),
            longitude NUMERIC(10,7),
            floor_area_sqm NUMERIC(12,2),
            capacity_note TEXT,
            responsible_person VARCHAR(255),
            responsible_phone VARCHAR(32),
            -- PENDING never assessed | APPROVED | CONDITIONAL | REJECTED
            status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
            is_primary BOOLEAN NOT NULL DEFAULT TRUE,
            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_fac_status CHECK (status IN
                ('PENDING','APPROVED','CONDITIONAL','REJECTED','CLOSED'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_fac_distributor "
               "ON distributor_facilities (distributor_id)")

    # ---- an assessment event ----------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS facility_assessments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            assessment_reference VARCHAR(40) UNIQUE NOT NULL,
            facility_id UUID NOT NULL REFERENCES distributor_facilities(id),
            distributor_id UUID NOT NULL REFERENCES distributors(id),
            assessed_on DATE NOT NULL,
            assessor_id UUID REFERENCES users(id),
            status VARCHAR(16) NOT NULL DEFAULT 'DRAFT',
            -- Snapshots taken when the assessment was submitted. Recomputing
            -- them later against a changed checklist would rewrite why a
            -- facility was approved.
            score NUMERIC(5,2),
            band VARCHAR(16),
            critical_failures INTEGER NOT NULL DEFAULT 0,
            items_assessed INTEGER NOT NULL DEFAULT 0,
            outcome VARCHAR(16),
            summary TEXT,
            reviewed_by UUID REFERENCES users(id),
            reviewed_at TIMESTAMPTZ,
            review_note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_fa_status CHECK (status IN
                ('DRAFT','SUBMITTED','APPROVED','REJECTED')),
            CONSTRAINT ck_fa_outcome CHECK (outcome IS NULL OR outcome IN
                ('PASS','CONDITIONAL','FAIL')),
            -- A submitted assessment must carry the figures it was judged on.
            CONSTRAINT ck_fa_submitted_scored CHECK (
                status = 'DRAFT' OR score IS NOT NULL)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_fa_facility "
               "ON facility_assessments (facility_id, assessed_on DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_fa_distributor "
               "ON facility_assessments (distributor_id)")

    # ---- one checklist answer ----------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS facility_assessment_items (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            assessment_id UUID NOT NULL
                REFERENCES facility_assessments(id) ON DELETE CASCADE,
            checklist_item_id UUID NOT NULL
                REFERENCES facility_checklist_items(id),
            result VARCHAR(24) NOT NULL,
            note TEXT,
            -- Evidence reuses distributor_documents rather than adding a
            -- second place photographs can live.
            evidence_document_id UUID REFERENCES distributor_documents(id),
            -- The requirement AS IT READ when answered. A checklist reworded
            -- next year must not silently change what an inspector agreed to.
            requirement_snapshot TEXT,
            kind_snapshot VARCHAR(16),
            weight_snapshot INTEGER,
            was_critical BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_fai_result CHECK (result IN
                ('PASS','FAIL','NOT_APPLICABLE','REQUIRES_CORRECTION')),
            CONSTRAINT uq_fai_once UNIQUE (assessment_id, checklist_item_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_fai_assessment "
               "ON facility_assessment_items (assessment_id)")

    # ---- corrective actions -------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS facility_corrective_actions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            action_reference VARCHAR(40) UNIQUE NOT NULL,
            assessment_id UUID NOT NULL REFERENCES facility_assessments(id),
            assessment_item_id UUID REFERENCES facility_assessment_items(id),
            distributor_id UUID NOT NULL REFERENCES distributors(id),
            finding TEXT NOT NULL,
            severity VARCHAR(16) NOT NULL DEFAULT 'MEDIUM',
            responsible_person VARCHAR(255),
            deadline DATE,
            corrective_action TEXT,
            status VARCHAR(16) NOT NULL DEFAULT 'OPEN',
            completed_at TIMESTAMPTZ,
            evidence_document_id UUID REFERENCES distributor_documents(id),
            verified_by UUID REFERENCES users(id),
            verified_at TIMESTAMPTZ,
            verification_note TEXT,
            closed_at TIMESTAMPTZ,
            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_ca_severity CHECK (severity IN
                ('LOW','MEDIUM','HIGH','CRITICAL')),
            CONSTRAINT ck_ca_status CHECK (status IN
                ('OPEN','IN_PROGRESS','COMPLETED','VERIFIED','CLOSED')),
            -- Closing a finding without verifying it is how a corrective
            -- action becomes a formality.
            CONSTRAINT ck_ca_closed_verified CHECK (
                status <> 'CLOSED' OR verified_at IS NOT NULL)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_ca_assessment "
               "ON facility_corrective_actions (assessment_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ca_open "
               "ON facility_corrective_actions (distributor_id, status) "
               "WHERE status <> 'CLOSED'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ca_deadline "
               "ON facility_corrective_actions (deadline) "
               "WHERE status NOT IN ('CLOSED','VERIFIED')")

    # ---- agreements ---------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS distributor_agreements (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agreement_reference VARCHAR(40) UNIQUE NOT NULL,
            distributor_id UUID NOT NULL REFERENCES distributors(id),

            -- reg_documents.id of the template this was generated from.
            -- Soft reference by design; see the module docstring.
            template_document_id VARCHAR(128),
            template_version VARCHAR(32),
            title VARCHAR(255) NOT NULL,

            -- The rendered contract as presented. Frozen once issued: what the
            -- distributor accepted must remain readable exactly as they read
            -- it, whatever the template becomes later.
            body TEXT NOT NULL,
            body_sha256 CHAR(64) NOT NULL,

            -- Commercial terms as agreed, kept structured so they can be
            -- reported on without parsing prose.
            terms JSONB NOT NULL DEFAULT '{}'::jsonb,
            territory_ids JSONB NOT NULL DEFAULT '[]'::jsonb,

            effective_from DATE,
            expires_on DATE,
            -- DRAFT -> ISSUED -> ACCEPTED -> COUNTERSIGNED -> ACTIVE
            --       -> EXPIRED | TERMINATED | SUPERSEDED
            status VARCHAR(16) NOT NULL DEFAULT 'DRAFT',
            issued_at TIMESTAMPTZ,
            accepted_at TIMESTAMPTZ,
            countersigned_at TIMESTAMPTZ,
            activated_at TIMESTAMPTZ,
            ended_at TIMESTAMPTZ,
            end_reason TEXT,
            supersedes_id UUID REFERENCES distributor_agreements(id),
            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_ag_status CHECK (status IN
                ('DRAFT','ISSUED','ACCEPTED','COUNTERSIGNED','ACTIVE',
                 'EXPIRED','TERMINATED','SUPERSEDED','DECLINED')),
            CONSTRAINT ck_ag_period CHECK (
                expires_on IS NULL OR effective_from IS NULL
                OR expires_on >= effective_from)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_ag_distributor "
               "ON distributor_agreements (distributor_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ag_expiry "
               "ON distributor_agreements (expires_on) "
               "WHERE status = 'ACTIVE'")
    # One live agreement per distributor. Two would make "the terms" ambiguous
    # at exactly the moment somebody needs to rely on them.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_agreement_one_active
            ON distributor_agreements (distributor_id)
         WHERE status IN ('ACTIVE','COUNTERSIGNED')
    """)

    # ---- signatures ---------------------------------------------------------
    #
    # Mirrors reg_signatures field for field: signer, ROLE, MEANING and CONTENT
    # HASH. The meaning is what turns a click into a signature ("I have read and
    # accept these terms"), and the hash binds it to the exact text signed --
    # without which a signature proves only that a button was pressed.
    op.execute("""
        CREATE TABLE IF NOT EXISTS distributor_agreement_signatures (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agreement_id UUID NOT NULL REFERENCES distributor_agreements(id),
            signer_name VARCHAR(255) NOT NULL,
            signer_role VARCHAR(64) NOT NULL,
            signer_user_id UUID REFERENCES users(id),
            meaning TEXT NOT NULL,
            content_hash CHAR(64) NOT NULL,
            ip_address VARCHAR(64),
            user_agent VARCHAR(500),
            signed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_sig_role CHECK (signer_role IN
                ('DISTRIBUTOR','COMPANY','WITNESS'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_agsig_agreement "
               "ON distributor_agreement_signatures (agreement_id)")

    # ---- immutability -------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION agreement_signature_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION USING MESSAGE =
                'Agreement signatures are append-only. A signature that could '
                'be edited afterwards is not evidence that anyone agreed to '
                'anything.';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_agsig_immutable "
               "ON distributor_agreement_signatures")
    op.execute("""
        CREATE TRIGGER trg_agsig_immutable
        BEFORE UPDATE OR DELETE ON distributor_agreement_signatures
        FOR EACH ROW EXECUTE FUNCTION agreement_signature_immutable()
    """)

    # Once issued, the text and its hash are frozen. Editing the contract after
    # presenting it -- or after it was accepted -- is the single worst thing
    # this table could permit.
    op.execute("""
        CREATE OR REPLACE FUNCTION agreement_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Agreements are not deletable. Terminate or supersede one '
                    'so the terms that were in force remain readable.';
            END IF;
            IF OLD.status <> 'DRAFT' AND (
                   NEW.body <> OLD.body
                OR NEW.body_sha256 <> OLD.body_sha256
                OR NEW.distributor_id <> OLD.distributor_id
                OR NEW.agreement_reference <> OLD.agreement_reference) THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Agreement ' || OLD.agreement_reference || ' has been '
                    'issued; its text and counterparty are fixed. Supersede '
                    'it with a new agreement instead.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_agreement_guard "
               "ON distributor_agreements")
    op.execute("""
        CREATE TRIGGER trg_agreement_guard
        BEFORE UPDATE OR DELETE ON distributor_agreements
        FOR EACH ROW EXECUTE FUNCTION agreement_guard()
    """)

    # A submitted assessment is a record of what an inspector found.
    op.execute("""
        CREATE OR REPLACE FUNCTION facility_assessment_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Facility assessments are not deletable. Re-assess the '
                    'facility; the earlier finding stays on the record.';
            END IF;
            IF OLD.status <> 'DRAFT' AND (
                   NEW.score IS DISTINCT FROM OLD.score
                OR NEW.critical_failures <> OLD.critical_failures
                OR NEW.assessed_on <> OLD.assessed_on
                OR NEW.facility_id <> OLD.facility_id) THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Assessment ' || OLD.assessment_reference || ' has been '
                    'submitted; its findings cannot be revised. Carry out a '
                    'new assessment instead.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_facility_assessment_guard "
               "ON facility_assessments")
    op.execute("""
        CREATE TRIGGER trg_facility_assessment_guard
        BEFORE UPDATE OR DELETE ON facility_assessments
        FOR EACH ROW EXECUTE FUNCTION facility_assessment_guard()
    """)

    # ---- seed the checklist -------------------------------------------------
    for i, (code, section, req, weight, critical, evidence) in enumerate(
            CHECKLIST, start=1):
        op.execute(sa.text("""
            INSERT INTO facility_checklist_items
                (id, code, section, requirement, requirement_kind, weight,
                 is_critical, requires_evidence, sort_order)
            VALUES (gen_random_uuid(), :c, :s, :r, 'COMPANY', :w, :crit,
                    :ev, :ord)
            ON CONFLICT (code) DO NOTHING
        """).bindparams(c=code, s=section, r=req, w=weight, crit=critical,
                        ev=evidence, ord=i * 10))


def downgrade():
    for trg, tbl in (
        ("trg_agsig_immutable", "distributor_agreement_signatures"),
        ("trg_agreement_guard", "distributor_agreements"),
        ("trg_facility_assessment_guard", "facility_assessments"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trg} ON {tbl}")
    for fn in ("agreement_signature_immutable", "agreement_guard",
               "facility_assessment_guard"):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
    for tbl in ("distributor_agreement_signatures", "distributor_agreements",
                "facility_corrective_actions", "facility_assessment_items",
                "facility_assessments", "distributor_facilities",
                "facility_checklist_items"):
        op.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
