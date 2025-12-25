# Quick backend deployment for bulk upload
$ip = "209.38.226.32"

Write-Host "Deploying backend bulk upload files..." -ForegroundColor Cyan

# Copy bulk_upload.py
Write-Host "1. Copying bulk_upload.py..." -ForegroundColor Yellow
scp backend/app/api/bulk_upload.py root@${ip}:/root/astroaxis_control/backend/app/api/

# Copy updated main.py
Write-Host "2. Copying main.py..." -ForegroundColor Yellow
scp backend/app/main.py root@${ip}:/root/astroaxis_control/backend/app/

# Copy requirements.txt
Write-Host "3. Copying requirements.txt..." -ForegroundColor Yellow
scp backend/requirements.txt root@${ip}:/root/astroaxis_control/backend/

Write-Host "`n4. Installing openpyxl and restarting..." -ForegroundColor Yellow
$commands = @"
cd /root/astroaxis_control
docker-compose exec -T backend pip install openpyxl
docker-compose restart backend
"@

$commands | ssh root@${ip} bash

Write-Host "`nDeployment complete!" -ForegroundColor Green
Write-Host "Testing endpoint..." -ForegroundColor Cyan

Start-Sleep -Seconds 5

try {
    Invoke-RestMethod -Uri "http://${ip}/api/bulk-upload/template/staff" -Method Get -OutFile "test-template.xlsx"
    Write-Host "✓ Template download working!" -ForegroundColor Green
    Remove-Item "test-template.xlsx" -ErrorAction SilentlyContinue
} catch {
    Write-Host "⚠ Endpoint not ready yet, check logs: docker-compose logs backend" -ForegroundColor Yellow
}
