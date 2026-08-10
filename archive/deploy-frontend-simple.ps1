# ASTRO-ASIX Frontend Deployment Script
# Simplified version - deploys React build to DigitalOcean droplet

param(
    [string]$DropletIP = "209.38.226.32",
    [string]$DropletUser = "root",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Continue"

Write-Host "`n═══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  ASTRO-ASIX Frontend Deployment to DigitalOcean  " -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════`n" -ForegroundColor Cyan

$FrontendPath = "C:\Users\USER\ASTROAXIS\frontend"
$BuildPath = "$FrontendPath\build"
$TempZip = "$env:TEMP\astroaxis-frontend.zip"
$SSHTarget = "$DropletUser@$DropletIP"

# Step 1: Build frontend
if (-not $SkipBuild) {
    Write-Host "[1/6] Building React frontend..." -ForegroundColor Yellow
    Push-Location $FrontendPath
    
    if (Test-Path $BuildPath) {
        Remove-Item -Recurse -Force $BuildPath -ErrorAction SilentlyContinue
    }
    
    npm run build
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "✗ Build failed!" -ForegroundColor Red
        Pop-Location
        exit 1
    }
    
    Pop-Location
    Write-Host "✓ Build completed`n" -ForegroundColor Green
} else {
    Write-Host "[1/6] Using existing build...`n" -ForegroundColor Yellow
}

# Step 2: Create archive
Write-Host "[2/6] Creating archive..." -ForegroundColor Yellow

if (Test-Path $TempZip) {
    Remove-Item -Force $TempZip
}

Compress-Archive -Path "$BuildPath\*" -DestinationPath $TempZip -Force
$zipSize = [math]::Round((Get-Item $TempZip).Length / 1MB, 2)
Write-Host "✓ Archive created: $zipSize MB`n" -ForegroundColor Green

# Step 3: Transfer to droplet
Write-Host "[3/6] Transferring to droplet..." -ForegroundColor Yellow
Write-Host "Target: $SSHTarget" -ForegroundColor Gray

scp $TempZip "${SSHTarget}:/root/frontend.zip"

if ($LASTEXITCODE -ne 0) {
    Write-Host "✗ Transfer failed! Check SSH connection" -ForegroundColor Red
    exit 1
}

Write-Host "✓ Transfer complete`n" -ForegroundColor Green

# Step 4: Extract on droplet
Write-Host "[4/6] Extracting on droplet..." -ForegroundColor Yellow

ssh $SSHTarget @"
cd /root/astroaxis_control/frontend
if [ -d build ]; then mv build build.backup.\$(date +%Y%m%d_%H%M%S); fi
mkdir -p build
unzip -q -o /root/frontend.zip -d build/
rm /root/frontend.zip
ls -lh build/ | head -5
"@

if ($LASTEXITCODE -ne 0) {
    Write-Host "⚠ Extraction had warnings`n" -ForegroundColor Yellow
} else {
    Write-Host "✓ Extraction complete`n" -ForegroundColor Green
}

# Step 5: Restart backend
Write-Host "[5/6] Restarting backend..." -ForegroundColor Yellow

ssh $SSHTarget @"
cd /root/astroaxis_control
docker compose restart backend
sleep 3
docker compose ps backend
"@

Write-Host "✓ Backend restarted`n" -ForegroundColor Green

# Step 6: Verify
Write-Host "[6/6] Verifying deployment..." -ForegroundColor Yellow

ssh $SSHTarget @"
echo -n 'HTTP Status: '
curl -s -o /dev/null -w '%{http_code}' http://localhost/
echo
"@

Write-Host ""

try {
    $response = Invoke-WebRequest -Uri "http://$DropletIP/" -TimeoutSec 10 -UseBasicParsing
    Write-Host "✓ Frontend accessible: HTTP $($response.StatusCode)`n" -ForegroundColor Green
} catch {
    Write-Host "⚠ External access check failed`n" -ForegroundColor Yellow
}

# Cleanup
Remove-Item -Force $TempZip -ErrorAction SilentlyContinue

Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  ✓ DEPLOYMENT COMPLETED" -ForegroundColor Green  
Write-Host "═══════════════════════════════════════════════════`n" -ForegroundColor Green

Write-Host "🌐 Application URL: http://$DropletIP" -ForegroundColor Cyan
Write-Host "📝 SSH Command: ssh $SSHTarget" -ForegroundColor Cyan
Write-Host "📊 View Logs: docker compose logs -f backend`n" -ForegroundColor Cyan
