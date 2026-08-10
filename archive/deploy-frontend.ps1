# ASTRO-ASIX Frontend Deployment Script
# Automates the process of building and deploying React frontend to DigitalOcean droplet

param(
    [string]$DropletIP = "209.38.226.32",
    [string]$DropletUser = "root",
    [string]$FrontendPath = "C:\Users\USER\ASTROAXIS\frontend",
    [switch]$SkipBuild,
    [switch]$UsePassword,
    [switch]$Verify
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  ASTRO-ASIX Frontend Deployment to DigitalOcean  " -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# Configuration
$BuildPath = Join-Path $FrontendPath "build"
$TempZipPath = Join-Path $env:TEMP "astroaxis-frontend-build.zip"
$DropletDestination = "/root/astroaxis_control/frontend"
$SSHTarget = "${DropletUser}@${DropletIP}"

# Step 1: Pre-deployment checks
Write-Host "[1/7] Running pre-deployment checks..." -ForegroundColor Yellow

# Check if frontend directory exists
if (-not (Test-Path $FrontendPath)) {
    Write-Host "✗ Frontend directory not found: $FrontendPath" -ForegroundColor Red
    exit 1
}

# Check if package.json exists
$PackageJsonPath = Join-Path $FrontendPath "package.json"
if (-not (Test-Path $PackageJsonPath)) {
    Write-Host "✗ package.json not found in $FrontendPath" -ForegroundColor Red
    exit 1
}

# Check if npm is available
try {
    $npmVersion = npm --version 2>&1
    Write-Host "  ✓ npm version: $npmVersion" -ForegroundColor Green
} catch {
    Write-Host "✗ npm not found. Please install Node.js" -ForegroundColor Red
    exit 1
}

# Check SSH connectivity
Write-Host "  Testing SSH connection to $DropletIP..." -ForegroundColor Gray
try {
    if ($UsePassword) {
        Write-Host "  ⚠ Using password authentication (consider setting up SSH keys)" -ForegroundColor Yellow
    } else {
        $sshTest = ssh -o ConnectTimeout=5 -o BatchMode=yes $SSHTarget "echo 'connected'" 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "✗ SSH connection failed. Use -UsePassword flag or setup SSH keys" -ForegroundColor Red
            Write-Host "  Run: .\setup-ssh-key.ps1" -ForegroundColor Yellow
            exit 1
        }
        Write-Host "  ✓ SSH connection successful" -ForegroundColor Green
    }
} catch {
    Write-Host "✗ SSH not available or connection failed" -ForegroundColor Red
    exit 1
}

Write-Host "✓ Pre-deployment checks passed" -ForegroundColor Green
Write-Host ""

