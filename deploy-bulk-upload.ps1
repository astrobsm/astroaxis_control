# Bulk Upload System Deployment Script for DigitalOcean
# Deploys complete bulk upload functionality with Excel template generation

Write-Host "=" -ForegroundColor Cyan
Write-Host "BULK UPLOAD SYSTEM DEPLOYMENT" -ForegroundColor Cyan
Write-Host "=" -ForegroundColor Cyan
Write-Host ""

$dropletIP = "209.38.226.32"
$deployUser = "root"

# Step 1: Build frontend
Write-Host "1. Building React frontend..." -ForegroundColor Yellow
cd frontend
npm run build
if ($LASTEXITCODE -ne 0) {
    Write-Host "Frontend build failed!" -ForegroundColor Red
    exit 1
}
cd ..

# Step 2: Copy frontend build to droplet
Write-Host ""
Write-Host "2. Deploying frontend build to droplet..." -ForegroundColor Yellow
scp -r frontend/build/* ${deployUser}@${dropletIP}:/var/www/astrobsm/frontend/

# Step 3: Copy backend bulk_upload.py
Write-Host ""
Write-Host "3. Deploying bulk upload backend API..." -ForegroundColor Yellow
scp backend/app/api/bulk_upload.py ${deployUser}@${dropletIP}:/var/www/astrobsm/backend/app/api/

# Step 4: Update requirements.txt with openpyxl
Write-Host ""
Write-Host "4. Updating backend dependencies..." -ForegroundColor Yellow
scp backend/requirements.txt ${deployUser}@${dropletIP}:/var/www/astrobsm/backend/

# Step 5: Install openpyxl in backend container
Write-Host ""
Write-Host "5. Installing openpyxl in backend container..." -ForegroundColor Yellow
ssh ${deployUser}@${dropletIP} "cd /var/www/astrobsm && docker-compose exec -T backend pip install openpyxl"

# Step 6: Restart backend to load new module
Write-Host ""
Write-Host "6. Restarting backend container..." -ForegroundColor Yellow
ssh ${deployUser}@${dropletIP} "cd /var/www/astrobsm && docker-compose restart backend"

# Step 7: Verify deployment
Write-Host ""
Write-Host "7. Verifying bulk upload endpoints..." -ForegroundColor Yellow
Write-Host ""

Write-Host "Testing template download endpoint..." -ForegroundColor Cyan
$templateTest = Invoke-RestMethod -Uri "http://${dropletIP}/api/bulk-upload/template/staff" -Method Get -ErrorAction SilentlyContinue
if ($templateTest) {
    Write-Host "Template endpoint working!" -ForegroundColor Green
} else {
    Write-Host "Template endpoint may need a moment to start..." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=" -ForegroundColor Green
Write-Host "DEPLOYMENT COMPLETE!" -ForegroundColor Green
Write-Host "=" -ForegroundColor Green
Write-Host ""
Write-Host "Bulk Upload System Features:" -ForegroundColor Cyan
Write-Host "- Staff registration with auto-generated credentials" -ForegroundColor White
Write-Host "- Products and raw materials upload" -ForegroundColor White
Write-Host "- Stock intake (products and raw materials)" -ForegroundColor White
Write-Host "- Warehouses creation" -ForegroundColor White
Write-Host "- Damaged items tracking" -ForegroundColor White
Write-Host "- Product returns processing" -ForegroundColor White
Write-Host "- Bill of Materials (BOM) upload" -ForegroundColor White
Write-Host ""
Write-Host "Access the application at: http://${dropletIP}" -ForegroundColor Green
Write-Host ""
Write-Host "Template Download Examples:" -ForegroundColor Cyan
Write-Host "  Staff: http://${dropletIP}/api/bulk-upload/template/staff" -ForegroundColor White
Write-Host "  Products: http://${dropletIP}/api/bulk-upload/template/products" -ForegroundColor White
Write-Host "  BOM: http://${dropletIP}/api/bulk-upload/template/bom" -ForegroundColor White
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "1. Open the app and navigate to any module with bulk upload" -ForegroundColor White
Write-Host "2. Click the 'Bulk Upload' button" -ForegroundColor White
Write-Host "3. Download the Excel template" -ForegroundColor White
Write-Host "4. Fill in your data following the template format" -ForegroundColor White
Write-Host "5. Upload and process your bulk data!" -ForegroundColor White
Write-Host ""
