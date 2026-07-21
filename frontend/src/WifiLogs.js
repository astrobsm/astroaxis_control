// Authentication Logs page — audit trail of Wi-Fi auth attempts.
import React, { useEffect, useState } from 'react';
import { wifiApi, fmtDateTime } from './wifiApi';

export default function WifiLogs() {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('all');
  const [err, setErr] = useState('');

  const load = async (result) => {
    setLoading(true);
    setErr('');
    try {
      const q = result && result !== 'all' ? `?result_filter=${result}` : '';
      setLogs(await wifiApi(`/api/wifi/logs${q}`));
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(filter); }, [filter]);

  return (
    <div>
      <div style={s.toolbar}>
        <select style={s.select} value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="all">All</option>
          <option value="success">Success</option>
          <option value="failure">Failure</option>
        </select>
        <button style={s.refresh} onClick={() => load(filter)}>Refresh</button>
      </div>
      {err && <div style={s.err}>{err}</div>}
      {loading ? <div style={{ padding: 16 }}>Loading…</div> : (
        <div style={{ overflowX: 'auto' }}>
          <table style={s.table}>
            <thead>
              <tr>
                <th style={s.th}>Time</th>
                <th style={s.th}>Username</th>
                <th style={s.th}>IP</th>
                <th style={s.th}>MAC</th>
                <th style={s.th}>Result</th>
                <th style={s.th}>Reason</th>
              </tr>
            </thead>
            <tbody>
              {logs.length === 0 && <tr><td colSpan={6} style={s.empty}>No log entries.</td></tr>}
              {logs.map((l) => (
                <tr key={l.id}>
                  <td style={s.td}>{fmtDateTime(l.timestamp)}</td>
                  <td style={s.td}>{l.username || '—'}</td>
                  <td style={s.td}>{l.ip_address || '—'}</td>
                  <td style={s.td}>{l.device_mac || '—'}</td>
                  <td style={s.td}><span style={badge(l.authentication_result)}>{l.authentication_result}</span></td>
                  <td style={s.td}>{l.failure_reason || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const badge = (result) => ({
  padding: '2px 10px', borderRadius: 12, fontSize: 11, fontWeight: 600, textTransform: 'uppercase',
  color: '#fff', background: result === 'success' ? '#16a34a' : '#dc2626',
});

const s = {
  toolbar: { display: 'flex', gap: 10, marginBottom: 14 },
  select: { border: '1px solid #d1d5db', borderRadius: 8, padding: '8px 10px' },
  refresh: { border: '1px solid #d1d5db', background: '#f9fafb', borderRadius: 8, padding: '8px 14px', cursor: 'pointer' },
  table: { width: '100%', borderCollapse: 'collapse', background: '#fff', borderRadius: 8 },
  th: { textAlign: 'left', padding: '10px 12px', fontSize: 12, color: '#6b7280', borderBottom: '2px solid #e5e7eb', textTransform: 'uppercase' },
  td: { padding: '10px 12px', fontSize: 14, borderBottom: '1px solid #f1f5f9' },
  empty: { padding: 20, textAlign: 'center', color: '#9ca3af' },
  err: { background: '#fef2f2', color: '#b91c1c', borderRadius: 8, padding: '10px 12px', marginBottom: 12 },
};
