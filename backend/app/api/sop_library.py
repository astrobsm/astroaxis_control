"""
SOP template library for Bonnesante Medicals - ASTRO BSM.
Provides GMP-ready SOP documents plus integration metadata.
"""

from copy import deepcopy


def _base_db_structure():
    return {
        "table": "sop_execution_logs",
        "description": "Stores operator execution logs, approvals, and deviations for SOP runs.",
        "columns": [
            {"name": "id", "type": "uuid", "nullable": False, "primary_key": True},
            {"name": "template_id", "type": "uuid", "nullable": False, "foreign_key": "sop_templates.id"},
            {"name": "sop_code", "type": "varchar(64)", "nullable": False, "index": True},
            {"name": "operator_name", "type": "varchar(255)", "nullable": False},
            {"name": "supervisor_name", "type": "varchar(255)", "nullable": False},
            {"name": "executed_at", "type": "timestamptz", "nullable": False},
            {"name": "batch_number", "type": "varchar(100)", "nullable": False, "index": True},
            {"name": "material_equipment_used", "type": "jsonb", "nullable": False},
            {"name": "checklist", "type": "jsonb", "nullable": False},
            {"name": "numeric_inputs", "type": "jsonb", "nullable": False},
            {"name": "operator_signature", "type": "varchar(255)", "nullable": False},
            {"name": "supervisor_signature", "type": "varchar(255)", "nullable": False},
            {"name": "comments", "type": "text", "nullable": True},
            {"name": "deviation", "type": "text", "nullable": True},
            {"name": "status", "type": "varchar(32)", "nullable": False},
            {"name": "approved_by", "type": "varchar(255)", "nullable": True},
            {"name": "approved_at", "type": "timestamptz", "nullable": True},
            {"name": "created_at", "type": "timestamptz", "nullable": False},
            {"name": "updated_at", "type": "timestamptz", "nullable": True},
        ],
    }


def _base_form_schema(checklist, numeric_fields):
    return {
        "fields": [
            {"name": "operator_name", "label": "Operator Name", "type": "text", "required": True},
            {"name": "supervisor_name", "label": "Supervisor Name", "type": "text", "required": True},
            {"name": "executed_at", "label": "Date and Time", "type": "datetime", "required": True},
            {"name": "batch_number", "label": "Batch Number", "type": "text", "required": True},
            {
                "name": "material_equipment_used",
                "label": "Material or Equipment Used",
                "type": "textarea",
                "required": True,
                "placeholder": "List lot numbers, equipment IDs, and quantities used",
            },
            {"name": "operator_signature", "label": "Operator Signature", "type": "signature", "required": True},
            {"name": "supervisor_signature", "label": "Supervisor Signature", "type": "signature", "required": True},
            {"name": "comments", "label": "Comments", "type": "textarea", "required": False},
            {"name": "deviation", "label": "Deviation", "type": "textarea", "required": False},
        ],
        "checklist": checklist,
        "numeric_fields": numeric_fields,
    }


def _base_validation_rules(extra_rules):
    base = [
        "operator_name must be provided and at least 3 characters",
        "supervisor_name must be provided and at least 3 characters",
        "executed_at must be a valid ISO-8601 datetime",
        "batch_number must be provided and match production batch reference",
        "material_equipment_used must not be empty",
        "all required checklist items must be checked before submission",
        "operator_signature and supervisor_signature are mandatory",
        "deviation field is mandatory when any checklist item fails",
    ]
    return base + extra_rules


