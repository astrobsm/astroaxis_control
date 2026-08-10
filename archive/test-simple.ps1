# Test Cache Fix
Write-Host "Testing Products API..." -ForegroundColor Cyan
$response = Invoke-RestMethod -Uri "http://209.38.226.32/api/products/" -Method GET
Write-Host "Products API working! Found $($response.total) products" -ForegroundColor Green

Write-Host "Testing Service Worker..." -ForegroundColor Cyan  
$swResponse = Invoke-WebRequest -Uri "http://209.38.226.32/serviceWorker.js" -UseBasicParsing
if ($swResponse.Content -match "v2\.4") {
    Write-Host "Service Worker v2.4 deployed successfully" -ForegroundColor Green
}

Write-Host "Cache fix deployed. Clear browser cache and test the frontend." -ForegroundColor Yellow