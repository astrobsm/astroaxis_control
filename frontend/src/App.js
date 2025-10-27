import React, { useState, useEffect } from 'react';
import Login from './Login';
import AppMain from './AppMain';
import Settings from './Settings';
import './styles.css';

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [currentUser, setCurrentUser] = useState(null);
  const [showSettings, setShowSettings] = useState(false);
  const [deferredPrompt, setDeferredPrompt] = useState(null);
  const [showInstallPrompt, setShowInstallPrompt] = useState(false);

  useEffect(() => {
    const token = localStorage.getItem('access_token');
    const user = localStorage.getItem('user');
    if (token && user) {
      try {
        setCurrentUser(JSON.parse(user));
        setIsAuthenticated(true);
      } catch (error) {
        console.error('Error parsing user data:', error);
        localStorage.clear();
      }
    }

    // PWA Install Prompt Handler
    const handleBeforeInstallPrompt = (e) => {
      e.preventDefault();
      setDeferredPrompt(e);
      setShowInstallPrompt(true);
    };

    window.addEventListener('beforeinstallprompt', handleBeforeInstallPrompt);

    // Detect if app is installed
    window.addEventListener('appinstalled', () => {
      console.log('✅ ASTRO-ASIX PWA installed successfully!');
      setShowInstallPrompt(false);
      setDeferredPrompt(null);
    });

    return () => {
      window.removeEventListener('beforeinstallprompt', handleBeforeInstallPrompt);
    };
  }, []);

  const handleInstallClick = async () => {
    if (!deferredPrompt) return;
    
    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    
    if (outcome === 'accepted') {
      console.log('✅ User accepted the install prompt');
    } else {
      console.log('❌ User dismissed the install prompt');
    }
    
    setDeferredPrompt(null);
    setShowInstallPrompt(false);
  };

  const handleLoginSuccess = (user) => {
    setCurrentUser(user);
    setIsAuthenticated(true);
  };

  const handleLogout = () => {
    const token = localStorage.getItem('access_token');
    if (token) {
      fetch('http://127.0.0.1:8004/api/auth/logout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      }).catch(err => console.error('Logout error:', err));
    }
    localStorage.removeItem('access_token');
    localStorage.removeItem('user');
    setIsAuthenticated(false);
    setCurrentUser(null);
    setShowSettings(false);
  };

  const hasPermission = (module, action) => {
    if (!currentUser) return false;
    if (currentUser.role === 'admin') return true;
    const permissions = {
      sales_staff: ['products', 'sales', 'customers', 'inventory'],
      marketer: ['products', 'customers', 'sales'],
      customer_care: ['customers', 'sales', 'products'],
      production_staff: ['production', 'products', 'raw_materials', 'inventory', 'warehouses'],
    };
    const allowedModules = permissions[currentUser.role] || [];
    return allowedModules.includes(module);
  };

  if (!isAuthenticated) {
    return <Login onLoginSuccess={handleLoginSuccess} />;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: '#f3f4f6' }}>
      <nav style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 24px', background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', color: 'white', boxShadow: '0 2px 8px rgba(0,0,0,0.1)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <img 
            src="/company-logo.png" 
            alt="ASTRO-ASIX Logo" 
            style={{ 
              width: '50px', 
              height: '50px', 
              objectFit: 'contain', 
              background: 'white', 
              padding: '6px', 
              borderRadius: '8px',
              boxShadow: '0 2px 4px rgba(0,0,0,0.1)'
            }}
            onError={(e) => { e.target.style.display = 'none'; }}
          />
          <h1 style={{ margin: 0, fontSize: '24px', fontWeight: 'bold' }}>ASTRO-ASIX ERP</h1>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '24px' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
            <span style={{ fontWeight: 600, fontSize: '16px' }}>{currentUser?.full_name}</span>
            <span style={{ fontSize: '12px', opacity: 0.9, padding: '2px 8px', background: 'rgba(255,255,255,0.2)', borderRadius: '12px', marginTop: '4px' }}>
              {currentUser?.role?.replace('_', ' ').toUpperCase()}
            </span>
          </div>
          <div style={{ display: 'flex', gap: '12px' }}>
            {showInstallPrompt && (
              <button 
                onClick={handleInstallClick} 
                style={{ 
                  padding: '8px 16px', 
                  border: '2px solid white', 
                  background: 'rgba(255,255,255,0.2)', 
                  color: 'white', 
                  borderRadius: '6px', 
                  cursor: 'pointer', 
                  fontWeight: 600,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px'
                }}
                title="Install ASTRO-ASIX as an app"
              >
                📱 Install App
              </button>
            )}
            {currentUser?.role === 'admin' && (
              <button onClick={() => setShowSettings(!showSettings)} style={{ padding: '8px 16px', border: '2px solid white', background: 'rgba(255,255,255,0.1)', color: 'white', borderRadius: '6px', cursor: 'pointer', fontWeight: 600 }}>
                 Settings
              </button>
            )}
            <button onClick={handleLogout} style={{ padding: '8px 16px', border: '2px solid white', background: 'rgba(255,255,255,0.1)', color: 'white', borderRadius: '6px', cursor: 'pointer', fontWeight: 600 }}>
               Logout
            </button>
          </div>
        </div>
      </nav>
      <div style={{ flex: 1, overflow: 'auto', padding: '24px' }}>
        {showSettings ? <Settings currentUser={currentUser} /> : <AppMain currentUser={currentUser} hasPermission={hasPermission} />}
      </div>
    </div>
  );
}

export default App;
