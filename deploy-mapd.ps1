# Deploy the Multi-Account Payment Distribution module (MAPD) to production.
#
# Targets the LIVE droplet 159.89.29.45 (ASTROMARCHANT), serving
# https://erp.bonnesantemedicals.com. docker-compose bind-mounts ./backend into
# the container, so uploading files and restarting is enough -- no image
# rebuild is required.
#
# TWO STEPS ON PURPOSE.
#
#   .\deploy-mapd.ps1                 CHECK  -- read-only. Reports which
#                                     migrations are pending on production and
#                                     what MAPD would find there. Changes
#                                     nothing.
#
#   .\deploy-mapd.ps1 -Apply          DEPLOY -- uploads the code, runs the
#                                     migration, restarts, smoke-tests.
#
# Run the check first and read it. If migrations OTHER than s8901234567r are
# pending, `alembic upgrade head` will apply those too -- decide that
# deliberately rather than discovering it afterwards.

param(
    [string]$IP   = "159.89.29.45",
    [string]$User = "root",
    [string]$Site = "https://erp.bonnesantemedicals.com",
    [switch]$Apply,
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"
$root   = $PSScriptRoot
$stage  = Join-Path $env:TEMP "astroaxis-mapd-deploy"
$target = "${User}@${IP}"
$remote = "/root/astroaxis_control"

if (-not (Test-Path $stage)) { New-Item -ItemType Directory -Path $stage | Out-Null }

# SSH options shared by every call.
#
# ConnectTimeout alone is not enough: it bounds the TCP connect only, so a
# session that establishes and then stalls waits forever. BatchMode makes ssh
# FAIL rather than prompt -- an unattended script sitting on a hidden prompt
# looks identical to a hang. ServerAlive bounds a mid-transfer stall.
$sshOpts = @(
    '-o', 'ConnectTimeout=15',
    '-o', 'BatchMode=yes',
    '-o', 'ServerAliveInterval=10',
    '-o', 'ServerAliveCountMax=6'
)

# -n is the one that actually mattered. ssh inherits the console's stdin, and
# inside a PowerShell script that handle never reaches EOF -- so ssh sat
# forwarding an input stream nobody was going to write to, while the identical
# command at the prompt returned in 1.5 seconds. -n reads stdin from null.
# None of the remote commands here take input (scripts are scp'd first and run
# by path), so this is safe on every call.
$sshRun = $sshOpts + @('-n')

function Write-Step($n, $text) { Write-Host "`n[$n] $text" -ForegroundColor Cyan }
function Write-Ok($text)       { Write-Host "  OK    $text" -ForegroundColor Green }
function Write-Warn($text)     { Write-Host "  WARN  $text" -ForegroundColor Yellow }
function Write-Fail($text)     { Write-Host "  FAIL  $text" -ForegroundColor Red }

# Write a bash script with LF endings only -- CRLF makes bash fail with
# "$'\r': command not found", which is a confusing way to lose a deploy.
function Write-RemoteScript($body, $path) {
    [System.IO.File]::WriteAllText($path, ($body -replace "`r`n", "`n"))
}

# ---------------------------------------------------------------------------
# Files this module adds or changes. Listed explicitly rather than syncing the
# tree: an unintended file reaching production is how unrelated work ships by
# accident.
# ---------------------------------------------------------------------------
$files = @(
    @{ Local = "backend\app\services\settlement.py";                         Remote = "backend/app/services/settlement.py" },
    @{ Local = "backend\app\api\settlements.py";                             Remote = "backend/app/api/settlements.py" },
    @{ Local = "backend\app\models.py";                                      Remote = "backend/app/models.py" },
    @{ Local = "backend\app\main.py";                                        Remote = "backend/app/main.py" },
    @{ Local = "backend\app\services\receivables.py";                        Remote = "backend/app/services/receivables.py" },
    @{ Local = "backend\alembic\versions\s8901234567r_mapd_settlement.py";   Remote = "backend/alembic/versions/s8901234567r_mapd_settlement.py" },
    @{ Local = "backend\scripts\setup_mapd.py";                              Remote = "backend/scripts/setup_mapd.py" },
    @{ Local = "backend\requirements.txt";                                   Remote = "backend/requirements.txt" }
)

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " MAPD deployment -- $(if ($Apply) {'APPLY'} else {'CHECK ONLY'})" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Droplet : $IP"
Write-Host " Site    : $Site"

# ---------------------------------------------------------------------------
# 1. SSH reachability
# ---------------------------------------------------------------------------
Write-Step 1 "Testing SSH..."
# Output is NOT swallowed: when this stalls, ssh's own message is the only
# thing that says why.
ssh @sshRun $target "echo ssh-ok"
if ($LASTEXITCODE -ne 0) {
    Write-Fail "SSH to $target failed (exit $LASTEXITCODE)."
    Write-Host @"
  Diagnose with:
      ssh -v -o ConnectTimeout=15 $target "echo ok"

  Common causes:
    * key not loaded          -> ssh-add, or ssh-copy-id $target
    * droplet under load      -> it is a 1 GB box running Postgres + the
                                 backend; sshd can be slow to fork when memory
                                 is tight. Retry, then check free -m on it.
    * passphrase prompt       -> BatchMode now refuses to prompt, which is why
                                 you get an error here instead of a hang.
"@ -ForegroundColor Yellow
    exit 1
}
Write-Ok "SSH reachable"

# ---------------------------------------------------------------------------
# 2. Pre-flight -- READ ONLY. What is pending on production right now?
# ---------------------------------------------------------------------------
Write-Step 2 "Pre-flight (read-only)..."
$preflight = @'
#!/bin/bash
set -e
cd /root/astroaxis_control
compose() { if command -v docker-compose >/dev/null 2>&1; then docker-compose "$@"; else docker compose "$@"; fi; }
echo "--- backend container ---"
compose ps backend 2>/dev/null | tail -n +1 || true
echo "--- alembic current (production) ---"
compose exec -T backend alembic current 2>&1 | tail -5 || echo "could not read alembic state"
echo "--- alembic heads (code) ---"
compose exec -T backend alembic heads 2>&1 | tail -5 || true
echo "--- MAPD tables present? ---"
compose exec -T backend python -c "
import asyncio, os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
async def go():
    e = create_async_engine(os.environ['DATABASE_URL'])
    async with e.connect() as c:
        for t in ('settlements','financial_accounts','gl_accounts','invoice_lines'):
            r = (await c.execute(text(\"SELECT to_regclass('public.'||:t) IS NOT NULL\"), {'t': t})).scalar()
            print(f'  {t:<20} {\"present\" if r else \"ABSENT\"}')
        for q, label in ((\"SELECT COUNT(*) FROM products\", 'products'),
                         (\"SELECT COUNT(*) FROM payments\", 'payments'),
                         (\"SELECT COUNT(*) FROM invoices\", 'invoices')):
            print(f'  {label:<20} {(await c.execute(text(q))).scalar()}')
    await e.dispose()
asyncio.run(go())
" 2>&1 | tail -12 || echo "could not inspect schema"
'@
$pf = Join-Path $stage "preflight.sh"
Write-RemoteScript $preflight $pf
scp @sshOpts -q $pf "${target}:/root/mapd-preflight.sh"
ssh @sshRun $target 'bash /root/mapd-preflight.sh; rm -f /root/mapd-preflight.sh'

if (-not $Apply) {
    Write-Host "`n------------------------------------------" -ForegroundColor Yellow
    Write-Host " CHECK ONLY -- nothing was changed." -ForegroundColor Yellow
    Write-Host "------------------------------------------" -ForegroundColor Yellow
    Write-Host " Read the 'alembic current' output above."
    Write-Host " If the only pending migration is s8901234567r, re-run with:"
    Write-Host "     .\deploy-mapd.ps1 -Apply" -ForegroundColor Green
    Write-Host " If OTHER migrations are pending, review them first --"
    Write-Host " 'alembic upgrade head' applies every one of them."
    exit 0
}

# ---------------------------------------------------------------------------
# 3. Upload backend files
# ---------------------------------------------------------------------------
Write-Step 3 "Uploading backend files..."
foreach ($f in $files) {
    $localPath = Join-Path $root $f.Local
    if (-not (Test-Path $localPath)) { Write-Fail "missing locally: $($f.Local)"; exit 1 }
    scp @sshOpts -q $localPath "${target}:${remote}/$($f.Remote)"
    if ($LASTEXITCODE -ne 0) { Write-Fail "upload failed: $($f.Remote)"; exit 1 }
    Write-Ok $f.Remote
}

# ---------------------------------------------------------------------------
# 4. Frontend build
# ---------------------------------------------------------------------------
if (-not $SkipFrontend) {
    Write-Step 4 "Packaging frontend build..."
    $buildDir = Join-Path $root "frontend\build"
    if (-not (Test-Path (Join-Path $buildDir "index.html"))) {
        Write-Fail "frontend\build is missing or empty. Run: cd frontend; npm run build"
        exit 1
    }
    $tar = Join-Path $stage "frontend-build.tar.gz"
    if (Test-Path $tar) { Remove-Item $tar -Force }
    Push-Location $buildDir
    tar -czf $tar .
    Pop-Location
    $sizeMB = [math]::Round((Get-Item $tar).Length / 1MB, 2)
    Write-Ok "frontend-build.tar.gz = $sizeMB MB"
    scp @sshOpts -q $tar "${target}:/root/frontend-build.tar.gz"
    if ($LASTEXITCODE -ne 0) { Write-Fail "frontend upload failed"; exit 1 }
} else {
    Write-Step 4 "Skipping frontend (-SkipFrontend)"
}

# ---------------------------------------------------------------------------
# 5. Migrate + restart on the droplet
# ---------------------------------------------------------------------------
Write-Step 5 "Migrating and restarting..."
$deployFrontend = if ($SkipFrontend) { "false" } else { "true" }
$deploy = @"
#!/bin/bash
set -e
cd /root/astroaxis_control
compose() { if command -v docker-compose >/dev/null 2>&1; then docker-compose "`$@"; else docker compose "`$@"; fi; }

if [ "$deployFrontend" = "true" ]; then
  ts=`$(date +%Y%m%d_%H%M%S)
  if [ -d frontend/build ]; then mv frontend/build "frontend/build.backup.`$ts"; fi
  mkdir -p frontend/build
  tar -xzf /root/frontend-build.tar.gz -C frontend/build/
  rm -f /root/frontend-build.tar.gz
  echo "frontend deployed (previous build kept as frontend/build.backup.`$ts)"
fi

echo "--- applying migrations ---"
compose exec -T backend alembic upgrade head

echo "--- alembic current after upgrade ---"
compose exec -T backend alembic current 2>&1 | tail -3

echo "--- restarting backend ---"
compose restart backend
sleep 6

echo "--- MAPD readiness ---"
compose exec -T backend python scripts/setup_mapd.py --status 2>&1 | tail -25 || true

echo "--- local probe ---"
curl -s -o /dev/null -w "HTTP %{http_code}  /api/health\n" http://localhost:8004/api/health || true
curl -s -o /dev/null -w "HTTP %{http_code}  /api/payments/health (401 expected: auth required)\n" http://localhost:8004/api/payments/health || true
"@
$dp = Join-Path $stage "deploy.sh"
Write-RemoteScript $deploy $dp
scp @sshOpts -q $dp "${target}:/root/mapd-deploy.sh"
ssh @sshRun $target 'bash /root/mapd-deploy.sh; rm -f /root/mapd-deploy.sh'
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Remote deploy failed. The backend may still be running the old code."
    Write-Host "  Roll back with: ssh $target 'cd $remote && git checkout -- backend && docker compose restart backend'" -ForegroundColor Yellow
    exit 1
}

# ---------------------------------------------------------------------------
# 6. External smoke test
# ---------------------------------------------------------------------------
Write-Step 6 "External smoke test..."
Start-Sleep -Seconds 4
foreach ($path in '/api/health', '/api/payments/health', '/api/finance/accounts', '/api/reports/settlements') {
    try {
        $r = Invoke-WebRequest -Uri "$Site$path" -Method GET -TimeoutSec 15 -UseBasicParsing
        Write-Ok ("{0,-32} HTTP {1}" -f $path, $r.StatusCode)
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        if ($code -in 401, 403) {
            Write-Ok ("{0,-32} HTTP {1} (auth required -- route exists)" -f $path, $code)
        } elseif ($code) {
            Write-Warn ("{0,-32} HTTP {1}" -f $path, $code)
        } else {
            Write-Fail ("{0,-32} {1}" -f $path, $_.Exception.Message)
        }
    }
}

Write-Host "`n==========================================" -ForegroundColor Green
Write-Host " Deployed." -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host @"

The module is live but settles nothing until it is configured -- an invoice
whose products have no destination account produces a SKIPPED settlement
recording that fact, never a guess. Next:

  1. Generate a config listing your real products:
       ssh $target "cd $remote && docker compose exec -T backend \
         python scripts/setup_mapd.py --template" > mapd-config.json

  2. Fill in the accounts and mappings, upload it, then rehearse and apply:
       scp mapd-config.json ${target}:$remote/backend/
       ssh $target "cd $remote && docker compose exec -T backend \
         python scripts/setup_mapd.py --bootstrap mapd-config.json"
       # ... then re-run with --commit

  3. Decide what happens to payments taken before today:
       python scripts/setup_mapd.py --mark-historical $(Get-Date -Format 'yyyy-MM-dd') --commit

  4. In the app: Finance -> Payment Distribution.

Ledger posting stays governed by ACCOUNTING_POSTING_ENABLED. Settlements are
recorded either way; that flag controls only whether the journal entry is
posted alongside them.
"@ -ForegroundColor Gray
