# Build and deploy to production.
#
#   .\deploy.ps1              build the frontend, upload, restart, verify
#   .\deploy.ps1 -Check       read-only: what is on the droplet right now
#   .\deploy.ps1 -SkipBuild   deploy the existing frontend/build as-is
#   .\deploy.ps1 -BackendOnly skip the frontend entirely
#
# Target: droplet 159.89.29.45 (ASTROMARCHANT), serving
# https://erp.bonnesantemedicals.com. docker-compose bind-mounts ./backend into
# the container, so uploading files and restarting is enough -- no image
# rebuild, no git pull.
#
# WHAT THIS SCRIPT KNOWS THAT THE OBVIOUS VERSION DOES NOT
# --------------------------------------------------------
# Every one of these cost a broken deploy on this droplet:
#
#   * `ssh` inherits the console's stdin, and inside a PowerShell script that
#     handle never reaches EOF -- so ssh hangs forever while the identical
#     command at the prompt returns in a second. Every call here passes -n.
#     ConnectTimeout does NOT cover this; it bounds the TCP connect only.
#   * docker-compose 1.29.2 raises KeyError 'ContainerConfig' when RECREATING a
#     container against a modern Docker Engine. `restart` is safe; `up -d` is
#     not. If a restart leaves the app unreachable we fall back to
#     `rm -sf` + `up -d`, which works because there is no old container left to
#     migrate volumes from.
#   * The healthcheck inside the container can pass while the host cannot reach
#     the published port, which is what makes nginx return 502. So the probe
#     runs on the HOST, against 127.0.0.1 rather than localhost (localhost can
#     resolve to ::1 and miss the v4 publish).
#   * A 401 from an authenticated route is SUCCESS -- it proves the route
#     exists and is guarded. Only /api/health should return 200.

param(
    [string]$IP   = "159.89.29.45",
    [string]$User = "root",
    [string]$Site = "https://erp.bonnesantemedicals.com",
    [switch]$Check,
    [switch]$SkipBuild,
    [switch]$BackendOnly
)

$ErrorActionPreference = "Stop"
$root   = $PSScriptRoot
$stage  = Join-Path $env:TEMP "astroaxis-deploy"
$target = "${User}@${IP}"
$remote = "/root/astroaxis_control"

if (-not (Test-Path $stage)) { New-Item -ItemType Directory -Path $stage | Out-Null }

# -n: read stdin from null. See the header -- this is the difference between a
# deploy that finishes and one that hangs with no output.
$sshOpts = @('-o', 'ConnectTimeout=20', '-o', 'BatchMode=yes',
             '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=6')
$sshRun  = $sshOpts + @('-n')

function Step($n, $text) { Write-Host "`n[$n] $text" -ForegroundColor Cyan }
function Ok($text)       { Write-Host "  OK    $text" -ForegroundColor Green }
function Warn($text)     { Write-Host "  WARN  $text" -ForegroundColor Yellow }
function Fail($text)     { Write-Host "  FAIL  $text" -ForegroundColor Red }

# bash scripts must be written with LF only; CRLF gives "$'\r': command not
# found", which is a confusing way to lose a deploy.
function WriteRemoteScript($body, $path) {
    [System.IO.File]::WriteAllText($path, ($body -replace "`r`n", "`n"))
}

