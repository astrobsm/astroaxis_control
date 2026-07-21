// Network & WiFi Management — Super Admin module.
// Orchestrates dashboard widgets + tabbed pages:
// WiFi Settings · Access Policies · Active Sessions · Connected Devices · Authentication Logs
import React, { useEffect, useState } from 'react';
import { wifiApi } from './wifiApi';
import WifiSettings from './WifiSettings';
import WifiSessions from './WifiSessions';
import WifiDevices from './WifiDevices';
import WifiLogs from './WifiLogs';

const TABS = [
  { key: 'overview', label: 'Dashboard' },
  { key: 'settings', label: 'WiFi Settings' },
  { key: 'policies', label: 'Access Policies' },
  { key: 'sessions', label: 'Active Sessions' },
  { key: 'devices', label: 'Connected Devices' },
  { key: 'logs', label: 'Authentication Logs' },
];

export default function NetworkManagementDashboard() {
  const [tab, setTab] = useState('overview');

  return (
    <div className="module-content">
      <div className="module-header">
        <div className="module-header-left">
          <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
          <h2>Network &amp; WiFi Management</h2>
        </div>
      </div>

      <div style={s.tabs}>
        {TABS.map((t) => (
          <button
            key={t.key}
            style={{ ...s.tab, ...(tab === t.key ? s.tabActive : {}) }}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div style={s.body}>
        {tab === 'overview' && <Overview />}
        {tab === 'settings' && <WifiSettings />}
        {tab === 'policies' && <AccessPolicies />}
        {tab === 'sessions' && <WifiSessions />}
        {tab === 'devices' && <WifiDevices />}
        {tab === 'logs' && <WifiLogs />}
      </div>
    </div>
  );
}

// ── Dashboard widgets ──────────────────────────────────────────────────────
function Overview() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const d = await wifiApi('/api/wifi/dashboard');
        if (alive) setData(d);
      } catch (e) {
        if (alive) setErr(e.message);
      }
    };
    load();
    const iv = setInterval(load, 30000);
    return () => { alive = false; clearInterval(iv); };
  }, []);

  if (err) return <div style={s.err}>{err}</div>;
  if (!data) return <div style={{ padding: 16 }}>Loading…</div>;

  const cards = [
    { title: 'Current WiFi Users', value: data.current_wifi_users, accent: '#2563eb' },
    { title: 'Active Sessions', value: data.active_sessions, accent: '#16a34a' },
    { title: "Today's Logins", value: data.todays_logins, accent: '#7c3aed' },
    { title: 'Failed Login Attempts', value: data.failed_login_attempts, accent: '#dc2626' },
    { title: 'Connected Devices', value: data.connected_devices, accent: '#f59e0b' },
    { title: 'Bandwidth Usage (MB)', value: data.bandwidth_usage_mb, accent: '#0891b2' },
  ];

  return (
    <div style={s.cards}>
      {cards.map((c) => (
        <div key={c.title} style={{ ...s.card, borderLeft: `4px solid ${c.accent}` }}>
          <div style={s.cardTitle}>{c.title}</div>
          <div style={s.cardValue}>{c.value}</div>
        </div>
      ))}
    </div>
  );
}

