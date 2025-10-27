import React, { useState, useEffect } from 'react';

function Settings({ currentUser }) {
  const [activeTab, setActiveTab] = useState('general');
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  
  // Permission management state
  const [users, setUsers] = useState([]);
  const [permissions, setPermissions] = useState([]);
  const [modules, setModules] = useState({});
  const [selectedUser, setSelectedUser] = useState(null);
  const [userPermissions, setUserPermissions] = useState([]);

  useEffect(() => {
    fetchSettings();
    if (currentUser && currentUser.role === 'admin') {
      fetchUsers();
      fetchPermissions();
      fetchModules();
    }
  }, [currentUser]);

  const fetchSettings = async () => {
    try {
      const response = await fetch('http://127.0.0.1:8004/api/settings/');
      const data = await response.json();
      setSettings(data);
    } catch (error) {
      console.error('Error fetching settings:', error);
    } finally {
      setLoading(false);
    }
  };

  const fetchUsers = async () => {
    try {
      const response = await fetch('http://127.0.0.1:8004/api/auth/users');
      const data = await response.json();
      setUsers(data);
    } catch (error) {
      console.error('Error fetching users:', error);
    }
  };

  const fetchPermissions = async () => {
    try {
      const response = await fetch('http://127.0.0.1:8004/api/permissions/');
      const data = await response.json();
      setPermissions(data);
    } catch (error) {
      console.error('Error fetching permissions:', error);
    }
  };

  const fetchModules = async () => {
    try {
      const response = await fetch('http://127.0.0.1:8004/api/permissions/modules');
      const data = await response.json();
      setModules(data);
    } catch (error) {
      console.error('Error fetching modules:', error);
    }
  };

  const fetchUserPermissions = async (userId) => {
    try {
      const response = await fetch(`http://127.0.0.1:8004/api/permissions/user/${userId}`);
      const data = await response.json();
      setUserPermissions(data.permissions.map(p => p.id));
    } catch (error) {
      console.error('Error fetching user permissions:', error);
      setUserPermissions([]);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setMessage('');

    try {
      const response = await fetch('http://127.0.0.1:8004/api/settings/', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings),
      });

      const data = await response.json();

      if (response.ok) {
        setMessage('✅ Settings saved successfully!');
        setTimeout(() => setMessage(''), 3000);
      } else {
        throw new Error(data.detail || 'Failed to save settings');
      }
    } catch (error) {
      setMessage('❌ Error: ' + error.message);
    } finally {
      setSaving(false);
    }
  };

  const handleGrantPermissions = async () => {
    if (!selectedUser) return;
    
    setSaving(true);
    setMessage('');

    try {
      const response = await fetch('http://127.0.0.1:8004/api/permissions/grant', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: selectedUser,
          permission_ids: userPermissions
        }),
      });

      const data = await response.json();

      if (response.ok) {
        setMessage('✅ Permissions updated successfully!');
        setTimeout(() => setMessage(''), 3000);
      } else {
        throw new Error(data.detail || 'Failed to update permissions');
      }
    } catch (error) {
      setMessage('❌ Error: ' + error.message);
    } finally {
      setSaving(false);
    }
  };

  const togglePermission = (permId) => {
    if (userPermissions.includes(permId)) {
      setUserPermissions(userPermissions.filter(id => id !== permId));
    } else {
      setUserPermissions([...userPermissions, permId]);
    }
  };

  const handleChange = (field, value) => {
    setSettings({ ...settings, [field]: value });
  };

  if (loading) {
    return <div className="p-6 text-center">Loading settings...</div>;
  }

  const tabs = [
    { id: 'general', label: 'General', icon: '🏢' },
    { id: 'localization', label: 'Localization', icon: '🌍' },
    { id: 'inventory', label: 'Inventory', icon: '📦' },
    { id: 'sales', label: 'Sales', icon: '💰' },
    { id: 'security', label: 'Security', icon: '🔒' },
    { id: 'modules', label: 'Modules', icon: '🧩' },
    ...(currentUser && currentUser.role === 'admin' ? [{ id: 'permissions', label: 'User Permissions', icon: '👥' }] : []),
  ];

  return (
    <div className="settings-container">
      <div className="settings-header">
        <h2>System Settings</h2>
        <p>Configure your ASTRO-ASIX ERP system</p>
      </div>

      {/* Tabs */}
      <div className="settings-tabs">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            className={`tab ${activeTab === tab.id ? 'active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            <span className="tab-icon">{tab.icon}</span>
            <span className="tab-label">{tab.label}</span>
          </button>
        ))}
      </div>

      {/* Tab Content */}
      <div className="settings-content">
        {activeTab === 'general' && (
          <div className="settings-section">
            <h3>General Settings</h3>
            <div className="form-grid">
              <div className="form-group">
                <label>Company Name</label>
                <input
                  type="text"
                  value={settings.company_name || ''}
                  onChange={(e) => handleChange('company_name', e.target.value)}
                />
              </div>
              <div className="form-group">
                <label>Theme Mode</label>
                <select
                  value={settings.theme_mode || 'light'}
                  onChange={(e) => handleChange('theme_mode', e.target.value)}
                >
                  <option value="light">Light</option>
                  <option value="dark">Dark</option>
                </select>
              </div>
              <div className="form-group">
                <label>Primary Color</label>
                <input
                  type="color"
                  value={settings.primary_color || '#3b82f6'}
                  onChange={(e) => handleChange('primary_color', e.target.value)}
                />
              </div>
            </div>
          </div>
        )}

        {activeTab === 'localization' && (
          <div className="settings-section">
            <h3>Localization Settings</h3>
            <div className="form-grid">
              <div className="form-group">
                <label>Currency Code</label>
                <input
                  type="text"
                  value={settings.currency_code || 'NGN'}
                  onChange={(e) => handleChange('currency_code', e.target.value)}
                />
              </div>
              <div className="form-group">
                <label>Currency Symbol</label>
                <input
                  type="text"
                  value={settings.currency_symbol || '₦'}
                  onChange={(e) => handleChange('currency_symbol', e.target.value)}
                />
              </div>
              <div className="form-group">
                <label>Timezone</label>
                <input
                  type="text"
                  value={settings.timezone || 'Africa/Lagos'}
                  onChange={(e) => handleChange('timezone', e.target.value)}
                />
              </div>
            </div>
          </div>
        )}

        {activeTab === 'inventory' && (
          <div className="settings-section">
            <h3>Inventory Settings</h3>
            <div className="form-grid">
              <div className="form-group">
                <label>Stock Valuation Method</label>
                <select
                  value={settings.stock_valuation_method || 'FIFO'}
                  onChange={(e) => handleChange('stock_valuation_method', e.target.value)}
                >
                  <option value="FIFO">FIFO (First In, First Out)</option>
                  <option value="LIFO">LIFO (Last In, First Out)</option>
                  <option value="AVG">Average Cost</option>
                </select>
              </div>
              <div className="form-group">
                <label>Auto Generate SKU</label>
                <input
                  type="checkbox"
                  checked={settings.auto_generate_sku || false}
                  onChange={(e) => handleChange('auto_generate_sku', e.target.checked)}
                />
              </div>
              <div className="form-group">
                <label>SKU Prefix</label>
                <input
                  type="text"
                  value={settings.sku_prefix || 'PRD'}
                  onChange={(e) => handleChange('sku_prefix', e.target.value)}
                />
              </div>
            </div>
          </div>
        )}

        {activeTab === 'sales' && (
          <div className="settings-section">
            <h3>Sales Settings</h3>
            <div className="form-grid">
              <div className="form-group">
                <label>Invoice Prefix</label>
                <input
                  type="text"
                  value={settings.invoice_prefix || 'INV'}
                  onChange={(e) => handleChange('invoice_prefix', e.target.value)}
                />
              </div>
              <div className="form-group">
                <label>Auto Numbering</label>
                <input
                  type="checkbox"
                  checked={settings.invoice_auto_numbering || false}
                  onChange={(e) => handleChange('invoice_auto_numbering', e.target.checked)}
                />
              </div>
              <div className="form-group">
                <label>Sales Tax Enabled</label>
                <input
                  type="checkbox"
                  checked={settings.sales_tax_enabled || false}
                  onChange={(e) => handleChange('sales_tax_enabled', e.target.checked)}
                />
              </div>
            </div>
          </div>
        )}

        {activeTab === 'security' && (
          <div className="settings-section">
            <h3>Security Settings</h3>
            <div className="form-grid">
              <div className="form-group">
                <label>Minimum Password Length</label>
                <input
                  type="number"
                  value={settings.password_min_length || 8}
                  onChange={(e) => handleChange('password_min_length', parseInt(e.target.value))}
                />
              </div>
              <div className="form-group">
                <label>Session Timeout (minutes)</label>
                <input
                  type="number"
                  value={settings.session_timeout_minutes || 30}
                  onChange={(e) => handleChange('session_timeout_minutes', parseInt(e.target.value))}
                />
              </div>
              <div className="form-group">
                <label>Enable 2FA</label>
                <input
                  type="checkbox"
                  checked={settings.enable_2fa || false}
                  onChange={(e) => handleChange('enable_2fa', e.target.checked)}
                />
              </div>
            </div>
          </div>
        )}

        {activeTab === 'modules' && (
          <div className="settings-section">
            <h3>Module Management</h3>
            <div className="modules-grid">
              <div className="module-card">
                <label>
                  <input
                    type="checkbox"
                    checked={settings.module_hr_enabled || false}
                    onChange={(e) => handleChange('module_hr_enabled', e.target.checked)}
                  />
                  <span>HR Management</span>
                </label>
              </div>
              <div className="module-card">
                <label>
                  <input
                    type="checkbox"
                    checked={settings.module_payroll_enabled || false}
                    onChange={(e) => handleChange('module_payroll_enabled', e.target.checked)}
                  />
                  <span>Payroll</span>
                </label>
              </div>
              <div className="module-card">
                <label>
                  <input
                    type="checkbox"
                    checked={settings.module_inventory_enabled || false}
                    onChange={(e) => handleChange('module_inventory_enabled', e.target.checked)}
                  />
                  <span>Inventory</span>
                </label>
              </div>
              <div className="module-card">
                <label>
                  <input
                    type="checkbox"
                    checked={settings.module_production_enabled || false}
                    onChange={(e) => handleChange('module_production_enabled', e.target.checked)}
                  />
                  <span>Production</span>
                </label>
              </div>
              <div className="module-card">
                <label>
                  <input
                    type="checkbox"
                    checked={settings.module_sales_enabled || false}
                    onChange={(e) => handleChange('module_sales_enabled', e.target.checked)}
                  />
                  <span>Sales</span>
                </label>
              </div>
              <div className="module-card">
                <label>
                  <input
                    type="checkbox"
                    checked={settings.module_accounting_enabled || false}
                    onChange={(e) => handleChange('module_accounting_enabled', e.target.checked)}
                  />
                  <span>Accounting</span>
                </label>
              </div>
            </div>
          </div>
        )}

        {activeTab === 'permissions' && currentUser && currentUser.role === 'admin' && (
          <div className="settings-section">
            <h3>User Permissions Management</h3>
            <p style={{ marginBottom: '20px', color: '#6b7280' }}>
              Grant or revoke access to modules for users
            </p>

            <div className="form-group" style={{ marginBottom: '30px' }}>
              <label>Select User</label>
              <select
                value={selectedUser || ''}
                onChange={(e) => {
                  setSelectedUser(e.target.value);
                  if (e.target.value) {
                    fetchUserPermissions(e.target.value);
                  } else {
                    setUserPermissions([]);
                  }
                }}
                className="form-control"
              >
                <option value="">-- Select a user --</option>
                {users.map(user => (
                  <option key={user.id} value={user.id}>
                    {user.full_name} ({user.email}) - {user.role}
                  </option>
                ))}
              </select>
            </div>

            {selectedUser && (
              <div>
                <h4 style={{ marginBottom: '15px', color: '#374151' }}>Permissions</h4>
                <div className="permissions-grid">
                  {Object.keys(modules).map(moduleName => (
                    <div key={moduleName} className="permission-module-card">
                      <h5 style={{ marginBottom: '10px', color: '#4f46e5', textTransform: 'capitalize' }}>
                        {moduleName}
                      </h5>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        {modules[moduleName].map(perm => (
                          <label key={perm.id} style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
                            <input
                              type="checkbox"
                              checked={userPermissions.includes(perm.id)}
                              onChange={() => togglePermission(perm.id)}
                            />
                            <span style={{ fontSize: '14px' }}>{perm.display_name}</span>
                            {perm.description && (
                              <span style={{ fontSize: '12px', color: '#9ca3af' }}>
                                - {perm.description}
                              </span>
                            )}
                          </label>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
                
                <button
                  onClick={handleGrantPermissions}
                  disabled={saving}
                  className="save-button"
                  style={{ marginTop: '20px' }}
                >
                  {saving ? 'Saving...' : 'Update Permissions'}
                </button>
              </div>
            )}
          </div>
        )}

        {/* Save Button */}
        <div className="settings-actions">
          {message && (
            <div className={`message ${message.includes('✅') ? 'success' : 'error'}`}>
              {message}
            </div>
          )}
          <button
            className="btn-save"
            onClick={handleSave}
            disabled={saving || currentUser?.role !== 'admin'}
          >
            {saving ? 'Saving...' : 'Save Settings'}
          </button>
          {currentUser?.role !== 'admin' && (
            <p className="permission-note">Only administrators can modify settings</p>
          )}
        </div>
      </div>

      <style jsx>{`
        .settings-container {
          background: white;
          border-radius: 12px;
          box-shadow: 0 2px 8px rgba(0,0,0,0.1);
          overflow: hidden;
        }

        .settings-header {
          padding: 24px;
          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
          color: white;
        }

        .settings-header h2 {
          margin: 0 0 8px 0;
          font-size: 24px;
        }

        .settings-header p {
          margin: 0;
          opacity: 0.9;
        }

        .settings-tabs {
          display: flex;
          border-bottom: 2px solid #e5e7eb;
          background: #f9fafb;
          overflow-x: auto;
        }

        .tab {
          padding: 16px 24px;
          border: none;
          background: none;
          cursor: pointer;
          display: flex;
          align-items: center;
          gap: 8px;
          transition: all 0.2s;
          border-bottom: 3px solid transparent;
          white-space: nowrap;
        }

        .tab:hover {
          background: #f3f4f6;
        }

        .tab.active {
          border-bottom-color: #667eea;
          background: white;
          color: #667eea;
          font-weight: 600;
        }

        .tab-icon {
          font-size: 20px;
        }

        .settings-content {
          padding: 24px;
        }

        .settings-section h3 {
          margin: 0 0 20px 0;
          color: #1f2937;
        }

        .form-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
          gap: 20px;
        }

        .form-group label {
          display: block;
          margin-bottom: 8px;
          font-weight: 600;
          color: #374151;
        }

        .form-group input[type="text"],
        .form-group input[type="number"],
        .form-group select {
          width: 100%;
          padding: 10px 12px;
          border: 1px solid #d1d5db;
          border-radius: 6px;
          font-size: 14px;
        }

        .form-group input[type="checkbox"] {
          width: 20px;
          height: 20px;
          cursor: pointer;
        }

        .form-group input[type="color"] {
          width: 100%;
          height: 44px;
          border: 1px solid #d1d5db;
          border-radius: 6px;
          cursor: pointer;
        }

        .modules-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
          gap: 16px;
        }

        .module-card {
          padding: 16px;
          border: 2px solid #e5e7eb;
          border-radius: 8px;
          transition: all 0.2s;
        }

        .module-card:hover {
          border-color: #667eea;
          background: #f9fafb;
        }

        .module-card label {
          display: flex;
          align-items: center;
          gap: 12px;
          cursor: pointer;
          font-weight: 500;
        }

        .settings-actions {
          margin-top: 32px;
          padding-top: 24px;
          border-top: 2px solid #e5e7eb;
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 12px;
        }

        .message {
          padding: 12px 24px;
          border-radius: 6px;
          font-weight: 500;
        }

        .message.success {
          background: #d1fae5;
          color: #065f46;
        }

        .message.error {
          background: #fee2e2;
          color: #991b1b;
        }

        .btn-save {
          padding: 12px 48px;
          background: #667eea;
          color: white;
          border: none;
          border-radius: 8px;
          font-size: 16px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.2s;
        }

        .btn-save:hover:not(:disabled) {
          background: #5568d3;
          transform: translateY(-1px);
          box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
        }

        .btn-save:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }

        .permission-note {
          color: #6b7280;
          font-size: 14px;
          font-style: italic;
        }
      `}</style>
    </div>
  );
}

export default Settings;
