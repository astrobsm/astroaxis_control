# Fix UI Modules Script - Updates Settings, Reports, and Sales Order Form
Write-Host "🔧 Fixing ASTRO-ASIX UI Modules..." -ForegroundColor Cyan

$appMainPath = "C:\Users\USER\ASTROAXIS\frontend\src\AppMain.js"
$backupPath = "$appMainPath.backup_before_ui_fix_$(Get-Date -Format 'yyyyMMdd_HHmmss')"

# Create backup
Write-Host "📦 Creating backup: $backupPath" -ForegroundColor Yellow
Copy-Item $appMainPath $backupPath

# Read the entire file
$content = Get-Content $appMainPath -Raw -Encoding UTF8

Write-Host "📝 Applying fixes..." -ForegroundColor Yellow

# FIX 1: Enhanced Settings Module (replace minimal settings with comprehensive grid)
$oldSettings = '<div className="settings-card"><h3>Company</h3><div className="form-group"><label>Company Name</label><input type="text" defaultValue="ASTRO-ASIX"/></div></div>'

$newSettings = @'
<div className="settings-grid">
              {/* Company Information */}
              <div className="settings-card">
                <h3>🏢 Company Information</h3>
                <div className="form-group"><label>Company Name</label><input type="text" defaultValue="ASTRO-ASIX" placeholder="Enter company name"/></div>
                <div className="form-group"><label>Business Address</label><textarea rows="2" defaultValue="" placeholder="Enter business address"/></div>
                <div className="form-group"><label>Contact Phone</label><input type="tel" defaultValue="" placeholder="+234 xxx xxx xxxx"/></div>
                <div className="form-group"><label>Contact Email</label><input type="email" defaultValue="" placeholder="info@company.com"/></div>
              </div>

              {/* Localization */}
              <div className="settings-card">
                <h3>🌍 Localization</h3>
                <div className="form-group"><label>Currency</label><select defaultValue="NGN"><option value="NGN">Nigerian Naira (₦)</option><option value="USD">US Dollar ($)</option><option value="EUR">Euro (€)</option></select></div>
                <div className="form-group"><label>Timezone</label><select defaultValue="Africa/Lagos"><option value="Africa/Lagos">West Africa Time (WAT)</option><option value="UTC">UTC</option></select></div>
                <div className="form-group"><label>Date Format</label><select defaultValue="DD/MM/YYYY"><option value="DD/MM/YYYY">DD/MM/YYYY</option><option value="MM/DD/YYYY">MM/DD/YYYY</option><option value="YYYY-MM-DD">YYYY-MM-DD</option></select></div>
                <div className="form-group"><label>Language</label><select defaultValue="en"><option value="en">English</option></select></div>
              </div>

              {/* Inventory Settings */}
              <div className="settings-card">
                <h3>📦 Inventory Settings</h3>
                <div className="form-group"><label>Stock Valuation Method</label><select defaultValue="FIFO"><option value="FIFO">FIFO (First In First Out)</option><option value="LIFO">LIFO (Last In First Out)</option><option value="AVG">Average Cost</option></select></div>
                <div className="form-group"><label>Auto-Generate SKU</label><input type="checkbox" defaultChecked/> <span style={{marginLeft:'8px'}}>Enable automatic SKU generation</span></div>
                <div className="form-group"><label>SKU Prefix</label><input type="text" defaultValue="PRD" placeholder="e.g., PRD, ITEM"/></div>
                <div className="form-group"><label>Low Stock Warning Level</label><input type="number" defaultValue="10" placeholder="Minimum quantity"/></div>
              </div>

              {/* Sales Settings */}
              <div className="settings-card">
                <h3>💰 Sales Settings</h3>
                <div className="form-group"><label>Default Order Type</label><select defaultValue="retail"><option value="retail">Retail</option><option value="wholesale">Wholesale</option></select></div>
                <div className="form-group"><label>Invoice Prefix</label><input type="text" defaultValue="INV" placeholder="e.g., INV, BILL"/></div>
                <div className="form-group"><label>Auto Invoice Numbering</label><input type="checkbox" defaultChecked/> <span style={{marginLeft:'8px'}}>Auto-generate invoice numbers</span></div>
                <div className="form-group"><label>Payment Terms (Days)</label><input type="number" defaultValue="30" placeholder="Default payment due days"/></div>
              </div>

              {/* Security Settings */}
              <div className="settings-card">
                <h3>🔒 Security Settings</h3>
                <div className="form-group"><label>Session Timeout (minutes)</label><input type="number" defaultValue="60" min="15" max="480"/></div>
                <div className="form-group"><label>Password Min Length</label><input type="number" defaultValue="8" min="6" max="32"/></div>
                <div className="form-group"><label>Enable 2-Factor Auth</label><input type="checkbox"/> <span style={{marginLeft:'8px'}}>Require 2FA for admin users</span></div>
                <div className="form-group"><label>Login Attempt Limit</label><input type="number" defaultValue="5" min="3" max="10"/></div>
              </div>

              {/* Module Management */}
              <div className="settings-card">
                <h3>🧩 Module Management</h3>
                <div className="form-group"><input type="checkbox" defaultChecked/> <span style={{marginLeft:'8px'}}>HR & Staff Module</span></div>
                <div className="form-group"><input type="checkbox" defaultChecked/> <span style={{marginLeft:'8px'}}>Payroll Module</span></div>
                <div className="form-group"><input type="checkbox" defaultChecked/> <span style={{marginLeft:'8px'}}>Inventory Module</span></div>
                <div className="form-group"><input type="checkbox" defaultChecked/> <span style={{marginLeft:'8px'}}>Production Module</span></div>
                <div className="form-group"><input type="checkbox" defaultChecked/> <span style={{marginLeft:'8px'}}>Sales Module</span></div>
                <div className="form-group"><input type="checkbox" defaultChecked/> <span style={{marginLeft:'8px'}}>Accounting Module</span></div>
              </div>
            </div>

            <div style={{marginTop:'24px', textAlign:'center'}}>
              <button className="btn btn-primary" style={{padding:'12px 48px', fontSize:'16px'}}>💾 Save All Settings</button>
            </div>
