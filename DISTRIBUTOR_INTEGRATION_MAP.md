# Distributor Module — Integration Map

**Astro BSM Stock Master (astroaxis_control) · Bonnesante Medicals**
**Status: inspection complete. No code written. Awaiting go-ahead.**

This is the required first response before implementation. It maps what exists,
what will be reused, what will be extended, what genuinely must be new, and
where the risks are.

---

## 1. Existing modules discovered

**137 live tables**, 50 API routers, ~442 API routes. Inspected against the
running production database, not just the repository.

| Domain | Routers | Status |
|---|---|---|
| Inventory | `stock`, `stock_management`, `warehouses`, `warehouse_transfers`, `receive_transfers`, `damaged_transfers`, `returns` | Mature |
| Sales / Orders | `sales`, `public_orders`, `payment_tracking`, `legacy_debts` | Mature |
| Accounting | `accounting`, `costs`, `financial`, `profits`, `tax`, `budgeting`, `assets`, `settlements` (MAPD) | **Very mature — full double-entry GL** |
| Procurement | `procurement`, `logistics` | Mature |
| Production | `production`, `bom`, `production_*` | Mature |
| People | `staff`, `attendance`, `payroll`, `hr_customercare`, `permissions`, `auth` | Mature |
| Quality/Regulatory | `regulatory`, `sop`, `sop_library`, `maintenance` | Mature |
| Commercial | `marketing` | Partial |
| Recent (this engagement) | `wallet`, `calls`, `telephony_webhook` | New |

**There is no distributor, territory, or geography concept anywhere.** The
`geo` router is GPS tagging for attendance and login events — not geography.
The only "distributor" strings in the codebase are MAPD's *distributor
commission* GL accounts (`2510`, `6310`), which are settlement rules, not an
entity.

---

## 2. Sources of truth — established and non-negotiable

| Domain | Authoritative store | Authoritative service | Distributor module's relationship |
|---|---|---|---|
| **Product** | `products`, `product_pricing` | `api/products.py` | Read only; add classification fields |
| **Company inventory** | `stock_levels`, `stock_movements` | **`services/inventory.py`** | Must call, never bypass |
| **Order** | `sales_orders`, `sales_order_lines` | `api/sales.py` | Extend with dimensions |
| **Invoice / AR** | `invoices`, `invoice_lines` | **`services/receivables.py`** | Must call, never duplicate |
| **Payment** | `payments` | `services/receivables.py` | Must call |
| **General ledger** | `gl_accounts`, `gl_journal_entries`, `gl_journal_lines` | **`services/ledger.py` → `post_entry()`** | Must call via `services/posting.py` |
| **Customer / debtor** | `customers` | `api/sales.py`, `services/customer_debt.py` | **Reuse as distributor's accounting identity** |
| **Stock location** | `warehouses` | `api/warehouses.py` | **Reuse as distributor's stock location** |
| **Documents / e-signature** | `reg_documents`, `reg_signatures` | `api/regulatory.py` | Reuse for agreements |
| **Auth / roles** | `users`, `role_permissions`, `user_module_access` | `api/auth.py` | Extend role set only |
| **Audit** | `audit_logs` + per-module append-only logs | — | Follow the existing trigger pattern |

### The exact path an order already takes

`api/sales.py` is the orchestration point and calls, in order:

```
apply_stock_movement()      → services/inventory.py   (stock_levels + stock_movements)
ensure_invoice_for_order()  → services/receivables.py (invoices)
record_payment()            → services/receivables.py (payments, recomputes paid_amount)
post_sale()                 → services/posting.py     → ledger.post_entry() (GL)
reverse_sale()              → the correction path
```

**The distributor module will call this same path.** It will not reimplement
any step of it.

---

## 3. The three decisions that shape everything

### Decision 1 — A distributor is a *Customer* + a *Warehouse* + a profile

This is the central integration decision and it resolves §5, §6 and §8 at once.

```
                    distributors  (NEW — commercial & compliance identity)
                          │
        ┌─────────────────┼─────────────────┐
        ↓                 ↓                 ↓
   customers.id     warehouses.id      users.id
  (accounting       (stock location,   (portal login,
   identity, AR,     receives stock     optional)
   invoices,         by transfer)
   payments)
```

