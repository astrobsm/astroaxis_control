# Staff Operational Wallet & Expense Accountability

**Bonnesante Medicals — ASTRO-ASIX ERP**

---

## The idea in one paragraph

Your company's bank account remains your company's bank account. When the
Managing Director gives the Factory Supervisor ₦100,000 — by transfer, by cash,
by company card — that money moves the way it always has, **outside this
application**. What the application records is the consequence:

> The company has entrusted this employee with ₦100,000 of its money.

From that moment the supervisor can spend without telephoning anyone, but every
naira must be evidenced, and whatever is left is still owed back. Management
opens one screen and sees:

> Factory Supervisor — ₦100,000 issued → ₦70,000 spent → ₦30,000 still held.

This is an **accountability ledger, not a bank account**. It holds no money and
moves no money. Bank integration is possible later without rebuilding any of it.

---

## 1. Architecture assessment — how this fits the existing application

Nothing was assumed; the existing codebase was inspected first.

| Concern | What already existed | What the wallet does |
|---|---|---|
| Backend | FastAPI + SQLAlchemy 2 async + asyncpg, 48 routers in `backend/app/api/`, logic in `backend/app/services/` | Adds one router and one service, same shape |
| Auth | JWT bearer, `require_authenticated_user` / `require_admin` in `app/api/auth.py`, re-loads the User row per request | Reused unchanged. No new auth code |
| Roles | `admin, sales_staff, marketer, customer_care, production_staff, warehouse_logistics` | **Not extended.** Approval authority is granted per-user in `wallet_approvers` |
| Accounting | Real double-entry GL in `app/services/ledger.py`, seeded chart of accounts, posting gated by `ACCOUNTING_POSTING_ENABLED` | Posts through the same `post_entry()`, behind the same gate |
| Immutability | MAPD settlement (`s8901234567r`) uses DB triggers to reject UPDATE/DELETE on money records | Same pattern, same style |
| Migrations | Linear alembic chain, head was `s8901234567r` | New head `t9012345678s` |
| Frontend | React 18 PWA, `AppMain.js` monolith with a sidebar `GROUPS` array, design system in `src/ui/` | Two self-contained components, mounted like `PaymentDistribution` |
| API calls | `authedFetch` in `src/utils/api.js` attaches the bearer token | Used for every call |

**Nothing existing was rewritten.** The files changed outside the new module are:

- `backend/app/main.py` — two lines, registering the router
- `frontend/src/AppMain.js` — imports, sidebar entries, two render blocks, one
  visibility rule

### The accounting decision that shapes everything

Issuing an advance is **not an expense**. It converts cash into a *claim on the
employee*, so it is an asset swap:

```
funding    Dr 1120 Staff Operational Advances / Cr 1100 Cash | 1200 Bank
expense    Dr <category expense account>      / Cr 1120 Staff Advances
return     Dr 1100 Cash | 1200 Bank           / Cr 1120 Staff Advances
```

A wallet balance is therefore exactly that employee's slice of one asset
account, and the wallet **cannot disagree with the books**.

Booking the advance straight to expense — the common shortcut — would overstate
costs on the day of funding, understate them across the period the money is
actually spent, and leave nothing on the balance sheet saying the employee owes
it back.

A **reimbursement** is the mirror case: the employee spent their *own* money, so
the company owes them. `Dr expense / Cr 2160 Staff Reimbursements Payable`. It
never touches a wallet balance, because nothing was entrusted.

---

## 2. Database schema

Fifteen new tables, migration `t9012345678s`. Two new GL accounts: **1120**
Staff Operational Advances (asset) and **2160** Staff Reimbursements Payable
(liability).

