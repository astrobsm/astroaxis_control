// Captive Portal Wi-Fi login page — served at /wifi-login.
// Employees authenticate with their EXISTING company account credentials.
// On success Wi-Fi access is granted and the user is redirected to the dashboard.
import React, { useState } from 'react';
import { wifiApi } from './wifiApi';

// Best-effort device MAC hint (browsers cannot read the real MAC; the gateway
// injects it as a query param on most captive-portal setups, e.g. ?mac=...).
function getQueryParam(name) {
  try {
    return new URLSearchParams(window.location.search).get(name) || '';
  } catch (e) {
    return '';
  }
}

export default function WifiLogin({ onAuthenticated = () => {} }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [rememberDevice, setRememberDevice] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setInfo('');
    if (!username || !password) {
      setError('Please enter your company username/email and password.');
      return;
    }
    setLoading(true);
    try {
      const deviceMac = getQueryParam('mac') || getQueryParam('client_mac') || undefined;
      const data = await wifiApi('/api/wifi/authenticate', {
        method: 'POST',
        body: JSON.stringify({
          username,
          password,
          device_mac: deviceMac,
          device_name: navigator.userAgent ? navigator.userAgent.slice(0, 120) : undefined,
          remember_device: rememberDevice,
        }),
      });

      // Persist the session so the dashboard recognises the user.
      if (data.access_token) localStorage.setItem('access_token', data.access_token);
      if (data.user) localStorage.setItem('user', JSON.stringify(data.user));

      let msg = 'Wi-Fi access granted. Redirecting…';
      if (data.attendance_registered) msg = 'Wi-Fi access granted. Time-In recorded. Redirecting…';
      setInfo(msg);

      // If the captive portal supplied a continue/redirect URL, honour it.
      const redirectUrl = getQueryParam('url') || getQueryParam('continue');
      setTimeout(() => {
        if (redirectUrl) {
          window.location.href = redirectUrl;
        } else {
          onAuthenticated(data);
        }
      }, 900);
    } catch (err) {
      setError(err.message || 'Authentication failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={styles.page}>
      <div style={styles.card}>
        <div style={styles.brand}>
          <img
            src="/company-logo.png"
            alt="Company logo"
            style={styles.logo}
            onError={(e) => { e.target.style.display = 'none'; }}
          />
          <h1 style={styles.title}>Company Wi-Fi Access</h1>
          <p style={styles.subtitle}>Sign in with your company account to connect.</p>
        </div>

        <form onSubmit={handleSubmit} style={styles.form}>
          <label style={styles.label} htmlFor="wifi-username">Username / Email</label>
          <input
            id="wifi-username"
            type="text"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="you@company.com"
            style={styles.input}
            disabled={loading}
          />

          <label style={styles.label} htmlFor="wifi-password">Password</label>
          <div style={styles.passwordRow}>
            <input
              id="wifi-password"
              type={showPassword ? 'text' : 'password'}
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              style={{ ...styles.input, marginBottom: 0, flex: 1 }}
              disabled={loading}
            />
            <button
              type="button"
              onClick={() => setShowPassword((s) => !s)}
              style={styles.toggleBtn}
              tabIndex={-1}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
            >
              {showPassword ? 'Hide' : 'Show'}
            </button>
          </div>

          <label style={styles.checkboxRow}>
            <input
              type="checkbox"
              checked={rememberDevice}
              onChange={(e) => setRememberDevice(e.target.checked)}
              disabled={loading}
            />
            <span>Remember this device</span>
          </label>

          {error && <div style={styles.error}>{error}</div>}
          {info && <div style={styles.info}>{info}</div>}

          <button type="submit" style={styles.submit} disabled={loading}>
            {loading ? 'Connecting…' : 'Connect to Wi-Fi'}
          </button>

          <a href="/?forgot=1" style={styles.forgot}>Forgot password?</a>
        </form>
      </div>
      <p style={styles.footer}>BONNESANTE MEDICALS · Secure Network Access</p>
    </div>
  );
}

const styles = {
  page: {
    minHeight: '100vh', display: 'flex', flexDirection: 'column',
    alignItems: 'center', justifyContent: 'center', padding: 16,
    background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
  },
  card: {
    width: '100%', maxWidth: 420, background: '#fff', borderRadius: 16,
    padding: 28, boxShadow: '0 20px 50px rgba(0,0,0,0.25)',
  },
  brand: { textAlign: 'center', marginBottom: 20 },
  logo: { width: 64, height: 64, objectFit: 'contain', marginBottom: 10 },
  title: { fontSize: 22, fontWeight: 700, color: '#1f2937', margin: '4px 0' },
  subtitle: { fontSize: 13, color: '#6b7280', margin: 0 },
  form: { display: 'flex', flexDirection: 'column' },
  label: { fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 6 },
  input: {
    border: '1px solid #d1d5db', borderRadius: 8, padding: '11px 12px',
    fontSize: 15, marginBottom: 16, outline: 'none', width: '100%',
    boxSizing: 'border-box',
  },
  passwordRow: { display: 'flex', gap: 8, alignItems: 'center', marginBottom: 16 },
  toggleBtn: {
    border: '1px solid #d1d5db', background: '#f9fafb', borderRadius: 8,
    padding: '11px 12px', fontSize: 13, cursor: 'pointer', color: '#374151',
  },
  checkboxRow: {
    display: 'flex', alignItems: 'center', gap: 8, fontSize: 14,
    color: '#374151', marginBottom: 16, cursor: 'pointer',
  },
  error: {
    background: '#fef2f2', color: '#b91c1c', border: '1px solid #fecaca',
    borderRadius: 8, padding: '10px 12px', fontSize: 13, marginBottom: 14,
  },
  info: {
    background: '#ecfdf5', color: '#047857', border: '1px solid #a7f3d0',
    borderRadius: 8, padding: '10px 12px', fontSize: 13, marginBottom: 14,
  },
  submit: {
    background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
    color: '#fff', border: 'none', borderRadius: 8, padding: '13px 16px',
    fontSize: 16, fontWeight: 700, cursor: 'pointer', marginBottom: 12,
  },
  forgot: { textAlign: 'center', color: '#667eea', fontSize: 13, textDecoration: 'none' },
  footer: { color: 'rgba(255,255,255,0.85)', fontSize: 12, marginTop: 18 },
};