**Consequences — all of them good:**

- Distributor invoices, payments, AR ageing and GL postings work **with zero new
  financial code**, because the distributor *is* a customer to the accounting
  system.
- Distributor stock is a **real warehouse balance**. Shipping to a distributor
  is `transfer_stock(main_warehouse → distributor_warehouse)` — an existing,
  audited, balance-checked operation. There is no second inventory.
- Credit limit already exists on `customers` (though **never enforced** — see
  risks).
- Territory/distributor reporting joins through `sales_orders.customer_id`.

**Rejected alternative:** a `distributor_accounts` / `distributor_stock` pair.
That is exactly the parallel-system prohibition in §33.

### Decision 2 — A distributor order *is* a `sales_order`

Add commercial dimensions to the existing table rather than creating a second
order entity:

```sql
ALTER TABLE sales_orders
  ADD COLUMN distributor_id UUID REFERENCES distributors(id),
  ADD COLUMN territory_id   UUID REFERENCES territories(id),
  ADD COLUMN sales_channel  VARCHAR(24) NOT NULL DEFAULT 'DIRECT';
```

`sales_channel` ∈ `DIRECT | DISTRIBUTOR | PUBLIC_ORDER`. Existing rows default
to `DIRECT` and behave exactly as today. The distributor portal becomes a
different *front door* to the same order engine, with its own approval steps
before the order is created.

**Status vocabulary:** the spec's 12 uppercase states map onto the existing
lowercase `sales_orders.status` (`pending/confirmed/production/shipped/
delivered/cancelled`) through a presentation mapping plus a distributor-side
workflow table for the pre-order steps. The existing status column is **not**
re-vocabularised — too much depends on it.

### Decision 3 — Downstream sales are genuinely new, and are *not* accounting records

This is the one place a new transaction entity is justified, and the reasoning
matters:

`sales_orders` records **company → customer**. A distributor selling to a
pharmacy is **distributor → end customer** — a transaction the company is not a
party to, earns no revenue from, and must not post to its GL. The company
already recognised revenue when it sold to the distributor.

So `distributor_sales` is a **reported commercial record**, not a financial one:

- It produces **no journal entry**. Posting one would double-count revenue.
- It moves stock only in the **distributor's** warehouse, never company stock.
- It carries `REPORTED → VERIFIED → REJECTED` provenance (§22), and only
  `VERIFIED` counts toward KPIs.

This is the same discipline as the wallet's `duration_source` and the call log's
provenance labels: a soft figure is never rendered as a hard one.

---

## 4. Entities: reuse / extend / create

### Reuse unchanged (no new tables)

`products` · `product_pricing` · `stock_levels` · `stock_movements` ·
`warehouses` · `invoices` · `invoice_lines` · `payments` · `gl_*` ·
`reg_documents` · `reg_signatures` · `audit_logs` · `users` · `role_permissions`

### Extend (additive columns only)

| Table | Columns added | Why |
|---|---|---|
| `sales_orders` | `distributor_id`, `territory_id`, `sales_channel` | §15 dimensions on the authoritative order |
| `customers` | `distributor_id`, `cac_number`, `tin` | Link the accounting identity; §21 duplicate detection |
| `warehouses` | `warehouse_kind`, `distributor_id` | Mark a location as distributor-held |
| `products` | `regulatory_class`, `registration_number`, `storage_requirement`, `batch_required`, `expiry_required`, `temperature_controlled` | §93 |
| `stock_movements` | `batch_id` | §19 traceability — **currently impossible** |

### Create (genuinely absent)

**Geography & territory** — `countries`, `states`, `lgas`, `zones`,
`territories`, `territory_assignments`, `territory_targets`

**Distributor identity & compliance** — `distributors`,
`distributor_documents`, `distributor_qualifications`, `distributor_facilities`,
`facility_assessments`, `facility_assessment_items`, `distributor_agreements`

**Commercial** — `distributor_marketers`, `distributor_sales`,
`distributor_sale_evidence`, `distributor_customers`

**Performance** — `distributor_performance`, `territory_performance`,
`corrective_action_plans`, `distributor_privileges`

**Traceability (a real gap)** — `product_batches`

**Configuration** — `kpi_configurations`, `regulatory_requirements`,
`business_rule_versions`

