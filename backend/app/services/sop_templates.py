"""SOP template engine for BONNESANTE MEDICALS / ASTROBSM.

Deterministic, pharma-grade SOP skeleton generator. Each category produces
a structured document with the canonical sections required by NAFDAC/WHO
GMP. Phase 2 will layer an AI assistant on top of these skeletons.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List

# ---------------------------------------------------------------------------
# Category registry (used for auto-numbering: SOP-<CODE>-NNN)
# ---------------------------------------------------------------------------

CATEGORIES: Dict[str, Dict[str, str]] = {
    "QA":   {"label": "Quality Assurance",  "prefix": "SOP-QA"},
    "PROD": {"label": "Manufacturing",      "prefix": "SOP-PROD"},
    "QC":   {"label": "Quality Control",    "prefix": "SOP-QC"},
    "WH":   {"label": "Warehouse",          "prefix": "SOP-WH"},
    "ENG":  {"label": "Engineering",        "prefix": "SOP-ENG"},
    "HVAC": {"label": "HVAC / Cleanroom",   "prefix": "SOP-HVAC"},
    "HR":   {"label": "Personnel & Hygiene", "prefix": "SOP-HR"},
    "REG":  {"label": "Regulatory Affairs", "prefix": "SOP-REG"},
}

DOC_TYPES = [
    "SOP",                     # Standard Operating Procedure
    "BMR",                     # Batch Manufacturing Record
    "BPR",                     # Batch Packaging Record
    "VMP",                     # Validation Master Plan
    "IQ", "OQ", "PQ",          # Qualification protocols
    "VAL",                     # Validation Report
    "DOSSIER",                 # Regulatory dossier
    "POLICY",                  # Quality policy
    "FORM",                    # Controlled form / log
]


# ---------------------------------------------------------------------------
# Canonical SOP skeleton
# ---------------------------------------------------------------------------

def _common_sections() -> List[Dict[str, str]]:
    return [
        {"heading": "1.0 PURPOSE", "body": ""},
        {"heading": "2.0 SCOPE", "body": ""},
        {"heading": "3.0 RESPONSIBILITY", "body": ""},
        {"heading": "4.0 DEFINITIONS & ABBREVIATIONS", "body": ""},
        {"heading": "5.0 MATERIALS / EQUIPMENT", "body": ""},
        {"heading": "6.0 PROCEDURE", "body": ""},
        {"heading": "7.0 PRECAUTIONS", "body": ""},
        {"heading": "8.0 DOCUMENTATION", "body": ""},
        {"heading": "9.0 REFERENCES", "body": ""},
        {"heading": "10.0 APPENDICES", "body": ""},
        {"heading": "11.0 REVISION HISTORY", "body": ""},
    ]


# ---------------------------------------------------------------------------
# Category-specific template bodies (concise but professional starter text).
# The QA/Production team edits before approval — this is the regulator-ready
# scaffold, not the final operator copy.
# ---------------------------------------------------------------------------

_TEMPLATES: Dict[str, Dict[str, str]] = {
    # QUALITY ASSURANCE
    "qa.capa": {
        "title": "Corrective and Preventive Action (CAPA)",
        "purpose": (
            "To establish a documented system for identifying, investigating, "
            "implementing, and verifying corrective and preventive actions "
            "arising from deviations, complaints, audit findings, OOS results, "
            "and recalls, in accordance with NAFDAC GMP and WHO GMP "
            "expectations."
        ),
        "scope": (
            "Applies to all GMP activities at BONNESANTE MEDICALS (ASTROBSM) "
            "wound-care manufacturing facility, including Quality Assurance, "
            "Quality Control, Production, Warehouse, Engineering, and "
            "Regulatory functions."
        ),
        "responsibility": (
            "QA Manager — owns the CAPA programme and final approval.\n"
            "Department Heads — initiate CAPA, implement actions, verify "
            "effectiveness within agreed timelines.\n"
            "QA Officer — maintains the CAPA register, tracks due dates, and "
            "escalates overdue items to the QA Manager."
        ),
        "procedure": (
            "6.1 Identify the issue (deviation, complaint, audit finding, "
            "OOS, recall, trend).\n"
            "6.2 Log the CAPA in the Regulatory Compliance module with a "
            "unique CAPA number.\n"
            "6.3 Perform root-cause analysis using an appropriate tool "
            "(5-Why, Fishbone, FMEA).\n"
            "6.4 Define corrective actions (eliminate the cause of the "
            "detected nonconformity) and preventive actions (eliminate the "
            "cause of potential nonconformities).\n"
            "6.5 Assign owners, due dates, and verification criteria.\n"
            "6.6 Implement actions and capture objective evidence.\n"
            "6.7 Verify effectiveness after a defined monitoring period "
            "(minimum 30 days for procedural, 90 days for systemic).\n"
            "6.8 Close the CAPA with QA Manager e-signature.\n"
        ),
        "references": (
            "NAFDAC Good Manufacturing Practice Guidelines for Pharmaceuticals\n"
            "WHO TRS 986, Annex 2 — WHO GMP for Pharmaceutical Products\n"
            "ICH Q9 — Quality Risk Management\n"
            "ICH Q10 — Pharmaceutical Quality System"
        ),
    },
    "qa.deviation": {
        "title": "Deviation Handling",
        "purpose": "To define the procedure for documenting, investigating, and resolving planned and unplanned deviations from approved procedures, specifications, or standards.",
        "scope": "All GMP-related activities including manufacturing, packaging, QC testing, warehousing, and engineering operations.",
        "responsibility": "QA Manager approves classification and closure; Department Heads investigate; QA Officer maintains the deviation register.",
        "procedure": (
            "6.1 Discovery — any GMP staff who identifies a deviation must log it within 1 working day.\n"
            "6.2 Classification — Minor, Major, or Critical, assigned by QA based on impact on product quality, patient safety, or data integrity.\n"
            "6.3 Immediate containment actions documented before investigation begins.\n"
            "6.4 Root-cause analysis using appropriate tools.\n"
            "6.5 Impact assessment on affected batches, processes, equipment, and other products.\n"
            "6.6 CAPA generation where applicable.\n"
            "6.7 Closure within 30 days (Minor), 45 days (Major), 60 days (Critical) of identification.\n"
        ),
        "references": "NAFDAC GMP Guidelines; WHO TRS 986 Annex 2 §1.4; ICH Q10.",
    },
    "qa.change_control": {
        "title": "Change Control",
        "purpose": "To ensure all changes affecting GMP systems, processes, equipment, materials, methods, or documentation are evaluated, approved, implemented, and verified in a controlled manner.",
        "scope": "All proposed changes to validated systems, registered products, approved suppliers, and controlled documents.",
        "responsibility": "Change Owner initiates; QA Manager approves; affected functions review impact.",
        "procedure": (
            "6.1 Submit Change Request via the Regulatory Compliance module.\n"
            "6.2 Cross-functional impact assessment (QA, QC, Production, Engineering, Regulatory).\n"
            "6.3 Risk assessment per ICH Q9.\n"
            "6.4 QA Manager approval before implementation.\n"
            "6.5 Update validated state — re-validation/re-qualification where required.\n"
            "6.6 Update affected documents (SOPs, BMRs, specifications).\n"
            "6.7 Notify regulatory authority where the change affects a registered product (NAFDAC variation submission).\n"
            "6.8 Post-implementation review and closure.\n"
        ),
        "references": "NAFDAC GMP; ICH Q10 §3.2.3; WHO TRS 986 Annex 2.",
    },
    "qa.internal_audit": {
        "title": "Internal GMP Audit",
        "purpose": "To verify, through a planned programme of internal audits, that the Pharmaceutical Quality System is implemented effectively and complies with NAFDAC GMP, WHO GMP, and ISO 9001/13485 expectations.",
        "scope": "All GMP-impacting departments at BONNESANTE MEDICALS.",
        "responsibility": "QA Manager schedules and chairs the audit programme; trained Internal Auditors execute audits; auditee departments respond to findings.",
        "procedure": (
            "6.1 Annual audit schedule approved by QA Manager.\n"
            "6.2 Pre-audit notification (minimum 5 working days) with scope and checklist.\n"
            "6.3 Audit execution against documented criteria.\n"
            "6.4 Findings classified as Critical / Major / Minor / Observation.\n"
            "6.5 Audit report issued within 10 working days.\n"
            "6.6 CAPA opened for all findings.\n"
            "6.7 Follow-up audit to verify CAPA effectiveness.\n"
        ),
        "references": "ISO 19011 — Guidelines for auditing management systems; NAFDAC GMP §1; WHO TRS 986 Annex 2 §1.7.",
    },
    "qa.recall": {
        "title": "Product Recall",
        "purpose": "To provide an effective and documented procedure for the prompt withdrawal of any product batch from the market that is known or suspected to be defective or hazardous.",
        "scope": "All BONNESANTE MEDICALS wound-care products released to the market, including HERA-WOUND GEL and WOUND-CLEX.",
        "responsibility": "QA Manager is the designated Recall Coordinator; Regulatory Affairs notifies NAFDAC; Logistics executes physical retrieval.",
        "procedure": (
            "6.1 Recall classification per NAFDAC: Class I (life-threatening), Class II (temporary/reversible harm), Class III (unlikely harm).\n"
            "6.2 Mock recall exercise annually to verify effectiveness; reconciliation within 72 hours.\n"
            "6.3 On actual recall: notify NAFDAC within 24 hours of decision.\n"
            "6.4 Contact all distributors and customers in writing within 48 hours.\n"
            "6.5 Quarantine returned product in clearly labelled RECALL area.\n"
            "6.6 Investigate root cause via CAPA system.\n"
            "6.7 Final recall report to NAFDAC within 30 days.\n"
        ),
        "references": "NAFDAC Product Recall Guidelines; WHO TRS 986 Annex 2 §1.7.",
    },

    # MANUFACTURING
    "prod.gel_mixing": {
        "title": "Wound-Care Gel Manufacturing — Mixing Operation",
        "purpose": "To define the controlled procedure for the mixing of wound-care gel products (e.g. HERA-WOUND GEL) to ensure batch-to-batch uniformity, microbiological control, and compliance with the approved Master Manufacturing Record.",
        "scope": "Applies to all aqueous gel formulations manufactured in the Gel Manufacturing Suite.",
        "responsibility": "Production Officer executes; Production Manager supervises; QA performs line clearance; QC samples for IPC and finished product testing.",
        "procedure": (
            "6.1 QA line clearance and verification of cleaned status of the mixing vessel (last cleaning log signed off).\n"
            "6.2 Dispensed raw materials verified against the BMR (material code, batch number, weighed quantity, double-check signature).\n"
            "6.3 Charge purified water (WHO PhEur grade) into the jacketed vessel at the temperature defined in the MMR.\n"
            "6.4 Add the gelling agent under continuous stirring at the defined RPM until fully hydrated.\n"
            "6.5 Add actives in the prescribed order; record start/end time and temperature for each addition.\n"
            "6.6 Mixing continues for the validated duration; IPC samples drawn for pH, viscosity, appearance.\n"
            "6.7 Hold under low-shear agitation until filling.\n"
            "6.8 All operations recorded in real time in the BMR with operator/verifier e-signatures.\n"
        ),
        "references": "Master Manufacturing Record HERA-WOUND GEL; NAFDAC GMP §5 Production; WHO TRS 986 Annex 2 §5.",
    },
    "prod.line_clearance": {
        "title": "Line Clearance",
        "purpose": "To ensure that all previous product, materials, documents, and labels are removed from a manufacturing or packaging line before the start of a new batch, preventing cross-contamination and mix-up.",
        "scope": "All manufacturing, filling, and packaging lines.",
        "responsibility": "Production Officer requests; QA Officer performs and signs line clearance; both signatures required before batch start.",
        "procedure": (
            "6.1 Production Officer prepares the line and notifies QA.\n"
            "6.2 QA inspects: (a) absence of previous product / materials / labels / documents; (b) cleaning records signed and within validity; (c) equipment status labels current; (d) environmental conditions within limits.\n"
            "6.3 Line Clearance Certificate signed by QA and attached to the new BMR.\n"
            "6.4 No batch may start without a current Line Clearance Certificate.\n"
        ),
        "references": "WHO TRS 986 Annex 2 §5.20; NAFDAC GMP.",
    },
    "prod.batch_numbering": {
        "title": "Batch Numbering System",
        "purpose": "To define a unique, sequential, and traceable batch-numbering system for all products manufactured by BONNESANTE MEDICALS.",
        "scope": "All commercial and engineering batches.",
        "responsibility": "QA assigns and controls batch numbers via the Regulatory Compliance module.",
        "procedure": (
            "6.1 Batch number format: PPP-YY-NNN where PPP = product code (e.g. HWG = HERA-WOUND GEL, WCX = WOUND-CLEX), YY = 2-digit year, NNN = sequential batch in the year.\n"
            "6.2 Batch numbers are issued only on receipt of an approved Manufacturing Order.\n"
            "6.3 Batch numbers are never re-used.\n"
            "6.4 Cancelled batches retain the assigned number, marked CANCELLED in the register, with reason.\n"
        ),
        "references": "NAFDAC GMP §4; WHO TRS 986 Annex 2 §4.",
    },

    # CLEANROOM & HVAC
    "hvac.diff_pressure": {
        "title": "Differential Pressure Monitoring",
        "purpose": "To maintain the validated pressure cascade between cleanroom areas of differing classification, preventing the ingress of contamination into critical zones.",
        "scope": "All classified manufacturing areas (Grade C, Grade D, and adjacent unclassified support areas).",
        "responsibility": "Production Officer records readings each shift; Engineering investigates excursions; QA reviews logs weekly.",
        "procedure": (
            "6.1 Each classified room is fitted with a calibrated magnehelic gauge / digital DP sensor.\n"
            "6.2 Acceptance limit: 10–15 Pa positive between classified and adjacent lower-grade areas; alert at ≤8 Pa, action at ≤5 Pa.\n"
            "6.3 Readings logged at the start of each shift in the Regulatory Compliance Environmental Monitoring module.\n"
            "6.4 Any excursion triggers an immediate deviation, line stop in affected areas until restored, and HVAC investigation.\n"
            "6.5 Monthly trend review by QA Manager.\n"
        ),
        "references": "WHO TRS 961 Annex 6 — WHO GMP for sterile pharmaceutical products; ISO 14644-4; NAFDAC GMP §3.",
    },
    "hvac.environmental_monitoring": {
        "title": "Cleanroom Environmental Monitoring",
        "purpose": "To monitor and control temperature, humidity, viable and non-viable particle counts in classified areas to ensure compliance with the validated environmental specification.",
        "scope": "All Grade C and Grade D rooms.",
        "responsibility": "QC Microbiology performs viable monitoring; Production records temperature/humidity; QA reviews and trends.",
        "procedure": (
            "6.1 Temperature: 18–25 °C; Humidity: 30–65 %RH; readings every shift.\n"
            "6.2 Non-viable particle counts at qualification and at defined frequency thereafter (Grade C: monthly; Grade D: quarterly).\n"
            "6.3 Viable monitoring — settle plates, contact plates, active air sampling — frequencies per the Environmental Monitoring Programme.\n"
            "6.4 Action and alert limits defined per ISO 14644 and WHO Annex 6.\n"
            "6.5 Out-of-limit results: investigate per the OOS / Deviation SOPs.\n"
        ),
        "references": "WHO TRS 961 Annex 6; ISO 14644-1/2; NAFDAC GMP.",
    },

    # QC
    "qc.sampling": {
        "title": "Sampling of Raw Materials, In-Process and Finished Products",
        "purpose": "To define the procedure for representative, contamination-free sampling for QC testing.",
        "scope": "All raw materials, packaging materials, in-process samples, finished products, and retention samples.",
        "responsibility": "QC Sampler executes; QC Manager approves the Sampling Plan; QA reviews.",
        "procedure": (
            "6.1 Sampling Plan derived from √n + 1 (raw materials) or per the approved Master Sampling Plan.\n"
            "6.2 Sampling performed in the designated Sampling Booth under unidirectional airflow.\n"
            "6.3 Use clean, calibrated, dedicated sampling tools.\n"
            "6.4 Each sampled container labelled with a SAMPLED status sticker.\n"
            "6.5 Sample identification: material code, batch number, sampling date, sampler signature, container number.\n"
            "6.6 Samples delivered to QC under chain-of-custody.\n"
        ),
        "references": "WHO TRS 929 Annex 4 — Sampling of pharmaceutical products; NAFDAC GMP §6.",
    },

    # WAREHOUSE
    "wh.receipt": {
        "title": "Receipt of Materials",
        "purpose": "To define the controlled receipt of raw materials, packaging materials, and other GMP materials into the warehouse.",
        "scope": "All GMP materials received at BONNESANTE MEDICALS.",
        "responsibility": "Warehouse Officer performs receipt; QA Officer verifies; QC samples for testing.",
        "procedure": (
            "6.1 Verify delivery documents against the approved Purchase Order.\n"
            "6.2 Visual inspection: damaged packaging, broken seals, contamination, temperature excursion (for cold-chain items).\n"
            "6.3 Reconcile: number of containers, supplier batch, manufacturing date, expiry date, CoA presence.\n"
            "6.4 Affix QUARANTINE label and place in the Quarantine Area.\n"
            "6.5 Enter receipt in the ERP and notify QC for sampling.\n"
            "6.6 No material may be issued to Production while in QUARANTINE status.\n"
        ),
        "references": "WHO TRS 957 Annex 5 — WHO Good Distribution Practices; NAFDAC GMP §6.",
    },

    # ENG
    "eng.calibration": {
        "title": "Calibration of Measuring Instruments",
        "purpose": "To ensure that all GMP-critical measuring instruments are calibrated against traceable standards at defined intervals and remain fit for purpose.",
        "scope": "All measuring instruments used for GMP measurements — balances, thermometers, pH meters, magnehelic gauges, timers, etc.",
        "responsibility": "Engineering maintains the Master Calibration Schedule; QA approves the schedule and reviews completion.",
        "procedure": (
            "6.1 Maintain a Master Calibration Register listing every instrument, location, range, accuracy, calibration frequency, and last/next calibration dates.\n"
            "6.2 Calibration by accredited external laboratory or by trained internal personnel using traceable reference standards.\n"
            "6.3 Calibration certificates retained as GMP records.\n"
            "6.4 Each instrument bears a current calibration sticker (calibration date, next due, signature).\n"
            "6.5 Out-of-calibration instrument: immediate REJECTED tag, removal from service, impact assessment on results obtained since last successful calibration.\n"
        ),
        "references": "ISO/IEC 17025; NAFDAC GMP; WHO TRS 986 Annex 2 §3.41.",
    },

    # HR
    "hr.gowning": {
        "title": "Personnel Gowning for Cleanroom Entry",
        "purpose": "To prevent the introduction of contamination into classified manufacturing areas by ensuring personnel are correctly gowned.",
        "scope": "All personnel entering Grade C and Grade D manufacturing areas.",
        "responsibility": "Each individual is responsible for correct gowning; Production Supervisor verifies; QA audits compliance.",
        "procedure": (
            "6.1 Remove personal outer garments and accessories in the change-room ante-room.\n"
            "6.2 Hand hygiene per the Hand Washing SOP.\n"
            "6.3 Don the following in sequence: hair cover, beard cover (where applicable), facemask, dedicated cleanroom suit, dedicated footwear, gloves.\n"
            "6.4 Inspect gowning in the mirror before entering the classified area.\n"
            "6.5 Re-glove and re-mask on exit/re-entry; never re-use gowning between shifts.\n"
            "6.6 Personnel showing signs of illness must not enter classified areas — report to HR/QA.\n"
        ),
        "references": "WHO TRS 961 Annex 6; ISO 14644; NAFDAC GMP §2 Personnel.",
    },
}


def list_templates() -> List[Dict[str, str]]:
    """Return all available template keys grouped by category prefix."""
    out = []
    for key, t in _TEMPLATES.items():
        cat_code = key.split(".")[0].upper()
        out.append({
            "key": key,
            "category_code": cat_code if cat_code in CATEGORIES else "QA",
            "category": CATEGORIES.get(cat_code, CATEGORIES["QA"])["label"],
            "title": t["title"],
        })
    out.sort(key=lambda x: (x["category"], x["title"]))
    return out


def generate_sop(template_key: str) -> Dict:
    """Build a fully populated SOP skeleton for the given template key.

    Returns a dict suitable for storing in `reg_documents.content_json`.
    """
    t = _TEMPLATES.get(template_key)
    if not t:
        # Generic skeleton if the template key is unknown
        t = {
            "title": "Standard Operating Procedure",
            "purpose": "[Define the purpose of this SOP.]",
            "scope": "[Define the scope.]",
            "responsibility": "[List responsibilities.]",
            "procedure": "[Detailed procedure.]",
            "references": "NAFDAC GMP; WHO GMP.",
        }

    sections = _common_sections()
    body_map = {
        "1.0 PURPOSE": t.get("purpose", ""),
        "2.0 SCOPE": t.get("scope", ""),
        "3.0 RESPONSIBILITY": t.get("responsibility", ""),
        "4.0 DEFINITIONS & ABBREVIATIONS": (
            "GMP — Good Manufacturing Practice\n"
            "NAFDAC — National Agency for Food and Drug Administration and Control\n"
            "WHO — World Health Organization\n"
            "QA — Quality Assurance\n"
            "QC — Quality Control\n"
            "SOP — Standard Operating Procedure\n"
            "BMR — Batch Manufacturing Record\n"
            "CAPA — Corrective and Preventive Action\n"
            "OOS — Out of Specification"
        ),
        "5.0 MATERIALS / EQUIPMENT": (
            "Refer to the relevant Master Manufacturing Record / Equipment List."
        ),
        "6.0 PROCEDURE": t.get("procedure", ""),
        "7.0 PRECAUTIONS": (
            "Operators shall be trained and qualified on this SOP before performing any task.\n"
            "All deviations from this SOP shall be recorded per the Deviation Handling SOP."
        ),
        "8.0 DOCUMENTATION": (
            "All records generated during execution shall be retained as GMP records "
            "for a minimum of one (1) year after the expiry of the last batch produced "
            "under this procedure, or as required by NAFDAC."
        ),
        "9.0 REFERENCES": t.get("references", "NAFDAC GMP; WHO GMP."),
        "10.0 APPENDICES": "Attach forms, checklists, and diagrams as applicable.",
        "11.0 REVISION HISTORY": "Version 1.0 — initial issue on " + date.today().isoformat() + ".",
    }
    for s in sections:
        s["body"] = body_map.get(s["heading"], "")

    return {
        "title": t["title"],
        "sections": sections,
    }