```
users ──┬── wallets ──┬── wallet_ledger        (APPEND-ONLY, trigger-enforced)
        │             ├── wallet_fundings      (no delete; figures frozen)
        │             ├── wallet_expenses      (no delete; figures frozen)
        │             │     └── wallet_receipts (APPEND-ONLY; bytes + SHA-256)
        │             ├── wallet_returns
        │             ├── wallet_fund_requests
        │             ├── wallet_reconciliations
        │             ├── wallet_category_limits
        │             └── wallet_flags
        ├── wallet_approvers        (who may approve, up to what, where)
        └── wallet_reimbursements   (never touches a wallet balance)

wallet_expense_categories ── gl_accounts   (each category maps to the P&L)
wallet_approval_rules                      (the thresholds, as DATA)
wallet_audit_logs                          (APPEND-ONLY, trigger-enforced)
```

### The ledger is the balance

`wallet_ledger` is the single source of truth. Each row records one movement in
terms of the **employee's accountability**:

- `CREDIT` — they have been entrusted with more (funding)
- `DEBIT` — they have discharged some of it (expense, return)

```
balance = SUM(credits) − SUM(debits)
        = funded + adjusted − spent − returned
```

Both halves are computed from the same rows on every read; if they ever
disagree the API **refuses to report a balance** rather than serving a number
nobody can defend.

The `balance`, `total_funded`, `total_spent` and `total_returned` columns on
`wallets` are a **cache**, rewritten from the ledger inside the same transaction
as every movement, so a dashboard listing forty wallets does not aggregate the
whole ledger forty times. `GET /api/wallet/wallets/{id}/verify` re-derives
everything and compares three independent figures: the cache, the ledger sum,
and the `balance_after` snapshot on the newest entry.

### "Outstanding" is not the same as "balance"

Money issued this morning with thirty days to account for it is **not**
outstanding. The same money on day thirty-one is. Advances are treated as
discharged oldest-first:

```
outstanding = max(0, overdue_funded − (spent + returned))
```

An advance with no `account_by` date **never** becomes outstanding — if
management did not set a deadline, the system does not invent one and then
accuse someone of missing it.

### Immutability is enforced by the database

| Table | Rule |
|---|---|
| `wallet_ledger` | UPDATE and DELETE rejected outright |
| `wallet_audit_logs` | UPDATE and DELETE rejected outright |
| `wallet_receipts` | UPDATE and DELETE rejected outright |
| `wallet_fundings` | No DELETE; wallet, amount, reference, issuer frozen |
| `wallet_expenses` | No DELETE; wallet, amount, date, reference, submitter frozen; a decided expense cannot return to PENDING |

Application-level immutability is a promise; a trigger is a guarantee. These
tables are what an auditor reads. **Corrections are reversals** that leave the
original visible, never edits.

The frozen-expense rule specifically stops a rejected ₦80,000 claim being
quietly edited to ₦8,000 and re-approved.

---

## 3. Permission matrix

Roles were **not** added to the global system. Authority is granted per-user in
`wallet_approvers`, scoped by tier, amount ceiling, department, and optionally a
single wallet. `users.role = 'admin'` is always MANAGEMENT with no ceiling.