# ---------------------------------------------------------------------------
# THE MANIFEST -- edit this when a change touches different files.
#
# Listed explicitly rather than syncing the tree. About 40 backend files on
# that droplet were deployed by scp and never committed, so a bulk sync or a
# `git checkout` would quietly overwrite or revert somebody's work.
# ---------------------------------------------------------------------------
$backendFiles = @(
    # Customer outstanding debt on invoices
    'backend/app/services/customer_debt.py',
    'backend/app/api/sales.py',

    # MAPD payment distribution
    'backend/app/services/settlement.py',
    'backend/app/api/settlements.py',
    'backend/app/services/receivables.py',
    'backend/app/models.py',
    'backend/app/main.py',
    'backend/alembic/versions/s8901234567r_mapd_settlement.py',
    'backend/scripts/setup_mapd.py',
    'backend/scripts/provision_product_wallets.py',

    # Staff operational wallet. main.py imports app.api.wallet, and router
    # registration RE-RAISES on failure -- so shipping main.py without these
    # three files takes the whole ERP down rather than just hiding the module.
    # If you ever trim this list, these move or go together.
    'backend/app/services/wallet.py',
    'backend/app/api/wallet.py',
    'backend/alembic/versions/t9012345678s_staff_wallet.py',

    # Company call log and click-to-call bridging. Same rule as the wallet
    # above: main.py imports app.api.calls AND app.api.telephony_webhook, and
    # router registration re-raises, so a partial upload is an outage.
    'backend/app/api/calls.py',
    'backend/app/api/telephony_webhook.py',
    'backend/app/services/telephony.py',
    'backend/alembic/versions/u0123456789t_call_log.py',
    'backend/alembic/versions/v1234567890u_call_telephony.py',

    # Call recording. app/api/calls.py imports both of these, so they travel
    # with it or the container will not start.
    'backend/app/services/recording.py',
    'backend/app/services/objectstore.py',
    'backend/alembic/versions/w2345678901v_call_recording.py',

    # Distributor foundation: geography, territories, distributor identity.
    # main.py imports both routers, and registration re-raises, so these
    # travel together or the container will not start.
    'backend/app/services/geography.py',
    'backend/app/services/distributors.py',
    'backend/app/api/geography.py',
    'backend/app/api/distributors.py',
    'backend/alembic/versions/x3456789012w_distributor_foundation.py',

    # Distributor compliance, phase 3. app/api/distributors.py imports
    # services/compliance.py at module level, so shipping the API without the
    # service would take the whole ERP down on the next restart.
    'backend/app/services/compliance.py',
    'backend/alembic/versions/y4567890123x_distributor_compliance.py',

    # Territory applications, phase 4. app/api/geography.py imports
    # services/applications.py at module level.
    'backend/app/services/applications.py',
    'backend/alembic/versions/z5678901234y_territory_applications.py',

    # Distributor ordering portal, phase 5. main.py registers portal.router in
    # the PUBLIC block and router registration re-raises, so main.py without
    # these two files takes the whole ERP down on the next restart.
    'backend/app/api/portal.py',
    'backend/app/services/portal.py',
    'backend/app/services/registration.py',
    'backend/alembic/versions/a6789012345z_distributor_portal.py',

    # Batch traceability, phase 6. inventory.py now names batch_id on every
    # stock movement INSERT, so it and the migration must travel together --
    # shipping the service without the migration would break EVERY stock
    # movement in the system, not just batched ones.
    'backend/app/services/inventory.py',
    'backend/app/services/batches.py',
    'backend/app/api/batches.py',
    'backend/alembic/versions/b7890123456a_product_batches.py',

    # Downstream sales, phase 7.
    'backend/app/services/downstream.py',
    'backend/app/api/downstream.py',
    'backend/alembic/versions/c8901234567b_downstream_sales.py',

    # Performance engine, phase 8.
    'backend/app/services/performance.py',
    'backend/app/api/performance.py',
    'backend/alembic/versions/d9012345678c_performance.py',

    # Attention list and jobs, phase 9.
    'backend/app/services/inbox.py',
    'backend/app/services/jobs.py',
    'backend/app/api/inbox.py',
    'backend/alembic/versions/e0123456789d_inbox_jobs.py',

    # Command centre, phase 10. No migration: this phase stores nothing.
    'backend/app/services/command_centre.py',
    'backend/app/api/command_centre.py',

    # Recalls, returns and complaints, phase 11.
    'backend/app/services/recalls.py',
    'backend/app/api/recalls.py',
    'backend/alembic/versions/f1234567890e_recall_workflow.py',

    # Hardening, phase 12. No migration: the permissions matrix is derived
    # from the running app, and the miss-log cap is a code change only.
    'backend/app/api/security_review.py',

    # Distributor access restricted to admin/sales/customer care, and all 774
    # LGAs seeded. auth.py defines the new guard, so it must travel with the
    # routers that import it or the container will not start.
    'backend/app/api/auth.py',
    'backend/app/services/inbox.py',
    'backend/alembic/versions/g2345678901f_all_lgas.py',
    'backend/alembic/versions/h3456789012g_registration_links.py',
    'backend/alembic/versions/i4567890123h_registration_link_recoverable.py',
    'backend/alembic/versions/j5678901234i_skipped_is_not_settled.py',

    'backend/requirements.txt'
)

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Deploy -- $(if ($Check) {'CHECK ONLY'} else {'APPLY'})" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Droplet : $IP"
Write-Host " Site    : $Site"
Write-Host " Backend : $($backendFiles.Count) file(s)"
Write-Host " Frontend: $(if ($BackendOnly) {'skipped'} elseif ($SkipBuild) {'existing build'} else {'rebuild'})"

