# Multi-Account Intelligent Payment Distribution (MAPD)

One invoice covering several products. One payment from the customer. Every
product's share lands in that product's designated account automatically, with
a complete audit trail and no manual transfer.

---

## The idea in one page

A hospital buys four different products on one invoice and pays ₦330,000 once:

| Product | Qty | Unit price | Line total | Destination account |
|---|---|---|---|---|
| Hera Wound Gel | 20 | ₦8,000 | ₦160,000 | Hera Account |
| Honey Gauze | 10 | ₦5,000 | ₦50,000 | Honey Account |
| Wound Clex Solution | 15 | ₦4,000 | ₦60,000 | Wound Clex Account |
| Sterile Dressing Pack | 8 | ₦7,500 | ₦60,000 | Dressing Pack Account |

The customer pays once. The engine reads the invoice lines, resolves each
product's settlement rule, allocates the money, writes an immutable settlement
record, and posts the movement to the general ledger — in the same database
transaction as the payment itself.

---

## Where it plugs in

`app.services.receivables.record_payment` is the single choke point every
customer payment in this ERP passes through — mark-paid, payment tracking, and
the public ordering portal all call it. Distribution hangs off that one call,
so a payment recorded through any route settles the same way.

```
record_payment()
  ├── INSERT payments                       (the money, recorded)
  ├── recompute_invoice_paid()              (derived caches resynced)
  ├── post_customer_payment()               Dr Bank      / Cr Receivable
  └── distribute_payment_safely()           Dr each destination / Cr Bank
        ├── build_plan()                    resolve rules, apportion to the cent
        ├── INSERT settlements              one live row per payment (unique index)
        ├── INSERT settlement_details       append-only, trigger-enforced
        └── post_entry()                    one balanced journal entry
```

---

## The three guarantees

**Exactness.** Allocations are `Decimal` and split by largest-remainder
apportionment, so a 70/20/10 division of ₦33.33 comes out as 23.33 + 6.67 +
3.33 — summing to the payment exactly. Rounding each share independently would
leave a stray cent that has to come out of somebody's account.

**All or nothing.** The whole plan is built and validated — every rule
resolved, every destination confirmed ACTIVE — before a single detail row is
written. A suspended destination account pauses the entire settlement rather
than allocating the other lines and leaving one behind.

**Never lose the cash.** If distribution fails, the *payment* still stands. The
money arrived; refusing to record that because a destination account is
suspended would be the worse error. The failure is recorded with its reason,
surfaced on the Exceptions tab, and retried once the cause is fixed.

---

## Configuration model

| Table | What it holds |
|---|---|
| `business_units` | Divisions whose products settle into their own accounts |
| `revenue_centers` | Reporting dimension below the business unit |
| `financial_accounts` | Destinations money can be sent to; each maps to one postable GL account |
| `product_accounts` | Per-product mapping: business unit, tax group, default destination |
| `settlement_rules` | How a product's (or unit's) revenue is divided |
| `settlement_rule_splits` | The individual destinations within a rule |
| `settlements` / `settlement_details` | What actually happened. Append-only |
| `mapd_refunds` | Reversals. Corrections are never edits |
| `mapd_audit_logs` | Every event, append-only at the database level |
| `payment_methods` | Bank transfer, POS, card, QR, USSD, wallet, cash, mobile money, cheque |

### A financial account is not a GL account

`financial_accounts` is the operational purse — a bank account, a wallet, a
division's float. Each row names exactly one postable account in `gl_accounts`.
That mapping is required and immutable after creation: an account money can be
sent to but that the ledger has never heard of is precisely how a distribution
engine ends up as a second, disagreeing set of books.

### Two kinds of allocation

**CASH** — the money physically moves. `Dr destination / Cr the account the
payment landed in`. Cash splits must account for **100%** of a line; that is
validated when the rule is authored, not when a customer pays.

**OBLIGATION** — the allocation creates a debt to a third party (a distributor
commission, a revenue share). `Dr expense / Cr liability`. These are
**additional** to the cash split, not carved out of it: owing a partner 10%
does not reduce the cash you banked.

### How a rule is chosen

Most specific wins, and the order is total so the same invoice always settles
the same way:

```
PRODUCT  >  BUSINESS_UNIT  >  GLOBAL      then lowest priority number, then oldest rule
```

If no rule matches, the product's `default_financial_account_id` takes the
whole share. If there is neither, the settlement is **SKIPPED** with the
product named — nothing is guessed.

---

## Partial payments

Each payment is allocated across invoice lines in proportion to their
**remaining capacity** (line total less what previous settlements already
sent). Instalments therefore converge on exactly the same split a single
payment would have produced, with no drift and no dependence on the order the
payments arrive in.

---

## Idempotence

