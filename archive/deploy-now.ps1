# ASTRO-ASIX Frontend Deployment
param([string]$IP = "209.38.226.32", [switch]$SkipBuild)
$ErrorActionPreference = "Stop"
Write-Host "Deploying to $IP..." -ForegroundColor Cyan

if (-not $SkipBuild) {
    Write-Host "[1/4] Building..." -ForegroundColor Yellow
    cd C:\Users\USER\ASTROAXIS\frontend
    npm run build
}

Write-Host "[2/4] Creating archive..." -ForegroundColor Yellow
$zip = "$env:TEMP\frontend.zip"
Compress-Archive -Path "C:\Users\USER\ASTROAXIS\frontend\build\*" -DestinationPath $zip -Force

Write-Host "[3/4] Transferring..." -ForegroundColor Yellow
scp $zip "root@${IP}:/root/frontend.zip"

Write-Host "[4/4] Extracting and restarting..." -ForegroundColor Yellow
ssh "root@$IP" "cd /root/astroaxis_control && mkdir -p frontend/build && unzip -q -o /root/frontend.zip -d frontend/build/ && rm /root/frontend.zip && docker compose restart backend"

Remove-Item $zip -Force
Write-Host "DONE! Visit: http://$IP" -ForegroundColor Green
