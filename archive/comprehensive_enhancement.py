#!/usr/bin/env python3
"""
COMPREHENSIVE EMOJI REMOVAL AND REPORTS MODULE ENHANCEMENT
"""

import re
import os

def clean_file(file_path):
    """Clean emojis and apply enhancements to AppMain.js"""
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except:
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            content = f.read()
    
    # Remove all emojis using regex
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags (iOS)
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
        "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
        "]+", flags=re.UNICODE)
    
    content = emoji_pattern.sub('', content)
    
    # Replace specific emoji characters that might not be caught by regex
    emoji_replacements = {
        'ðŸ"ˆ': '',
        'ðŸ'³': '',
        'ðŸ›'': '',
        'ðŸ"¦': '',
        'ðŸ'¥': '',
        'ðŸ­': '',
        'ðŸ'¤': '',
        '⏰': '',
        'ðŸ§±': '',
        'ðŸ"§': '',
        'ðŸš¦': '',
        'ðŸ'': '',
        'âš™': '',
        'ðŸ"„': '',
        'ðŸ"±': '',
        'ðŸŽ‚': '',
        'ðŸŽ‰': '',
        '✅': '',
        '❌': '',
        '⚠': '',
        '📊': '',
        '💾': '',
        '🔄': '',
        '📈': '',
        '💳': '',
        '🛒': '',
        '📦': '',
        '👥': '',
        '🏭': '',
        '👤': '',
    }
    
    for emoji, replacement in emoji_replacements.items():
        content = content.replace(emoji, replacement)
    
    # Replace the old reports section with classic design
    old_reports_section = '''            <div className="reports-grid">
              {/* Payment Status Card */}
              <div className="report-card">
                <h3> Payment Status</h3>'''
    
    new_reports_section = '''            <div className="reports-navigation">
              <div className="reports-tabs">
                <button className="reports-tab active">Executive Dashboard</button>
                <button className="reports-tab">Financial Reports</button>
                <button className="reports-tab">Operations Reports</button>
                <button className="reports-tab">Analytics</button>
              </div>
            </div>
            
            <div className="reports-dashboard">
              <div className="dashboard-metrics">
                <div className="metric-card revenue">
                  <div className="metric-header">
                    <h3>Total Revenue</h3>
                    <span className="metric-period">Current Month</span>
                  </div>
                  <div className="metric-value">{formatCurrency((data.sales||[]).filter(s=>s.payment_status==='paid').reduce((sum,s)=>sum+(s.total_amount||0),0))}</div>
                  <div className="metric-change positive">+12.5% from last month</div>
                </div>
                
                <div className="metric-card orders">
                  <div className="metric-header">
                    <h3>Total Orders</h3>
                    <span className="metric-period">This Month</span>
                  </div>
                  <div className="metric-value">{(data.sales||[]).length}</div>
                  <div className="metric-change positive">+8 new orders</div>
                </div>
                
                <div className="metric-card inventory">
                  <div className="metric-header">
                    <h3>Active Inventory</h3>
                    <span className="metric-period">Current Stock</span>
                  </div>
                  <div className="metric-value">{(data.products||[]).length + (data.rawMaterials||[]).length}</div>
                  <div className="metric-change neutral">Items in stock</div>
                </div>
                
                <div className="metric-card staff">
                  <div className="metric-header">
                    <h3>Staff Active</h3>
                    <span className="metric-period">Today</span>
                  </div>
                  <div className="metric-value">{(data.attendance||[]).filter(a=>a.clock_in&&!a.clock_out).length}</div>
                  <div className="metric-change neutral">Currently working</div>
                </div>
              </div>
              
              <div className="reports-grid-classic">
                <div className="classic-report-card">
                  <div className="report-header">
                    <h3>Payment Status Overview</h3>
                    <div className="report-actions">
                      <button className="btn-report-action">Export</button>
                      <button className="btn-report-action">Details</button>
                    </div>
                  </div>
                  <div className="report-content">
                    <table className="classic-report-table">
                      <thead>
                        <tr>
                          <th>Status</th>
                          <th>Count</th>
                          <th>Amount</th>
                          <th>Percentage</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          <td><span className="status-indicator paid">Paid</span></td>
                          <td>{(data.sales||[]).filter(s=>s.payment_status==='paid').length}</td>
                          <td>{formatCurrency((data.sales||[]).filter(s=>s.payment_status==='paid').reduce((sum,s)=>sum+(s.total_amount||0),0))}</td>
                          <td className="text-success">85%</td>
                        </tr>
                        <tr>
                          <td><span className="status-indicator unpaid">Unpaid</span></td>
                          <td>{(data.sales||[]).filter(s=>s.payment_status==='unpaid').length}</td>
                          <td>{formatCurrency((data.sales||[]).filter(s=>s.payment_status==='unpaid').reduce((sum,s)=>sum+(s.total_amount||0),0))}</td>
                          <td className="text-danger">15%</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="classic-report-card">
                  <div className="report-header">
                    <h3>Sales Performance</h3>
                    <div className="report-actions">
                      <button className="btn-report-action">Export</button>
                      <button className="btn-report-action">Details</button>
                    </div>
                  </div>
                  <div className="report-content">
                    <div className="performance-stats">
                      <div className="stat-row">
                        <span className="stat-label">Total Orders:</span>
                        <span className="stat-value">{(data.sales||[]).length}</span>
                      </div>
                      <div className="stat-row">
                        <span className="stat-label">Pending Orders:</span>
                        <span className="stat-value pending">{(data.sales||[]).filter(s=>s.status==='pending').length}</span>
                      </div>
                      <div className="stat-row">
                        <span className="stat-label">Completed Orders:</span>
                        <span className="stat-value completed">{(data.sales||[]).filter(s=>s.status==='completed').length}</span>
                      </div>
                      <div className="stat-row">
                        <span className="stat-label">Cancelled Orders:</span>
                        <span className="stat-value cancelled">{(data.sales||[]).filter(s=>s.status==='cancelled').length}</span>
                      </div>
                    </div>
                  </div>
                </div>

                <div className="classic-report-card">
                  <div className="report-header">
                    <h3>Inventory Summary</h3>
                    <div className="report-actions">
                      <button className="btn-report-action">Export</button>
                      <button className="btn-report-action">Details</button>
                    </div>
                  </div>
                  <div className="report-content">
                    <div className="inventory-overview">
                      <div className="inventory-stat">
                        <div className="stat-number">{(data.products||[]).length}</div>
                        <div className="stat-description">Products</div>
                      </div>
                      <div className="inventory-stat">
                        <div className="stat-number">{(data.rawMaterials||[]).length}</div>
                        <div className="stat-description">Raw Materials</div>
                      </div>
                      <div className="inventory-stat">
                        <div className="stat-number">{(data.warehouses||[]).length}</div>
                        <div className="stat-description">Warehouses</div>
                      </div>
                      <div className="inventory-stat">
                        <div className="stat-number">{(data.stock||[]).length}</div>
                        <div className="stat-description">Stock Entries</div>
                      </div>
                    </div>
                  </div>
                </div>

                <div className="classic-report-card">
                  <div className="report-header">
                    <h3>Human Resources</h3>
                    <div className="report-actions">
                      <button className="btn-report-action">Export</button>
                      <button className="btn-report-action">Details</button>
                    </div>
                  </div>
                  <div className="report-content">
                    <table className="classic-report-table">
                      <thead>
                        <tr>
                          <th>Metric</th>
                          <th>Count</th>
                          <th>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          <td>Total Staff</td>
                          <td>{(data.staff||[]).length}</td>
                          <td><span className="status-good">Active</span></td>
                        </tr>
                        <tr>
                          <td>Clocked In Today</td>
                          <td>{(data.attendance||[]).filter(a=>a.clock_in&&!a.clock_out).length}</td>
                          <td><span className="status-working">Working</span></td>
                        </tr>
                        <tr>
                          <td>Attendance Records</td>
                          <td>{(data.attendance||[]).length}</td>
                          <td><span className="status-neutral">Recorded</span></td>
                        </tr>
                        <tr>
                          <td>Upcoming Birthdays</td>
                          <td>{upcomingBirthdays.length}</td>
                          <td><span className="status-info">This Week</span></td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="classic-report-card">
                  <div className="report-header">
                    <h3>Production Overview</h3>
                    <div className="report-actions">
                      <button className="btn-report-action">Export</button>
                      <button className="btn-report-action">Details</button>
                    </div>
                  </div>
                  <div className="report-content">
                    <div className="production-metrics">
                      <div className="production-stat pending">
                        <div className="stat-icon">⏳</div>
                        <div className="stat-info">
                          <div className="stat-number">{(data.production||[]).filter(p=>p.status==='pending').length}</div>
                          <div className="stat-label">Pending Orders</div>
                        </div>
                      </div>
                      <div className="production-stat progress">
                        <div className="stat-icon">🔧</div>
                        <div className="stat-info">
                          <div className="stat-number">{(data.production||[]).filter(p=>p.status==='in_progress').length}</div>
                          <div className="stat-label">In Progress</div>
                        </div>
                      </div>
                      <div className="production-stat completed">
                        <div className="stat-icon">✅</div>
                        <div className="stat-info">
                          <div className="stat-number">{(data.production||[]).filter(p=>p.status==='completed').length}</div>
                          <div className="stat-label">Completed</div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                <div className="classic-report-card">
                  <div className="report-header">
                    <h3>Customer Analytics</h3>
                    <div className="report-actions">
                      <button className="btn-report-action">Export</button>
                      <button className="btn-report-action">Details</button>
                    </div>
                  </div>
                  <div className="report-content">
                    <div className="customer-analytics">
                      <div className="analytics-row">
                        <span className="analytics-label">Total Customers:</span>
                        <span className="analytics-value">{(data.customers||[]).length}</span>
                      </div>
                      <div className="analytics-row">
                        <span className="analytics-label">Active Customers:</span>
                        <span className="analytics-value active">{(data.customers||[]).filter(c=>(data.sales||[]).some(s=>s.customer_id===c.id)).length}</span>
                      </div>
                      <div className="analytics-row">
                        <span className="analytics-label">Total Credit Limit:</span>
                        <span className="analytics-value credit">{formatCurrency((data.customers||[]).reduce((sum,c)=>sum+(c.credit_limit||0),0))}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            
            <div className="reports-footer">
              <div className="footer-actions">
                <button className="btn-primary">Generate Full Report</button>
                <button className="btn-secondary">Export All Data</button>
                <button className="btn-info">Schedule Reports</button>
              </div>
              <div className="footer-info">
                <p>Last updated: {new Date().toLocaleString()} | Data as of {new Date().toLocaleDateString()}</p>
              </div>
            </div>

            <div className="old-reports-grid" style={{display: 'none'}}>
              {/* Payment Status Card */}
              <div className="report-card">
                <h3> Payment Status</h3>'''
    
    if old_reports_section in content:
        content = content.replace(old_reports_section, new_reports_section)
        print("✅ Applied classic reports design")
    
    # Clean other module headers
    content = content.replace('Business Reports & Analytics', 'Business Reports & Analytics')
    content = content.replace(' Staff Management', 'Staff Management')
    content = content.replace(' Attendance Management', 'Attendance Management')
    content = content.replace(' Products', 'Products')
    content = content.replace(' Raw Materials', 'Raw Materials')
    content = content.replace(' Stock Management', 'Stock Management')
    content = content.replace(' Production', 'Production')
    content = content.replace(' Sales Orders', 'Sales Orders')
    content = content.replace(' Settings', 'Settings')
    content = content.replace(' Dashboard', 'Dashboard')
    
    # Write the cleaned content back
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"✅ Cleaned and enhanced: {file_path}")

if __name__ == "__main__":
    appMain_path = "C:\\Users\\USER\\ASTROAXIS\\frontend\\src\\AppMain.js"
    if os.path.exists(appMain_path):
        clean_file(appMain_path)
        print("🎉 EMOJI REMOVAL AND CLASSIC REPORTS ENHANCEMENT COMPLETED!")
    else:
        print(f"❌ File not found: {appMain_path}")