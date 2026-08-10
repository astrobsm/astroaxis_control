# ======================================================
# ASTRO-ASIX Frontend Deployment Script
# ======================================================
# Transfers pre-built React frontend to DigitalOcean droplet
# Author: ASTRO-ASIX DevOps
# Date: October 28, 2025
# ======================================================

param(
    [string]$DropletIP = "209.38.226.32",
    [string]$DropletUser = "root",
    [string]$FrontendBuildPath = "$PSScriptRoot\frontend\build",
    [string]$DropletPath = "/root/astroaxis_control/frontend",
    [switch]$SkipBuild,
    [switch]$RestartContainers
)

# Colors for output
function Write-ColorOutput($ForegroundColor) {
    $fc = $host.UI.RawUI.ForegroundColor
    $host.UI.RawUI.ForegroundColor = $ForegroundColor
    if ($args) {
        Write-Output $args
    }
    $host.UI.RawUI.ForegroundColor = $fc
}

Write-Host "`n=====================================================" -ForegroundColor Cyan
Write-Host "   ASTRO-ASIX Frontend Deployment to DigitalOcean" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "Droplet IP: $DropletIP" -ForegroundColor Yellow
Write-Host "Build Path: $FrontendBuildPath" -ForegroundColor Yellow
Write-Host "Target: ${DropletUser}@${DropletIP}:${DropletPath}" -ForegroundColor Yellow
Write-Host "=====================================================`n" -ForegroundColor Cyan

