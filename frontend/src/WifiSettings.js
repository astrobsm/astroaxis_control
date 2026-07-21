// WiFi Settings page — Super Admin only.
// Secrets are stored AES-encrypted server-side and never returned in plaintext;
// the form shows a "(saved)" indicator and lets admins reveal/replace each one.
import React, { useEffect, useState } from 'react';
import { wifiApi } from './wifiApi';

const SECRET_FIELDS = [
  { key: 'company_password', label: 'Company WiFi Password', setFlag: 'company_password_set' },
  { key: 'guest_password', label: 'Guest WiFi Password', setFlag: 'guest_password_set' },
  { key: 'current_wifi_password', label: 'Current WiFi Password', setFlag: 'current_wifi_password_set', hint: 'Central management password for the organization Wi-Fi.' },
  { key: 'radius_secret', label: 'RADIUS Secret Key', setFlag: 'radius_secret_set' },
];

export default function WifiSettings() {
  const [settings, setSettings] = useState(null);
  const [form, setForm] = useState({});
  const [secrets, setSecrets] = useState({});       // plaintext entered by admin
  const [reveal, setReveal] = useState({});         // which secrets are visible
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const data = await wifiApi('/api/wifi/settings');
      setSettings(data);
      setForm({
        company_ssid: data.company_ssid || '',
        guest_ssid: data.guest_ssid || '',
        radius_server_ip: data.radius_server_ip || '',
        captive_portal_url: data.captive_portal_url || '',
        session_timeout: data.session_timeout ?? 60,
        max_devices: data.max_devices ?? 3,
        bandwidth_limit: data.bandwidth_limit ?? 0,
        guest_network_enabled: !!data.guest_network_enabled,
        attendance_on_login: !!data.attendance_on_login,
      });
    } catch (e) {
      setMsg({ type: 'error', text: e.message });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const setField = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const revealSecret = async (field) => {
    if (reveal[field]?.shown) {
      setReveal((r) => ({ ...r, [field]: { shown: false } }));
      return;
    }
    try {
      const data = await wifiApi('/api/wifi/settings/reveal', {
        method: 'POST',
        body: JSON.stringify({ field }),
      });
      setReveal((r) => ({ ...r, [field]: { shown: true, value: data.value } }));
    } catch (e) {
      setMsg({ type: 'error', text: e.message });
    }
  };

  const save = async (e) => {
    e.preventDefault();
    setSaving(true);
    setMsg(null);
    try {
      const payload = { ...form };
      // Only include secrets that were actually typed.
      SECRET_FIELDS.forEach(({ key }) => {
        if (secrets[key]) payload[key] = secrets[key];
      });
      const data = await wifiApi('/api/wifi/settings', {
        method: 'PUT',
        body: JSON.stringify(payload),
      });
      setSettings(data.settings);
      setSecrets({});
      setReveal({});
      setMsg({ type: 'success', text: 'Settings saved successfully.' });
    } catch (e2) {
      setMsg({ type: 'error', text: e2.message });
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div style={{ padding: 20 }}>Loading settings…</div>;

  return (
    <form onSubmit={save} style={s.wrap}>
      {msg && (
        <div style={{ ...s.banner, ...(msg.type === 'error' ? s.bannerErr : s.bannerOk) }}>
          {msg.text}
        </div>
      )}

      <div style={s.grid}>
        <Field label="Company WiFi SSID">
          <input style={s.input} value={form.company_ssid} onChange={(e) => setField('company_ssid', e.target.value)} />
        </Field>
        <Field label="Guest WiFi SSID">
          <input style={s.input} value={form.guest_ssid} onChange={(e) => setField('guest_ssid', e.target.value)} />
        </Field>

        {SECRET_FIELDS.map(({ key, label, setFlag, hint }) => (
          <Field key={key} label={label} hint={hint}>
            <div style={s.secretRow}>
              <input
                style={{ ...s.input, flex: 1, marginBottom: 0 }}
                type={reveal[key]?.shown ? 'text' : 'password'}
                value={reveal[key]?.shown ? (reveal[key].value || '') : (secrets[key] || '')}
                placeholder={settings?.[setFlag] ? '•••••••• (saved)' : 'Not set'}
                onChange={(e) => { setSecrets((x) => ({ ...x, [key]: e.target.value })); setReveal((r) => ({ ...r, [key]: { shown: false } })); }}
                readOnly={reveal[key]?.shown}
              />
              <button type="button" style={s.smallBtn} onClick={() => revealSecret(key)} disabled={!settings?.[setFlag] && !secrets[key]}>
                {reveal[key]?.shown ? 'Hide' : 'Show'}
              </button>
            </div>
          </Field>
        ))}

        <Field label="Captive Portal URL">
          <input style={s.input} value={form.captive_portal_url} onChange={(e) => setField('captive_portal_url', e.target.value)} placeholder="https://portal.company.com/wifi-login" />
        </Field>
        <Field label="RADIUS Server IP">
          <input style={s.input} value={form.radius_server_ip} onChange={(e) => setField('radius_server_ip', e.target.value)} placeholder="10.0.0.5" />
        </Field>

        <Field label="Session Timeout (Minutes)">
          <input style={s.input} type="number" min={1} max={1440} value={form.session_timeout} onChange={(e) => setField('session_timeout', Number(e.target.value))} />
        </Field>
        <Field label="Maximum Devices Per Employee">
          <input style={s.input} type="number" min={1} max={50} value={form.max_devices} onChange={(e) => setField('max_devices', Number(e.target.value))} />
        </Field>
        <Field label="Bandwidth Limit Per Employee (Mbps)" hint="0 = unlimited">
          <input style={s.input} type="number" min={0} max={10000} value={form.bandwidth_limit} onChange={(e) => setField('bandwidth_limit', Number(e.target.value))} />
        </Field>

        <Field label="Guest Network Enabled">
          <select style={s.input} value={form.guest_network_enabled ? 'yes' : 'no'} onChange={(e) => setField('guest_network_enabled', e.target.value === 'yes')}>
            <option value="no">No</option>
            <option value="yes">Yes</option>
          </select>
        </Field>
        <Field label="Enable Attendance On Login">
          <select style={s.input} value={form.attendance_on_login ? 'yes' : 'no'} onChange={(e) => setField('attendance_on_login', e.target.value === 'yes')}>
            <option value="no">No</option>
            <option value="yes">Yes</option>
          </select>
        </Field>
      </div>

      <button type="submit" style={s.saveBtn} disabled={saving}>
        {saving ? 'Saving…' : 'Save Settings'}
      </button>
    </form>
  );
}

const Field = ({ label, hint, children }) => (
  <div style={s.field}>
    <label style={s.label}>{label}</label>
    {children}
    {hint && <span style={s.hint}>{hint}</span>}
  </div>
);

const s = {
  wrap: { padding: 4 },
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 },
  field: { display: 'flex', flexDirection: 'column' },
  label: { fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 6 },
  hint: { fontSize: 11, color: '#9ca3af', marginTop: 4 },
  input: { border: '1px solid #d1d5db', borderRadius: 8, padding: '10px 12px', fontSize: 14, width: '100%', boxSizing: 'border-box' },
  secretRow: { display: 'flex', gap: 8, alignItems: 'center' },
  smallBtn: { border: '1px solid #d1d5db', background: '#f9fafb', borderRadius: 8, padding: '10px 12px', fontSize: 13, cursor: 'pointer' },
  saveBtn: { marginTop: 20, background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', color: '#fff', border: 'none', borderRadius: 8, padding: '12px 28px', fontSize: 15, fontWeight: 700, cursor: 'pointer' },
  banner: { padding: '10px 14px', borderRadius: 8, marginBottom: 16, fontSize: 14 },
  bannerOk: { background: '#ecfdf5', color: '#047857', border: '1px solid #a7f3d0' },
  bannerErr: { background: '#fef2f2', color: '#b91c1c', border: '1px solid #fecaca' },
};