**Workflow** — `distributor_applications`, `approval_requests`

Roughly **28 new tables**, all additive. No existing table is dropped or
re-shaped.

---

## 5. Synchronisation strategy

**There is none, by design.** Synchronisation implies two copies that can
disagree. Because a distributor *is* a customer and *is* a warehouse, financial
and stock figures are read live by join — there is nothing to sync.

The only cached values are **performance aggregates** (`distributor_performance`,
`territory_performance`), which are:

- derived from `sales_orders` + `distributor_sales` by a scheduled job,
- stamped with `calculated_at` and the `rule_version` used (§92),
- **recomputable from source at any time**, and
- never an input to any financial or stock decision.

This mirrors the wallet's `balance` cache: derived, stamped, and verifiable
against the ledger on demand.

Where §18 requires "existing accounting system wins" — there is no second
figure to lose to it.

---

## 6. Potential conflicts

| # | Conflict | Severity | Handling |
|---|---|---|---|
| 1 | **No batch/lot system exists.** `stock_movements` has no batch column. | **High** | §19 traceability and §41 recall are *unbuildable* today. Must add `product_batches` + `stock_movements.batch_id`. Existing rows get NULL — historical stock is untraceable and that must be stated, not hidden. |
| 2 | `sales_orders` is the most-used table in the app | **High** | Additive nullable columns + default only. No backfill, no re-vocabularising `status`. |
| 3 | `customers.credit_limit` exists but **is never enforced** for customers (only suppliers, in procurement) | Medium | §30 credit limits need enforcement built. It will apply to distributors first; extending it to all customers is a separate decision. |
| 4 | Notifications are **broadcast-only** — the push store is keyed by browser endpoint with no user link | Medium | §27 weekly per-distributor notifications cannot use push. Use the derived-inbox pattern already proven in the wallet module. |
| 5 | Three people concepts: `users` (login), `staff` (payroll/attendance), `employees` | Medium | Distributor marketers are **not** company staff. New table, linked to `distributors`, not to `staff`. |
| 6 | `marketing_daily_logs.marketer_staff_id` → `staff` | Low | Company marketing ≠ distributor marketers. Keep separate; do not conflate. |
| 7 | `warehouses.manager_id` → `staff`, but distributors are `users` | Low | Leave `manager_id` NULL for distributor warehouses. |
| 8 | Product master has no regulatory fields | Medium | Additive columns; nothing reads them today. |
| 9 | `ACCOUNTING_POSTING_ENABLED=true` in production | **Note** | GL posting is **live**. Any distributor transaction touching `post_sale()` posts immediately. Correct, but must be deliberate. |
| 10 | Frontend is one 12.5k-line `AppMain.js` | Medium | Follow the `PaymentDistribution`/`WalletAdmin` pattern: self-contained components, minimal edits to the monolith. |

---

## 7. Risks

1. **Scale.** This specification is 103 sections. Honestly assessed it is
   **several weeks** of work, not one session. Delivering it as one
   undifferentiated block would be untestable and unreviewable.
2. **Territory data.** Nigeria has 36 states + FCT and **774 LGAs**. That
   reference data must be seeded accurately; a wrong LGA list silently
   corrupts every territory report built on it.
3. **Batch retrofit.** Adding batches to a live system with existing stock means
   historical movements have no batch. Recall coverage starts from the switch-on
   date. This must be stated plainly to anyone relying on it for safety.
4. **Order table changes** touch the busiest code path in the application.
   Regression testing of existing sales is mandatory (§66).
5. **Legal text.** §8 lists 41 agreement clauses. I will build the *versioning,
   presentation, acceptance and signature* machinery and a clearly-marked
   template placeholder. **I will not draft binding legal language** — that
   needs a Nigerian lawyer, and §8 itself says so.
6. **The ₦1m target** is a commercial assumption, not a fact. Built as
   configurable data with per-territory override and change history (§61, §95).

---

## 8. Recommended implementation sequence

Each phase ends deployable, tested, and useful on its own. No phase leaves the
system in a half-working state.

