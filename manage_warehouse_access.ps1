# Warehouse Access Management Script for ASTRO-AXIS ERP
# Usage: .\manage_warehouse_access.ps1

param(
    [Parameter(Mandatory=$false)]
    [string]$Action = "list"  # list, grant, revoke
)

$server = "root@209.38.226.32"

function Show-Users {
    Write-Host "`n=== USERS ===" -ForegroundColor Cyan
    ssh $server 'docker exec astroaxis_db psql -U postgres -d axis_db -c "SELECT id, email, full_name, role FROM users ORDER BY full_name;"'
}

function Show-Warehouses {
    Write-Host "`n=== WAREHOUSES ===" -ForegroundColor Cyan
    ssh $server 'docker exec astroaxis_db psql -U postgres -d axis_db -c "SELECT id, code, name, location FROM warehouses ORDER BY name;"'
}

function Show-Access {
    Write-Host "`n=== CURRENT WAREHOUSE ACCESS ===" -ForegroundColor Cyan
    ssh $server 'docker exec astroaxis_db psql -U postgres -d axis_db -c "SELECT u.full_name, u.email, u.role, w.name as warehouse, w.code FROM user_warehouses uw JOIN users u ON uw.user_id = u.id JOIN warehouses w ON uw.warehouse_id = w.id ORDER BY u.full_name, w.name;"'
}

function Grant-Access {
    param([string]$UserEmail, [string]$WarehouseCode)
    
    Write-Host "`nGranting access for user: $UserEmail to warehouse: $WarehouseCode" -ForegroundColor Yellow
    
    $sql = "INSERT INTO user_warehouses (user_id, warehouse_id) 
            SELECT u.id, w.id 
            FROM users u, warehouses w 
            WHERE u.email = '$UserEmail' AND w.code = '$WarehouseCode' 
            AND NOT EXISTS (
                SELECT 1 FROM user_warehouses uw 
                WHERE uw.user_id = u.id AND uw.warehouse_id = w.id
            );"
    
    ssh $server "docker exec astroaxis_db psql -U postgres -d axis_db -c `"$sql`""
    Write-Host "Access granted!" -ForegroundColor Green
}

function Revoke-Access {
    param([string]$UserEmail, [string]$WarehouseCode)
    
    Write-Host "`nRevoking access for user: $UserEmail from warehouse: $WarehouseCode" -ForegroundColor Yellow
    
    $sql = "DELETE FROM user_warehouses 
            WHERE user_id = (SELECT id FROM users WHERE email = '$UserEmail') 
            AND warehouse_id = (SELECT id FROM warehouses WHERE code = '$WarehouseCode');"
    
    ssh $server "docker exec astroaxis_db psql -U postgres -d axis_db -c `"$sql`""
    Write-Host "Access revoked!" -ForegroundColor Green
}

# Main Menu
Write-Host "`n╔════════════════════════════════════════════════════════╗" -ForegroundColor Magenta
Write-Host "║   ASTRO-AXIS ERP - Warehouse Access Management        ║" -ForegroundColor Magenta
Write-Host "╚════════════════════════════════════════════════════════╝" -ForegroundColor Magenta

switch ($Action.ToLower()) {
    "list" {
        Show-Users
        Show-Warehouses
        Show-Access
        
        Write-Host "`n=== USAGE ===" -ForegroundColor Yellow
        Write-Host "Grant access:  .\manage_warehouse_access.ps1 -Action grant"
        Write-Host "Revoke access: .\manage_warehouse_access.ps1 -Action revoke"
    }
    
    "grant" {
        Show-Users
        Show-Warehouses
        
        Write-Host "`n"
        $email = Read-Host "Enter user email"
        $code = Read-Host "Enter warehouse code (e.g., WH-001)"
        
        Grant-Access -UserEmail $email -WarehouseCode $code
        
        Write-Host "`nUpdated access:" -ForegroundColor Cyan
        Show-Access
    }
    
    "revoke" {
        Show-Access
        
        Write-Host "`n"
        $email = Read-Host "Enter user email"
        $code = Read-Host "Enter warehouse code (e.g., WH-001)"
        
        Revoke-Access -UserEmail $email -WarehouseCode $code
        
        Write-Host "`nUpdated access:" -ForegroundColor Cyan
        Show-Access
    }
    
    default {
        Write-Host "Invalid action. Use: list, grant, or revoke" -ForegroundColor Red
    }
}

Write-Host ""
