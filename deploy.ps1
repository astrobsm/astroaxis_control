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
for p in /api/health /api/payments/health /api/finance/accounts; do
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
foreach ($path in '/api/health', '/api/payments/health', '/api/reports/settlements') {
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
  * Sales order form shows the customer's real outstanding balance (from
    payments received, including brought-forward legacy debts) plus a live
    TOTAL PAYABLE for the order being written.
  * All three invoice formats print a PREVIOUS OUTSTANDING BALANCE section with
    aging, and THIS INVOICE + PREVIOUS BALANCE = TOTAL AMOUNT PAYABLE.
  * The invoice/receipt now actually downloads after creating an order; it read
    the wrong field before and silently never fired.
"@ -ForegroundColor Gray