| Phase | Delivers | Depends on |
|---|---|---|
| **1. Geography & territory** | `countries/states/lgas/zones/territories`, full Nigeria seed, target engine with change history, territory CRUD + admin screen | — |
| **2. Distributor identity** | `distributors` + customer/warehouse linkage, application workflow, duplicate detection, documents, qualifications | 1 |
| **3. Compliance** | Facility assessment + scoring, eligibility scoring, agreements on `reg_documents`/`reg_signatures` | 2 |
| **4. Territory assignment** | Application → review → assignment, exclusivity enforcement, assignment history | 1, 2 |
| **5. Ordering** | Distributor portal ordering via **existing** `sales_orders`, approval workflow, credit check | 2, 4 |
| **6. Stock & batch** | `product_batches`, `stock_movements.batch_id`, distributor warehouse transfers, quarantine/recall blocking | 5 |
| **7. Downstream sales** | `distributor_sales` with REPORTED/VERIFIED provenance, evidence upload, marketers | 5 |
| **8. Performance** | Calendar-aware run-rate engine, traffic light, scorecard, 3-month review trigger | 7 |
| **9. Notifications & jobs** | Derived inbox, weekly performance, document expiry, idempotent scheduled jobs | 8 |
| **10. Dashboards & reports** | Command centre, heat map, ranking, exports | 8 |
| **11. Returns, complaints, recall** | Via existing returns workflow; recall traceability | 6 |
| **12. Hardening** | Permissions matrix, audit review, regression suite, security review | all |

**Phases 1–2 are the load-bearing foundation.** Everything else hangs off
territory and distributor identity, and both are pure additions with no risk to
existing functionality.

---

## 9. What I need from you before starting

Three decisions I should not make unilaterally:

1. **Distributor = customer + warehouse.** Confirm. Everything depends on it,
   and it is the difference between integrating and building a parallel system.
2. **Batch/lot traceability** — it does not exist. Build it now (needed for
   recall) or defer it? It is the largest single piece of work and it touches
   the live inventory path.
3. **Scope of this pass.** My recommendation: Phases 1–2, delivered complete
   and tested, then reassess. That gives you territories and distributors as
   real, usable, deployable functionality within one session rather than twelve
   half-finished features.

---

## 10. Delivered so far

### Phases 1–2 — geography, territory, distributor identity (`x3456789012w`)

As mapped in sections 3–4. A distributor resolves to a `customers` row and a
`warehouses` row; there is no second ledger and no second stock balance.

### Phase 3 — compliance (`y4567890123x`)

Facility assessment, corrective actions and agreements. Two constraints in this
phase are worth stating because they are the ones most easily eroded later:

**Company policy is never recorded as law.** `facility_checklist_items` carries
a `requirement_kind` of REGULATORY, COMPANY or COMMERCIAL, and a database check
constraint refuses a REGULATORY item that does not name the authority imposing
it. All 25 items seeded with the migration are COMPANY with no authority — the
migration does not know which Nigerian regulations apply to this company's
premises, and guessing would put a false legal claim in front of distributors in
Bonnesante's name. Marking an item regulatory is a deliberate act by someone who
knows which regulation they mean.

**A critical failure is not absorbed by the score.** `score_assessment` returns
the weighted percentage and the failed critical and regulatory items separately.
The outcome is FAIL whenever either list is non-empty, whatever the percentage
says. A store with no quarantine area scores 95% and still fails, and the screen
says so in those words. `NOT_APPLICABLE` is excluded from both sides of the
score, so a facility is not marked down for lacking a cold chain it does not
need.

Supporting guarantees, all enforced in the database rather than by convention:

- An answer snapshots the requirement text, kind and weight as they read when
  it was answered. Rewording the checklist next year cannot change what a past
  inspector is recorded as having found.
- A submitted assessment freezes and cannot be deleted. To change a finding you
  carry out a new assessment; the earlier one stays on the record.
- Every FAIL and REQUIRES_CORRECTION raises a tracked corrective action with a
  severity, and closing one requires recording who verified the fix.
- An agreement's text and hash freeze at issue. `sign_agreement` refuses a
  signature whose `body_sha256` does not match the text on record, and the
  browser computes that hash from the words actually displayed — so a signature
  attaches to specific text rather than to a button press. Where the browser
  cannot hash (no secure context), the UI disables signing rather than falling
  back to a hash the signer never verified.
- A signature must state what it means, in the signer's own words.
- Signatures are append-only; agreements cannot be deleted; only one agreement
  can be in force per distributor at a time.
