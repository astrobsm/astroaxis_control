// Active Sessions page — lists current Wi-Fi sessions, allows disconnect.
import React, { useCallback, useEffect, useState } from 'react';
import { wifiApi, fmtDateTime } from './wifiApi';

export default function WifiSessions() {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('active');
  const [err, setErr] = useState('');

  const load = async (status) => {
    setLoading(true);
    setErr('');
    try {
      const q = status && status !== 'all' ? `?status_filter=${status}` : '';
      setSessions(await wifiApi(`/api/wifi/sessions${q}`));
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(filter); }, [filter]);

  const disconnect = async (id) => {
    try {
      await wifiApi('/api/wifi/logout', { method: 'POST', body: JSON.stringify({ session_id: id }) });
      load(filter);
    } catch (e) {
      setErr(e.message);
    }
  };

  return (
    <div>
      <div style={s.toolbar}>
        <select style={s.select} value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="active">Active</option>
          <option value="closed">Closed</option>
          <option value="expired">Expired</option>
          <option value="all">All</option>
        </select>
        <button style={s.refresh} onClick={() => load(filter)}>Refresh</button>
      </div>
      {err && <div style={s.err}>{err}</div>}
      {loading ? <div style={{ padding: 16 }}>Loading…</div> : (
        <div style={{ overflowX: 'auto' }}>
          <table style={s.table}>
            <thead>
              <tr>
                <th style={s.th}>Employee</th>
                <th style={s.th}>Device</th>
                <th style={s.th}>MAC</th>
                <th style={s.th}>IP</th>
                <th style={s.th}>Login</th>
                <th style={s.th}>Status</th>
                <th style={s.th}>Data (MB)</th>
                <th style={s.th}></th>
              </tr>
            </thead>
            <tbody>
              {sessions.length === 0 && (
                <tr><td colSpan={8} style={s.empty}>No sessions found.</td></tr>
              )}
              {sessions.map((x) => (
                <tr key={x.id}>
                  <td style={s.td}>{x.employee_name}</td>
                  <td style={s.td}>{x.device_name || '—'}</td>
                  <td style={s.td}>{x.device_mac || '—'}</td>
                  <td style={s.td}>{x.ip_address || '—'}</td>
                  <td style={s.td}>{fmtDateTime(x.login_time)}</td>
                  <td style={s.td}><span style={badge(x.session_status)}>{x.session_status}</span></td>
                  <td style={s.td}>{x.data_used_mb}</td>
                  <td style={s.td}>
                    {x.session_status === 'active' && (
                      <button style={s.disc} onClick={() => disconnect(x.id)}>Disconnect</button>
                    )}
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
  color: '#fff', background: status === 'active' ? '#16a34a' : status === 'expired' ? '#f59e0b' : '#6b7280',
});

const s = {
  toolbar: { display: 'flex', gap: 10, marginBottom: 14 },
  select: { border: '1px solid #d1d5db', borderRadius: 8, padding: '8px 10px' },
  refresh: { border: '1px solid #d1d5db', background: '#f9fafb', borderRadius: 8, padding: '8px 14px', cursor: 'pointer' },
  table: { width: '100%', borderCollapse: 'collapse', background: '#fff', borderRadius: 8 },
  th: { textAlign: 'left', padding: '10px 12px', fontSize: 12, color: '#6b7280', borderBottom: '2px solid #e5e7eb', textTransform: 'uppercase' },
  td: { padding: '10px 12px', fontSize: 14, borderBottom: '1px solid #f1f5f9' },
  empty: { padding: 20, textAlign: 'center', color: '#9ca3af' },
  disc: { background: '#fef2f2', color: '#b91c1c', border: '1px solid #fecaca', borderRadius: 6, padding: '5px 10px', fontSize: 12, cursor: 'pointer' },
  err: { background: '#fef2f2', color: '#b91c1c', borderRadius: 8, padding: '10px 12px', marginBottom: 12 },
};
