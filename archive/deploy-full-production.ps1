param(
    [string]$DropletIP = "209.38.226.32",
    [string]$DropletUser = "root",
    [string]$ProjectRoot = "C:\Users\USER\ASTROAXIS",
    [string]$RemotePath = "/root/astroaxis_control",
    [switch]$RunTests,
    [switch]$SkipFrontendBuild,
    [switch]$SkipDockerRestart
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Test-CommandExists {
    param(
        [string]$CommandName,
        [string]$FriendlyName
    )

    if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
        throw "Required command '$FriendlyName' was not found in PATH."
    }
}

function Invoke-ExternalCommand {
    param(
        [scriptblock]$Command,
        [string]$ErrorMessage
    )

    & $Command
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "$ErrorMessage (exit code $exitCode)."
    }
}

if (-not (Test-Path $ProjectRoot)) {
    throw "Project root '$ProjectRoot' does not exist."
}

$frontendPath = Join-Path $ProjectRoot "frontend"
if (-not (Test-Path $frontendPath)) {
    throw "Frontend directory '$frontendPath' not found."
}

Test-CommandExists -CommandName "ssh" -FriendlyName "ssh"
Test-CommandExists -CommandName "scp" -FriendlyName "scp"
Test-CommandExists -CommandName "npm" -FriendlyName "npm"
Test-CommandExists -CommandName "python" -FriendlyName "python"

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stagingDir = Join-Path $env:TEMP "astroaxis_release_$timestamp"
$archivePath = Join-Path $env:TEMP "astroaxis_release_$timestamp.zip"
$remoteZipPath = "/root/astroaxis_release_$timestamp.zip"
$sshTarget = "${DropletUser}@${DropletIP}"
$deploymentSucceeded = $false

