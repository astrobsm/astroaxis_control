# Deploy to New Droplet - ASTROMARCHANT
# IP: 159.89.29.45
# Ubuntu 24.04 (LTS) x64
# 1 GB Memory / 25 GB Disk

param(
    [string]$DropletIP = "159.89.29.45",
    [string]$DropletUser = "root"
)

$ErrorActionPreference = "Stop"

Write-Host "==================================" -ForegroundColor Cyan
Write-Host "Deploying to New Droplet" -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor Cyan
Write-Host "IP: $DropletIP" -ForegroundColor Yellow
Write-Host "Name: ASTROMARCHANT" -ForegroundColor Yellow
Write-Host ""

# Step 1: Test SSH Connection
Write-Host "[1/8] Testing SSH connection..." -ForegroundColor Cyan
try {
    ssh -o ConnectTimeout=10 "${DropletUser}@${DropletIP}" "echo 'SSH Connection OK'"
    Write-Host "✅ SSH connection successful!" -ForegroundColor Green
} catch {
    Write-Host "❌ SSH connection failed! Please check:" -ForegroundColor Red
    Write-Host "   1. SSH key is configured" -ForegroundColor Yellow
    Write-Host "   2. Droplet IP is correct: $DropletIP" -ForegroundColor Yellow
    Write-Host "   3. Run: ssh-copy-id ${DropletUser}@${DropletIP}" -ForegroundColor Yellow
    exit 1
}

# Step 2: Install Docker and Dependencies
Write-Host "`n[2/8] Installing Docker and dependencies..." -ForegroundColor Cyan
ssh "${DropletUser}@${DropletIP}" @"
apt-get update
apt-get install -y docker.io docker-compose git python3 python3-pip postgresql-client
systemctl start docker
systemctl enable docker
docker --version
"@
Write-Host "✅ Docker installed!" -ForegroundColor Green

# Step 3: Clone Repository
Write-Host "`n[3/8] Cloning repository..." -ForegroundColor Cyan
ssh "${DropletUser}@${DropletIP}" @"
cd /root
if [ -d astroaxis_control ]; then
    cd astroaxis_control
    git pull origin main
else
    git clone https://github.com/astrobsm/astroaxis_control.git
    cd astroaxis_control
fi
echo "Repository ready"
"@
Write-Host "✅ Repository cloned!" -ForegroundColor Green

# Step 4: Build Frontend
Write-Host "`n[4/8] Building frontend locally..." -ForegroundColor Cyan
cd "e:\bussiness file app\astroaxis_control\frontend"
npm run build
Write-Host "✅ Frontend built!" -ForegroundColor Green

# Step 5: Upload Frontend Build
Write-Host "`n[5/8] Uploading frontend build..." -ForegroundColor Cyan
scp -r build/* "${DropletUser}@${DropletIP}:/root/astroaxis_control/frontend/build/"
Write-Host "✅ Frontend uploaded!" -ForegroundColor Green

# Step 6: Start Docker Containers
Write-Host "`n[6/8] Starting Docker containers..." -ForegroundColor Cyan
ssh "${DropletUser}@${DropletIP}" @"
cd /root/astroaxis_control
docker-compose down
docker-compose up -d --build
sleep 10
docker-compose ps
"@
Write-Host "✅ Containers started!" -ForegroundColor Green

# Step 7: Run Database Migrations
Write-Host "`n[7/8] Running database migrations..." -ForegroundColor Cyan
ssh "${DropletUser}@${DropletIP}" @"
cd /root/astroaxis_control/backend
docker-compose exec -T backend alembic upgrade head
"@
Write-Host "✅ Migrations complete!" -ForegroundColor Green

# Step 8: Create Admin User
Write-Host "`n[8/8] Creating admin user..." -ForegroundColor Cyan
ssh "${DropletUser}@${DropletIP}" @"
cd /root/astroaxis_control/backend
docker-compose exec -T backend python create_admin.py
docker-compose exec -T backend python update_admin_phone.py
"@
Write-Host "✅ Admin user created!" -ForegroundColor Green

# Final Status
Write-Host "`n==================================" -ForegroundColor Green
Write-Host "DEPLOYMENT COMPLETE!" -ForegroundColor Green
Write-Host "==================================" -ForegroundColor Green
Write-Host ""
Write-Host "Application URL: http://$DropletIP" -ForegroundColor Cyan
Write-Host "Admin Phone: 08033328385" -ForegroundColor Cyan
Write-Host "Admin Email: admin@astrobsm.com" -ForegroundColor Cyan
Write-Host "Admin Password: admin123" -ForegroundColor Cyan
Write-Host ""
Write-Host "Test the deployment:" -ForegroundColor Yellow
Write-Host "curl http://$DropletIP/api/health" -ForegroundColor White
