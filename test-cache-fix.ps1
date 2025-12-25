# Clear Browser Cache and Test PWA
# This script helps clear browser cache and test the PWA after deployment

Write-Host "🧹 Clearing browser cache and testing PWA..." -ForegroundColor Cyan

# Instructions for manual cache clearing
Write-Host "`n📝 Manual Steps to Clear PWA Cache:" -ForegroundColor Yellow
Write-Host "1. Open Developer Tools (F12)" -ForegroundColor White
Write-Host "2. Go to Application tab" -ForegroundColor White
Write-Host "3. Click 'Storage' in left panel" -ForegroundColor White
Write-Host "4. Click 'Clear site data'" -ForegroundColor White
Write-Host "5. Refresh the page (Ctrl+F5)" -ForegroundColor White

Write-Host "`n🤖 Or run this JavaScript in browser console:" -ForegroundColor Yellow
Write-Host "Copy and paste the contents of cache-reset-utility.js into browser console" -ForegroundColor White

# Test the products API endpoint
Write-Host "`n🔍 Testing Products API..." -ForegroundColor Cyan
try {
    $response = Invoke-RestMethod -Uri "http://209.38.226.32/api/products/" -Method GET
    Write-Host "✅ Products API is working! Found $($response.total) products" -ForegroundColor Green
    
    # Show first product as sample
    if ($response.items -and $response.items.Count -gt 0) {
        $product = $response.items[0]
        Write-Host "📦 Sample Product: $($product.name) - SKU: $($product.sku)" -ForegroundColor White
        if ($product.retail_price) {
            Write-Host "💰 Retail Price: ₦$($product.retail_price)" -ForegroundColor White
        }
        if ($product.wholesale_price) {
            Write-Host "💰 Wholesale Price: ₦$($product.wholesale_price)" -ForegroundColor White
        }
    }
} catch {
    Write-Host "❌ Products API Error: $($_.Exception.Message)" -ForegroundColor Red
}

# Test service worker version
Write-Host "`n🔍 Testing Service Worker..." -ForegroundColor Cyan
try {
    $swResponse = Invoke-WebRequest -Uri "http://209.38.226.32/serviceWorker.js" -UseBasicParsing
    if ($swResponse.Content -match "v2\.4") {
        Write-Host "✅ Service Worker v2.4 is deployed" -ForegroundColor Green
    } else {
        Write-Host "⚠️ Service Worker version not found" -ForegroundColor Yellow
    }
} catch {
    Write-Host "❌ Service Worker Error: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "`n🎯 Next Steps:" -ForegroundColor Cyan
Write-Host "1. Clear browser cache using the manual steps above" -ForegroundColor White
Write-Host "2. Visit http://209.38.226.32 in a fresh browser window" -ForegroundColor White
Write-Host "3. Check if products load in Sales Order form" -ForegroundColor White
Write-Host "4. Test unit dropdown functionality" -ForegroundColor White

Write-Host "`n📱 PWA Features to Test:" -ForegroundColor Cyan
Write-Host "- Install App button in navbar" -ForegroundColor White
Write-Host "- Offline functionality" -ForegroundColor White
Write-Host "- Auto-update notifications" -ForegroundColor White

Read-Host "Press Enter to continue"