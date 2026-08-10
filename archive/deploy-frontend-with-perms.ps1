# ASTRO-ASIX Frontend Deployment with Automatic Permission Fix
# This script deploys the frontend and ensures static files are readable

Write-Host "`n🚀 ASTRO-ASIX Frontend Deployment`n" -ForegroundColor Cyan

# Step 1: Build
Write-Host "📦 Building frontend..." -ForegroundColor Yellow
Set-Location frontend
npm run build
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Build failed!" -ForegroundColor Red
    exit 1
}
Set-Location ..
Write-Host "✅ Build complete`n" -ForegroundColor Green

# Step 2: Deploy files
Write-Host "📤 Deploying to server..." -ForegroundColor Yellow
scp -r frontend/build/* root@209.38.226.32:/root/astroaxis_control/frontend/build/
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Deployment failed!" -ForegroundColor Red
    exit 1
}
Write-Host "✅ Files deployed`n" -ForegroundColor Green

# Step 3: Fix permissions (THIS IS CRITICAL!)
Write-Host "🔧 Fixing static file permissions..." -ForegroundColor Yellow
ssh root@209.38.226.32 "chmod -R 755 /root/astroaxis_control/frontend/build/static"
Write-Host "✅ Permissions fixed`n" -ForegroundColor Green

# Step 4: Restart container
Write-Host "🔄 Restarting backend container..." -ForegroundColor Yellow
ssh root@209.38.226.32 "docker restart astroaxis_backend"
Start-Sleep -Seconds 5
Write-Host "✅ Container restarted`n" -ForegroundColor Green

# Step 5: Verify
Write-Host "🔍 Verifying deployment..." -ForegroundColor Yellow
$jsStatus = ssh root@209.38.226.32 "curl -s -o /dev/null -w '%{http_code}' http://localhost/static/js/main.*.js 2>/dev/null | head -1"
$cssStatus = ssh root@209.38.226.32 "curl -s -o /dev/null -w '%{http_code}' http://localhost/static/css/main.*.css 2>/dev/null | head -1"

Write-Host "`n📊 Deployment Status:" -ForegroundColor Cyan
Write-Host "  JavaScript: $jsStatus" -ForegroundColor $(if ($jsStatus -eq "200") { "Green" } else { "Red" })
Write-Host "  CSS: $cssStatus" -ForegroundColor $(if ($cssStatus -eq "200") { "Green" } else { "Red" })

if ($jsStatus -eq "200" -and $cssStatus -eq "200") {
    Write-Host "`n✅ DEPLOYMENT SUCCESSFUL!" -ForegroundColor Green
    Write-Host "🌐 Site: http://209.38.226.32" -ForegroundColor Cyan
    Write-Host "⚠️  Clear browser cache: Ctrl+Shift+R`n" -ForegroundColor Yellow
} else {
    Write-Host "`n❌ DEPLOYMENT FAILED - Files not accessible!" -ForegroundColor Red
}