A unique partial index allows **one live settlement per payment**:

```sql
CREATE UNIQUE INDEX uq_settlement_live_per_payment ON settlements (payment_id)
 WHERE status IN ('PENDING','COMPLETED','SKIPPED');
```

A retried gateway callback, a double-clicked button, or a re-run of the retry
job cannot pay a destination account twice. `POST /api/payments/verify` is
idempotent on `(invoice_id, reference)` for the same reason. FAILED and
REVERSED rows are excluded from the index, so a retry inserts a fresh attempt
while the failed attempts remain as history.

---

## Immutability

`settlement_details` and `mapd_audit_logs` reject `UPDATE` and `DELETE` via a
database trigger. `settlements` may change status (PENDING → COMPLETED) but its
`payment_id`, `gross_amount` and reference cannot change, and the row cannot be
deleted. Application-level immutability is a convention; a trigger is a
guarantee, and these are the rows an auditor reads to see where a customer's
money went.

Corrections are reversals. A refund posts the mirror entry and marks the
settlement REVERSED — which also releases that invoice's line capacity, so a
later payment redistributes correctly.

---

## Refunds require two administrators

`POST /api/payments/refund` needs the requesting admin's session **and** a
second admin's email and password in the body. The approver must be a
different, active administrator. Reversing an allocation moves money back out
of accounts other people are reconciling against; one open session should not
be enough.

A full refund mirrors the original journal entry. A partial refund is
apportioned across the original destinations, so each account gives back its
share — clawing it all from one would leave the others overstated.

This reverses the **distribution**. Refunding the customer and reversing the
sale remains the Returns module's job; doing both here would reverse the sale
twice.

---

## API

```
POST   /api/payments/initiate            quote what is owed + preview the split
POST   /api/payments/verify              record receipt and distribute (idempotent)
POST   /api/payments/distribute          distribute one payment, or retry all failures
GET    /api/payments/{id}                the payment and what became of it
GET    /api/payments/{id}/settlements    every allocation, to the naira
POST   /api/payments/refund              reverse a distribution (dual control)
GET    /api/payments/undistributed       money that has not reached its accounts
GET    /api/payments/methods             supported payment sources
GET    /api/payments/health              is every payment accounted for?

GET    /api/finance/accounts             destination accounts
POST   /api/finance/accounts             register one
PUT    /api/finance/accounts/{id}        update (code and GL mapping are immutable)
GET    /api/finance/business-units       + POST
GET    /api/finance/revenue-centers      + POST
GET    /api/finance/product-accounts     every product and its configuration
PUT    /api/finance/product-accounts     map one product (upsert)
GET    /api/finance/settlement-rules     + POST
PATCH  /api/finance/settlement-rules/{id}/active
GET    /api/finance/dashboard            collections, destinations, failures
GET    /api/finance/preview/{payment_id} what the split WOULD be; writes nothing
GET    /api/finance/audit-log            the immutable trail (admin only)

GET    /api/reports/revenue              grouped by product | account | business_unit | day
GET    /api/reports/settlements          the settlement register
GET    /api/reports/refunds              reversals
```

Reads are open to any authenticated user. Everything that changes where money
goes — accounts, rules, product mappings, refunds — is admin-only.

---

## Going live

Production is the droplet `159.89.29.45`, serving
`https://erp.bonnesantemedicals.com`. `docker-compose` bind-mounts `./backend`
into the container, so a deploy is upload + migrate + restart — no image
rebuild.

### 1. Check what production looks like first

```powershell
.\deploy-mapd.ps1
```

Read-only. Changes nothing. It reports the backend container state, `alembic
current` versus the code's head, which MAPD tables exist, and the row counts
for products / payments / invoices.

**Read the `alembic current` output before going further.** `alembic upgrade
head` applies *every* pending migration, not just this one. If anything other
than `s8901234567r` is outstanding, decide about it deliberately.

### 2. Deploy

```powershell
.\deploy-mapd.ps1 -Apply
```

Uploads the eight changed/new backend files (listed explicitly in the script,
so unrelated work cannot ship by accident), packages `frontend/build`, runs the
migration, restarts the backend, prints the readiness report, and smoke-tests
the live domain. The previous frontend build is kept as
`frontend/build.backup.<timestamp>`.

Add `-SkipFrontend` to ship backend only.

### 3. Configure

The module is live at this point but settles nothing — an invoice whose
products have no destination account produces a SKIPPED settlement recording
that fact, never a guess. `backend/scripts/setup_mapd.py` does the setup;
everything is a dry run until `--commit`.

