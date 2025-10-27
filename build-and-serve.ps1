# ASTRO-ASIX ERP Build and Serve Script
# This script builds the React frontend and starts the unified backend

Write-Host "🏗️  Building ASTRO-ASIX ERP..." -ForegroundColor Blue

# Stop any running servers
Write-Host "🛑 Stopping any running servers..." -ForegroundColor Yellow
try {
    taskkill /F /IM python.exe 2>$null
    taskkill /F /IM node.exe 2>$null
} catch {
    # Ignore if no processes are running
}

# Navigate to frontend and build
Write-Host "📦 Building React frontend..." -ForegroundColor Green
Set-Location -Path "c:\Users\USER\ASTROAXIS\frontend"
npm run build

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ Frontend build completed successfully!" -ForegroundColor Green
    
    # Navigate to backend and start server
    Write-Host "🚀 Starting unified backend server..." -ForegroundColor Cyan
    Set-Location -Path "c:\Users\USER\ASTROAXIS\backend"
    
    Write-Host "🌐 ASTRO-ASIX ERP will be available at:" -ForegroundColor Magenta
    Write-Host "   • Frontend: http://localhost:8000" -ForegroundColor White
    Write-Host "   • API Docs: http://localhost:8000/docs" -ForegroundColor White
    Write-Host "   • Health:   http://localhost:8000/api/health" -ForegroundColor White
    Write-Host ""
    Write-Host "Press Ctrl+C to stop the server" -ForegroundColor Yellow
    Write-Host ""
    
    # Start the server
    python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
} else {
    Write-Host "❌ Frontend build failed!" -ForegroundColor Red
    exit 1
}