# ---------------------------------------------------------------------------
# 1. SSH
# ---------------------------------------------------------------------------
Step 1 "Testing SSH..."
ssh @sshRun $target "echo ssh-ok"
if ($LASTEXITCODE -ne 0) {
    Fail "SSH to $target failed (exit $LASTEXITCODE)."
    Write-Host @"
  Diagnose:  ssh -v -o ConnectTimeout=20 $target "echo ok"

  If ping ALSO times out but the site still serves traffic, nothing on the
  droplet is blocking you -- it is upstream. Check the DigitalOcean panel:
  Networking -> Firewalls (something allowing 80/443 but not 22), or your home
  IP changed. The browser console at Droplets -> ASTROMARCHANT -> Access ->
  Launch Droplet Console works without SSH.
"@ -ForegroundColor Yellow
    exit 1
}
Ok "SSH reachable"

# ---------------------------------------------------------------------------
# 2. What is on the droplet now
# ---------------------------------------------------------------------------
Step 2 "Droplet state (read-only)..."
$preflight = @'
#!/bin/bash
cd /root/astroaxis_control || exit 1
compose() { if command -v docker-compose >/dev/null 2>&1; then docker-compose "$@"; else docker compose "$@"; fi; }
echo "--- container ---"
docker ps --filter name=astroaxis_backend --format '{{.Names}} | {{.Status}}'
echo "--- alembic ---"
compose exec -T backend alembic current 2>&1 | tail -2
echo "--- host reachability (this is what nginx sees) ---"
curl -s -o /dev/null -m 8 -w "  127.0.0.1:8004/api/health  HTTP %{http_code}\n" http://127.0.0.1:8004/api/health
echo "--- disk / memory ---"
df -h / | tail -1
free -m | head -2 | tail -1
'@
$pf = Join-Path $stage "preflight.sh"
WriteRemoteScript $preflight $pf
scp @sshOpts -q $pf "${target}:/root/deploy-preflight.sh"
ssh @sshRun $target 'bash /root/deploy-preflight.sh; rm -f /root/deploy-preflight.sh'

if ($Check) {
    Write-Host "`n CHECK ONLY -- nothing was changed." -ForegroundColor Yellow
    Write-Host " Run .\deploy.ps1 to build and deploy." -ForegroundColor Yellow
    exit 0
}

# ---------------------------------------------------------------------------
# 3. Build the frontend
# ---------------------------------------------------------------------------
if (-not $BackendOnly -and -not $SkipBuild) {
    Step 3 "Building frontend (npm run build)..."
    Push-Location (Join-Path $root 'frontend')
    try {
        # CI=false so warnings are not promoted to errors; GENERATE_SOURCEMAP=false
        # keeps the upload small and the maps off a public server.
        $env:CI = 'false'
        $env:GENERATE_SOURCEMAP = 'false'
        & npx --no-install react-scripts build
        if ($LASTEXITCODE -ne 0) { throw "react-scripts build failed (exit $LASTEXITCODE)" }
    } finally {
        Remove-Item Env:CI -ErrorAction SilentlyContinue
        Remove-Item Env:GENERATE_SOURCEMAP -ErrorAction SilentlyContinue
        Pop-Location
    }
    Ok "frontend built"
} else {
    Step 3 "Frontend build skipped"
}

