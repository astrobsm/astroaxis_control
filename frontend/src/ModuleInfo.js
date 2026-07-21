import React, { useState } from 'react';

// Responsibilities & duties for each module's authorised users.
// Written as a senior operations/ERP architect — concise, role-aware, audit-friendly.
export const MODULE_RESPONSIBILITIES = {
  dashboard: {
    title: 'Dashboard',
    roles: 'All authenticated users',
    duties: [
      'Review the daily KPI snapshot (staff, products, active orders, warehouses) at the start of each shift.',
      'Action upcoming birthday notifications (send greetings, prepare cake/gifts where applicable).',
      'Use Quick Actions to jump to high-frequency tasks (sales, production, attendance, payments).',
      'Escalate any KPI anomaly (e.g., zero stock, payment backlog) to the responsible module owner immediately.'
    ]
  },
  staff: {
    title: 'Staff Management',
    roles: 'HR, Admin, Factory Supervisor (read-only)',
    duties: [
      'Register every new employee with complete bio-data, NIN, bank details, position and hourly/monthly rate.',
      'Maintain accurate contact + next-of-kin records and update on change of address or status.',
      'Generate and securely communicate clock-in PIN; revoke on exit.',
      'Conduct quarterly data audits — flag missing fields and dormant accounts.',
      'Coordinate with Payroll to ensure salary structure matches signed contracts.'
    ]
  },
  attendance: {
    title: 'Attendance',
    roles: 'All staff (clock in/out), HR & Supervisors (oversight)',
    duties: [
      'Staff: clock in on arrival and clock out at end of shift using personal PIN — no proxy clocking.',
      'Supervisors: monitor live attendance, validate overtime, approve adjustments with written reason.',
      'HR: reconcile attendance vs. roster daily; investigate absenteeism within 24 hours.',
      'Lock prior-week records every Monday after sign-off to preserve payroll integrity.'
    ]
  },
  salaryPayroll: {
    title: 'Salary & Payroll',
    roles: 'Finance, HR, Admin',
    duties: [
      'Run payroll calculation only after attendance is locked for the period.',
      'Verify deductions, bonuses, and statutory contributions (PAYE, pension, NHF) before approval.',
      'Generate and distribute payslips (PDF) confidentially via secure channel.',
      'Maintain a clean audit trail — never edit posted payroll; raise correction entries instead.',
      'Reconcile total wages with bank disbursement file before sign-off.'
    ]
  },
  products: {
    title: 'Products',
    roles: 'Production Manager, Sales Lead, Admin',
    duties: [
      'Maintain master product catalogue: SKU, unit, BOM linkage, NAFDAC #, packaging, lead time.',
      'Set & review cost / wholesale / retail prices monthly against margin targets.',
      'Define re-order level and minimum order quantity to prevent stock-outs.',
      'Retire obsolete SKUs with a written justification; never delete with sales history.'
    ]
  },
  rawMaterials: {
    title: 'Raw Materials',
    roles: 'Procurement, Store-keeper, Production',
    duties: [
      'Record every raw-material intake with batch #, supplier, unit cost, expiry, and Certificate of Analysis where required.',
      'Update unit costs on every purchase to keep BOM costing accurate.',
      'Conduct weekly bin-card reconciliation against system stock.',
      'Quarantine and report any non-conforming material to QA immediately.'
    ]
  },
  stockManagement: {
    title: 'Stock Management',
    roles: 'Store-keeper, Warehouse Officer, Admin',
    duties: [
      'Maintain real-time stock accuracy across all warehouses — investigate variance > 1%.',
      'Enforce FIFO/FEFO on dispatch (especially for medical / consumable items).',
      'Run cycle counts on schedule; perform full count quarterly with a sign-off sheet.',
      'Restrict adjustments — every quantity change must carry a documented reason.'
    ]
  },
  production: {
    title: 'Production',
    roles: 'Production Manager, Production Staff, QA',
    duties: [
      'Plan production orders against confirmed sales orders and reorder thresholds.',
      'Confirm raw-material availability via BOM check before starting a run.',
      'Follow approved SOPs — deviation requires Production Manager + QA sign-off.',
      'Log machine usage, downtime and yield in real time for traceability.'
    ],
    sections: [
      {
        heading: 'Production Manager — Core Responsibilities',
        subtitle: 'Aligned with WHO GMP, ISO 13485 (medical devices) and NAFDAC GMP for Drugs & Medical Devices',
        items: [
          'Own the Master Production Schedule (MPS): convert confirmed sales orders, forecasts and reorder triggers into a weekly production plan.',
          'Guarantee GMP compliance on the floor: cleanliness, gowning, line clearance, environmental monitoring (temperature, humidity, particulate where applicable).',
          'Approve every Batch Manufacturing Record (BMR) and Batch Packaging Record (BPR) before the line starts and after the run is completed.',
          'Supervise raw-material dispensing in the presence of QA — verify batch number, expiry, Certificate of Analysis (CoA) and approved status.',
          'Enforce in-process controls (IPC) at defined stages: weight, fill volume, appearance, microbial limit (where applicable), seal integrity, label legibility.',
          'Lead deviation, change-control and CAPA (Corrective & Preventive Action) processes; nothing is released without QA sign-off.',
          'Maintain full traceability — every finished good must be traceable to its raw-material batches, operators, machines and date.',
          'Manage the team: roster, training, competency assessment and discipline on hygiene & SOP adherence.',
          'Plan and verify preventive maintenance and calibration of all production equipment with the Maintenance Engineer.',
          'Liaise with QA, Procurement, Stores and Regulatory Affairs to keep NAFDAC product registration, factory licensing and GMP certification current.'
        ]
      },
      {
        heading: 'Step-by-Step Production Workflow (international woundcare best practice)',
        subtitle: 'Pre-production → Production → Post-production → Release',
        items: [
          'STEP 1 — Demand & planning. Pull confirmed sales orders, reorder alerts and forecast. Use the Material Requirements Calculator on this page (select product, quantity, warehouse) to confirm raw-material sufficiency before committing.',
          'STEP 2 — Raise Production Order. Click "+ New Production Order", enter product, quantity, target completion date and assign batch number using the standard format (PRD/YY/MM/SEQ). Save — the system reserves the BOM materials.',
          'STEP 3 — BMR preparation. Print the Batch Manufacturing Record from the approved master template. Verify the BOM, process steps, IPC checkpoints and acceptance criteria are the current revision.',
          'STEP 4 — Line clearance. Inspect the area, equipment and documents. Confirm the previous batch is fully cleared, surfaces are clean & dry, no stray materials, and only current batch documents are present. QA signs the line-clearance form.',
          'STEP 5 — Dispensing. Withdraw raw materials from Stores against the BMR. Verify item, batch, expiry, quantity and CoA approval status. Two-person check (operator + QA). Reject any non-conforming material to the Quarantine bin.',
          'STEP 6 — Operator gowning & hygiene. Hand-wash, sanitise, wear approved garments (cap, mask, gloves, dedicated footwear). Visitors follow the same gowning. No food, jewellery or mobile phones on the floor.',
          'STEP 7 — Manufacturing. Follow the approved SOP exactly: mixing time, temperature, sterilisation cycle, fill volume, soak time, drying — whatever the product requires. Record actual values in the BMR at every checkpoint, not from memory.',
          'STEP 8 — In-process QC. At each defined IPC point QA samples and tests (e.g., honey gauze: weight, honey loading, gauze GSM, packaging seal; wound clex: pH, fill volume, label position). Out-of-spec results trigger immediate hold and deviation report.',
          'STEP 9 — Packaging & labelling. Verify primary pack, secondary pack, leaflet, label artwork, batch number, manufacturing date, expiry date and NAFDAC registration number against the approved artwork. Reconcile labels issued vs. used vs. destroyed.',
          'STEP 10 — Yield reconciliation. Compare actual yield against theoretical yield. Investigate any variance outside the approved limit (typically ±2% for liquids, ±1% for unit-dose). Document the investigation in the BMR.',
          'STEP 11 — Cleaning & equipment status. Clean the line per cleaning SOP, post the "CLEAN — ready for next batch" status label, and update equipment log. Sample rinse water if cleaning validation requires it.',
          'STEP 12 — Production Completion entry. Open the Production Completions module the SAME day. Record qty produced, qty damaged (with reason), staff hours, raw-material consumption, consumables, energy and lunch costs so unit costing is accurate.',
          'STEP 13 — Quarantine & QA release. Transfer finished goods to the Quarantine warehouse. QA performs final sampling per the sampling plan (e.g., √n+1 or ANSI Z1.4). On pass, QA issues a Certificate of Analysis and releases the batch to Finished Goods.',
          'STEP 14 — Documentation review. Production Manager reviews the BMR/BPR for completeness — every box ticked, every signature present, every deviation closed. Hand over to QA for batch release decision.',
          'STEP 15 — Archival & traceability. File the completed BMR, BPR, CoA, line-clearance form, deviation reports and CAPA records in the Batch File. Retain for the regulatory minimum (NAFDAC: at least 1 year past expiry, recommended ≥ 5 years).'
        ]
      },
      {
        heading: 'NAFDAC & International Compliance Checklist',
        subtitle: 'Run through this list every batch — non-negotiable',
        items: [
          'NAFDAC registration number printed on every primary and secondary pack — current, not expired.',
          'Manufacturing licence and GMP certificate displayed and within validity at the facility.',
          'Only NAFDAC-approved raw materials and packaging components used (each with valid CoA).',
          'Water used in production meets WHO purified-water standards; record daily quality checks.',
          'Environmental monitoring records (temperature, humidity, where applicable particulate count) available for the production day.',
          'All operators have current GMP training on file; new staff trained before touching the line.',
          'Equipment calibration certificates valid; out-of-calibration equipment is tagged and removed from use.',
          'Pest-control, waste-disposal and effluent-handling logs current and signed.',
          'Recall procedure documented and tested at least once per year — every batch must be recallable within 24 hours.',
          'Adverse-event / complaint handling procedure live; all complaints logged, investigated and reported to NAFDAC where required.'
        ]
      },
      {
        heading: 'Daily Cadence for the Production Manager',
        subtitle: 'A predictable rhythm prevents fire-fighting',
        items: [
          'Morning (07:30) — review yesterday\'s output, deviations and today\'s plan with shift leaders. Issue the day\'s BMRs.',
          'Mid-morning — floor walk: gowning, line clearance, IPC checks, equipment status. Sign at least one BMR live.',
          'Midday — review raw-material balances and tomorrow\'s plan with Stores & Procurement.',
          'Afternoon — close completed batches, sign Production Completion entries, hand over to QA.',
          'End of day — update the production KPI board: planned vs. actual, yield %, downtime, deviations open, batches released.',
          'Weekly — CAPA review, training review, preventive-maintenance review, supplier-quality review.',
          'Monthly — management review meeting: OEE, scrap %, cost per unit, regulatory status, audit readiness.'
        ]
      }
    ]
  },
  productionCompletions: {
    title: 'Production Completions',
    roles: 'Production Supervisor, QA, Finance (cost review)',
    duties: [
      'Record completion the same day production finishes — quantity produced, damaged and reason.',
      'Capture all production inputs: staff hours, wages, raw materials, consumables, energy, lunch.',
      'Confirm finished goods are transferred to the correct destination warehouse.',
      'Review cost-per-unit weekly with Finance; flag any unit-cost variance > 10%.'
    ]
  },
  consumables: {
    title: 'Consumables',
    roles: 'Store-keeper, Production',
    duties: [
      'Track usage of indirect production materials (gloves, lubricants, cleaning agents).',
      'Re-order when below minimum — never wait for stock-out.',
      'Charge consumables against the relevant production batch for accurate costing.'
    ]
  },
  machinesEquipment: {
    title: 'Machines & Equipment',
    roles: 'Maintenance Engineer, Production Manager, Finance (assets)',
    duties: [
      'Maintain asset register: serial, manufacturer, purchase cost, depreciation, location.',
      'Schedule and log preventive maintenance — never skip due dates.',
      'Log faults, downtime and repair cost; close the loop with root-cause analysis.',
      'Review depreciation and current value monthly with Finance.'
    ]
  },
  transfers: {
    title: 'Stock Transfers',
    roles: 'Warehouse Officer, Logistics',
    duties: [
      'Raise transfers between warehouses with full quantity, batch and waybill detail.',
      'Confirm dispatch only when truck is loaded and waybill signed by both parties.',
      'Track in-transit balances; escalate any transfer not received within 48 hours.'
    ]
  },
  returns: {
    title: 'Returned Products',
    roles: 'Customer Care, Sales, QA',
    duties: [
      'Capture every return with reason, condition, batch and original invoice reference.',
      'Route damaged/expired returns to QA for disposal certification.',
      'Process refund / replacement within agreed SLA (default 72 hours).',
      'Track recurring return reasons and feed them back to Production / QA.'
    ]
  },
  damagedTransfers: {
    title: 'Damaged Transfers',
    roles: 'Warehouse Officer, Logistics, QA',
    duties: [
      'Document all damages discovered on receipt with photos and waybill copy.',
      'Determine liability (carrier, supplier, internal) and raise the appropriate claim.',
      'Quarantine damaged items pending disposal sign-off — never return to active stock.'
    ]
  },
  receiveTransfers: {
    title: 'Receive Transfers',
    roles: 'Warehouse Officer, Store-keeper',
    duties: [
      'Verify physical quantity vs. waybill before signing receipt.',
      'Record short-shipments and damages on the same day; do not back-date.',
      'Update stock immediately to keep system and floor in sync.'
    ]
  },
  sales: {
    title: 'Sales',
    roles: 'Sales Officer, Sales Manager, Finance (credit check)',
    duties: [
      'Capture every sales order with customer, items, prices, discount and payment terms.',
      'Verify customer credit limit before raising orders on account.',
      'Issue invoice, receipt and waybill from the system — no manual documents.',
      'Follow up on quotations within 48 hours and convert to orders where possible.'
    ]
  },
  customers: {
    title: 'Customers',
    roles: 'Sales, Customer Care, Marketing',
    duties: [
      'Maintain a single, deduplicated customer record — no duplicates, no shorthand names.',
      'Capture full contact details, delivery address, TIN and credit terms.',
      'Tag customers by segment (hospital, pharmacy, distributor) for targeted marketing.',
      'Record every interaction (call, visit, complaint) for a complete relationship history.'
    ]
  },
  paymentTracking: {
    title: 'Payments & Debt',
    roles: 'Finance, Sales, Admin',
    duties: [
      'Post every customer payment within 24 hours of receipt — bank reference mandatory.',
      'Reconcile receivables weekly; circulate aging report to Sales.',
      'Issue receipts immediately and email/print for the customer.',
      'Escalate debts > 30 days to Sales Manager; > 60 days to Admin for legal action.'
    ]
  },
  procurement: {
    title: 'Procurement',
    roles: 'Procurement Officer, Admin, Finance',
    duties: [
      'Source from approved suppliers only; obtain at least 3 quotes for any PO above threshold.',
      'Raise PO from confirmed requisition — no verbal orders.',
      'Track delivery vs. PO; reject incomplete or off-spec deliveries.',
      'Review supplier performance quarterly (price, quality, lead time, reliability).'
    ]
  },
  logistics: {
    title: 'Logistics',
    roles: 'Logistics Officer, Drivers, Warehouse',
    duties: [
      'Plan daily delivery routes for cost and time efficiency.',
      'Ensure every dispatch carries the right waybill, invoice and product safety documents.',
      'Track vehicle location, fuel and driver hours; report incidents immediately.',
      'Confirm proof of delivery (POD) for every drop and file in the system.'
    ]
  },
  marketing: {
    title: 'Marketer',
    roles: 'Marketing Officer, Marketing Manager',
    duties: [
      'Submit weekly marketing plan every Monday with target facilities, KPIs and budget.',
      'Log every facility visit the same day — contact person, outcome, next action.',
      'Maintain an up-to-date facility & contact register with relationship status.',
      'Hand over qualified leads to Sales within 24 hours of conversion intent.',
      'Review own performance scorecard weekly; act on the gaps.'
    ]
  },
  hrCustomerCare: {
    title: 'HR / Customer Care',
    roles: 'HR Officer, Customer Care Officer, Admin',
    duties: [
      'Resolve every customer complaint within agreed SLA; escalate the rest with documented reason.',
      'Maintain a complaint log with root cause and corrective action.',
      'Coordinate staff welfare matters (leave, queries, disciplinary actions) confidentially.',
      'Run periodic staff and customer satisfaction surveys; share findings with management.'
    ]
  },
  reports: {
    title: 'Reports',
    roles: 'Management, Department Heads',
    duties: [
      'Pull and review module reports on the agreed cadence (daily, weekly, monthly).',
      'Validate figures against source modules before circulating externally.',
      'Use exports (PDF/Excel) for official communication only — never alter source data.',
      'Highlight trends (positive or negative) in the management meeting.'
    ]
  },
  financial: {
    title: 'Financial',
    roles: 'Finance Manager, Accountant, Admin',
    duties: [
      'Maintain accurate ledgers: revenue, COGS, expenses, payables, receivables.',
      'Perform monthly bank reconciliation; close the books within 5 working days of month-end.',
      'Prepare P&L, cash-flow and balance-sheet snapshots for management.',
      'Enforce segregation of duties — recorder, approver and payer must not be the same person.'
    ]
  },
  sop: {
    title: 'SOP / GMP',
    roles: 'QA Manager, Department Heads, All Operational Staff',
    duties: [
      'Read, understand and sign off on the SOP relevant to your role before performing the task.',
      'Follow GMP standards strictly — hygiene, gowning, documentation, batch records.',
      'Report any deviation immediately; never work around an SOP without written approval.',
      'Participate in scheduled SOP refresher training; keep training records updated.',
      'Department heads: review and revise SOPs at least annually or after any incident.'
    ]
  },
  communication: {
    title: 'Communication',
    roles: 'All staff',
    duties: [
      'Check notices and direct messages at least twice daily during work hours.',
      'Acknowledge receipt of any notice that requires action.',
      'Use professional language — internal communication is auditable.',
      'Escalate any urgent operational matter via the established channel, not informally.'
    ]
  },
  userManagement: {
    title: 'User Management',
    roles: 'System Administrator only',
    duties: [
      'Create user accounts only after authorised request; assign least-privilege roles.',
      'Review module access matrix monthly; revoke access on transfer or exit the same day.',
      'Enforce strong password policy and password rotation.',
      'Audit login history and flag suspicious activity (off-hours, foreign IP, repeated failures).'
    ]
  },
  letters: {
    title: 'Letters',
    roles: 'Admin, HR (issuing officers)',
    duties: [
      'Issue official letters (offer, confirmation, query, warning, reference, termination) using the approved template.',
      'Personalise content carefully — verify names, dates, positions before printing.',
      'Obtain authorised signature; deliver against acknowledgement.',
      'File a copy in the staff record and archive the digital version.'
    ]
  },
  settings: {
    title: 'Settings',
    roles: 'System Administrator',
    duties: [
      'Configure company profile, branding and currency only with management approval.',
      'Test changes in a controlled window — settings affect the entire app.',
      'Document every configuration change in the change log with date and reason.',
      'Back up settings before any major change.'
    ]
  }
};

export default function ModuleInfo({ module }) {
  const [open, setOpen] = useState(false);
  const info = MODULE_RESPONSIBILITIES[module];
  if (!info) return null;
  return (
    <div className="module-info-panel" role="region" aria-label={`${info.title} responsibilities`}>
      <button
        type="button"
        className="module-info-toggle"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
      >
        <span className="module-info-title">
          <span className="module-info-icon" aria-hidden="true">i</span>
          Responsibilities &amp; Duties — {info.title}
        </span>
        <span className="module-info-roles">{info.roles}</span>
        <span className="module-info-chevron" aria-hidden="true">{open ? '' : '+'}</span>
      </button>
      {open && (
        <div className="module-info-body">
          <ul className="module-info-list">
            {info.duties.map((d, i) => <li key={i}>{d}</li>)}
          </ul>
          {Array.isArray(info.sections) && info.sections.map((sec, si) => (
            <div className="module-info-section" key={si}>
              <div className="module-info-section-heading">{sec.heading}</div>
              {sec.subtitle && <div className="module-info-section-subtitle">{sec.subtitle}</div>}
              <ul className="module-info-list module-info-list--sub">
                {sec.items.map((it, ii) => <li key={ii}>{it}</li>)}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