| Action | Staff (holder) | Supervisor grant | Finance grant | Management grant | Admin |
|---|---|---|---|---|---|
| View own wallet & transactions | ✅ | ✅ | ✅ | ✅ | ✅ |
| View another's wallet | ❌ | Within their scope | Within their scope | Within their scope | ✅ |
| Submit an expense on own wallet | ✅ | ✅ | ✅ | ✅ | ✅ |
| Submit on someone else's wallet | ❌ | ❌ | ❌ | ❌ | ❌ |
| Approve **their own** expense | ❌ | ❌ | ❌ | ❌ | ❌ |
| Approve SUPERVISOR-tier expense | ❌ | ✅ ≤ ceiling | ✅ ≤ ceiling | ✅ | ✅ |
| Approve MANAGEMENT-tier expense | ❌ | ❌ | ❌ | ✅ | ✅ |
| Upload a receipt | Own only | Own only | Own only | Own only | ✅ |
| Request more funds | ✅ | ✅ | ✅ | ✅ | ✅ |
| Decide a fund request | ❌ | If `can_fund` | If `can_fund` | If `can_fund` | ✅ |
| Record funds issued | ❌ | If `can_fund` | If `can_fund` | If `can_fund` | ✅ |
| Record funds issued **to self** | ❌ | ❌ | ❌ | ❌ | ✅ |
| Declare returned funds | Own only | Own only | Own only | Own only | ✅ |
| Confirm a return | ❌ | If `can_reconcile` | If `can_reconcile` | If `can_reconcile` | ✅ |
| Confirm **own** declared return | ❌ | ❌ | ❌ | ❌ | ✅ |
| Submit reconciliation | Own only | Own only | Own only | Own only | ✅ |
| Settle a reconciliation | ❌ | If `can_reconcile` | If `can_reconcile` | If `can_reconcile` | ✅ |
| Settle **own** reconciliation | ❌ | ❌ | ❌ | ❌ | ❌ |
| Approve a reimbursement claim | ❌ | ✅ | ✅ | ✅ | ✅ |
| Approve **own** claim | ❌ | ❌ | ❌ | ❌ | ❌ |
| Pay a reimbursement | ❌ | If `can_reconcile` | If `can_reconcile` | If `can_reconcile` | ✅ |
| Reverse an expense or funding | ❌ | ❌ | ❌ | ❌ | ✅ |
| Management dashboard | ❌ | ✅ | ✅ | ✅ | ✅ |
| Review flags | ❌ | ✅ | ✅ | ✅ | ✅ |
| Read the audit log | ❌ | ❌ | ❌ | ❌ | ✅ |
| Open/edit wallets, categories, thresholds, approvers | ❌ | ❌ | ❌ | ❌ | ✅ |
| Edit or delete any historical record | ❌ | ❌ | ❌ | ❌ | ❌ |

**The rule with no exception: nobody approves their own spending.** Not a
supervisor, not the Managing Director. An administrator who holds a wallet still
needs a second person for anything above their self-approval limit. That is the
intended cost of segregation of duties — it is what makes the rest of the module
worth having.

Finance outranks a supervisor and can settle money, but **cannot** wave through
a MANAGEMENT-tier expense: the person who pays is not the person who authorises.

---

## 4. API specification

All routes are under `/api/wallet` and require a valid bearer token
(registered behind `require_authenticated_user` in `main.py`). Authorization is
then checked per route **and** again inside the service.

### Staff

| Method | Path | Purpose |
|---|---|---|
| GET | `/me` | My wallets with full figures, plus my capabilities |
| GET | `/inbox` | What needs my attention now |
| GET | `/categories` | Active expense categories |
| GET | `/wallets/{id}` | One wallet's summary (access-checked) |
| GET | `/wallets/{id}/transactions` | The append-only ledger, paginated |
| GET | `/wallets/{id}/expenses` | Expenses on this wallet |
| POST | `/wallets/{id}/expenses` | Record money spent |
| POST | `/expenses/{id}/receipt` | Attach a receipt (multipart, ≤5 MB) |
| GET | `/expenses/{id}/receipts` | List a receipt's metadata |
| GET | `/receipts/{id}` | Download a receipt image/PDF |
| POST | `/wallets/{id}/fund-requests` | Ask for more money |
| POST | `/wallets/{id}/returns` | Declare money handed back |
| POST | `/wallets/{id}/reconciliations` | Close off a period |
| POST | `/reimbursements` | Claim back own money spent |
| POST | `/reimbursements/{id}/receipt` | Attach a claim receipt |

### Approvers and management