SOP_LIBRARY = [
    {
        "sop_code": "SOP-CLN-001",
        "title": "SOP for Cleaning of Production Surfaces and Equipment",
        "sop_number": "CLN-001",
        "version": "1.0",
        "effective_date": "2026-04-26",
        "document": {
            "purpose": "Define validated cleaning instructions for surfaces and equipment using bleach, acetic acid, and liquid soap to maintain GMP hygiene and prevent contamination.",
            "scope": "Applies to all production rooms, compounding vessels, transfer tools, packaging tables, and contact surfaces in Bonnesante Medicals facilities.",
            "responsibilities": {
                "Store": "Issue approved cleaning agents and record lot numbers.",
                "QC": "Verify concentrations, contact times, and final rinse acceptance.",
                "Production": "Perform cleaning exactly as documented and record all entries.",
                "Supervisor": "Witness execution, review logs, and release cleaned area for use."
            },
            "materials_equipment_required": [
                "Sodium hypochlorite bleach stock solution 5.0%",
                "Acetic acid stock solution 5.0%",
                "Pharmaceutical-grade liquid soap",
                "UV-irradiated water only",
                "Calibrated measuring cylinders (10 mL to 1000 mL)",
                "Color-coded cleaning buckets and lint-free wipes",
                "PPE: nitrile gloves, goggles, mask, apron"
            ],
            "procedures": [
                "1. Verify line is stopped and all materials are removed before cleaning.",
                "2. Prepare bleach disinfectant at 0.5% w/v: mix 100 mL of 5.0% bleach stock with 900 mL UV-irradiated water to make 1 liter.",
                "3. Prepare acetic acid rinse at 0.5% v/v: mix 100 mL of 5.0% acetic acid stock with 900 mL UV-irradiated water to make 1 liter.",
                "4. Prepare soap wash solution: mix 20 mL liquid soap with 980 mL UV-irradiated water to make 1 liter.",
                "5. Surface cleaning sequence: dry wipe, soap wash, rinse with UV water, apply 0.5% bleach and keep wet contact for 10 minutes, final UV water rinse.",
                "6. Equipment cleaning sequence: dismantle product-contact parts, wash with soap solution, rinse with UV water, sanitize with 0.5% bleach for 15 minutes, rinse with UV water, air dry.",
                "7. For stainless steel sensitive parts, perform acetic acid passivation wipe after bleach rinse using 0.5% acetic acid for 5 minutes, then final UV water rinse.",
                "8. Record start and finish times for each zone and equipment ID.",
                "9. QC swab and visual clearance must be completed before release."
            ],
            "safety_precautions": [
                "Never mix bleach directly with acetic acid; prepare in separate labeled containers.",
                "Use only UV-irradiated water source documented in utility log.",
                "Wear full PPE and replace gloves between dirty and clean tasks.",
                "Ensure room ventilation is active during chemical preparation."
            ],
            "documentation_records": [
                "Cleaning preparation log",
                "Surface and equipment cleaning checklist",
                "Chemical dilution verification sheet",
                "QC cleaning release form"
            ],
            "approval_workflow": [
                "Operator executes and signs",
                "Supervisor reviews and signs",
                "QC verifies and signs release",
                "QA archives record in audit trail"
            ],
            "deviation_handling": [
                "Stop operation immediately when concentration, contact time, or rinse fails.",
                "Record deviation with root cause and affected area/equipment.",
                "Repeat cleaning cycle after corrective action and supervisor approval.",
                "Escalate recurring deviations to QA within same shift."
            ]
        },
        "form_schema": _base_form_schema(
            checklist=[
                {"name": "line_stopped", "label": "Line stopped and materials cleared", "required": True},
                {"name": "bleach_prepared", "label": "0.5% bleach prepared and verified", "required": True},
                {"name": "acetic_prepared", "label": "0.5% acetic acid prepared and verified", "required": True},
                {"name": "uv_water_confirmed", "label": "UV water source confirmed", "required": True},
                {"name": "qc_release", "label": "QC release obtained", "required": True},
            ],
            numeric_fields=[
                {"name": "bleach_stock_ml", "label": "Bleach stock (mL)", "required": True, "min": 0, "max": 1000},
                {"name": "bleach_water_ml", "label": "Bleach dilution water (mL)", "required": True, "min": 0, "max": 5000},
                {"name": "acetic_stock_ml", "label": "Acetic acid stock (mL)", "required": True, "min": 0, "max": 1000},
                {"name": "soap_ml_per_liter", "label": "Soap dosage (mL per L)", "required": True, "min": 0, "max": 100},
                {"name": "bleach_contact_minutes", "label": "Bleach contact time (min)", "required": True, "min": 10, "max": 30},
            ],
        ),
        "db_table_structure": _base_db_structure(),
        "validation_rules": _base_validation_rules([
            "bleach_stock_ml:bleach_water_ml ratio must be 1:9 for 0.5% target from 5.0% stock",
            "soap_ml_per_liter must be exactly 20 +/- 2 mL",
            "bleach_contact_minutes must be >= 10 for surfaces and >= 15 for equipment"
        ]),
    },
    {
        "sop_code": "SOP-PRD-002",
        "title": "SOP for Setting Up Production",
        "sop_number": "PRD-002",
        "version": "1.0",
        "effective_date": "2026-04-26",
        "document": {
            "purpose": "Ensure production start-up is controlled through line clearance, equipment readiness, PPE compliance, environment checks, and documentation readiness.",
            "scope": "Applies before every manufacturing batch in compounding and packaging areas.",
            "responsibilities": {
                "Store": "Issue approved raw and packaging materials against batch record.",
                "QC": "Confirm environmental and line clearance criteria.",
                "Production": "Set up equipment and complete start-up checklist.",
                "Supervisor": "Authorize production start after record verification."
            },
            "materials_equipment_required": ["Current approved BMR", "Calibrated equipment", "PPE set", "Line clearance labels"],
            "procedures": [
                "1. Verify previous batch documentation is closed and line status is CLEANED/RELEASED.",
                "2. Perform line clearance and remove all labels, remnants, and documents from previous batch.",
                "3. Verify equipment cleaning status tags and calibration due dates.",
                "4. Confirm PPE compliance for all assigned operators.",
                "5. Record room temperature, relative humidity, and differential pressure against limits.",
                "6. Verify raw materials and packaging components against BMR and approved status.",
                "7. Open batch record and assign operator/supervisor signatures before charging or mixing.",
                "8. Supervisor performs independent check and issues start authorization."
            ],
            "safety_precautions": ["No production start with expired calibration", "No production start without PPE", "Stop if environmental limits exceed approved range"],
            "documentation_records": ["Line clearance form", "Equipment readiness log", "Environmental log", "Batch start authorization"],
            "approval_workflow": ["Operator completion", "Supervisor verification", "QC countersign", "QA archival"],
            "deviation_handling": ["Record blocked start-up condition", "Escalate to QA within 30 minutes", "Restart only after documented CAPA"]
        },
        "form_schema": _base_form_schema(
            checklist=[
                {"name": "line_clearance_done", "label": "Line clearance completed", "required": True},
                {"name": "equipment_ready", "label": "Equipment readiness verified", "required": True},
                {"name": "ppe_compliant", "label": "PPE compliance verified", "required": True},
                {"name": "env_within_limits", "label": "Environment within limits", "required": True},
                {"name": "docs_ready", "label": "Batch documentation opened and signed", "required": True},
            ],
            numeric_fields=[
                {"name": "room_temp_c", "label": "Room temperature (C)", "required": True, "min": 18, "max": 27},
                {"name": "rh_percent", "label": "Relative humidity (%)", "required": True, "min": 30, "max": 65},
                {"name": "dp_pa", "label": "Differential pressure (Pa)", "required": True, "min": 5, "max": 25},
            ],
        ),
        "db_table_structure": _base_db_structure(),
        "validation_rules": _base_validation_rules(["room_temp_c, rh_percent, and dp_pa must be within approved HVAC limits"]),
    },
    {
        "sop_code": "SOP-PKG-003",
        "title": "SOP for Packaging with QA Controls",
        "sop_number": "PKG-003",
        "version": "1.0",
        "effective_date": "2026-04-26",
        "document": {
            "purpose": "Control packaging through label verification, batch coding, seal integrity, and product inspection.",
            "scope": "Applies to all finished wound care product packaging runs.",
            "responsibilities": {
                "Store": "Issue approved packaging materials by batch.",
                "QC": "Perform in-process and final packaging checks.",
                "Production": "Operate line and perform self-inspection.",
                "Supervisor": "Verify coding, reconciliation, and release documentation."
            },
            "materials_equipment_required": ["Approved labels/cartons", "Coding machine", "Seal tester", "Reconciliation sheet"],
            "procedures": [
                "1. Verify label artwork code and revision against approved master.",
                "2. Set and verify batch code, MFG date, and EXP date on coding unit.",
                "3. Run first-off sample and obtain QA approval before full run.",
                "4. Inspect seal integrity every 30 minutes or every 500 units, whichever occurs first.",
                "5. Conduct visual inspection for legibility, alignment, contamination, and damage.",
                "6. Reconcile issued versus used versus rejected packaging components.",
                "7. Segregate nonconforming units and open deviation if limits exceeded."
            ],
            "safety_precautions": ["Handle cutting/sealing jaws with guards", "Use gloves when handling sterile-contact packs"],
            "documentation_records": ["Packaging checklist", "First-off approval", "In-process QA checks", "Reconciliation record"],
            "approval_workflow": ["Operator sign-off", "Supervisor sign-off", "QA release"],
            "deviation_handling": ["Quarantine suspect units", "Investigate coder/sealer settings", "Document CAPA before restart"]
        },
        "form_schema": _base_form_schema(
            checklist=[
                {"name": "label_verified", "label": "Label and artwork verified", "required": True},
                {"name": "batch_code_verified", "label": "Batch coding verified", "required": True},
                {"name": "seal_check_done", "label": "Seal integrity check completed", "required": True},
                {"name": "inspection_done", "label": "Product inspection criteria passed", "required": True},
            ],
            numeric_fields=[
                {"name": "units_packed", "label": "Units packed", "required": True, "min": 1, "max": 1000000},
                {"name": "units_rejected", "label": "Units rejected", "required": True, "min": 0, "max": 1000000},
                {"name": "seal_test_frequency_minutes", "label": "Seal check interval (min)", "required": True, "min": 1, "max": 120},
            ],
        ),
        "db_table_structure": _base_db_structure(),
        "validation_rules": _base_validation_rules(["units_rejected must be <= units_packed", "seal_test_frequency_minutes must be <= 30"]),
    },
    {
        "sop_code": "SOP-CMP-004A",
        "title": "SOP for Compounding - Wound Clex",
        "sop_number": "CMP-004A",
        "version": "1.0",
        "effective_date": "2026-04-26",
        "document": {
            "purpose": "Define controlled compounding sequence for Wound Clex solution.",
            "scope": "Applies to all Wound Clex batches.",
            "responsibilities": {
                "Store": "Issue approved lots per BMR.",
                "QC": "Verify in-process pH and appearance.",
                "Production": "Charge and mix as per sequence.",
                "Supervisor": "Witness critical control points and batch closure."
            },
            "materials_equipment_required": ["Compounding tank", "Overhead mixer", "Calibrated balance", "pH meter"],
            "procedures": [
                "1. Verify tank identity and cleaning release status.",
                "2. Charge UV-irradiated purified water to 70% of target batch volume.",
                "3. Add chlorhexidine gluconate to target 0.5% w/v under 150 rpm agitation.",
                "4. Add glycerin to target 2.0% w/v and mix for 10 minutes.",
                "5. Add sodium chloride to target 0.9% w/v and mix until dissolved.",
                "6. Adjust pH to 5.5 to 6.5 using approved acid/base.",
                "7. Bring volume to 100% with UV water and mix for 20 minutes.",
                "8. Hold for QC sampling; release only after QC approval."
            ],
            "safety_precautions": ["Use splash goggles during concentrate charging", "No open containers after charging"],
            "documentation_records": ["Compounding record", "Raw material lot trace", "In-process QC results"],
            "approval_workflow": ["Operator sign", "Supervisor verify", "QC release"],
            "deviation_handling": ["Stop on pH out-of-range", "Rework only with QA authorization"]
        },
        "form_schema": _base_form_schema(
            checklist=[
                {"name": "tank_released", "label": "Tank cleaning release confirmed", "required": True},
                {"name": "raw_lots_verified", "label": "Raw material lots verified", "required": True},
                {"name": "qc_sample_taken", "label": "QC sample taken and logged", "required": True},
            ],
            numeric_fields=[
                {"name": "batch_volume_l", "label": "Target batch volume (L)", "required": True, "min": 1, "max": 2000},
                {"name": "chlorhexidine_percent", "label": "Chlorhexidine concentration (%)", "required": True, "min": 0.4, "max": 0.6},
                {"name": "glycerin_percent", "label": "Glycerin concentration (%)", "required": True, "min": 1.5, "max": 2.5},
                {"name": "final_ph", "label": "Final pH", "required": True, "min": 5.5, "max": 6.5},
            ],
        ),
        "db_table_structure": _base_db_structure(),
        "validation_rules": _base_validation_rules(["final_ph must be between 5.5 and 6.5", "chlorhexidine_percent must be 0.5 +/- 0.1"]),
    },
    {
        "sop_code": "SOP-CMP-004B",
        "title": "SOP for Compounding - Hera Wound Gel",
        "sop_number": "CMP-004B",
        "version": "1.0",
        "effective_date": "2026-04-26",
        "document": {
            "purpose": "Define controlled compounding sequence for Hera Wound Gel.",
            "scope": "Applies to all Hera Wound Gel batches.",
            "responsibilities": {
                "Store": "Issue approved lots.",
                "QC": "Check viscosity, pH, and appearance.",
                "Production": "Follow hydration and neutralization sequence.",
                "Supervisor": "Approve critical points."
            },
            "materials_equipment_required": ["Jacketed gel tank", "High-shear mixer", "Viscometer", "pH meter"],
            "procedures": [
                "1. Charge UV-irradiated water to 80% target volume.",
                "2. Disperse carbomer at 0.8% w/w under high shear to avoid lumps.",
                "3. Add glycerin at 5.0% w/w and mix for 15 minutes.",
                "4. Add preservative system as per approved BMR.",
                "5. Neutralize with triethanolamine slowly to achieve pH 6.0 to 7.0 and gel formation.",
                "6. De-aerate batch under low-speed mixing.",
                "7. Submit in-process sample for viscosity and pH approval before filling."
            ],
            "safety_precautions": ["Control dust during carbomer addition", "Avoid over-neutralization"],
            "documentation_records": ["Compounding sheet", "Viscosity log", "pH log"],
            "approval_workflow": ["Operator", "Supervisor", "QC"],
            "deviation_handling": ["If viscosity out-of-range, hold batch and escalate to QA"]
        },
        "form_schema": _base_form_schema(
            checklist=[
                {"name": "hydration_complete", "label": "Carbomer hydration complete", "required": True},
                {"name": "deaeration_done", "label": "De-aeration completed", "required": True},
                {"name": "qc_release", "label": "QC release for fill stage", "required": True},
            ],
            numeric_fields=[
                {"name": "batch_size_kg", "label": "Batch size (kg)", "required": True, "min": 1, "max": 2000},
                {"name": "carbomer_percent", "label": "Carbomer (%)", "required": True, "min": 0.6, "max": 1.0},
                {"name": "glycerin_percent", "label": "Glycerin (%)", "required": True, "min": 4.0, "max": 6.0},
                {"name": "final_ph", "label": "Final pH", "required": True, "min": 6.0, "max": 7.0},
                {"name": "viscosity_cps", "label": "Viscosity (cP)", "required": True, "min": 10000, "max": 60000},
            ],
        ),
        "db_table_structure": _base_db_structure(),
        "validation_rules": _base_validation_rules(["final_ph must be between 6.0 and 7.0", "viscosity_cps must be within approved product spec"]),
    },
    {
        "sop_code": "SOP-CMP-004C",
        "title": "SOP for Compounding - WoundCare Honey Gauze",
        "sop_number": "CMP-004C",
        "version": "1.0",
        "effective_date": "2026-04-26",
        "document": {
            "purpose": "Define controlled impregnation process for WoundCare Honey Gauze.",
            "scope": "Applies to all honey gauze production lots.",
            "responsibilities": {
                "Store": "Issue sterile gauze and approved medical honey.",
                "QC": "Verify coating weight and bioburden results.",
                "Production": "Perform impregnation and drying per procedure.",
                "Supervisor": "Verify coating uniformity and line clearance."
            },
            "materials_equipment_required": ["Sterile gauze rolls", "Medical grade honey", "Coating tray", "Drying rack"],
            "procedures": [
                "1. Verify sterile gauze lot and certificate status.",
                "2. Prepare impregnation mix containing medical honey at 80% w/w and sterile humectant phase at 20% w/w.",
                "3. Immerse gauze in impregnation bath and maintain uniform saturation.",
                "4. Pass gauze through calibrated nip rollers to target pick-up weight.",
                "5. Dry under controlled room conditions until target residual moisture is achieved.",
                "6. Cut, fold, and transfer to sterile packaging line.",
                "7. Submit samples for QC coating uniformity and microbiological check."
            ],
            "safety_precautions": ["Use sterile handling practices", "Segregate line from non-sterile operations"],
            "documentation_records": ["Impregnation log", "Coating weight log", "Drying log", "QC release"],
            "approval_workflow": ["Operator", "Supervisor", "QC"],
            "deviation_handling": ["Quarantine non-uniform coated rolls and open deviation immediately"]
        },
        "form_schema": _base_form_schema(
            checklist=[
                {"name": "sterile_materials_verified", "label": "Sterile materials verified", "required": True},
                {"name": "coating_uniform", "label": "Coating uniformity acceptable", "required": True},
                {"name": "qc_sample_sent", "label": "QC sample sent", "required": True},
            ],
            numeric_fields=[
                {"name": "honey_percent", "label": "Honey concentration (%)", "required": True, "min": 75, "max": 85},
                {"name": "pickup_weight_gsm", "label": "Pick-up weight (g/m2)", "required": True, "min": 50, "max": 500},
                {"name": "residual_moisture_percent", "label": "Residual moisture (%)", "required": True, "min": 5, "max": 25},
            ],
        ),
        "db_table_structure": _base_db_structure(),
        "validation_rules": _base_validation_rules(["honey_percent must be 80 +/- 5", "residual_moisture_percent must remain within validated drying range"]),
    },
    {
        "sop_code": "SOP-SDN-005",
        "title": "SOP for Shutting Down Production",
        "sop_number": "SDN-005",
        "version": "1.0",
        "effective_date": "2026-04-26",
        "document": {
            "purpose": "Ensure controlled equipment shutdown, post-run cleaning, and documentation closure.",
            "scope": "Applies after each production campaign and planned shutdown.",
            "responsibilities": {
                "Store": "Receive returnable materials and update stock records.",
                "QC": "Verify post-run cleaning status.",
                "Production": "Execute shutdown sequence.",
                "Supervisor": "Close records and hand over area status."
            },
            "materials_equipment_required": ["Shutdown checklist", "Cleaning materials", "Lockout tags"],
            "procedures": [
                "1. Stop feed and complete in-process material reconciliation.",
                "2. Power down equipment in validated sequence per manufacturer instructions.",
                "3. Apply lockout-tagout where maintenance cleaning is required.",
                "4. Perform end-of-run cleaning and waste disposal.",
                "5. Record meter readings and utility shutdown status.",
                "6. Close batch documents and transfer to supervisor review."
            ],
            "safety_precautions": ["Use lockout-tagout for energized equipment", "Do not clean rotating equipment before full stop"],
            "documentation_records": ["Shutdown checklist", "Cleaning log", "Batch close log"],
            "approval_workflow": ["Operator", "Supervisor", "QC if required"],
            "deviation_handling": ["Escalate unsafe shutdown condition to engineering and QA"]
        },
        "form_schema": _base_form_schema(
            checklist=[
                {"name": "reconciliation_done", "label": "Material reconciliation completed", "required": True},
                {"name": "shutdown_sequence_done", "label": "Shutdown sequence completed", "required": True},
                {"name": "post_cleaning_done", "label": "Post-run cleaning completed", "required": True},
            ],
            numeric_fields=[
                {"name": "shutdown_duration_minutes", "label": "Shutdown duration (min)", "required": True, "min": 1, "max": 1000},
            ],
        ),
        "db_table_structure": _base_db_structure(),
        "validation_rules": _base_validation_rules([]),
    },
    {
        "sop_code": "SOP-RRM-006",
        "title": "SOP for Receiving Raw Materials",
        "sop_number": "RRM-006",
        "version": "1.0",
        "effective_date": "2026-04-26",
        "document": {
            "purpose": "Control receipt, quarantine, sampling, and QC disposition of raw materials.",
            "scope": "Applies to all incoming active and excipient materials.",
            "responsibilities": {
                "Store": "Inspect receipt condition and quarantine materials.",
                "QC": "Sample and release or reject lots.",
                "Production": "Use only approved released lots.",
                "Supervisor": "Ensure traceable receipt records."
            },
            "materials_equipment_required": ["Receiving checklist", "Quarantine labels", "Sampling tools"],
            "procedures": [
                "1. Verify supplier, COA, delivery note, and purchase order match.",
                "2. Inspect container integrity and tamper evidence.",
                "3. Assign GRN and apply QUARANTINE status label.",
                "4. QC samples according to sampling plan and records lot details.",
                "5. Store under specified conditions pending disposition.",
                "6. Change status to APPROVED/REJECTED based on QC result and QA review."
            ],
            "safety_precautions": ["Handle hazardous powders with mask and extraction", "No direct use before release"],
            "documentation_records": ["GRN", "Quarantine log", "Sampling log", "QC release"],
            "approval_workflow": ["Store receiver", "QC analyst", "QA release"],
            "deviation_handling": ["Nonconforming receipt must be isolated and supplier notified"]
        },
        "form_schema": _base_form_schema(
            checklist=[
                {"name": "docs_verified", "label": "PO, COA and delivery docs verified", "required": True},
                {"name": "quarantine_applied", "label": "Quarantine status applied", "required": True},
                {"name": "sampling_done", "label": "Sampling completed", "required": True},
                {"name": "qc_disposition", "label": "QC disposition recorded", "required": True},
            ],
            numeric_fields=[
                {"name": "containers_received", "label": "Containers received", "required": True, "min": 1, "max": 100000},
                {"name": "containers_damaged", "label": "Containers damaged", "required": True, "min": 0, "max": 100000},
            ],
        ),
        "db_table_structure": _base_db_structure(),
        "validation_rules": _base_validation_rules(["containers_damaged must be less than or equal to containers_received"]),
    },
    {
        "sop_code": "SOP-RPM-007",
        "title": "SOP for Receiving Packaging Materials",
        "sop_number": "RPM-007",
        "version": "1.0",
        "effective_date": "2026-04-26",
        "document": {
            "purpose": "Control receipt of labels, cartons, foils, and pouches with identity and integrity checks.",
            "scope": "Applies to all incoming packaging materials.",
            "responsibilities": {
                "Store": "Receive and quarantine packaging materials.",
                "QC": "Verify label correctness and material quality.",
                "Production": "Use released packaging materials only.",
                "Supervisor": "Approve receipt records and reconciliation readiness."
            },
            "materials_equipment_required": ["Artwork master", "Receiving checklist", "Quarantine tags"],
            "procedures": [
                "1. Verify item code and artwork revision against approved master.",
                "2. Inspect packaging material for tears, moisture damage, print defects, and contamination.",
                "3. Segregate damaged components and raise discrepancy report.",
                "4. Quarantine received stock pending QC release.",
                "5. Update inventory status after disposition."
            ],
            "safety_precautions": ["Prevent mix-up by one-item-at-a-time verification"],
            "documentation_records": ["Packaging GRN", "Artwork verification record", "Discrepancy report"],
            "approval_workflow": ["Store", "QC", "Supervisor/QA"],
            "deviation_handling": ["Reject or quarantine incorrect artwork immediately"]
        },
        "form_schema": _base_form_schema(
            checklist=[
                {"name": "artwork_match", "label": "Artwork and label code match", "required": True},
                {"name": "integrity_check", "label": "Material integrity passed", "required": True},
                {"name": "quarantine_done", "label": "Quarantine completed", "required": True},
            ],
            numeric_fields=[
                {"name": "units_received", "label": "Units received", "required": True, "min": 1, "max": 10000000},
                {"name": "units_rejected", "label": "Units rejected", "required": True, "min": 0, "max": 10000000},
            ],
        ),
        "db_table_structure": _base_db_structure(),
        "validation_rules": _base_validation_rules(["units_rejected must not exceed units_received"]),
    },
    {
        "sop_code": "SOP-LCL-008",
        "title": "SOP for Line Cleaning After Each Batch",
        "sop_number": "LCL-008",
        "version": "1.0",
        "effective_date": "2026-04-26",
        "document": {
            "purpose": "Prevent cross-contamination through validated line cleaning after every batch.",
            "scope": "Applies to all product-contact lines after each batch completion.",
            "responsibilities": {
                "Store": "Issue validated cleaning agents.",
                "QC": "Perform clearance checks and swabs.",
                "Production": "Execute cleaning sequence.",
                "Supervisor": "Authorize line release for next batch."
            },
            "materials_equipment_required": ["0.5% bleach", "0.5% acetic acid", "20 mL/L soap solution", "UV water"],
            "procedures": [
                "1. Remove all residual product and labels.",
                "2. Wash with soap solution (20 mL/L UV water).",
                "3. Rinse with UV water until no visible residue remains.",
                "4. Apply 0.5% bleach with minimum 15-minute contact time.",
                "5. Perform final rinse with UV water, then acetic neutralization wipe where required.",
                "6. Dry and affix CLEANED status label with date/time.",
                "7. QC verifies cleanliness and signs clearance."
            ],
            "safety_precautions": ["Separate cleaning tools by product family", "Avoid aerosol generation during spray application"],
            "documentation_records": ["Line cleaning checklist", "Cross-contamination prevention check", "QC clearance"],
            "approval_workflow": ["Operator", "Supervisor", "QC"],
            "deviation_handling": ["Repeat full cleaning on failed swab/test"]
        },
        "form_schema": _base_form_schema(
            checklist=[
                {"name": "residue_removed", "label": "Residue and labels removed", "required": True},
                {"name": "disinfected", "label": "Disinfection completed", "required": True},
                {"name": "clearance_signed", "label": "Clearance signed", "required": True},
            ],
            numeric_fields=[
                {"name": "bleach_contact_minutes", "label": "Bleach contact time (min)", "required": True, "min": 15, "max": 30},
                {"name": "rinse_cycles", "label": "Number of rinse cycles", "required": True, "min": 1, "max": 10},
            ],
        ),
        "db_table_structure": _base_db_structure(),
        "validation_rules": _base_validation_rules(["bleach_contact_minutes must be >= 15", "rinse_cycles must be >= 1"]),
    },
    {
        "sop_code": "SOP-MNT-009",
        "title": "SOP for Reporting Need for Maintenance",
        "sop_number": "MNT-009",
        "version": "1.0",
        "effective_date": "2026-04-26",
        "document": {
            "purpose": "Establish a controlled workflow for identifying faults, reporting, escalation, and maintenance closure.",
            "scope": "Applies to all plant, utility, and production equipment.",
            "responsibilities": {
                "Store": "Support spare-parts traceability where required.",
                "QC": "Assess product impact of equipment failure.",
                "Production": "Raise maintenance request promptly.",
                "Supervisor": "Escalate based on severity and approve closure."
            },
            "materials_equipment_required": ["Maintenance request form", "Equipment logbook", "Fault tag"],
            "procedures": [
                "1. Stop equipment safely and isolate if fault is safety-critical.",
                "2. Record fault description, observed symptoms, and affected batch/line.",
                "3. Assign severity level: Level 1 (critical), Level 2 (major), Level 3 (minor).",
                "4. Notify maintenance team and supervisor according to severity matrix.",
                "5. Track response time and completion time in maintenance log.",
                "6. Verify repair, perform test run, and release equipment for use.",
                "7. Document root cause and preventive action if repeat failure."
            ],
            "safety_precautions": ["Do not bypass interlocks", "Use lockout-tagout before intervention"],
            "documentation_records": ["Maintenance request", "Escalation log", "Repair completion record", "Test run report"],
            "approval_workflow": ["Operator raises request", "Supervisor validates", "Maintenance closes", "QA reviews impact"],
            "deviation_handling": ["Any maintenance impacting product quality requires QA deviation and impact assessment"]
        },
        "form_schema": _base_form_schema(
            checklist=[
                {"name": "fault_logged", "label": "Fault logged with equipment ID", "required": True},
                {"name": "severity_assigned", "label": "Severity level assigned", "required": True},
                {"name": "escalation_done", "label": "Escalation completed", "required": True},
                {"name": "closure_verified", "label": "Closure and test run verified", "required": True},
            ],
            numeric_fields=[
                {"name": "severity_level", "label": "Severity level (1-3)", "required": True, "min": 1, "max": 3},
                {"name": "response_time_minutes", "label": "Response time (min)", "required": True, "min": 0, "max": 10000},
                {"name": "repair_time_minutes", "label": "Repair time (min)", "required": True, "min": 0, "max": 100000},
            ],
        ),
        "db_table_structure": _base_db_structure(),
        "validation_rules": _base_validation_rules([
            "severity_level must be integer between 1 and 3",
            "critical faults (severity_level=1) must have response_time_minutes <= 30",
        ]),
    },
]


def get_sop_templates():
    return deepcopy(SOP_LIBRARY)
