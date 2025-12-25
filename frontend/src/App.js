import React, { useState, useEffect } from 'react';
import Login from './Login';
import AppMain from './AppMain';
import Settings from './Settings';
import NotificationSettings from './NotificationSettings';
import API_BASE_URL from './config';
import { isPushSupported, getNotificationPermission, subscribeToPush } from './utils/pushNotifications';
import './styles.css';

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [currentUser, setCurrentUser] = useState(null);
  const [showSettings, setShowSettings] = useState(false);
  const [showNotificationSettings, setShowNotificationSettings] = useState(false);
  const [deferredPrompt, setDeferredPrompt] = useState(null);
  const [showInstallPrompt, setShowInstallPrompt] = useState(false);
  const [online, setOnline] = useState(navigator.onLine);

  useEffect(() => {
    const token = localStorage.getItem('access_token');
    const user = localStorage.getItem('user');
    if (token && user) {
      try {
        setCurrentUser(JSON.parse(user));
        setIsAuthenticated(true);
        
        // Auto-subscribe to push notifications if permission granted
        if (isPushSupported() && getNotificationPermission() === 'granted') {
          subscribeToPush().catch(err => console.log('Push subscription check:', err));
        }
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

    // Online/offline handlers
    const handleOnline = () => setOnline(true);
    const handleOffline = () => setOnline(false);

    window.addEventListener('beforeinstallprompt', handleBeforeInstallPrompt);
    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);

    // Detect if app is installed
    window.addEventListener('appinstalled', () => {
      console.log('✅ ASTRO-ASIX PWA installed successfully!');
      setShowInstallPrompt(false);
      setDeferredPrompt(null);
    });

    return () => {
      window.removeEventListener('beforeinstallprompt', handleBeforeInstallPrompt);
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
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
      fetch(`${API_BASE_URL}/api/auth/logout`, {
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

  if (!isAuthenticated) {
    return <Login onLoginSuccess={handleLoginSuccess} />;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: '#f3f4f6' }}>
      <nav style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 24px', background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', color: 'white', boxShadow: '0 2px 8px rgba(0,0,0,0.1)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <img 
            src="/company-logo.png" 
            alt="AstroBSM StockMaster" 
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
          <h1 style={{ margin: 0, fontSize: '24px', fontWeight: 'bold' }}>AstroBSM StockMaster</h1>
          {/* Offline indicator */}
          {!online && (
            <span style={{ 
              marginLeft: '12px', 
              padding: '4px 10px', 
              background: '#ff6b6b', 
              borderRadius: '12px', 
              fontSize: '11px', 
              fontWeight: 600 
            }}>
              OFFLINE
            </span>
          )}
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
                Install App
              </button>
            )}
            {/* Notification Settings Button */}
            <button 
              onClick={() => setShowNotificationSettings(!showNotificationSettings)} 
              style={{ 
                padding: '8px 16px', 
                border: '2px solid white', 
                background: showNotificationSettings ? 'rgba(255,255,255,0.3)' : 'rgba(255,255,255,0.1)', 
                color: 'white', 
                borderRadius: '6px', 
                cursor: 'pointer', 
                fontWeight: 600 
              }}
              title="Notification Settings"
            >
              🔔
            </button>
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
      
      {/* Notification Settings Modal */}
      {showNotificationSettings && (
        <div style={{ 
          position: 'fixed', 
          top: 0, 
          left: 0, 
          right: 0, 
          bottom: 0, 
          background: 'rgba(0,0,0,0.5)', 
          display: 'flex', 
          alignItems: 'center', 
          justifyContent: 'center',
          zIndex: 1000 
        }}>
          <div style={{ 
            background: 'white', 
            borderRadius: '12px', 
            maxWidth: '500px', 
            width: '90%', 
            maxHeight: '80vh', 
            overflow: 'auto',
            boxShadow: '0 20px 50px rgba(0,0,0,0.3)'
          }}>
            <NotificationSettings onClose={() => setShowNotificationSettings(false)} />
          </div>
        </div>
      )}
      
      <div style={{ flex: 1, overflow: 'auto', padding: '24px' }}>
        {showSettings ? <Settings currentUser={currentUser} /> : <AppMain currentUser={currentUser} />}
      </div>
    </div>
  );
}

export default App;