| Method | Path | Purpose |
|---|---|---|
| GET | `/approvals/queue` | Expenses **this** user can actually approve |
| POST | `/expenses/{id}/decision` | `{approve, note}` |
| GET | `/dashboard` | Company-wide + per-department position |
| GET | `/wallets` | Wallets in scope |
| GET | `/wallets/{id}/report` | One employee's complete money picture |
| GET | `/fund-requests` | Requests, filtered by status |
| POST | `/fund-requests/{id}/decision` | APPROVED / PARTIALLY_APPROVED / REJECTED / INFO_REQUESTED |
| POST | `/wallets/{id}/fundings` | Record funds issued |
| GET | `/returns` | Declared returns |
| POST | `/returns/{id}/confirm` | `{confirm, note}` — this moves the balance |
| GET | `/reconciliations` | Reconciliations |
| POST | `/reconciliations/{id}/review` | `{accept, note}` |
| GET | `/reimbursements` | Claims |
| POST | `/reimbursements/{id}/decision` | Approve / reject |
| POST | `/reimbursements/{id}/pay` | Settle the liability |
| GET | `/flags` | Open anomalies |
| POST | `/flags/{id}/review` | REVIEWED / DISMISSED |
| GET | `/reports/expenses.csv` | Expense register for a period |

### Administrators only

| Method | Path | Purpose |
|---|---|---|
| POST | `/wallets` | Open a wallet |
| PATCH | `/wallets/{id}` | Limits, purpose, status |
| GET | `/wallets/{id}/verify` | Integrity check |
| POST/PATCH | `/admin/categories[/{id}]` | Expense categories |
| GET/POST/DELETE | `/admin/approval-rules[/{id}]` | The threshold ladder |
| GET/POST/DELETE | `/admin/approvers[/{id}]` | Who may approve |
| GET/PUT | `/admin/wallets/{id}/category-limits` | Per-category monthly caps |
| POST | `/flags/sweep` | Re-scan for missing receipts |
| GET | `/audit` | The append-only trail |
| POST | `/fundings/{id}/reverse` | Undo an advance recorded in error |
| POST | `/expenses/{id}/reverse` | Correct an approved expense |

---

## 5. Screens

**My Wallet** (`activeModule === 'wallet'`, visible to everyone)

```
╔══════════════════════════════════════╗
║  AVAILABLE TO SPEND                  ║
║  ₦55,000                    [ACTIVE] ║
║  WAL-FAC-0001 · Factory              ║
║ ─────────────────────────────────────║
║ RECEIVED    SPENT      OUTSTANDING   ║
║ ₦150,000    ₦95,000    ₦0            ║
╚══════════════════════════════════════╝

[ ADD EXPENSE ] [ REQUEST FUNDS ]
[ RETURN FUNDS ] [ RECONCILE ]

( Transactions | My expenses )          ↻ Refresh
```

Add Expense opens as a bottom sheet: **amount → category → purpose → camera →
submit**. Four fields, one tap to photograph the receipt, date and vendor hidden
behind "+ Add date or vendor". Inputs are 16px so iOS Safari does not zoom the
page on focus. GPS is offered, never demanded — a denied location must not stop
someone recording money they have already spent.

**Wallet Control** (`activeModule === 'walletAdmin'`, admins and approvers)

Tabs, ordered the way the work is done: **Approvals** (first, with a count
badge) · Overview · Wallets · Fund requests · Returns · Reimbursements ·
Reconciliations · Flags · Configuration.

---

## 6. Anomaly detection

Patterns are surfaced as **questions for a human, never verdicts**. Nothing here
blocks a transaction, reverses anything, or changes anyone's status.

| Flag | Trigger |
|---|---|
| `DUPLICATE_RECEIPT` | The same image (by SHA-256) already submitted elsewhere |
| `REPEATED_IDENTICAL` | 3+ identical amounts, same category, within 7 days |
| `THRESHOLD_SPLITTING` | 3+ expenses within 7 days in the top 10% below an approval threshold |
| `UNUSUAL_SPEND` | A day more than 4× this wallet's own 60-day average |
| `FREQUENT_TOPUP` | 4+ fund requests in 30 days |
| `MISSING_RECEIPT` | A receipt-mandatory expense with nothing attached |

Two deliberate choices: the splitting check reads the **live** threshold from
`wallet_approval_rules` (a hard-coded ₦50,000 would quietly stop working the day
management retuned the ladder), and unusual spending is judged against *this
wallet's own* history — a factory wallet and a marketing wallet have different
normals, and one company-wide threshold would either shout constantly or never
fire at all.

---

## 7. Security & financial integrity