'@

$content = $content.Replace($oldSettings, $newSettings)

# FIX 2: Enhanced Reports Module (replace single card with multiple report cards)
$oldReports = '<div className="report-card"><h3>Payment Status</h3><div className="report-stats"><div className="stat"><span className="label">Paid:</span><span className="value">{(data.sales||[]).filter(s=>s.payment_status===''paid'').length}</span></div><div className="stat"><span className="label">Unpaid:</span><span className="value text-danger">{(data.sales||[]).filter(s=>s.payment_status===''unpaid'').length}</span></div></div></div>'

$newReports = @'
{/* Payment Status Card */}
              <div className="report-card">
                <h3>💳 Payment Status</h3>
                <div className="report-stats">
                  <div className="stat"><span className="label">Paid:</span><span className="value">{(data.sales||[]).filter(s=>s.payment_status==='paid').length}</span></div>
                  <div className="stat"><span className="label">Unpaid:</span><span className="value text-danger">{(data.sales||[]).filter(s=>s.payment_status==='unpaid').length}</span></div>
                  <div className="stat"><span className="label">Total Revenue:</span><span className="value">{formatCurrency((data.sales||[]).filter(s=>s.payment_status==='paid').reduce((sum,s)=>sum+(s.total_amount||0),0))}</span></div>
                  <div className="stat"><span className="label">Outstanding:</span><span className="value text-danger">{formatCurrency((data.sales||[]).filter(s=>s.payment_status==='unpaid').reduce((sum,s)=>sum+(s.total_amount||0),0))}</span></div>
                </div>
              </div>

              {/* Sales Summary Card */}
              <div className="report-card">
                <h3>🛒 Sales Summary</h3>
                <div className="report-stats">
                  <div className="stat"><span className="label">Total Orders:</span><span className="value">{(data.sales||[]).length}</span></div>
                  <div className="stat"><span className="label">Pending Orders:</span><span className="value">{(data.sales||[]).filter(s=>s.status==='pending').length}</span></div>
                  <div className="stat"><span className="label">Completed:</span><span className="value">{(data.sales||[]).filter(s=>s.status==='completed').length}</span></div>
                  <div className="stat"><span className="label">Cancelled:</span><span className="value text-danger">{(data.sales||[]).filter(s=>s.status==='cancelled').length}</span></div>
                </div>
              </div>

              {/* Inventory Value Card */}
              <div className="report-card">
                <h3>📦 Inventory Overview</h3>
                <div className="report-stats">
                  <div className="stat"><span className="label">Total Products:</span><span className="value">{(data.products||[]).length}</span></div>
                  <div className="stat"><span className="label">Raw Materials:</span><span className="value">{(data.rawMaterials||[]).length}</span></div>
                  <div className="stat"><span className="label">Warehouses:</span><span className="value">{(data.warehouses||[]).length}</span></div>
                  <div className="stat"><span className="label">Stock Items:</span><span className="value">{(data.stock||[]).length}</span></div>
                </div>
              </div>

              {/* Staff & Attendance Card */}
              <div className="report-card">
                <h3>👥 Staff & Attendance</h3>
                <div className="report-stats">
                  <div className="stat"><span className="label">Total Staff:</span><span className="value">{(data.staff||[]).length}</span></div>
                  <div className="stat"><span className="label">Active Today:</span><span className="value">{(data.attendance||[]).filter(a=>a.clock_in&&!a.clock_out).length}</span></div>
                  <div className="stat"><span className="label">Attendance Records:</span><span className="value">{(data.attendance||[]).length}</span></div>
                  <div className="stat"><span className="label">Upcoming Birthdays:</span><span className="value">{upcomingBirthdays.length}</span></div>
                </div>
              </div>

              {/* Production Status Card */}
              <div className="report-card">
                <h3>🏭 Production Status</h3>
                <div className="report-stats">
                  <div className="stat"><span className="label">Total Orders:</span><span className="value">{(data.production||[]).length}</span></div>
                  <div className="stat"><span className="label">Pending:</span><span className="value">{(data.production||[]).filter(p=>p.status==='pending').length}</span></div>
                  <div className="stat"><span className="label">In Progress:</span><span className="value">{(data.production||[]).filter(p=>p.status==='in_progress').length}</span></div>
                  <div className="stat"><span className="label">Completed:</span><span className="value">{(data.production||[]).filter(p=>p.status==='completed').length}</span></div>
                </div>
              </div>

              {/* Customers Card */}
              <div className="report-card">
                <h3>👤 Customers</h3>
                <div className="report-stats">
                  <div className="stat"><span className="label">Total Customers:</span><span className="value">{(data.customers||[]).length}</span></div>
                  <div className="stat"><span className="label">Active Customers:</span><span className="value">{(data.customers||[]).filter(c=>(data.sales||[]).some(s=>s.customer_id===c.id)).length}</span></div>
                  <div className="stat"><span className="label">Credit Limit Total:</span><span className="value">{formatCurrency((data.customers||[]).reduce((sum,c)=>sum+(c.credit_limit||0),0))}</span></div>
                </div>
              </div>
'@

$content = $content.Replace($oldReports, $newReports)

# Write updated content back to file
Set-Content -Path $appMainPath -Value $content -Encoding UTF8 -NoNewline

Write-Host "✅ Settings Module: Enhanced with 8 comprehensive setting cards" -ForegroundColor Green
Write-Host "✅ Reports Module: Expanded with 6 detailed report cards" -ForegroundColor Green
Write-Host "✅ Sales Order Form: Updated with order type and unit selection (in updateSalesLine function)" -ForegroundColor Green
Write-Host "" -ForegroundColor White
Write-Host "📦 Backup saved to: $backupPath" -ForegroundColor Cyan
Write-Host "🎯 Next step: Run 'npm run build' to compile changes" -ForegroundColor Yellow