- Activation requires both a DISTRIBUTOR and a COMPANY signature.

**What this phase deliberately does not do:** it does not draft contract
language. Section 8 of the specification is explicit that the agreement template
is a company document requiring legal review, and generating clauses that read
as settled law would be the opposite of that. The app supplies the lifecycle,
the hashing and the signature discipline; the words are pasted in by whoever is
qualified to approve them.

Tests: `backend/tests/test_compliance.py` (19), run against the real migration.

### Phase 4 — territory applications and real exclusivity (`z5678901234y`)

**The correction this phase makes.** Phase 1 enforced exclusivity with a partial
unique index: one live exclusive holder per territory. That is correct, and it
stays. It is also not sufficient, and the gap only appears once real territories
are drawn.

`territory_lgas` is the authoritative coverage, and nothing stopped two
territories covering the same LGA. "Lagos Mainland" and "Ikeja Corridor" can
both include Ikeja. Each is exclusive, each has exactly one holder, and the
per-territory index is satisfied in both cases — while two distributors now hold
exclusive rights over the same ground. That is the precise dispute exclusivity
exists to prevent, it surfaces months later when both are selling into Ikeja and
each holds a signed agreement saying the area is theirs, and by then the company
has promised the same thing twice in writing.

Exclusivity is now enforced **per LGA**, by database triggers, on both routes in:

- granting an assignment whose territory shares an LGA with another exclusive
  territory held by a different distributor;
- adding an LGA to a territory that is already assigned, which would otherwise
  create the same clash without touching `territory_assignments` at all.

The error names the LGA, the conflicting territory and the incumbent. Overlap
between territories held by the *same* distributor is allowed — that is one
promise made twice, not a contradiction. Overlap where either side is
non-exclusive is also allowed, and reported as advisory rather than blocking.

Triggers rather than application checks, because an application check is one
untested code path away from not running, and this is a promise the company makes
in a contract.

**Applications.** `distributor_applications` arrived in phase 1 and nothing used
it. It already modelled what a territory request needs — an applicant, a scored
review, a decision with a note and a decider — so rather than create a second
near-identical table it gained a `kind` and a nullable `territory_id`. One table,
one review queue.

An approved application creates its assignment in the same transaction and
records which one, so a decision and its effect cannot drift apart. The
eligibility score is snapshotted when review begins and never recomputed —
rerunning today's weights against last year's decision would rewrite why that
decision was made. The conflicts as they stood are stored with the decision, so a
reviewer who granted over a known overlap cannot later say the system never
showed them one.

**Termination releases territory.** Previously a terminated distributor kept its
live assignment, which satisfied the exclusivity index and made the territory
permanently ungrantable with nothing on screen explaining why. `set_status` now
ends those assignments — ended, never deleted, so historical sales stay attached
to whoever made them.

**What the UI refuses to do.** A blocking conflict is not offered with a
confirmation, because the database will refuse it regardless and a button that
always fails teaches people to distrust every other button. Only advisory
overlaps get an "I intend this" checkbox. Eligibility, compliance and conflicts
are shown as three separate answers and never combined into one score.

Tests: `backend/tests/test_territory_applications.py` (16), run against the real
migration chain.

### Phase 5 — the distributor ordering portal (`a6789012345z`)

A distributor is issued a shareable link and orders at `/order/<token>` with no
login. They see no prices; once they have chosen everything they see one total
for the basket.

**The order is an ordinary sales order.** `sales_orders` + `sales_order_lines`,
priced from `product_pricing` exactly as the existing public ordering path
prices them, with `sales_channel = 'DISTRIBUTOR'` and `distributor_id` set —
columns that already existed from phase 1. There is no distributor order table,
no staging queue and nothing to reconcile; the order appears in sales reporting,
receivables and despatch like every other one. The wholesale minimum-quantity
rules are imported from `public_orders.py` rather than reimplemented, because two
copies of a commercial rule disagree the first time one is changed.