# ---------------------------------------------------------------------------
# 4. Upload backend
# ---------------------------------------------------------------------------
Step 4 "Uploading backend files..."
foreach ($rel in $backendFiles) {
    $localPath = Join-Path $root ($rel -replace '/', '\')
    if (-not (Test-Path $localPath)) { Fail "missing locally: $rel"; exit 1 }
    scp @sshOpts -q $localPath "${target}:$remote/$rel"
    if ($LASTEXITCODE -ne 0) { Fail "upload failed: $rel"; exit 1 }
    Ok $rel
}

# ---------------------------------------------------------------------------
# 5. Upload frontend
# ---------------------------------------------------------------------------
if (-not $BackendOnly) {
    Step 5 "Packaging and uploading frontend..."
    $buildDir = Join-Path $root 'frontend\build'
    if (-not (Test-Path (Join-Path $buildDir 'index.html'))) {
        Fail "frontend\build has no index.html. Run without -SkipBuild."
        exit 1
    }
    $tar = Join-Path $stage 'frontend-build.tar.gz'
    if (Test-Path $tar) { Remove-Item $tar -Force }
    Push-Location $buildDir
    tar -czf $tar .
    Pop-Location
    Ok ("frontend-build.tar.gz = {0} MB" -f [math]::Round((Get-Item $tar).Length / 1MB, 2))
    scp @sshOpts -q $tar "${target}:/root/frontend-build.tar.gz"
    if ($LASTEXITCODE -ne 0) { Fail "frontend upload failed"; exit 1 }
} else {
    Step 5 "Frontend upload skipped (-BackendOnly)"
}

# ---------------------------------------------------------------------------
# 6. Migrate, restart, verify -- on the droplet
# ---------------------------------------------------------------------------
Step 6 "Migrating and restarting..."
$doFrontend = if ($BackendOnly) { 'false' } else { 'true' }
$deploy = @"
#!/bin/bash
set -e
cd /root/astroaxis_control
compose() { if command -v docker-compose >/dev/null 2>&1; then docker-compose "`$@"; else docker compose "`$@"; fi; }

probe() { curl -s -o /dev/null -m 8 -w '%{http_code}' http://127.0.0.1:8004/api/health || echo 000; }

if [ "$doFrontend" = "true" ]; then
  ts=`$(date +%Y%m%d_%H%M%S)
  if [ -d frontend/build ]; then mv frontend/build "frontend/build.backup.`$ts"; fi
  mkdir -p frontend/build
  tar -xzf /root/frontend-build.tar.gz -C frontend/build/
  rm -f /root/frontend-build.tar.gz
  echo "frontend deployed (previous kept as frontend/build.backup.`$ts)"
  # Keep only the three most recent backups; this box has 25 GB.
  ls -1dt frontend/build.backup.* 2>/dev/null | tail -n +4 | xargs -r rm -rf
fi

echo "--- migrations ---"
compose exec -T backend alembic upgrade head 2>&1 | grep -Ev '^INFO' || true
compose exec -T backend alembic current 2>&1 | tail -1

echo "--- restart ---"
compose restart backend
sleep 12
code=`$(probe)
echo "after restart: HTTP `$code"

# docker-compose 1.29.2 cannot RECREATE a container against a modern engine
# (KeyError 'ContainerConfig'), and a plain restart can leave the published
# port unbound -- which is a 502 at nginx even though the app is healthy
# inside. Removing the container first sidesteps both: there is no old
# container to migrate volumes from.
if [ "`$code" != "200" ]; then
  echo "not reachable on the host -- recreating the container"
  compose rm -sf backend
  compose up -d backend
  sleep 18
  code=`$(probe)
  echo "after recreate: HTTP `$code"
fi

if [ "`$code" != "200" ]; then
  echo "STILL DOWN -- last 30 log lines:"
  compose logs --tail=30 backend 2>&1 | tail -30
  exit 1
fi

echo "--- route check (401 = exists and guarded) ---"
for p in /api/health /api/payments/health /api/wallet/me /api/calls/me /api/geography/states /api/distributors; do
  printf '  %-32s HTTP ' "`$p"
  curl -s -o /dev/null -m 8 -w '%{http_code}\n' "http://127.0.0.1:8004`$p" || echo 000
done
"@
$dp = Join-Path $stage 'deploy.sh'
WriteRemoteScript $deploy $dp
scp @sshOpts -q $dp "${target}:/root/deploy-run.sh"
ssh @sshRun $target 'bash /root/deploy-run.sh; rc=$?; rm -f /root/deploy-run.sh; exit $rc'
if ($LASTEXITCODE -ne 0) {
    Fail "Remote deploy failed -- see the log lines above."
    Write-Host @"
  The app may be running the new code but unreachable, or failing to import.
  Recover with:
      ssh -n $target "cd $remote && docker-compose rm -sf backend && docker-compose up -d backend"

  Do NOT `git checkout` backend/ -- roughly 40 files there were deployed by scp
  and never committed, so that would revert unrelated work.
"@ -ForegroundColor Yellow
    exit 1
}

# ---------------------------------------------------------------------------
# 7. External verification
# ---------------------------------------------------------------------------
Step 7 "External check..."
Start-Sleep -Seconds 4
$allGood = $true
foreach ($path in '/api/health', '/api/payments/health', '/api/wallet/me', '/api/calls/me', '/api/geography/states', '/api/distributors') {
    try {
        $r = Invoke-WebRequest -Uri "$Site$path" -Method GET -TimeoutSec 20 -UseBasicParsing
        Ok ("{0,-32} HTTP {1}" -f $path, $r.StatusCode)
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        if ($code -in 401, 403) {
            Ok ("{0,-32} HTTP {1} (guarded -- route exists)" -f $path, $code)
        } elseif ($code) {
            Warn ("{0,-32} HTTP {1}" -f $path, $code); $allGood = $false
        } else {
            Fail ("{0,-32} {1}" -f $path, $_.Exception.Message); $allGood = $false
        }
    }
}

if ($allGood) {
    Write-Host "`n==========================================" -ForegroundColor Green
    Write-Host " Deployed and verified." -ForegroundColor Green
    Write-Host "==========================================" -ForegroundColor Green
} else {
    Write-Host "`n Deployed, but the external check was not clean." -ForegroundColor Yellow
    Write-Host " The host probe passed, so the app is up -- suspect nginx." -ForegroundColor Yellow
    Write-Host " Check: ssh -n $target 'systemctl status nginx; nginx -t'" -ForegroundColor Yellow
}

Write-Host @"

Hard-refresh the browser (Ctrl+F5) -- the PWA service worker caches the old
bundle otherwise.

New in this deploy:
  * Distributor foundation, phases 1-2. A distributor is a CUSTOMER plus a
    WAREHOUSE plus a profile -- approving one creates both automatically, so
    invoices, payments, AR and stock transfers run through the EXISTING
    accounting and inventory engines. There is no second ledger and no second
    stock balance to reconcile.
  * Territories, with Nigeria's 36 states and the FCT seeded. Targets are
    superseded rather than edited, so a past month keeps the figure it was
    measured against; assignments are closed rather than rewritten, so
    historical sales stay attached to whoever made them; and exclusivity is a
    partial unique index rather than an application check.
  * Duplicate detection across BOTH distributors and existing customers, through
    renaming, punctuation and phone formatting. Candidates are reported for a
    person to judge; nothing is ever merged automatically.
  * New sidebar group "Distribution Network".
  * Migration x3456789012w. sales_orders, customers and warehouses each gain
    nullable columns with defaults -- no backfill, and every existing row
    behaves exactly as before. Regression tests cover that.
  * Distributor compliance, phase 3. Facility assessment against a 25-item
    checklist, corrective actions, and agreements with signatures.

    TWO THINGS TO KNOW BEFORE ANYONE USES IT:

    1. All 25 checklist items ship as COMPANY POLICY, not law. The migration
       does not know which Nigerian regulations apply to your premises, and
       putting a false legal claim in front of a distributor in Bonnesante's
       name would be worse than saying nothing. To mark an item regulatory you
       must name the authority that imposes it -- the database refuses a
       regulatory claim without one. Have someone who knows the regulations
       review the checklist and set those.

    2. The app does NOT write your contracts. Paste in the agreement your legal
       adviser has approved. The app freezes the text at issue, hashes it, and
       ties each signature to the exact words signed -- it does not supply the
       terms, and nothing in it is legal advice.

    A critical or legal failure makes an assessment FAIL whatever the
    percentage says. Submitted assessments and signatures cannot be edited.
  * Migration y4567890123x -- seven new tables, no existing table touched.
  * Territory applications, phase 4. A territory is now normally granted by
    DECIDING AN APPLICATION rather than by a direct assign: the application
    records who asked, what the reviewer was shown, and what the conflicts were
    at the moment of the decision.

    THE CHANGE THAT MATTERS: exclusivity is now enforced per LGA, not per
    territory. Two exclusive territories can cover the same LGA -- "Lagos
    Mainland" and "Ikeja Corridor" both include Ikeja. Each satisfied the old
    per-territory check. Together they promised the same ground to two
    distributors, in writing, and nobody found out until both were selling
    there. The database now refuses that, by every route in, and names the LGA
    and the incumbent when it does.

    Terminating a distributor now RELEASES its territories. Previously a
    terminated distributor kept holding ground forever -- its live assignment
    satisfied the exclusivity index, so the territory could never be granted to
    anyone else and nothing on screen said why.
  * Migration z5678901234y -- columns added to distributor_applications, which
    nothing was using; no existing data is touched.
  * Distributor ordering portal, phase 5. Issue a distributor a shareable link
    (dossier -> Ordering) and they order at /order/<token> with no login. They
    see no prices -- only the total, once they have chosen everything -- and
    what they place is an ORDINARY SALES ORDER, so it shows up in sales
    reporting, receivables and despatch like any other. No second order book.

    TREAT THE LINK AS A PASSWORD. Anyone holding it can order on that
    distributor's account. It is shown ONCE on creation and cannot be
    retrieved -- only its hash is stored, so nothing can recover it. It always
    expires, it can be revoked immediately and permanently, and every use,
    every miss and every order is logged against it.

    BE AWARE: a basket total lets a determined person work out unit prices by
    quoting one item at a time and taking the difference. That is inherent in
    showing a total at all. What the design does prevent is the price list
    being lifted, screenshotted or forwarded in one go.
  * Migration a6789012345z -- two new tables plus one nullable column on
    sales_orders. No existing data is touched.
  * Batch traceability, quarantine and recall, phase 6. New sidebar entry
    "Batches & Recall" under Distribution Network.

    A quarantined, recalled, withdrawn or expired batch CANNOT be despatched.
    Both the service and a database trigger refuse it, so no code path -- not
    even a raw INSERT -- can let affected goods leave. Stock can still be
    returned IN, or a recall could never be collected.

    A recall produces two lists: what is still on a shelf (stop it) and who was
    already sent it (chase them). Recall is permanent -- a recalled batch can
    never go back on sale.

    READ THE TRACEABILITY TAB BEFORE RELYING ON ANY OF THIS. Everything that
    moved before this deploy has NO batch and cannot be traced. Nothing has
    invented a batch number for it, because that would be fabricating a
    traceability record. The screen shows traceable and untraceable as two
    quantities, never as a percentage. Untraceable stock clears as it sells
    through, or sooner if a physical count assigns real batch numbers to what
    is on the shelf.

    Picking is offered first-expiry-first-out, not first-in-first-out. Batches
    with no expiry date recorded sort LAST -- an unknown date is not a distant
    one.
  * Migration b7890123456a -- two new tables, plus nullable batch_id on
    stock_movements and sales_order_lines. No existing row is changed, and no
    batch is invented for historical stock.
  * Downstream sales, phase 7. What distributors sold onward, in the dossier's
    new "Sell-through" tab, with marketers and outlets.

    THIS POSTS NO JOURNAL ENTRY, and that is deliberate. A distributor selling
    to a pharmacy is a transaction the company is not party to and already
    recognised revenue on when it shipped to the distributor. Posting it would
    double-count revenue in the live ledger.

    VERIFIED AND CLAIMED ARE NEVER ADDED TOGETHER. Almost all of this is
    self-reported. A sale arrives as REPORTED and counts toward nothing until
    somebody OTHER than the person who reported it checks it against uploaded
    evidence. Only the verified figure drives performance; the claimed figure
    sits beside it labelled as unchecked. There is no combined total anywhere
    in the API or the UI, on purpose.

    Where a distributor reports selling more than we recorded shipping them,
    the sale is still recorded and flagged as a stock discrepancy -- stock is
    NOT driven negative to make it balance. Either the shipment record is
    incomplete or the report is inflated, and both are worth knowing.

    Batches follow the goods to the outlet, so a recall can now name the
    pharmacy that bought an affected batch.
  * Migration c8901234567b -- five new tables. No existing table is touched.
  * Performance engine, phase 8. New "Performance" tab in the distributor
    dossier: run-rate, month-by-month history, reviews and a scorecard.

    EARLY IN A MONTH THE APP REFUSES TO PROJECT. Below a quarter of the
    month's selling days it shows what has been verified so far and says no
    forecast is possible yet. That is deliberate: four days of sales
    extrapolated across a month is arithmetic, not a forecast, and a red light
    built on it gets a distributor phoned about nothing.

    Elapsed time is counted in SELLING DAYS (weekends excluded). Public
    holidays are not, because Nigeria's move and some are declared days ahead
    -- a fixed list would be confidently wrong rather than roughly right.

    Every past month is measured against the target THAT WAS IN FORCE THEN.
    Raising a target today does not retrospectively fail a distributor.

    ONLY VERIFIED SALES COUNT toward any band, projection or review trigger.
    The claimed figure is shown greyed beside it and counts toward nothing.

    THERE IS NO OVERALL SCORE. Performance, compliance and evidence quality are
    three separate readings -- averaging them would let a good sales month
    outvote an expired licence.

    Three consecutive months below 70% of target raise a review. A month with
    NO target set breaks the run rather than counting as a failure: nobody can
    miss a target that was never set. A review can conclude TARGET_RESET --
    sometimes the honest finding is that the target was wrong.
  * Migration d9012345678c -- two new tables. No existing table is touched.
  * Attention list and scheduled jobs, phase 9. New sidebar entry
    "Needs Attention" under Distribution Network.

    THERE IS NO NOTIFICATIONS TABLE, deliberately. Every row is computed live
    from the data that holds the truth, so an item disappears the moment the
    problem is fixed and nothing can go stale. There is no mark-as-read,
    because there is nothing stored to mark.

    NOTHING IS SENT ANYWHERE, and that is worth knowing before anyone relies
    on this. The app's push notification store lives in a file replaced on
    every deploy, is not tied to user accounts, and the only send path
    broadcasts to every subscriber -- so an alert about one distributor's
    performance would reach whoever happened to be listening, or nobody. The
    list is surfaced in the app instead.

    Critical items -- recalled stock still in the field, critical corrective
    actions -- cannot be snoozed. Everything else can, but snoozes always
    expire and are per person: one manager hiding a row does not hide it from
    the rest.

    Jobs are idempotent by DATABASE CONSTRAINT, not by convention: a job runs
    once per period, so a double-registered cron or a retry cannot repeat the
    work. Press Run twice and the second press returns what the first found.
    The review sweep REPORTS which distributors have earned a performance
    review but does not open them -- that is a decision a person takes.

  * ALSO FIXES A PHASE 6 DEAD END: a recalled batch could not be written off.
    Every outbound movement was blocked, which was right for selling and wrong
    for disposal -- recalled stock could never leave the system. DAMAGE and
    ADJUST_OUT are now permitted, because destroying the goods is how a recall
    is completed. Selling, transferring and consuming remain blocked.
  * Migration e0123456789d -- two new tables. No existing table is touched.
  * Distribution command centre, phase 10. New sidebar entry "Command Centre".
    Coverage map, monthly ranking, territory roll-up and CSV exports.

    NO MIGRATION AND NO CACHE. This phase stores nothing: every figure is
    computed from the data that already holds it. A cached aggregate is wrong
    for exactly as long as nobody notices.

    ZERO IS NOT UNKNOWN, and the coverage screen is built around that. A state
    with no LGAs loaded reports "not known", hatched, NOT a zero in pale green.
    Those are opposite findings -- one says sell harder, the other says finish
    the data entry -- and a single colour ramp renders them identically. Only
    26 of Nigeria's 774 LGAs are loaded, so most of the map is currently
    "unknown" and the screen says so at the top.

    THERE IS NO HEALTH SCORE. One number for a board pack would have to average
    a compliance failure against a good sales month.

    EXPORTS ARE RECORDED in the distributor audit trail: a distributor CSV
    carries names, phone numbers and trading history out of the building. The
    sales export has separate verified and claimed columns rather than one
    total -- a spreadsheet is where a claim becomes a fact, and nothing can put
    the distinction back once the file is sent on.
  * Recalls, returns and complaints, phase 11. New sidebar entry
    "Recalls & Complaints". Raising a recall now goes through a managed
    process: it blocks despatch AND produces the list of who holds the goods
    and who was already sent them.

    THE FIGURE TO READ IS "NEVER FOUND". A recall reconciliation shows four
    quantities -- at risk when raised, returned, destroyed, still on our
    shelves -- and whatever is left over is UNACCOUNTED. That is the number
    that matters, because those units are still out there. A recall carrying
    unaccounted units CANNOT BE CLOSED until somebody writes down what is
    believed to have happened to them.

    Returns go through the EXISTING returns screen. Two columns were added to
    returned_stock (batch and recall); no second returns path was built,
    because two paths would mean two answers to "how much came back".

    ADVERSE EVENTS: a complaint can be flagged as possible patient harm. THE
    APP NOTIFIES NO REGULATOR AND CANNOT. The flag records that a reporting
    duty may have arisen; the regulator columns record what a PERSON did about
    it. The warning is repeated every time, not shown once.

    A complaint's description cannot be edited after it is recorded -- what the
    complainant said is the thing being investigated.
  * Migration f1234567890e -- three new tables, two nullable columns on the
    existing returned_stock. No existing data is touched.
  * Hardening, phase 12. NO MIGRATION.

    A PERMISSIONS MATRIX YOU CAN TRUST, because it is not a document. GET
    /api/security/permissions walks the running application and reports what
    each route actually requires. It cannot drift from the code, because it is
    read from the code. Current state of the distributor module: 44 admin-only,
    70 authenticated, and exactly 3 public -- all three the distributor
    ordering portal, each listed by name with its justification.

    A TEST NOW FAILS IF ANYONE ADDS AN UNAUTHENTICATED ROUTE. Forgetting a
    dependency, registering a router in the wrong block, or copying an existing
    file all break the build and name the offending route, instead of shipping
    quietly.

    FIXED A REAL HOLE THIS REVIEW FOUND: the public ordering portal wrote a
    database row for EVERY failed token. Logging misses is right -- a run of
    them is what guessing at links looks like -- but from an unauthenticated
    endpoint it let anyone on the internet grow the table without bound. That
    is the failure mode where the monitoring is the outage. Attempts past 20
    per address per hour are still refused; they just stop being written, and
    the last row records that the cap was reached.

    ALSO NEW: /api/security/audit shows the audit trail WITH the triggers that
    make it append-only, and /api/security/immutability lists every
    immutability guarantee this module claims so somebody can check they are
    still true of the database actually running.

    KNOWN LIMITATION, STATED RATHER THAN HIDDEN: the matrix reports what the
    guards REQUIRE. It catches a missing guard; it does not catch a guard that
    is present but wrong. Record-level access is not modelled -- any staff
    login can read any distributor's record, and document and evidence
    downloads are authenticated but not scoped to a distributor.
  * WHO CAN SEE A DISTRIBUTOR RECORD is now restricted. The register,
    territories, sell-through, performance and the command centre require
    admin, sales_staff or customer_care. Marketers, production and warehouse
    staff no longer see them, and the sidebar entries disappear for those
    roles.

    Batches, recalls and the attention list are DELIBERATELY still open to
    every staff login: a picker has to know which batch to take, and a recall
    has to be collected by whoever is in the warehouse. The attention list
    filters instead -- non-distribution roles see recall and stock items but
    not a distributor's commercial position.

    NOTE: 'marketer' is excluded because the instruction named admin, sales
    and customer care. If marketers need the register, add 'marketer' to
    DISTRIBUTION_ROLES in backend/app/api/auth.py.

  * ALL 774 LGAs ARE NOW SEEDED, up from 26. Every state has its full
    complement, so territories can be drawn anywhere in Nigeria and the
    coverage map no longer reports most of the country as "unknown".

    The migration CHECKS ITSELF against the official per-state counts and
    refuses to apply if any state is short, has a duplicate, or the national
    total is not 774. Re-running it changes nothing and existing Lagos and FCT
    rows keep their ids, so any territory already drawn on them is untouched.

ONLY Lagos (20) and the FCT area councils (6) of Nigeria's 774 LGAs are seeded.
Import the rest from an authoritative source via /api/geography/lgas/import --
a misspelt LGA silently corrupts every territory report built on it.
"@ -ForegroundColor Gray
