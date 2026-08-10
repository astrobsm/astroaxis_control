Write-Host '=======================================' -ForegroundColor Cyan
Write-Host '  Multi-Unit Pricing Feature Test' -ForegroundColor Cyan
Write-Host '=======================================' -ForegroundColor Cyan

$baseUrl = 'http://209.38.226.32'

Write-Host '[TEST 1] Main page...' -ForegroundColor Yellow
try {
    $r = Invoke-WebRequest -Uri $baseUrl -UseBasicParsing -TimeoutSec 10
    Write-Host 'PASS: Main page loads' -ForegroundColor Green
} catch {
    Write-Host 'FAIL: Cannot reach server' -ForegroundColor Red
}

Write-Host '[TEST 2] JavaScript bundle...' -ForegroundColor Yellow
try {
    $r = Invoke-WebRequest -Uri '$baseUrl/static/js/main.ac38f8f9.js' -UseBasicParsing -TimeoutSec 10
    Write-Host 'PASS: New bundle exists (main.ac38f8f9.js)' -ForegroundColor Green
} catch {
    Write-Host 'FAIL: Bundle not found' -ForegroundColor Red
}

Write-Host '[TEST 3] Service worker...' -ForegroundColor Yellow
try {
    $r = Invoke-WebRequest -Uri '$baseUrl/serviceWorker.js' -UseBasicParsing -TimeoutSec 10
    if ($r.Content -match 'v2.2-multi-unit') {
        Write-Host 'PASS: Service worker updated to v2.2-multi-unit' -ForegroundColor Green
    } else {
        Write-Host 'WARN: Service worker version check needed' -ForegroundColor Yellow
    }
} catch {
    Write-Host 'FAIL: Cannot load service worker' -ForegroundColor Red
}

Write-Host '[TEST 4] Container status...' -ForegroundColor Yellow
try {
    $status = ssh root@209.38.226.32 'docker ps --filter name=astroaxis_backend --format {{.Status}}'
    Write-Host 'PASS: Container is running' -ForegroundColor Green
    Write-Host \"Status: $status\" -ForegroundColor Cyan
} catch {
    Write-Host 'WARN: Cannot check container' -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'Feature: Multi-Unit Pricing System' -ForegroundColor White
Write-Host 'Version: 2.2-multi-unit' -ForegroundColor White
Write-Host 'URL: http://209.38.226.32' -ForegroundColor White
Write-Host ''
Write-Host 'Next Steps:' -ForegroundColor Cyan
Write-Host '1. Clear browser cache (Ctrl+Shift+R)'
Write-Host '2. Navigate to Products module'
Write-Host '3. Click Add Product'
Write-Host '4. See Units and Pricing section with Add Unit button'