**The token is a credential and is treated as one.** Only its SHA-256 is stored,
so a dump of the table — a backup on a laptop, a support export, a leaked
replica — hands out no working links. It is shown once, on creation, and the app
genuinely cannot show it again. `expires_at` is NOT NULL with no "never" value:
a permanent unauthenticated entry point outlives the relationship it was issued
for and nobody remembers to revoke it. Revocation is immediate, permanent and
recorded, and a database trigger refuses to un-revoke — reinstating one would
make the audit trail lie about the window in which a leaked credential worked.
Every use is written to an append-only table, including tokens matching nothing,
because a run of those is what guessing at links looks like and it is invisible
otherwise.

**How prices are hidden, and what that is worth.** The catalogue query does not
select the price columns at all. Selecting them and dropping them from the
response would have been easier and is precisely what to avoid: a response
filter is one refactor away from leaking the list, and what is never fetched
cannot be returned by accident. The order is priced again from the database at
submission, so a client that sends its own total changes nothing.

Being straight about the limit: **a basket total reveals unit prices to anyone
who wants them.** Quote one carton, then two, and the difference is the unit
price. That is inherent in showing a total at all and no care in the
implementation changes it. What the design does buy is real but narrower — the
price list cannot be lifted wholesale, screenshotted or forwarded, and the
distributor is not handed a per-item column to compare against a competitor's.
If unit prices must be genuinely secret from the person ordering, they cannot be
shown a total either, and that is a commercial decision rather than a technical
one.

**Credit is reported, not enforced at the portal.** The order is accepted and the
credit position is attached for the staff who confirm it. A distributor told
mid-basket that they are over a limit, by a screen that cannot say by how much
or what to pay, is worse than useless — and the company confirms every order
anyway, which is where a credit decision belongs. The lookup runs inside a
SAVEPOINT: the first version caught its failure and returned "unchecked", which
looked harmless and was not, because a failed statement poisons the whole
transaction and the caller's later commit rolled back the order itself. The
distributor was handed an order number for an order that did not exist.

Tests: `backend/tests/test_portal.py` (19), run against the real migration chain.

### Phase 6 — batches, quarantine and recall (`b7890123456a`)

This is the phase the map flagged as the largest single piece of work, and the
one §19 traceability and §41 recall were waiting on.

**There is no batch balance table.** A batch's quantity on hand is derived by
summing `stock_movements`, exactly as every other balance already is. A
`batch_stock_levels` cache would have been faster and is the obvious thing to
build; it is also a second copy of a number the system already knows, and two
stores of the same quantity drift. The drift would be discovered during a
recall — the one moment the figure has to be right.

**Quarantine and recall are enforced in the database as well as the service.**
`inventory.apply_stock_movement` is the single write path and checks it, but
that module's own docstring records that ~12 call sites once hand-rolled their
own balance updates. A recalled batch leaving the building because someone wrote
a raw INSERT is not a failure mode worth leaving open to save one trigger, so
the trigger exists too. Both were tested by going straight at the table.

Only *outbound* movements are blocked. Stock must still be able to come back in,
or a recall could never be collected — a rule that blocked returns would make
recalled goods impossible to retrieve.

**A recall produces two lists, not a total.** Stock still held can be stopped
with a call to a warehouse; stock already despatched has to be chased to a named
customer with a phone number. Merging them would hide which work is urgent.
Recall is permanent: a recalled batch can never return to sale, and if a recall
was raised in error that is a decision recorded against new stock rather than an
edit to the old record.

**What cannot be traced is stated, not smoothed over.** Every unit that moved
before this migration has no batch and never will. Nothing is backfilled,
because inventing a batch number for it would be fabricating a traceability
record — worse than having none. `traceability_report` answers in two absolute
quantities and returns no percentage at all: "94% traced" reads like a pass mark
when what it means is that some quantity of medical goods is somewhere nobody
can name. The report also compares the movement history against `stock_levels`
and says so loudly when they disagree, rather than quietly reporting the
prettier number.

**Picking is first-expiry-first-out.** FIFO is a proxy for FEFO and the two
differ exactly when it matters: a batch received later with a shorter life is
the one that should go first. Batches with no expiry recorded sort *last*, not
first — an unknown date is not a distant one.

**Transfers carry the batch on both legs**, so moving goods between shelves
cannot move them out of traceability.

A batch is registered and received separately. Registering one creates no stock,
so inventory cannot be conjured by filling in a form.

Tests: `backend/tests/test_batches.py` (22), run against the real migration
chain, including the raw-INSERT bypass attempts.
