# ======================================================
# SSH Key Setup for DigitalOcean Droplet
# ======================================================
# Sets up passwordless SSH authentication
# Run this ONCE before using deploy-frontend-to-droplet.ps1
# ======================================================

param(
    [string]$DropletIP = "209.38.226.32",
    [string]$DropletUser = "root"
)

Write-Host "`n=====================================================" -ForegroundColor Cyan
Write-Host "   SSH Key Setup for ASTRO-ASIX Droplet" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "Target: ${DropletUser}@${DropletIP}" -ForegroundColor Yellow
Write-Host "=====================================================`n" -ForegroundColor Cyan

$sshDir = Join-Path $env:USERPROFILE ".ssh"
$privateKey = Join-Path $sshDir "id_rsa"
$publicKey = Join-Path $sshDir "id_rsa.pub"

# Step 1: Check if OpenSSH is installed
Write-Host "[1/4] Checking OpenSSH installation..." -ForegroundColor Green

$sshClient = Get-Command ssh -ErrorAction SilentlyContinue
if ($null -eq $sshClient) {
    Write-Host "✗ OpenSSH not found!" -ForegroundColor Red
    Write-Host "`nInstalling OpenSSH Client..." -ForegroundColor Yellow
    Write-Host "You may need to run PowerShell as Administrator" -ForegroundColor Yellow
    Write-Host "`nRun this command:" -ForegroundColor Cyan
    Write-Host "  Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0" -ForegroundColor White
    exit 1
} else {
    Write-Host "✓ OpenSSH found: $($sshClient.Source)" -ForegroundColor Green
}

# Step 2: Check if SSH directory exists
Write-Host "`n[2/4] Checking SSH directory..." -ForegroundColor Green

if (-not (Test-Path $sshDir)) {
    Write-Host "Creating .ssh directory: $sshDir" -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $sshDir -Force | Out-Null
    # Set proper permissions (only user can read/write)
    $acl = Get-Acl $sshDir
    $acl.SetAccessRuleProtection($true, $false)
    $accessRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $env:USERNAME, "FullControl", "ContainerInherit,ObjectInherit", "None", "Allow"
    )
    $acl.AddAccessRule($accessRule)
    Set-Acl -Path $sshDir -AclObject $acl
    Write-Host "✓ Created .ssh directory" -ForegroundColor Green
} else {
    Write-Host "✓ SSH directory exists: $sshDir" -ForegroundColor Green
}

# Step 3: Generate SSH key if not exists
Write-Host "`n[3/4] Checking SSH key pair..." -ForegroundColor Green

if (Test-Path $privateKey) {
    Write-Host "✓ SSH key already exists: $privateKey" -ForegroundColor Green
    
    # Ask if user wants to use existing or create new
    $response = Read-Host "Use existing key? (Y/N)"
    if ($response -ne "Y" -and $response -ne "y") {
        Write-Host "Generating new SSH key..." -ForegroundColor Yellow
        Write-Host "Your existing key will be backed up" -ForegroundColor Gray
        
        # Backup existing keys
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        Copy-Item $privateKey "$privateKey.backup_$timestamp" -Force
        Copy-Item $publicKey "$publicKey.backup_$timestamp" -Force
        
        # Generate new key
        ssh-keygen -t rsa -b 4096 -f $privateKey -N '""' -C "astroaxis-deploy-$(Get-Date -Format 'yyyyMMdd')"
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host "✓ New SSH key generated" -ForegroundColor Green
        } else {
            Write-Host "✗ Failed to generate SSH key" -ForegroundColor Red
            exit 1
        }
    }
} else {
    Write-Host "No SSH key found. Generating new key..." -ForegroundColor Yellow
    
    ssh-keygen -t rsa -b 4096 -f $privateKey -N '""' -C "astroaxis-deploy-$(Get-Date -Format 'yyyyMMdd')"
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ SSH key generated successfully" -ForegroundColor Green
    } else {
        Write-Host "✗ Failed to generate SSH key" -ForegroundColor Red
        exit 1
    }
}

# Display public key
Write-Host "`nYour public key:" -ForegroundColor Cyan
Get-Content $publicKey | Write-Host -ForegroundColor White

# Step 4: Copy public key to droplet
Write-Host "`n[4/4] Copying public key to droplet..." -ForegroundColor Green
Write-Host "You will be asked for the droplet password" -ForegroundColor Yellow

$publicKeyContent = Get-Content $publicKey -Raw

# Try to copy using ssh-copy-id (if available)
$sshCopyId = Get-Command ssh-copy-id -ErrorAction SilentlyContinue

if ($null -ne $sshCopyId) {
    Write-Host "Using ssh-copy-id..." -ForegroundColor Gray
    ssh-copy-id -i $publicKey "${DropletUser}@${DropletIP}"
} else {
    # Manual method
    Write-Host "Manual key copy (ssh-copy-id not available)..." -ForegroundColor Gray
    Write-Host "Enter droplet password when prompted:" -ForegroundColor Yellow
    
    $command = "mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '$publicKeyContent' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && echo 'Key added successfully'"
    
    ssh "${DropletUser}@${DropletIP}" $command
}

if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ Public key copied to droplet" -ForegroundColor Green
} else {
    Write-Host "✗ Failed to copy key to droplet" -ForegroundColor Red
    Write-Host "`nManual steps:" -ForegroundColor Yellow
    Write-Host "1. SSH into droplet: ssh ${DropletUser}@${DropletIP}" -ForegroundColor White
    Write-Host "2. Run: mkdir -p ~/.ssh && chmod 700 ~/.ssh" -ForegroundColor White
    Write-Host "3. Run: nano ~/.ssh/authorized_keys" -ForegroundColor White
    Write-Host "4. Paste this key and save:" -ForegroundColor White
    Write-Host $publicKeyContent -ForegroundColor Cyan
    Write-Host "5. Run: chmod 600 ~/.ssh/authorized_keys" -ForegroundColor White
    exit 1
}

# Test passwordless SSH
Write-Host "`n[Test] Verifying passwordless SSH..." -ForegroundColor Green

$testResult = ssh -o BatchMode=yes -o ConnectTimeout=5 "${DropletUser}@${DropletIP}" "echo 'SSH key authentication successful!'" 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ $testResult" -ForegroundColor Green
    Write-Host "`n=====================================================" -ForegroundColor Cyan
    Write-Host "           SSH SETUP COMPLETE! ✓" -ForegroundColor Green
    Write-Host "=====================================================" -ForegroundColor Cyan
    Write-Host "`nYou can now deploy without passwords:" -ForegroundColor Yellow
    Write-Host "  .\deploy-frontend-to-droplet.ps1 -RestartContainers" -ForegroundColor White
    Write-Host "`nTest your connection:" -ForegroundColor Yellow
    Write-Host "  ssh ${DropletUser}@${DropletIP}" -ForegroundColor White
    Write-Host "=====================================================`n" -ForegroundColor Cyan
} else {
    Write-Host "✗ Passwordless SSH test failed" -ForegroundColor Red
    Write-Host "You may need to:" -ForegroundColor Yellow
    Write-Host "1. Check if password authentication is disabled on droplet" -ForegroundColor White
    Write-Host "2. Verify the key was added correctly" -ForegroundColor White
    Write-Host "3. Try manual SSH: ssh ${DropletUser}@${DropletIP}" -ForegroundColor White
    exit 1
}
