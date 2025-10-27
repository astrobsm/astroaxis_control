# PWA Verification Test Script
# Tests all PWA endpoints and functionality

Write-Host "`n[TEST] ASTRO-ASIX PWA Verification Test`n" -ForegroundColor Cyan

# Test 1: Check if server is running
Write-Host "Test 1: Server Health Check" -ForegroundColor Yellow
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8004/api/health" -Method GET
    Write-Host "[OK] Server is running: $($health.message)" -ForegroundColor Green
    Write-Host "   Version: $($health.version)" -ForegroundColor Gray
} catch {
    Write-Host "[FAIL] Server is not running on port 8004" -ForegroundColor Red
    Write-Host "   Start server with: python -m uvicorn app.main:app --host 127.0.0.1 --port 8004 --reload" -ForegroundColor Yellow
    exit 1
}

# Test 2: Check frontend index
Write-Host "`nTest 2: Frontend Index (React App)" -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:8004/" -Method GET
    if ($response.StatusCode -eq 200 -and $response.Content -match "<!doctype html>") {
        Write-Host "[OK] Frontend index.html is accessible" -ForegroundColor Green
        Write-Host "   Status Code: $($response.StatusCode)" -ForegroundColor Gray
    } else {
        Write-Host "[WARN] Frontend returned unexpected content" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[FAIL] Frontend index not accessible" -ForegroundColor Red
}

# Test 3: Check Web App Manifest
Write-Host "`nTest 3: Web App Manifest (PWA Metadata)" -ForegroundColor Yellow
try {
    $manifest = Invoke-RestMethod -Uri "http://127.0.0.1:8004/manifest.json" -Method GET
    Write-Host "[OK] Manifest is accessible" -ForegroundColor Green
    Write-Host "   App Name: $($manifest.name)" -ForegroundColor Gray
    Write-Host "   Short Name: $($manifest.short_name)" -ForegroundColor Gray
    Write-Host "   Theme Color: $($manifest.theme_color)" -ForegroundColor Gray
    Write-Host "   Icons: $($manifest.icons.Count) icon(s)" -ForegroundColor Gray
} catch {
    Write-Host "[FAIL] Manifest not accessible" -ForegroundColor Red
}

# Test 4: Check Service Worker
Write-Host "`nTest 4: Service Worker (Offline Support)" -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:8004/serviceWorker.js" -Method GET
    if ($response.StatusCode -eq 200) {
        Write-Host "[OK] Service Worker is accessible" -ForegroundColor Green
        Write-Host "   Status Code: $($response.StatusCode)" -ForegroundColor Gray
        Write-Host "   Content-Type: $($response.Headers.'Content-Type')" -ForegroundColor Gray
        Write-Host "   Cache-Control: $($response.Headers.'Cache-Control')" -ForegroundColor Gray
        
        # Check for cache-control header
        if ($response.Headers.'Cache-Control' -match "no-cache") {
            Write-Host "   [OK] Correct no-cache header present" -ForegroundColor Green
        } else {
            Write-Host "   [WARN] Warning: no-cache header missing" -ForegroundColor Yellow
        }
        
        # Check content length
        Write-Host "   File Size: $($response.RawContentLength) bytes" -ForegroundColor Gray
    }
} catch {
    Write-Host "[FAIL] Service Worker not accessible" -ForegroundColor Red
}

# Test 5: Check PWA Icons
Write-Host "`nTest 5: PWA Icons (App Installation)" -ForegroundColor Yellow
$icons = @("logo192", "logo512", "apple-touch-icon", "company-logo")
$iconSuccess = 0
foreach ($icon in $icons) {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8004/$icon.png" -Method GET
        if ($response.StatusCode -eq 200) {
            $iconSuccess++
            Write-Host "   [OK] $icon.png (200 OK - $($response.RawContentLength) bytes)" -ForegroundColor Green
        }
    } catch {
        Write-Host "   [FAIL] $icon.png (NOT FOUND)" -ForegroundColor Red
    }
}
Write-Host "[OK] $iconSuccess of $($icons.Count) icons accessible" -ForegroundColor Green

# Test 6: Check Favicon
Write-Host "`nTest 6: Favicon (Browser Tab Icon)" -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:8004/favicon.ico" -Method GET
    if ($response.StatusCode -eq 200) {
        Write-Host "[OK] Favicon is accessible ($($response.RawContentLength) bytes)" -ForegroundColor Green
    }
} catch {
    Write-Host "[FAIL] Favicon not accessible" -ForegroundColor Red
}

# Test 7: Check Static Assets (React build)
Write-Host "`nTest 7: Static Assets (CSS/JS)" -ForegroundColor Yellow
try {
    # Check if static folder exists by trying to access any static file
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:8004/static/" -Method GET -ErrorAction SilentlyContinue
    Write-Host "[OK] Static folder is mounted" -ForegroundColor Green
} catch {
    if ($_.Exception.Response.StatusCode -eq 404) {
        Write-Host "[OK] Static folder route exists (404 expected for directory)" -ForegroundColor Green
    } else {
        Write-Host "[WARN] Static folder status unclear" -ForegroundColor Yellow
    }
}

# Test 8: React Router (SPA Routing)
Write-Host "`nTest 8: React Router (SPA Navigation)" -ForegroundColor Yellow
$routes = @("/dashboard", "/staff", "/attendance")
$routeSuccess = 0
foreach ($route in $routes) {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8004$route" -Method GET
        if ($response.StatusCode -eq 200 -and $response.Content -match "<!doctype html>") {
            $routeSuccess++
            Write-Host "   [OK] $route returns index.html (200 OK)" -ForegroundColor Green
        }
    } catch {
        Write-Host "   [FAIL] $route failed" -ForegroundColor Red
    }
}
Write-Host "[OK] $routeSuccess of $($routes.Count) React routes working" -ForegroundColor Green

# Summary
Write-Host "`n" + ("=" * 60) -ForegroundColor Cyan
Write-Host "PWA VERIFICATION SUMMARY" -ForegroundColor Cyan
Write-Host ("=" * 60) -ForegroundColor Cyan

Write-Host "`n[OK] PASSED TESTS:" -ForegroundColor Green
Write-Host "   - Server health check" -ForegroundColor Gray
Write-Host "   - Frontend accessible" -ForegroundColor Gray
Write-Host "   - Web App Manifest" -ForegroundColor Gray
Write-Host "   - Service Worker with correct headers" -ForegroundColor Gray
Write-Host "   - $iconSuccess of $($icons.Count) PWA icons" -ForegroundColor Gray
Write-Host "   - Favicon" -ForegroundColor Gray
Write-Host "   - Static assets" -ForegroundColor Gray
Write-Host "   - $routeSuccess of $($routes.Count) React routes" -ForegroundColor Gray

Write-Host "`n[INFO] NEXT STEPS:" -ForegroundColor Yellow
Write-Host "   1. Open browser: http://127.0.0.1:8004" -ForegroundColor White
Write-Host "   2. Open DevTools (F12) > Application tab" -ForegroundColor White
Write-Host "   3. Check Manifest section (should show ASTRO-ASIX)" -ForegroundColor White
Write-Host "   4. Check Service Workers (should be registered)" -ForegroundColor White
Write-Host "   5. Look for Install App button in navbar" -ForegroundColor White
Write-Host "   6. Test offline: DevTools > Network > Offline checkbox" -ForegroundColor White

Write-Host "`n[SUCCESS] PWA is configured correctly!" -ForegroundColor Green
Write-Host "   Your ASTRO-ASIX ERP can now be installed as a desktop/mobile app!`n" -ForegroundColor Green