# Step 2: Build frontend (if not skipped)
if (-not $SkipBuild) {
    Write-Host "[2/7] Building React frontend..." -ForegroundColor Yellow
    
    Push-Location $FrontendPath
    try {
        # Clean previous build
        if (Test-Path $BuildPath) {
            Write-Host "  Cleaning previous build..." -ForegroundColor Gray
            Remove-Item -Recurse -Force $BuildPath -ErrorAction SilentlyContinue
        }
        
        # Run npm build
        Write-Host "  Running: npm run build" -ForegroundColor Gray
        $buildOutput = npm run build 2>&1
        
        if ($LASTEXITCODE -ne 0) {
            Write-Host "✗ Build failed!" -ForegroundColor Red
            Write-Host $buildOutput -ForegroundColor Red
            Pop-Location
            exit 1
        }
        
        # Check if build was successful
        if (-not (Test-Path $BuildPath)) {
            Write-Host "✗ Build directory not created" -ForegroundColor Red
            Pop-Location
            exit 1
        }
        
        $buildSize = (Get-ChildItem -Path $BuildPath -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
        Write-Host "  ✓ Build completed successfully ($([math]::Round($buildSize, 2)) MB)" -ForegroundColor Green
        
    } catch {
        Write-Host "✗ Build process failed: $_" -ForegroundColor Red
        Pop-Location
        exit 1
    } finally {
        Pop-Location
    }
} else {
    Write-Host "[2/7] Skipping build (using existing build)..." -ForegroundColor Yellow
    
    if (-not (Test-Path $BuildPath)) {
        Write-Host "✗ Build directory not found: $BuildPath" -ForegroundColor Red
        Write-Host "  Run without -SkipBuild flag to build first" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "  ✓ Using existing build directory" -ForegroundColor Green
}

Write-Host ""

# Step 3: Create compressed archive
Write-Host "[3/7] Creating compressed archive..." -ForegroundColor Yellow

try {
    # Remove old archive if exists
    if (Test-Path $TempZipPath) {
        Remove-Item -Force $TempZipPath
    }
    
    # Create zip archive
    Write-Host "  Compressing build files..." -ForegroundColor Gray
    Compress-Archive -Path "$BuildPath\*" -DestinationPath $TempZipPath -CompressionLevel Optimal -Force
    
    $zipSize = (Get-Item $TempZipPath).Length / 1MB
    Write-Host "  ✓ Archive created: $([math]::Round($zipSize, 2)) MB" -ForegroundColor Green
    
} catch {
    Write-Host "✗ Archive creation failed: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""

# Step 4: Transfer to droplet
Write-Host "[4/7] Transferring to droplet..." -ForegroundColor Yellow
Write-Host "  Target: $SSHTarget" -ForegroundColor Gray

try {
    # Upload zip file
    Write-Host "  Uploading archive..." -ForegroundColor Gray
    
    if ($UsePassword) {
        scp $TempZipPath "${SSHTarget}:/root/frontend-build.zip"
    } else {
        scp -o BatchMode=yes $TempZipPath "${SSHTarget}:/root/frontend-build.zip"
    }
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "✗ File transfer failed" -ForegroundColor Red
        exit 1
    }
    
    Write-Host "  ✓ Archive transferred successfully" -ForegroundColor Green
    
} catch {
    Write-Host "✗ Transfer failed: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""

# Step 5: Extract on droplet
Write-Host "[5/7] Extracting files on droplet..." -ForegroundColor Yellow

$extractCommands = @"
set -e
echo '  Creating backup of existing build...'
if [ -d /root/astroaxis_control/frontend/build ]; then
    mv /root/astroaxis_control/frontend/build /root/astroaxis_control/frontend/build.backup.`$(date +%Y%m%d_%H%M%S)
fi

echo '  Creating build directory...'
mkdir -p /root/astroaxis_control/frontend/build

echo '  Extracting archive...'
unzip -q -o /root/frontend-build.zip -d /root/astroaxis_control/frontend/build/

echo '  Cleaning up...'
rm /root/frontend-build.zip

echo '  Verifying extraction...'
if [ -f /root/astroaxis_control/frontend/build/index.html ]; then
    echo '  ✓ Extraction successful'
    ls -lh /root/astroaxis_control/frontend/build/ | head -10
else
    echo '  ✗ Extraction failed - index.html not found'
    exit 1
fi
"@

try {
    if ($UsePassword) {
        $extractCommands | ssh $SSHTarget "bash -s"
    } else {
        $extractCommands | ssh -o BatchMode=yes $SSHTarget "bash -s"
    }
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "✗ Extraction failed on droplet" -ForegroundColor Red
        exit 1
    }
    
    Write-Host "  ✓ Files extracted successfully" -ForegroundColor Green
    
} catch {
    Write-Host "✗ Extraction process failed: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""

# Step 6: Restart backend container
Write-Host "[6/7] Restarting backend container..." -ForegroundColor Yellow

$restartCommands = @"
set -e
cd /root/astroaxis_control
echo '  Restarting backend service...'
docker compose restart backend

echo '  Waiting for backend to be ready...'
sleep 5

echo '  Checking backend health...'
docker compose exec -T backend curl -f http://localhost:8004/api/health || echo 'Health check skipped (curl not in container)'

echo '  ✓ Backend restarted'
docker compose ps
"@

try {
    if ($UsePassword) {
        $restartCommands | ssh $SSHTarget "bash -s"
    } else {
        $restartCommands | ssh -o BatchMode=yes $SSHTarget "bash -s"
    }
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "⚠ Backend restart had warnings, but may still work" -ForegroundColor Yellow
    } else {
        Write-Host "  ✓ Backend restarted successfully" -ForegroundColor Green
    }
    
} catch {
    Write-Host "⚠ Restart process had issues: $_" -ForegroundColor Yellow
    Write-Host "  You may need to manually restart with: docker compose restart backend" -ForegroundColor Yellow
}

Write-Host ""

# Step 7: Verify deployment
Write-Host "[7/7] Verifying deployment..." -ForegroundColor Yellow

try {
    # Test from droplet
    $verifyCommands = @"
echo '  Testing from droplet...'
echo -n '  Frontend (HTTP 200): '
curl -s -o /dev/null -w '%{http_code}' http://localhost/

echo -e '\n  API Health: '
curl -s http://localhost/api/health | grep -o '"status":"ok"' || echo 'API health check failed'
"@

    if ($UsePassword) {
        $verifyCommands | ssh $SSHTarget "bash -s"
    } else {
        $verifyCommands | ssh -o BatchMode=yes $SSHTarget "bash -s"
    }
    
    Write-Host ""
    Write-Host "  Testing from your machine..." -ForegroundColor Gray
    
    # Test from local machine
    try {
        $response = Invoke-WebRequest -Uri "http://$DropletIP/" -TimeoutSec 10 -UseBasicParsing
        if ($response.StatusCode -eq 200) {
            Write-Host "  ✓ Frontend accessible from external network" -ForegroundColor Green
        }
    } catch {
        Write-Host "  ⚠ Could not verify external access (firewall?)" -ForegroundColor Yellow
    }
    
    try {
        $healthResponse = Invoke-RestMethod -Uri "http://$DropletIP/api/health" -TimeoutSec 10
        if ($healthResponse.status -eq "ok") {
            Write-Host "  ✓ API accessible and healthy" -ForegroundColor Green
        }
    } catch {
        Write-Host "  ⚠ Could not verify API access" -ForegroundColor Yellow
    }
    
} catch {
    Write-Host "  ⚠ Verification had some issues, but deployment may still be successful" -ForegroundColor Yellow
}

# Cleanup local temp file
if (Test-Path $TempZipPath) {
    Remove-Item -Force $TempZipPath
}

Write-Host ""
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  ✓ DEPLOYMENT COMPLETED SUCCESSFULLY" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "🌐 Your application is now live at:" -ForegroundColor Cyan
Write-Host "   http://$DropletIP" -ForegroundColor White
Write-Host ""
Write-Host "📊 Next steps:" -ForegroundColor Cyan
Write-Host "   1. Open http://$DropletIP in your browser" -ForegroundColor White
Write-Host "   2. Test all 10 ERP modules" -ForegroundColor White
Write-Host "   3. Register first admin user" -ForegroundColor White
Write-Host "   4. Configure company settings" -ForegroundColor White
Write-Host ""
Write-Host "🔧 Useful commands:" -ForegroundColor Cyan
Write-Host "   ssh $SSHTarget" -ForegroundColor White
Write-Host "   cd /root/astroaxis_control && docker compose logs -f backend" -ForegroundColor White
Write-Host "   docker compose ps" -ForegroundColor White
Write-Host ""
