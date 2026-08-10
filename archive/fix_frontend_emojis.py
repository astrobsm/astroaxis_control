#!/usr/bin/env python3
"""
Clean script to remove emojis and apply classic reports design to ASTRO-ASIX frontend
"""
import re
import os
import shutil

def backup_file(file_path):
    """Create a backup of the file"""
    backup_path = f"{file_path}.backup_{os.getpid()}"
    shutil.copy2(file_path, backup_path)
    print(f"Created backup: {backup_path}")
    return backup_path

def remove_emojis_from_file(file_path):
    """Remove all emojis from a JavaScript file"""
    print(f"Processing: {file_path}")
    
    # Read the file
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Define emoji patterns and their replacements
    emoji_replacements = {
        # Reports module emojis
        r'📈\s*Reports': 'Reports',
        r'💳\s*Payment Status': 'Payment Status',
        r'🛒\s*Sales Summary': 'Sales Summary',
        r'📦\s*Inventory Overview': 'Inventory Overview',
        r'👥\s*Staff & Attendance': 'Staff & Attendance',
        r'🏭\s*Production Status': 'Production Status',
        r'👤\s*Customers': 'Customers',
        
        # Navigation and other emojis
        r'📊\s*Dashboard': 'Dashboard',
        r'👥\s*Staff': 'Staff',
        r'📋\s*Attendance': 'Attendance',
        r'💰\s*Payroll': 'Payroll',
        r'📦\s*Products': 'Products',
        r'🏪\s*Sales': 'Sales',
        r'🏭\s*Production': 'Production',
        r'📈\s*Reports': 'Reports',
        r'⚙️\s*Settings': 'Settings',
        r'🚪\s*Logout': 'Logout',
        
        # Form and action emojis
        r'➕\s*Add': '+ Add',
        r'✏️\s*Edit': 'Edit',
        r'🗑️\s*Delete': 'Delete',
        r'👀\s*View': 'View',
        r'💾\s*Save': 'Save',
        r'❌\s*Cancel': 'Cancel',
        r'🔍\s*Search': 'Search',
        r'📄\s*Print': 'Print',
        r'📥\s*Download': 'Download',
        
        # Status emojis
        r'✅\s*Active': 'Active',
        r'❌\s*Inactive': 'Inactive',
        r'⏳\s*Pending': 'Pending',
        r'✔️\s*Completed': 'Completed',
        r'🔄\s*In Progress': 'In Progress',
        
        # General emoji cleanup
        r'[📈💳🛒📦👥🏭👤📊📋💰🏪⚙️🚪➕✏️🗑️👀💾❌🔍📄📥✅⏳✔️🔄🎉🎊🎈🎁🎀🎯🎲🎳🎮🎰🎱🎴🎺🎸🎷🎵🎶🎼🎤🎧🎬🎭🎨🎪🎫🎟️🎠🎡🎢🎣🎖️🎗️🎞️🎥🎦🎧🎨🎩🎪🎫🎬🎭🎮🎯🎰🎱🎲🎳🎴🎵🎶🎷🎸🎹🎺🎻🎼🎽🎾🎿🏀🏁🏂🏃🏄🏅🏆🏇🏈🏉🏊🏋️🏌️🏍️🏎️🏏🏐🏑🏒🏓🏔️🏕️🏖️🏗️🏘️🏙️🏚️🏛️🏜️🏝️🏞️🏟️🏠🏡🏢🏣🏤🏥🏦🏧🏨🏩🏪🏫🏬🏭🏮🏯🏰]': '',
    }
    
    # Apply replacements
    for pattern, replacement in emoji_replacements.items():
        content = re.sub(pattern, replacement, content, flags=re.UNICODE)
    
    # Remove any remaining isolated emojis
    emoji_pattern = r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002702-\U000027B0\U000024C2-\U0001F251]+'
    content = re.sub(emoji_pattern, '', content)
    
    # Write back to file
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"Emojis removed from: {file_path}")