# Step 1: Build frontend (unless skipped)
if (-not $SkipBuild) {
    Write-Host "[1/6] Building React frontend..." -ForegroundColor Green
    $frontendDir = Join-Path $PSScriptRoot "frontend"
    
    if (-not (Test-Path $frontendDir)) {
        Write-Host "ERROR: Frontend directory not found: $frontendDir" -ForegroundColor Red
        exit 1
    }
    
    Push-Location $frontendDir
    Write-Host "Running: npm run build" -ForegroundColor Gray
    $buildOutput = npm run build 2>&1
    $buildSuccess = $LASTEXITCODE -eq 0
    Pop-Location
    
    if ($buildSuccess) {
        Write-Host "✓ Frontend built successfully" -ForegroundColor Green
        Write-Host "$($buildOutput | Select-String 'Compiled|File sizes' | Out-String)" -ForegroundColor Gray
    } else {
        Write-Host "✗ Build failed!" -ForegroundColor Red
        Write-Host $buildOutput -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "[1/6] Skipping build (using existing build)" -ForegroundColor Yellow
}

# Step 2: Verify build directory exists
Write-Host "`n[2/6] Verifying build directory..." -ForegroundColor Green

if (-not (Test-Path $FrontendBuildPath)) {
    Write-Host "✗ Build directory not found: $FrontendBuildPath" -ForegroundColor Red
    Write-Host "Run without -SkipBuild flag to build first" -ForegroundColor Yellow
    exit 1
}

$buildFiles = Get-ChildItem -Path $FrontendBuildPath -Recurse -File
$totalSize = ($buildFiles | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host "✓ Build directory found" -ForegroundColor Green
Write-Host "  Files: $($buildFiles.Count)" -ForegroundColor Gray
Write-Host "  Size: $([math]::Round($totalSize, 2)) MB" -ForegroundColor Gray

# Step 3: Check SSH connectivity
Write-Host "`n[3/6] Testing SSH connection..." -ForegroundColor Green

$sshTest = ssh -o ConnectTimeout=5 -o BatchMode=yes ${DropletUser}@${DropletIP} "echo 'Connected'" 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ SSH connection successful" -ForegroundColor Green
} else {
    Write-Host "✗ SSH connection failed!" -ForegroundColor Red
    Write-Host "Make sure you can SSH without password (use ssh-keygen and ssh-copy-id)" -ForegroundColor Yellow
    Write-Host "Or run manually:" -ForegroundColor Yellow
    Write-Host "  ssh-keygen -t rsa -b 4096" -ForegroundColor Cyan
    Write-Host "  type `$env:USERPROFILE\.ssh\id_rsa.pub | ssh ${DropletUser}@${DropletIP} 'cat >> ~/.ssh/authorized_keys'" -ForegroundColor Cyan
    exit 1
}

# Step 4: Create temporary archive
Write-Host "`n[4/6] Creating compressed archive..." -ForegroundColor Green

$tempZip = Join-Path $env:TEMP "astroaxis-frontend-$(Get-Date -Format 'yyyyMMdd-HHmmss').zip"

try {
    Compress-Archive -Path "$FrontendBuildPath\*" -DestinationPath $tempZip -Force
    $zipSize = (Get-Item $tempZip).Length / 1MB
    Write-Host "✓ Archive created: $(Split-Path $tempZip -Leaf)" -ForegroundColor Green
    Write-Host "  Size: $([math]::Round($zipSize, 2)) MB" -ForegroundColor Gray
} catch {
    Write-Host "✗ Failed to create archive: $_" -ForegroundColor Red
    exit 1
}

# Step 5: Transfer to droplet
Write-Host "`n[5/6] Transferring files to droplet..." -ForegroundColor Green
Write-Host "This may take 1-2 minutes depending on your connection..." -ForegroundColor Gray

$remoteZip = "/root/frontend-build.zip"
$scpCommand = "scp -o ConnectTimeout=30 `"$tempZip`" ${DropletUser}@${DropletIP}:${remoteZip}"

Write-Host "Running: scp $(Split-Path $tempZip -Leaf) -> ${DropletUser}@${DropletIP}:${remoteZip}" -ForegroundColor Gray

scp -o ConnectTimeout=30 "$tempZip" "${DropletUser}@${DropletIP}:${remoteZip}"

if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ File transfer completed" -ForegroundColor Green
} else {
    Write-Host "✗ Transfer failed!" -ForegroundColor Red
    Remove-Item $tempZip -Force -ErrorAction SilentlyContinue
    exit 1
}

# Clean up local temp file
Remove-Item $tempZip -Force -ErrorAction SilentlyContinue
Write-Host "  Cleaned up local temp file" -ForegroundColor Gray

# Step 6: Extract on droplet and restart containers
Write-Host "`n[6/6] Extracting files on droplet..." -ForegroundColor Green

$remoteCommands = @"
cd /root/astroaxis_control/frontend
echo '→ Backing up existing build...'
rm -rf build.backup
mv build build.backup 2>/dev/null || true
mkdir -p build

echo '→ Extracting new build...'
unzip -o /root/frontend-build.zip -d build/

echo '→ Verifying extraction...'
ls -lh build/ | head -10

echo '→ Checking key files...'
if [ -f 'build/index.html' ]; then
    echo '✓ index.html found'
else
    echo '✗ index.html missing!'
    exit 1
fi

if [ -d 'build/static' ]; then
    echo '✓ static directory found'
else
    echo '✗ static directory missing!'
    exit 1
fi

echo '→ Cleaning up...'
rm -f /root/frontend-build.zip

echo '→ Setting permissions...'
chmod -R 755 build/

echo ''
echo '✓ Frontend deployment complete!'
echo ''
"@

if ($RestartContainers) {
    $remoteCommands += @"
echo '→ Restarting Docker containers...'
cd /root/astroaxis_control
docker compose restart backend

echo ''
echo 'Waiting for backend to start...'
sleep 5

echo ''
echo '→ Testing backend health...'
curl -f http://localhost/api/health || echo '✗ Backend health check failed'

echo ''
echo '→ Testing frontend serving...'
curl -sI http://localhost/ | head -5

echo ''
echo '✓ Deployment complete! Visit http://209.38.226.32'
"@
}

Write-Host "Executing remote commands..." -ForegroundColor Gray
$remoteOutput = ssh ${DropletUser}@${DropletIP} $remoteCommands

if ($LASTEXITCODE -eq 0) {
    Write-Host $remoteOutput -ForegroundColor Gray
    Write-Host "`n✓ Extraction successful!" -ForegroundColor Green
} else {
    Write-Host "✗ Extraction failed!" -ForegroundColor Red
    Write-Host $remoteOutput -ForegroundColor Red
    exit 1
}

# Final summary
Write-Host "`n=====================================================" -ForegroundColor Cyan
Write-Host "           DEPLOYMENT SUCCESSFUL! ✓" -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "Frontend URL: http://$DropletIP" -ForegroundColor Yellow
Write-Host "API Health:   http://$DropletIP/api/health" -ForegroundColor Yellow
Write-Host "`nNext steps:" -ForegroundColor Cyan
if (-not $RestartContainers) {
    Write-Host "  1. SSH into droplet: ssh ${DropletUser}@${DropletIP}" -ForegroundColor White
    Write-Host "  2. Restart backend: cd /root/astroaxis_control && docker compose restart backend" -ForegroundColor White
    Write-Host "  3. Test: curl http://localhost/api/health" -ForegroundColor White
    Write-Host "`nOr re-run with -RestartContainers flag to automate" -ForegroundColor Gray
} else {
    Write-Host "  1. Open browser: http://$DropletIP" -ForegroundColor White
    Write-Host "  2. Test all modules and features" -ForegroundColor White
    Write-Host "  3. Monitor logs: ssh ${DropletUser}@${DropletIP} 'docker compose logs -f backend'" -ForegroundColor White
}
Write-Host "=====================================================`n" -ForegroundColor Cyan
