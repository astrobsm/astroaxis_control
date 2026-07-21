// Shared API helper for the Network & WiFi Management module.
import API_BASE_URL from './config';
import { authedFetch } from './utils/api';

export const authHeaders = () => {
  const t = localStorage.getItem('access_token');
  return t ? { Authorization: `Bearer ${t}` } : {};
};

export async function wifiApi(path, opts = {}) {
  const res = await authedFetch(`${API_BASE_URL}${path}`, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(opts.headers || {}),
    },
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      msg = j.detail || j.message || msg;
    } catch (e) { /* ignore */ }
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  const ct = res.headers.get('content-type') || '';
  return ct.includes('json') ? res.json() : res.text();
}

export const fmtDateTime = (d) => {
  if (!d) return '—';
  try { return new Date(d).toLocaleString(); } catch (e) { return d; }
};