```bash
# on the droplet, inside the backend container
docker compose exec -T backend python scripts/setup_mapd.py --status

# a starter config listing YOUR real products
docker compose exec -T backend python scripts/setup_mapd.py --template > mapd-config.json
# ... fill in accounts and mappings, then:
docker compose exec -T backend python scripts/setup_mapd.py --bootstrap mapd-config.json
docker compose exec -T backend python scripts/setup_mapd.py --bootstrap mapd-config.json --commit
```

The bootstrap is **atomic and idempotent**: if any account, mapping or rule in
the file is wrong — a ledger account that cannot be posted to, an unknown SKU,
percentages that total 90% — it reports every problem and writes *nothing*,
even with `--commit`. A partly-applied configuration is worse than none,
because the operator would believe products are mapped that are not.

Or configure through the UI: Finance → Payment Distribution → Accounts,
Products, Rules.

### 4. Decide about historical payments

Payments taken before the module existed were banked under whatever
arrangement was in force at the time. Distributing them now would post
back-dated ledger entries for money movements that never happened, so the
default treatment mirrors the accounting cutover date:

```bash
python scripts/setup_mapd.py --mark-historical 2026-07-27 --commit
```

That writes a SKIPPED settlement per payment explaining the decision, and stops
them sitting in the Exceptions list forever. `--backfill <date>` distributes
them for real instead — only defensible when the destinations are internal
divisions of one balance and the period's books are still open.

### 5. Tighten up

Once every product is mapped (`--status` says so), set `MAPD_STRICT=true` so an
unmapped product fails loudly rather than being skipped.

---

## Roll-out flags

Environment (see `backend/.env.example`):

| Variable | Default | Effect |
|---|---|---|
| `MAPD_ENABLED` | `true` | Automatic distribution on `record_payment` |
| `MAPD_STRICT` | `false` | Unconfigured product FAILS instead of SKIPS |
| `ACCOUNTING_POSTING_ENABLED` | `false` | Whether settlements also post to the ledger |
| `BUSINESS_TIMEZONE` | `Africa/Lagos` | Which calendar day an entry belongs to |

`MAPD_ENABLED` defaults on because switching it on changes nothing until
somebody configures it: an invoice whose products have no destination account
produces a SKIPPED settlement recording that fact, not a distribution.

Distribution records settlements regardless of `ACCOUNTING_POSTING_ENABLED`;
that flag controls only whether the corresponding journal entry is posted. Turn
it on when the ledger cutover is complete.

### Configuration order

1. **Business units** — Distribution → Accounts → Business units.
2. **Destination accounts** — one per purse, each pointed at a postable GL
   account. Obligation accounts additionally need the liability account.
3. **Products** — give every product a default destination account. The
   Products tab flags anything unmapped.
4. **Rules** — only where a product's revenue splits several ways. A product
   with a default account and no rule settles 100% to that account.

### Rolling back

The migration only creates new tables and seeds five new GL accounts plus the
payment-method reference list — it alters nothing existing, so backing it out
is contained:

```bash
docker compose exec -T backend alembic downgrade r7890123456q
```

To stop distribution without touching the schema, set `MAPD_ENABLED=false` and
restart. Settlements already recorded stay; nothing new is written. To back out
the code, the deploy script prints the `git checkout -- backend` rollback line
on failure.

---

## Operating it

**Dashboard** — distributed totals, revenue by account / product / business
unit, daily collections, and a warning banner for anything unconfigured.

**Settlements** — the register, filterable by status and date.

**Exceptions** — payments that have not reached their accounts, with the
reason, and a retry button per payment or for all of them.

**Accounts / Products / Rules** — the configuration above.

**Audit & Refunds** — the append-only trail and the dual-control reversal form.

`GET /api/payments/health` answers three questions from the rows rather than
from a status flag: is any received payment still undistributed, does any
settlement's details disagree with its header, and did any settlement send more
than the payment brought in.

---

## Tests

```bash
export TEST_DATABASE_URL='postgresql+asyncpg://postgres:pw@localhost:5432/astro_test'
cd backend && pytest tests/test_settlement.py -v
```

Requires real PostgreSQL — the triggers, the partial unique index and the row
locking are the things under test, and none of them exist in SQLite. The suite
covers the worked example above end to end, indivisible amounts, instalment
convergence, idempotence under repeated distribution, the suspended-account
pause, unconfigured products, ledger agreement, obligation accounting, trigger
immutability, and full and partial reversal.

---

## Known boundaries

* **VAT.** `product_accounts.tax_group` is configuration and reporting
  metadata. VAT recognition stays with `app.services.tax`, which derives it
  from the ledger. Carving VAT out here as well would double-count it.
* **Multi-currency.** `financial_accounts.currency` is stored and reported but
  no conversion is performed; every account on one invoice must share a
  currency.
* **Gateway integration.** `POST /api/payments/verify` is the callback surface
  and is idempotent on the reference, but this module does not call any
  gateway's verification API itself — wire that in front of it.
