# Frontend Deployment to DigitalOcean - Quick Guide

## Automated Deployment Script

### Basic Usage (Recommended)
```powershell
# From project root directory
.\deploy-frontend-to-droplet.ps1 -RestartContainers
```

This will:
1. ✅ Build the React frontend
2. ✅ Create compressed archive
3. ✅ Transfer to droplet (209.38.226.32)
4. ✅ Extract files on server
5. ✅ Restart backend container
6. ✅ Test deployment

### Advanced Options

**Use existing build (skip rebuild):**
```powershell
.\deploy-frontend-to-droplet.ps1 -SkipBuild -RestartContainers
```

**Deploy without restarting containers:**
```powershell
.\deploy-frontend-to-droplet.ps1
```

**Custom droplet IP:**
```powershell
.\deploy-frontend-to-droplet.ps1 -DropletIP "192.168.1.100" -RestartContainers
```

**Custom paths:**
```powershell
.\deploy-frontend-to-droplet.ps1 `
    -FrontendBuildPath "C:\custom\path\build" `
    -DropletPath "/opt/astroaxis/frontend" `
    -RestartContainers
```

## Prerequisites

### 1. SSH Key Authentication (Required)

The script requires passwordless SSH access. Set it up once:

**Generate SSH key (if you don't have one):**
```powershell
ssh-keygen -t rsa -b 4096 -f "$env:USERPROFILE\.ssh\id_rsa" -N '""'
```

**Copy public key to droplet:**
```powershell
type "$env:USERPROFILE\.ssh\id_rsa.pub" | ssh root@209.38.226.32 "cat >> ~/.ssh/authorized_keys"
```

**Test connection:**
```powershell
ssh root@209.38.226.32 "echo 'SSH connected!'"
```

### 2. OpenSSH Client (Windows 10/11)

Ensure OpenSSH is installed:
```powershell
# Check if installed
Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Client*'

# Install if needed (run as Administrator)
Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
```

### 3. Frontend Build Dependencies

Ensure Node.js and npm are installed locally:
```powershell
node --version   # Should show v18+
npm --version    # Should show v10+
```

## Troubleshooting

### Error: "SSH connection failed"
**Solution:** Set up SSH key authentication (see Prerequisites above)

### Error: "Build directory not found"
**Solution:** Remove `-SkipBuild` flag to build first, or verify path

### Error: "Transfer failed"
**Solution:** 
- Check internet connection
- Verify droplet is running: `ping 209.38.226.32`
- Check SSH port is open: `Test-NetConnection -ComputerName 209.38.226.32 -Port 22`

### Error: "Extraction failed"
**Solution:**
- SSH into droplet: `ssh root@209.38.226.32`
- Check disk space: `df -h`
- Check permissions: `ls -la /root/astroaxis_control/frontend/`

## Manual Deployment (Alternative)

If the script fails, deploy manually:

### On Windows:
```powershell
# 1. Build frontend
cd C:\Users\USER\ASTROAXIS\frontend
npm run build

# 2. Create archive
Compress-Archive -Path "build\*" -DestinationPath "$env:TEMP\frontend.zip" -Force

# 3. Transfer to droplet
scp "$env:TEMP\frontend.zip" root@209.38.226.32:/root/
```

### On Droplet (SSH):
```bash
# 4. Extract files
cd /root/astroaxis_control/frontend
rm -rf build.backup
mv build build.backup 2>/dev/null || true
mkdir -p build
unzip -o /root/frontend.zip -d build/

# 5. Restart backend
cd /root/astroaxis_control
docker compose restart backend

# 6. Test
curl http://localhost/api/health
curl -I http://localhost/
```

## Verification Steps

After deployment, verify:

### 1. Check files on droplet
```bash
ssh root@209.38.226.32 "ls -lh /root/astroaxis_control/frontend/build/"
```

### 2. Test API
```powershell
Invoke-RestMethod -Uri "http://209.38.226.32/api/health" -Method GET
```

### 3. Test frontend
```powershell
# Should return HTML
Invoke-WebRequest -Uri "http://209.38.226.32/" -UseBasicParsing | Select-Object -ExpandProperty Content
```

### 4. Check logs
```bash
ssh root@209.38.226.32 "cd /root/astroaxis_control && docker compose logs --tail=50 backend"
```

## Deployment Workflow

### Development Cycle
1. Make frontend changes locally
2. Test locally: `npm start` (http://localhost:3000)
3. Build: `npm run build`
4. Deploy: `.\deploy-frontend-to-droplet.ps1 -RestartContainers`
5. Verify: http://209.38.226.32

### Quick Updates (no rebuild)
```powershell
.\deploy-frontend-to-droplet.ps1 -SkipBuild -RestartContainers
```

### Full Redeployment
```powershell
# Stop containers
ssh root@209.38.226.32 "cd /root/astroaxis_control && docker compose down"

# Deploy fresh
.\deploy-frontend-to-droplet.ps1 -RestartContainers

# Rebuild database if needed
ssh root@209.38.226.32 "cd /root/astroaxis_control && docker compose exec backend alembic upgrade head"
```

## Environment Variables

Edit `.env` on droplet if needed:
```bash
ssh root@209.38.226.32 "cd /root/astroaxis_control && nano .env"
```

Required variables:
- `DB_PASSWORD` - PostgreSQL password
- `SECRET_KEY` - JWT secret key
- `DEBUG` - Set to `False` for production
- `ENVIRONMENT` - Set to `production`

## Performance Tips

### 1. Optimize Build Size
```powershell
# Check build size
Get-ChildItem -Path "C:\Users\USER\ASTROAXIS\frontend\build" -Recurse | 
    Measure-Object -Property Length -Sum | 
    Select-Object @{Name="SizeMB";Expression={[math]::Round($_.Sum/1MB,2)}}
```

### 2. Enable Compression on Backend
Already configured in FastAPI's `StaticFiles` middleware.

### 3. Monitor Transfer Speed
```powershell
# Use -v flag for verbose SCP
scp -v "$env:TEMP\frontend.zip" root@209.38.226.32:/root/
```

## Security Notes

1. **SSH Keys**: Store private keys securely (`$env:USERPROFILE\.ssh\id_rsa`)
2. **Secrets**: Never commit `.env` files to Git
3. **Firewall**: Ensure droplet firewall allows ports 22 (SSH) and 80 (HTTP)
4. **HTTPS**: Consider adding SSL/TLS for production (Let's Encrypt)

## Support

For issues or questions:
1. Check logs: `docker compose logs backend`
2. Test health endpoint: `curl http://localhost/api/health`
3. Verify container status: `docker compose ps`
4. Check disk space: `df -h`
5. Review build output for errors

## Script Parameters Reference

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `-DropletIP` | string | 209.38.226.32 | Droplet IP address |
| `-DropletUser` | string | root | SSH username |
| `-FrontendBuildPath` | string | .\frontend\build | Local build directory |
| `-DropletPath` | string | /root/astroaxis_control/frontend | Remote directory |
| `-SkipBuild` | switch | false | Skip npm build step |
| `-RestartContainers` | switch | false | Restart Docker containers after deployment |

## Next Steps

After successful deployment:
1. ✅ Configure domain name (optional)
2. ✅ Set up SSL certificate (Let's Encrypt)
3. ✅ Configure backup schedule
4. ✅ Set up monitoring (UptimeRobot, etc.)
5. ✅ Configure CI/CD pipeline (GitHub Actions)