- **Server is the only authority.** No balance is ever accepted from a client.
  Every figure is derived from `wallet_ledger` on read.
- **One writer per wallet.** Every mutating operation takes
  `SELECT … FOR UPDATE` on the wallet row first. Two phones submitting at the
  same moment serialise; without it both would see the same ₦10,000 and both
  would be accepted.
- **Retries are not second transactions.** Callers pass an idempotency key; a
  partial unique index makes the duplicate lose.
- **One transaction per request.** The route commits; the service never does. An
  expense, its ledger movement, its journal entry and its audit row all land or
  none do.
- **Decimal at 2dp throughout**, reusing `ledger.money()`. No float arithmetic
  touches money.
- **Receipts stored in PostgreSQL**, not on disk: on DigitalOcean App Platform
  the container filesystem is replaced on every deploy, so a receipt written to
  `/app/uploads` would be evidence with an expiry date. In the database it is
  covered by the same backups as the transaction it proves. Capped at 5 MB,
  read in bounded chunks, content-type allow-listed, served
  `Cache-Control: private, no-store`.
- **Cannot spend what was never given.** An expense larger than the available
  balance is refused — and the error points the employee at reimbursement,
  which is the correct mechanism for their own money.
- **Pending claims encumber the balance**, so the same ₦10,000 cannot be claimed
  five times while all five await approval.

### One known limitation, stated plainly

**Targeted push notifications are not possible today.** The push subscription
store in `app/api/notifications.py` is keyed by browser endpoint with no user
association — it carries a `TODO` saying exactly that. Sending "an expense needs
your approval" to every subscriber would be worse than not sending it. So the
module provides a **derived inbox** (`GET /api/wallet/inbox`) computed from
current state instead: accurate by construction, impossible to miss, and not
dependent on a delivery that may have failed while a phone was off. Wiring
per-user push is a small change to the notifications module, and the wallet will
use it the day user-scoped subscriptions exist.

---

## 8. Tests

`backend/tests/test_wallet.py` — 24 tests. Requires real PostgreSQL (triggers,
partial unique indexes, row locking). The suite **runs the real migration**
rather than a hand-copied schema, so it fails if the two ever drift apart.

```bash
export TEST_DATABASE_URL='postgresql+asyncpg://postgres:PASSWORD@localhost:5432/astro_test'
cd backend && pytest tests/test_wallet.py -v
```

What is proven:

- The §42 worked example lands to the naira: ₦100,000 → ₦70,000 spent →
  ₦30,000 returned → ₦0 outstanding
- The balance identity holds through reversals
- Overspending is refused; pending claims encumber the balance
- Nobody approves their own expense, **including an admin**
- An approver cannot exceed their granted ceiling
- One employee cannot see another's wallet
- The approval queue never offers work that would be refused on click
- Thresholds are data: a new rule changes behaviour with no deployment
- Limits (single / daily / category) enforced server-side
- Receipts required where the category says so
- A rejected expense moves nothing
- A declared return only moves the balance once confirmed, and not by the
  person who declared it
- A retried funding with the same idempotency key is recorded once
- **Concurrency**: two simultaneous ₦8,000 expenses against a ₦10,000 balance —
  exactly one is accepted
- Ledger, audit log and receipts genuinely cannot be updated or deleted
- A decided expense cannot be edited or pushed back into the queue
- Duplicate receipt images are flagged, not blocked
- Threshold splitting and frequent top-ups are flagged
- Outstanding counts only overdue advances
- Reimbursements never touch a wallet balance
- With posting on, journals are correct and balance; with it off, nothing posts

---

# HOW TO MAKE THIS WORK IN THE REAL WORLD

Written for someone who is not a software engineer. Follow it in order.

> **The most important thing to understand, before anything else:**
>
> | Money movement — OUTSIDE the app | Accountability — INSIDE the app |
> |---|---|
> | You transfer ₦100,000 from the company bank account | You record "₦100,000 issued to the Factory Supervisor" |
> | You hand over ₦20,000 in cash | You record it as a cash disbursement |
> | The supervisor pays ₦20,000 for diesel | He records the expense and photographs the receipt |
> | He hands ₦30,000 back to you | He declares it; **you confirm you received it** |
>
> The app never touches your bank account. It keeps the record.