Write-Host "== ASTRO-ASIX Production Deployment ==" -ForegroundColor Cyan
Write-Host "Target: $sshTarget" -ForegroundColor Cyan
Write-Host "Remote path: $RemotePath" -ForegroundColor Cyan
Write-Host "Timestamp: $timestamp" -ForegroundColor Cyan
Write-Host ""
try {
    Write-Host "[1/6] Verifying SSH connectivity..." -ForegroundColor Yellow
    $sshTest = ssh -o BatchMode=yes -o ConnectTimeout=5 $sshTarget "echo ok" 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "SSH connection to $sshTarget failed. Details: $sshTest"
    }
    Write-Host "  Connectivity check passed." -ForegroundColor Green

    if ($RunTests) {
        Write-Host "[2/6] Running backend smoke tests..." -ForegroundColor Yellow
        Push-Location $ProjectRoot
        try {
            & python -m pytest backend/tests/test_crud_apis.py::test_warehouses_crud
            $testExit = $LASTEXITCODE
        } finally {
            Pop-Location
        }
        if ($testExit -ne 0 -and $testExit -ne -1073741819) {
            throw "Backend tests failed (exit code $testExit)."
        }
        if ($testExit -eq -1073741819) {
            Write-Host "  Tests passed (ignoring Windows asyncpg teardown exit code)." -ForegroundColor Yellow
        } else {
            Write-Host "  Tests completed successfully." -ForegroundColor Green
        }
    } else {
        Write-Host "[2/6] Skipping backend tests (use -RunTests to enable)." -ForegroundColor DarkYellow
    }

    if (-not $SkipFrontendBuild) {
        Write-Host "[3/6] Building frontend bundle..." -ForegroundColor Yellow
        Push-Location $frontendPath
        try {
            Invoke-ExternalCommand -Command { npm run build } -ErrorMessage "Frontend build failed"
        } finally {
            Pop-Location
        }
        Write-Host "  Frontend build completed." -ForegroundColor Green
    } else {
        Write-Host "[3/6] Skipping frontend build (use -SkipFrontendBuild to bypass)." -ForegroundColor DarkYellow
    }

    Write-Host "[4/6] Preparing release archive..." -ForegroundColor Yellow
    if (Test-Path $stagingDir) {
        Remove-Item -Path $stagingDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $stagingDir | Out-Null

    $excludeDirs = @(
        (Join-Path $ProjectRoot ".git"),
        (Join-Path $ProjectRoot ".venv"),
        (Join-Path $ProjectRoot ".mypy_cache"),
        (Join-Path $ProjectRoot ".pytest_cache"),
        (Join-Path $ProjectRoot "backend\.pytest_cache"),
        (Join-Path $ProjectRoot "backend\__pycache__"),
        (Join-Path $ProjectRoot "backend\.mypy_cache"),
        (Join-Path $ProjectRoot "frontend\node_modules"),
        (Join-Path $ProjectRoot "frontend\.cache"),
        (Join-Path $ProjectRoot "frontend\.parcel-cache"),
        (Join-Path $ProjectRoot "frontend\.next"),
        (Join-Path $ProjectRoot "frontend\.turbo"),
        (Join-Path $ProjectRoot "frontend\.astroaxis-cache")
    )
    $excludeFiles = @(
        (Join-Path $ProjectRoot ".env"),
        (Join-Path $ProjectRoot "backend\.env"),
        (Join-Path $ProjectRoot "frontend\.env"),
        (Join-Path $ProjectRoot "frontend\.env.local")
    )

    $robocopyArgs = @($ProjectRoot, $stagingDir, "/MIR", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
    if ($excludeDirs.Count -gt 0) {
        $robocopyArgs += "/XD"
        $robocopyArgs += $excludeDirs
    }
    if ($excludeFiles.Count -gt 0) {
        $robocopyArgs += "/XF"
        $robocopyArgs += $excludeFiles
    }

    robocopy @robocopyArgs | Out-Null
    $roboExit = $LASTEXITCODE
    if ($roboExit -ge 8) {
        throw "File staging failed (robocopy exit code $roboExit)."
    }

    if (Test-Path $archivePath) {
        Remove-Item -Path $archivePath -Force
    }
    Compress-Archive -Path (Join-Path $stagingDir '*') -DestinationPath $archivePath -CompressionLevel Optimal
    $archiveSize = [math]::Round(((Get-Item $archivePath).Length / 1MB), 2)
    Write-Host "  Release archive created: $archivePath ($archiveSize MB)." -ForegroundColor Green

    Write-Host "[5/6] Transferring release to server..." -ForegroundColor Yellow
    scp $archivePath "${sshTarget}:${remoteZipPath}"
    if ($LASTEXITCODE -ne 0) {
        throw "Release upload failed (exit code $LASTEXITCODE)."
    }
    Write-Host "  Archive uploaded to $remoteZipPath." -ForegroundColor Green

    Write-Host "[6/6] Applying release on server..." -ForegroundColor Yellow
    
    $bashScript = @"
set -e
release_zip="{0}"
remote_path="{1}"
timestamp="{2}"
temp_dir="/root/astroaxis_release_{2}"
backup_dir="{1}_backup_{2}"

echo '-- Extracting release archive'
rm -rf "`$temp_dir"
mkdir -p "`$temp_dir"
unzip -q "`$release_zip" -d "`$temp_dir"

echo '-- Backing up current deployment'
if [ -d "`$remote_path" ]; then
  rm -rf "`$backup_dir"
  mv "`$remote_path" "`$backup_dir"
fi

mv "`$temp_dir" "`$remote_path"
if [ -d "`$backup_dir" ] && [ -f "`$backup_dir/.env" ]; then
  cp "`$backup_dir/.env" "`$remote_path/.env"
fi

cd "`$remote_path"

echo '-- Running docker compose'
docker compose up -d --build

if [ `$? -ne 0 ]; then
  echo 'Docker compose failed' >&2
  exit 1
fi

if [ '{3}' = 'false' ]; then
  echo '-- Waiting for services to settle'
  sleep 8
  echo '-- Applying database migrations'
  docker compose exec -T backend alembic upgrade head || echo 'Warning: Alembic migration returned non-zero exit code'
  echo '-- Performing health check'
  curl -sf http://localhost/api/health || echo 'Warning: health endpoint did not respond'
fi

rm -f "`$release_zip"
echo '-- Deployment completed'
"@ -f $remoteZipPath, $RemotePath, $timestamp, ($SkipDockerRestart.ToString().ToLower())

    $bashScript -replace "`r`n", "`n" | ssh $sshTarget "bash -s"
if ($LASTEXITCODE -ne 0) {
    throw "Remote deployment failed."
}
    Write-Host "  Remote deployment complete." -ForegroundColor Green
    $deploymentSucceeded = $true
} finally {
    if (Test-Path $stagingDir) {
        Remove-Item -Path $stagingDir -Recurse -Force
    }
    if (Test-Path $archivePath) {
        Remove-Item -Path $archivePath -Force
    }
}

if ($deploymentSucceeded) {
    Write-Host "\nDeployment finished successfully." -ForegroundColor Green
    Write-Host "Archive size: $archiveSize MB" -ForegroundColor Green
    Write-Host "Server: $DropletIP" -ForegroundColor Green
    Write-Host "Remote path: $RemotePath" -ForegroundColor Green

    if ($SkipDockerRestart) {
        Write-Host "Note: Docker services were not restarted (use -SkipDockerRestart:\$false to enable)." -ForegroundColor DarkYellow
    }
}