def apply_classic_reports_design(file_path):
    """Apply classic professional reports module design"""
    print(f"Applying classic reports design to: {file_path}")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Find and replace the reports module section
    reports_pattern = r'(case \'reports\':\s*return\s*\(\s*<div[^>]*className="reports-module"[^>]*>)(.*?)(<\/div>\s*\);)'
    
    classic_reports_html = '''
        <div className="module-header">
          <h2>Business Intelligence Reports</h2>
          <p className="module-subtitle">Comprehensive business analytics and reporting</p>
        </div>
        
        <div className="reports-grid">
          <div className="report-section">
            <div className="section-header">
              <h3>Financial Analytics</h3>
            </div>
            <div className="report-cards">
              <div className="report-card">
                <div className="card-header">
                  <h4>Payment Status</h4>
                </div>
                <div className="card-content">
                  <div className="metric-row">
                    <span className="metric-label">Paid:</span>
                    <span className="metric-value">0</span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Unpaid:</span>
                    <span className="metric-value">0</span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Total Revenue:</span>
                    <span className="metric-value">₦0.00</span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Outstanding:</span>
                    <span className="metric-value">₦0.00</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="report-section">
            <div className="section-header">
              <h3>Sales Performance</h3>
            </div>
            <div className="report-cards">
              <div className="report-card">
                <div className="card-header">
                  <h4>Sales Summary</h4>
                </div>
                <div className="card-content">
                  <div className="metric-row">
                    <span className="metric-label">Total Orders:</span>
                    <span className="metric-value">0</span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Pending Orders:</span>
                    <span className="metric-value">0</span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Completed:</span>
                    <span className="metric-value">0</span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Cancelled:</span>
                    <span className="metric-value">0</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="report-section">
            <div className="section-header">
              <h3>Inventory Management</h3>
            </div>
            <div className="report-cards">
              <div className="report-card">
                <div className="card-header">
                  <h4>Inventory Overview</h4>
                </div>
                <div className="card-content">
                  <div className="metric-row">
                    <span className="metric-label">Total Products:</span>
                    <span className="metric-value">2</span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Raw Materials:</span>
                    <span className="metric-value">2</span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Warehouses:</span>
                    <span className="metric-value">1</span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Stock Items:</span>
                    <span className="metric-value">2</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="report-section">
            <div className="section-header">
              <h3>Human Resources</h3>
            </div>
            <div className="report-cards">
              <div className="report-card">
                <div className="card-header">
                  <h4>Staff & Attendance</h4>
                </div>
                <div className="card-content">
                  <div className="metric-row">
                    <span className="metric-label">Total Staff:</span>
                    <span className="metric-value">1</span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Active Today:</span>
                    <span className="metric-value">1</span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Attendance Records:</span>
                    <span className="metric-value">1</span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Upcoming Birthdays:</span>
                    <span className="metric-value">-</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="report-section">
            <div className="section-header">
              <h3>Production Analytics</h3>
            </div>
            <div className="report-cards">
              <div className="report-card">
                <div className="card-header">
                  <h4>Production Status</h4>
                </div>
                <div className="card-content">
                  <div className="metric-row">
                    <span className="metric-label">Total Orders:</span>
                    <span className="metric-value">0</span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Pending:</span>
                    <span className="metric-value">0</span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">In Progress:</span>
                    <span className="metric-value">0</span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Completed:</span>
                    <span className="metric-value">0</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="report-section">
            <div className="section-header">
              <h3>Customer Relations</h3>
            </div>
            <div className="report-cards">
              <div className="report-card">
                <div className="card-header">
                  <h4>Customers</h4>
                </div>
                <div className="card-content">
                  <div className="metric-row">
                    <span className="metric-label">Total Customers:</span>
                    <span className="metric-value">1</span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Active Customers:</span>
                    <span className="metric-value">0</span>
                  </div>
                  <div className="metric-row">
                    <span className="metric-label">Credit Limit Total:</span>
                    <span className="metric-value">₦200,000.00</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
    '''
    
    if re.search(reports_pattern, content, re.DOTALL):
        content = re.sub(reports_pattern, f'\\1{classic_reports_html}\\3', content, flags=re.DOTALL)
        print("Applied classic reports design")
    else:
        print("Reports section pattern not found")
    
    # Write back to file
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

def main():
    """Main function to process the frontend files"""
    frontend_path = r"C:\Users\USER\ASTROAXIS\frontend\src\AppMain.js"
    
    if not os.path.exists(frontend_path):
        print(f"Error: File not found: {frontend_path}")
        return
    
    # Create backup
    backup_path = backup_file(frontend_path)
    
    try:
        # Remove emojis
        remove_emojis_from_file(frontend_path)
        
        # Apply classic reports design
        apply_classic_reports_design(frontend_path)
        
        print("\n✅ SUCCESS: Frontend enhancement completed!")
        print("- All emojis removed")
        print("- Classic reports design applied")
        print(f"- Backup created: {backup_path}")
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        print(f"Restoring from backup: {backup_path}")
        shutil.copy2(backup_path, frontend_path)

if __name__ == "__main__":
    main()