### 1. What software and services you need

Everything is already in use by ASTRO-ASIX. Nothing new:

- PostgreSQL 15 (your existing database)
- Python 3.11+ (your existing backend)
- Node.js 18+ (to rebuild the frontend once)

### 2. What accounts need creating

None. No new external service, no payment provider, no bank integration.

### 3. Configure the database

Nothing to configure. The wallet uses your existing database. **Take a backup
before you migrate** (step 6).

### 4. Build the application

```powershell
cd C:\Users\HomePC\Documents\GitHub\astroaxis_control\frontend
npm install
npm run build
```

### 5. Environment variables

The wallet needs **no new environment variables**. Two existing ones control
whether it writes to your accounting books:

| Variable | Effect |
|---|---|
| `ACCOUNTING_POSTING_ENABLED` | Unset or `false` (default): the wallet works fully, but writes no journal entries. `true`: advances and expenses reach your balance sheet and P&L automatically |
| `ACCOUNTING_CUTOVER_DATE` | `YYYY-MM-DD` — ignore anything dated before this |

**Recommendation: leave posting off for the first month.** Run the wallet, get
comfortable, then switch it on. Nothing has to be redone — posting starts from
that day forward.

### 6. Run the database migration

**Back up first. This is not optional.**

```powershell
pg_dump -U postgres -d axis_db -F c -f backup-before-wallet.dump
```

Then:

```powershell
cd backend
alembic upgrade head
```

This creates fifteen tables, two GL accounts, seventeen expense categories and
the default approval ladder. It touches **no existing table** and changes no
existing data.

Check it worked:

```powershell
psql -U postgres -d axis_db -c "SELECT COUNT(*) FROM wallet_expense_categories;"
```

You should see `17`.

### 7. Deploy

Exactly as you deploy today — the wallet is part of the same application:

```powershell
.\deploy.ps1
```

Or, for the unified local server:

```powershell
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Confirm it is alive: open `http://your-server/docs` and look for the
**Staff Wallet** section.

### 8. The administrator account

You already have one. Log in as usual. You will now see a new sidebar group,
**Operational Funds**, with **My Wallet** and **Wallet Control**.

### 9. Add employees

Also already done — the wallet uses your existing staff logins. A wallet holder
must be able to **log in**, because they record their own spending. If the
Factory Supervisor has no account yet, create one the usual way under **User
Management** and activate it.

### 10. Create the first wallet

1. **Wallet Control → Wallets → Open a wallet**
2. Employee: **Factory Supervisor**
3. Wallet type: **FACTORY**, Department: **Factory**
4. Purpose: *Factory operational expenses*
5. Limits (sensible starting values, all changeable later):
   - Per transaction: **20,000**
   - Per day: **30,000**
   - Per month: **150,000**
   - May spend without asking, up to: **10,000**
6. **Open the wallet**

Then give someone approval authority so the supervisor is not blocked on you for
every ₦15,000: **Configuration → Give someone authority** → pick the person,
level **Supervisor**, ceiling **50,000**, department **Factory**.

### 11. Record the first ₦100,000 advance

**Do the real thing first.** Transfer the ₦100,000, or hand over the cash. Then:

1. **Wallet Control → Record funds issued**
2. Wallet: **Factory Supervisor — FACTORY**
3. Amount: **100000**
4. Purpose: *Factory operational expenses*
5. How was the money given: **Bank transfer**
6. Taken from: **Bank account**
7. Transfer reference: the bank reference — *this is what you will search for
   when reconciling the statement*
8. Account for it by: **30 days from today**
9. **Record the advance**

The supervisor's wallet now reads **₦100,000**.

### 12. The employee records an expense

On his phone, the supervisor opens the app → **My Wallet** → **Add expense**:

1. Amount: **20000**
2. Category: **Fuel**
3. Purpose: *Diesel for the generator*
4. Tap the receipt field — the camera opens — photograph the receipt
5. **Submit expense**

Under ₦20,000 and within his ₦10,000 self-approval limit? No — ₦20,000 is above
it, so it goes to the supervisor-level approver, who sees it under **Approvals**
and taps Approve. The balance drops to **₦80,000**.

He repeats for maintenance ₦15,000, transport ₦10,000 and supplies ₦25,000.
Balance: **₦30,000**.

### 13. Reconciling

**The employee** opens **My Wallet → Reconcile**, chooses the period, counts the
cash in his hand and enters it. The system compares his figure with its own and
shows the difference.

**Then he returns the money**: **Return funds** → ₦30,000 → Cash.

**Then you confirm it.** Count the cash. **Wallet Control → Returns → Confirm
received.** Only now does the balance reach **₦0**.

Finally, **Wallet Control → Reconciliations → Accept**.

The wallet now shows: issued ₦100,000 · spent ₦70,000 · returned ₦30,000 ·
outstanding ₦0 — and every transaction stays on the record permanently.

### 14. Backups

Receipts live in the database, so **your database backup is your receipt
backup** — this is exactly why they are stored there rather than on disk.

- **DigitalOcean managed database**: daily backups are automatic. Confirm the
  retention period in the control panel and raise it if it is under 7 days.
- **Self-hosted**: schedule `pg_dump` daily and copy the file off the server.
  A backup on the same machine is not a backup.
- Test a restore **once**, onto a scratch database, before you rely on it.

Watch the database size: at roughly 200 KB per receipt, 500 receipts a month is
about 100 MB a year. Comfortable for years on any managed plan.

### 15. Moving from testing to production

1. Restore a copy of production onto a test database and run
   `alembic upgrade head` there first. Confirm it completes.
2. Run the test suite against that database (never against production — a guard
   in `tests/conftest.py` refuses).
3. Back up production.
4. Deploy and migrate production during a quiet hour.
5. Open **one** wallet with a small advance — ₦20,000. Run it for a week.
6. Once the flow feels right, open the rest and set `ACCOUNTING_POSTING_ENABLED=true`.

---

## User guide — the short version

**Management: how do I fund a wallet?**
Send the money the way you always do. Then *Wallet Control → Record funds
issued*. The app records it; it does not send it.

**Staff: how do I spend?**
Spend as normal. Then *My Wallet → Add expense*: amount, category, purpose,
photograph the receipt, submit. Under a minute.

**Staff: how do I submit receipts?**
In the same step. If it fails to upload, the expense is still saved — attach it
again from *My expenses*.

**Management: how do I approve?**
*Wallet Control → Approvals*. Tap the tick to view the receipt, then Approve or
Reject with a note. You will never see your own expenses there.

**How does reconciliation work?**
The employee submits what they were given, spent and still hold. The system
computes its own figures and shows the difference. You accept or send it back.

**What about money not accounted for?**
It appears as **Outstanding** once the accounting date passes, on the employee's
own screen and on the management dashboard, and it stays there until it is spent
with evidence or returned and confirmed.

**Someone made a mistake — how do we fix it?**
Nothing is ever edited or deleted. An administrator **reverses** the entry, which
appends a correction, and the correct figure is submitted fresh. Both stay on the
record with the reason. That is what makes this an audit trail rather than a
spreadsheet.

---

## Later phases (not built, by design)

The architecture leaves room for these without touching the ledger:

- **Phase 3** — company call and data-usage metadata against the wallet
- **Phase 4** — device management, location history, lost/stolen workflow.
  Note that staff location tracking carries obligations under the Nigeria Data
  Protection Act: written employee notice and a lawful basis are needed
  **before** it is switched on.
- **Phase 5** — bank / payment-provider integration. The intended shape is a
  `PaymentProvider` abstraction that produces the same `wallet_fundings` rows
  the manual flow produces today, so the ledger and every screen above it are
  unchanged.
