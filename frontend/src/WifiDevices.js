// Connected Devices page — register, block/unblock and remove employee devices.
import React, { useEffect, useState } from 'react';
import { wifiApi, fmtDateTime } from './wifiApi';

export default function WifiDevices() {
  const [devices, setDevices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ device_name: '', device_mac: '', device_type: 'phone' });

  const load = async () => {
    setLoading(true);
    setErr('');
    try {
      setDevices(await wifiApi('/api/wifi/devices'));
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const register = async (e) => {
    e.preventDefault();
    try {
      await wifiApi('/api/wifi/register-device', { method: 'POST', body: JSON.stringify(form) });
      setForm({ device_name: '', device_mac: '', device_type: 'phone' });
      setShowForm(false);
      load();
    } catch (e2) {
      setErr(e2.message);
    }
  };

  const setStatus = async (id, action) => {
    try {
      await wifiApi(`/api/wifi/devices/${id}/${action}`, { method: 'POST' });
      load();
    } catch (e) {
      setErr(e.message);
    }
  };

  const remove = async (id) => {
    if (!window.confirm('Remove this device?')) return;
    try {
      await wifiApi(`/api/wifi/remove-device?device_id=${id}`, { method: 'DELETE' });
      load();
    } catch (e) {
      setErr(e.message);
    }
  };

  return (
    <div>
      <div style={s.toolbar}>
        <button style={s.add} onClick={() => setShowForm((v) => !v)}>{showForm ? 'Cancel' : '+ Register Device'}</button>
        <button style={s.refresh} onClick={load}>Refresh</button>
      </div>

      {showForm && (
        <form onSubmit={register} style={s.form}>
          <input style={s.input} placeholder="Device name" value={form.device_name} onChange={(e) => setForm({ ...form, device_name: e.target.value })} required />
          <input style={s.input} placeholder="MAC address (AA:BB:CC:DD:EE:FF)" value={form.device_mac} onChange={(e) => setForm({ ...form, device_mac: e.target.value })} required />
          <select style={s.input} value={form.device_type} onChange={(e) => setForm({ ...form, device_type: e.target.value })}>
            <option value="phone">Phone</option>
            <option value="laptop">Laptop</option>
            <option value="tablet">Tablet</option>
            <option value="other">Other</option>
          </select>
          <button type="submit" style={s.save}>Save</button>
        </form>
      )}

      {err && <div style={s.err}>{err}</div>}

      {loading ? <div style={{ padding: 16 }}>Loading…</div> : (
        <div style={{ overflowX: 'auto' }}>
          <table style={s.table}>
            <thead>
              <tr>
                <th style={s.th}>Employee</th>
                <th style={s.th}>Device</th>
                <th style={s.th}>MAC</th>
                <th style={s.th}>Type</th>
                <th style={s.th}>Last Connected</th>
                <th style={s.th}>Status</th>
                <th style={s.th}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {devices.length === 0 && <tr><td colSpan={7} style={s.empty}>No devices registered.</td></tr>}
              {devices.map((d) => (
                <tr key={d.id}>
                  <td style={s.td}>{d.employee_name}</td>
                  <td style={s.td}>{d.device_name || '—'}</td>
                  <td style={s.td}>{d.device_mac}</td>
                  <td style={s.td}>{d.device_type}</td>
                  <td style={s.td}>{fmtDateTime(d.last_connected)}</td>
                  <td style={s.td}><span style={badge(d.status)}>{d.status}</span></td>
                  <td style={s.td}>
                    {d.status === 'blocked'
                      ? <button style={s.unblock} onClick={() => setStatus(d.id, 'unblock')}>Unblock</button>
                      : <button style={s.block} onClick={() => setStatus(d.id, 'block')}>Block</button>}
                    <button style={s.remove} onClick={() => remove(d.id)}>Remove</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const badge = (status) => ({
  padding: '2px 10px', borderRadius: 12, fontSize: 11, fontWeight: 600, textTransform: 'uppercase',
  color: '#fff', background: status === 'blocked' ? '#dc2626' : '#16a34a',
});

const s = {
  toolbar: { display: 'flex', gap: 10, marginBottom: 14 },
  add: { background: '#667eea', color: '#fff', border: 'none', borderRadius: 8, padding: '8px 14px', cursor: 'pointer', fontWeight: 600 },
  refresh: { border: '1px solid #d1d5db', background: '#f9fafb', borderRadius: 8, padding: '8px 14px', cursor: 'pointer' },
  form: { display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 16, padding: 14, background: '#f9fafb', borderRadius: 8 },
  input: { border: '1px solid #d1d5db', borderRadius: 8, padding: '9px 12px', fontSize: 14, minWidth: 180 },
  save: { background: '#16a34a', color: '#fff', border: 'none', borderRadius: 8, padding: '9px 18px', cursor: 'pointer', fontWeight: 600 },
  table: { width: '100%', borderCollapse: 'collapse', background: '#fff', borderRadius: 8 },
  th: { textAlign: 'left', padding: '10px 12px', fontSize: 12, color: '#6b7280', borderBottom: '2px solid #e5e7eb', textTransform: 'uppercase' },
  td: { padding: '10px 12px', fontSize: 14, borderBottom: '1px solid #f1f5f9' },
  empty: { padding: 20, textAlign: 'center', color: '#9ca3af' },
  block: { background: '#fffbeb', color: '#b45309', border: '1px solid #fde68a', borderRadius: 6, padding: '5px 10px', fontSize: 12, cursor: 'pointer', marginRight: 6 },
  unblock: { background: '#ecfdf5', color: '#047857', border: '1px solid #a7f3d0', borderRadius: 6, padding: '5px 10px', fontSize: 12, cursor: 'pointer', marginRight: 6 },
  remove: { background: '#fef2f2', color: '#b91c1c', border: '1px solid #fecaca', borderRadius: 6, padding: '5px 10px', fontSize: 12, cursor: 'pointer' },
  err: { background: '#fef2f2', color: '#b91c1c', borderRadius: 8, padding: '10px 12px', marginBottom: 12 },
};