// ── Access Policies (policy-focused subset of settings) ────────────────────
function AccessPolicies() {
  const [form, setForm] = useState(null);
  const [msg, setMsg] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    wifiApi('/api/wifi/settings').then((d) => setForm({
      session_timeout: d.session_timeout ?? 60,
      max_devices: d.max_devices ?? 3,
      bandwidth_limit: d.bandwidth_limit ?? 0,
      guest_network_enabled: !!d.guest_network_enabled,
      attendance_on_login: !!d.attendance_on_login,
    })).catch((e) => setMsg({ type: 'error', text: e.message }));
  }, []);

  if (!form) return <div style={{ padding: 16 }}>Loading…</div>;
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const save = async (e) => {
    e.preventDefault();
    setSaving(true);
    setMsg(null);
    try {
      await wifiApi('/api/wifi/settings', { method: 'PUT', body: JSON.stringify(form) });
      setMsg({ type: 'success', text: 'Access policies updated.' });
    } catch (e2) {
      setMsg({ type: 'error', text: e2.message });
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={save}>
      {msg && <div style={{ ...s.banner, ...(msg.type === 'error' ? s.bannerErr : s.bannerOk) }}>{msg.text}</div>}
      <div style={s.policyGrid}>
        <Pol label="Session Timeout (Minutes)">
          <input style={s.input} type="number" min={1} max={1440} value={form.session_timeout} onChange={(e) => set('session_timeout', Number(e.target.value))} />
        </Pol>
        <Pol label="Maximum Devices Per Employee">
          <input style={s.input} type="number" min={1} max={50} value={form.max_devices} onChange={(e) => set('max_devices', Number(e.target.value))} />
        </Pol>
        <Pol label="Bandwidth Limit Per Employee (Mbps)" hint="0 = unlimited">
          <input style={s.input} type="number" min={0} max={10000} value={form.bandwidth_limit} onChange={(e) => set('bandwidth_limit', Number(e.target.value))} />
        </Pol>
        <Pol label="Guest Network Enabled">
          <select style={s.input} value={form.guest_network_enabled ? 'yes' : 'no'} onChange={(e) => set('guest_network_enabled', e.target.value === 'yes')}>
            <option value="no">No</option><option value="yes">Yes</option>
          </select>
        </Pol>
        <Pol label="Enable Attendance On Login">
          <select style={s.input} value={form.attendance_on_login ? 'yes' : 'no'} onChange={(e) => set('attendance_on_login', e.target.value === 'yes')}>
            <option value="no">No</option><option value="yes">Yes</option>
          </select>
        </Pol>
      </div>
      <button type="submit" style={s.saveBtn} disabled={saving}>{saving ? 'Saving…' : 'Save Policies'}</button>
    </form>
  );
}

const Pol = ({ label, hint, children }) => (
  <div style={{ display: 'flex', flexDirection: 'column' }}>
    <label style={s.label}>{label}</label>
    {children}
    {hint && <span style={s.hint}>{hint}</span>}
  </div>
);

const s = {
  tabs: { display: 'flex', gap: 6, flexWrap: 'wrap', margin: '12px 0 18px', borderBottom: '2px solid #e5e7eb' },
  tab: { border: 'none', background: 'transparent', padding: '10px 16px', fontSize: 14, fontWeight: 600, color: '#6b7280', cursor: 'pointer', borderBottom: '3px solid transparent' },
  tabActive: { color: '#667eea', borderBottom: '3px solid #667eea' },
  body: { padding: 4 },
  cards: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16 },
  card: { background: '#fff', borderRadius: 12, padding: 18, boxShadow: '0 2px 6px rgba(15,23,42,0.06)' },
  cardTitle: { color: '#64748b', fontSize: 12, textTransform: 'uppercase', fontWeight: 600 },
  cardValue: { fontSize: 30, fontWeight: 700, color: '#0f172a', marginTop: 8 },
  policyGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16 },
  input: { border: '1px solid #d1d5db', borderRadius: 8, padding: '10px 12px', fontSize: 14, width: '100%', boxSizing: 'border-box' },
  label: { fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 6 },
  hint: { fontSize: 11, color: '#9ca3af', marginTop: 4 },
  saveBtn: { marginTop: 20, background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', color: '#fff', border: 'none', borderRadius: 8, padding: '12px 28px', fontSize: 15, fontWeight: 700, cursor: 'pointer' },
  banner: { padding: '10px 14px', borderRadius: 8, marginBottom: 16, fontSize: 14 },
  bannerOk: { background: '#ecfdf5', color: '#047857', border: '1px solid #a7f3d0' },
  bannerErr: { background: '#fef2f2', color: '#b91c1c', border: '1px solid #fecaca' },
  err: { background: '#fef2f2', color: '#b91c1c', borderRadius: 8, padding: '10px 12px' },
};
