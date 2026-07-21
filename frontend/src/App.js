import React, { useState, useEffect } from 'react';
import Login from './Login';
import AppMain from './AppMain';
import Settings from './Settings';
import NotificationSettings from './NotificationSettings';
import WifiLogin from './WifiLogin';
import API_BASE_URL from './config';
import { isPushSupported, getNotificationPermission, subscribeToPush } from './utils/pushNotifications';
import './styles.css';
import { authedFetch } from './utils/api';

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [currentUser, setCurrentUser] = useState(null);
  const [showSettings, setShowSettings] = useState(false);
  const [showNotificationSettings, setShowNotificationSettings] = useState(false);
  const [deferredPrompt, setDeferredPrompt] = useState(null);
  const [showInstallPrompt, setShowInstallPrompt] = useState(false);
  const [online, setOnline] = useState(navigator.onLine);
  const [commUnread, setCommUnread] = useState({ notices: 0, messages: {}, messages_total: 0 });
  const [commToast, setCommToast] = useState(null);

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

  // ========= GLOBAL COMMUNICATION NOTIFICATION POLLING =========
  useEffect(() => {
    if (!isAuthenticated) return;

    // Initialize last-seen timestamps from localStorage
    const getLastSeen = (key) => localStorage.getItem(key) || new Date(0).toISOString();

    const fetchUnread = async () => {
      if (document.visibilityState !== 'visible') return;
      try {
        const nSince = getLastSeen('comm_notices_last_seen');
        const mSince = getLastSeen('comm_messages_last_seen');
        const res = await authedFetch(`/api/communication/unread?notices_since=${encodeURIComponent(nSince)}&messages_since=${encodeURIComponent(mSince)}`);
        if (!res.ok) return;
        const data = await res.json();

        setCommUnread(prev => {
          // Show toast if counts increased
          const prevTotal = prev.notices + prev.messages_total;
          const newTotal = data.notices + data.messages_total;
          if (newTotal > prevTotal && prevTotal >= 0) {
            const diff = newTotal - prevTotal;
            if (data.notices > prev.notices) {
              setCommToast({ type: 'notice', text: `${data.notices - prev.notices} new notice${data.notices - prev.notices > 1 ? 's' : ''} posted`, time: Date.now() });
            } else if (data.messages_total > prev.messages_total) {
              // Find which channel got new messages
              const chanNames = Object.keys(data.messages);
              const newChan = chanNames.find(ch => (data.messages[ch] || 0) > (prev.messages[ch] || 0));
              setCommToast({ type: 'chat', text: `${data.messages_total - prev.messages_total} new message${diff > 1 ? 's' : ''} in ${newChan || 'chat'}`, time: Date.now() });
            }
            // Play notification sound
            try {
              const ctx = new (window.AudioContext || window.webkitAudioContext)();
              const osc = ctx.createOscillator();
              const gain = ctx.createGain();
              osc.connect(gain);
              gain.connect(ctx.destination);
              osc.type = 'sine';
              osc.frequency.setValueAtTime(880, ctx.currentTime);
              osc.frequency.setValueAtTime(1100, ctx.currentTime + 0.1);
              gain.gain.setValueAtTime(0.3, ctx.currentTime);
              gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.3);
              osc.start(ctx.currentTime);
              osc.stop(ctx.currentTime + 0.3);
            } catch(e) {}
          }
          return data;
        });
      } catch(e) { /* ignore polling errors */ }
    };

    fetchUnread();
    const interval = setInterval(fetchUnread, 10000);
    return () => clearInterval(interval);
  }, [isAuthenticated]);

  // Auto-dismiss toast after 5 seconds
  useEffect(() => {
    if (!commToast) return;
    const t = setTimeout(() => setCommToast(null), 5000);
    return () => clearTimeout(t);
  }, [commToast]);

  // Helper: mark notices as seen (called from AppMain when user views Notice Board)
  const markNoticesSeen = () => {
    localStorage.setItem('comm_notices_last_seen', new Date().toISOString());
    setCommUnread(prev => ({ ...prev, notices: 0 }));
  };
  // Helper: mark messages as seen (called from AppMain when user views Team Chat)
  const markMessagesSeen = () => {
    localStorage.setItem('comm_messages_last_seen', new Date().toISOString());
    setCommUnread(prev => ({ ...prev, messages: {}, messages_total: 0 }));
  };

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
      authedFetch(`${API_BASE_URL}/api/auth/logout`, {
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

  // Captive portal route — render the Wi-Fi login page regardless of app auth.
  if (typeof window !== 'undefined' && window.location.pathname === '/wifi-login') {
    return (
      <WifiLogin
        onAuthenticated={(data) => {
          if (data && data.user) {
            setCurrentUser(data.user);
            setIsAuthenticated(true);
            window.history.replaceState({}, '', '/');
          }
        }}
      />
    );
  }

  if (!isAuthenticated) {
    return <Login onLoginSuccess={handleLoginSuccess} />;
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: '#f3f4f6' }}>
      <nav style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 24px', background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', color: 'white', boxShadow: '0 2px 8px rgba(0,0,0,0.1)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <img 
            src="/company-logo.png?v=20260118" 
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
            {/* Notification Settings Button with unread badge */}
            <button 
              onClick={() => setShowNotificationSettings(!showNotificationSettings)} 
              style={{ 
                padding: '8px 16px', 
                border: '2px solid white', 
                background: showNotificationSettings ? 'rgba(255,255,255,0.3)' : 'rgba(255,255,255,0.1)', 
                color: 'white', 
                borderRadius: '6px', 
                cursor: 'pointer', 
                fontWeight: 600,
                position: 'relative'
              }}
              title="Notification Settings"
            >
              <span role="img" aria-label="notifications">&#x1F514;</span>
              {(commUnread.notices + commUnread.messages_total) > 0 && (
                <span style={{
                  position:'absolute', top:'-6px', right:'-6px',
                  background:'#ef4444', color:'#fff', fontSize:'11px', fontWeight:700,
                  minWidth:'20px', height:'20px', borderRadius:'10px',
                  display:'flex', alignItems:'center', justifyContent:'center',
                  padding:'0 5px', border:'2px solid #764ba2',
                  animation:'badgePulse 2s infinite'
                }}>
                  {commUnread.notices + commUnread.messages_total > 99 ? '99+' : commUnread.notices + commUnread.messages_total}
                </span>
              )}
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
      
      {/* WhatsApp-style Toast Notification */}
      {commToast && (
        <div onClick={() => setCommToast(null)} style={{
          position:'fixed', top:'80px', right:'24px', zIndex:9998,
          background: commToast.type === 'notice' ? 'linear-gradient(135deg, #1a3a8a, #3b7ddd)' : 'linear-gradient(135deg, #059669, #34d399)',
          color:'#fff', padding:'14px 20px', borderRadius:'12px',
          boxShadow:'0 8px 32px rgba(0,0,0,0.25)', cursor:'pointer',
          display:'flex', alignItems:'center', gap:'12px',
          animation:'toastSlideIn 0.4s ease-out', maxWidth:'360px',
          border:'1px solid rgba(255,255,255,0.2)'
        }}>
          <span style={{fontSize:'24px'}}>{commToast.type === 'notice' ? '\u{1F4CB}' : '\u{1F4AC}'}</span>
          <div>
            <div style={{fontWeight:700, fontSize:'14px'}}>{commToast.type === 'notice' ? 'New Notice' : 'New Message'}</div>
            <div style={{fontSize:'13px', opacity:0.9, marginTop:'2px'}}>{commToast.text}</div>
          </div>
          <span style={{marginLeft:'auto', fontSize:'18px', opacity:0.7}}>&times;</span>
        </div>
      )}

      <div style={{ flex: 1, overflow: 'auto', padding: '24px' }}>
        {showSettings ? <Settings currentUser={currentUser} /> : (
          <AppMain
            currentUser={currentUser}
            commUnread={commUnread}
            markNoticesSeen={markNoticesSeen}
            markMessagesSeen={markMessagesSeen}
          />
        )}
      </div>

      {/* Notification animation styles */}
      <style>{`
        @keyframes badgePulse {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.15); }
        }
        @keyframes toastSlideIn {
          from { transform: translateX(120%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>
    </div>
  );
}

export default App;
