import React, { useEffect, useMemo, useRef, useState } from 'react';
import { authedFetch } from './utils/api';

// Geomap module — visualises geo-tagged staff clock-in/out events and
// platform access (login) events on a Leaflet map.
// Leaflet itself is loaded via <link>/<script> tags in public/index.html
// so it is available on window.L.

const DEFAULT_CENTER = [6.5244, 3.3792]; // Lagos
const DEFAULT_ZOOM = 6;

function fmtTime(iso) {
  if (!iso) return '';
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function escapeHtml(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export default function Geomap() {
  const [tab, setTab] = useState('attendance'); // 'attendance' | 'access'
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [attendance, setAttendance] = useState([]);
  const [access, setAccess] = useState([]);

  const mapElRef = useRef(null);
  const mapRef = useRef(null);
  const layerRef = useRef(null);

  // Initialise Leaflet map once (waits for the deferred script to load).
  useEffect(() => {
    let cancelled = false;
    const init = () => {
      if (cancelled) return;
      const L = window.L;
      if (!L) { setTimeout(init, 250); return; }
      if (!mapElRef.current || mapRef.current) return;
      const map = L.map(mapElRef.current).setView(DEFAULT_CENTER, DEFAULT_ZOOM);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap contributors',
      }).addTo(map);
      mapRef.current = map;
      layerRef.current = L.layerGroup().addTo(map);
      // Force a resize once visible (containers that mount hidden render at 0x0)
      setTimeout(() => { try { map.invalidateSize(); } catch {} }, 200);
    };
    init();
    return () => { cancelled = true; };
  }, []);

  // Re-invalidate map size when the tab/parent layout changes.
  useEffect(() => {
    if (mapRef.current) {
      setTimeout(() => { try { mapRef.current.invalidateSize(); } catch {} }, 150);
    }
  }, [tab]);

  // Fetch data when tab or days change.
  useEffect(() => {
    let abort = false;
    async function load() {
      setLoading(true);
      setError('');
      try {
        const token = localStorage.getItem('token');
        const headers = token ? { Authorization: `Bearer ${token}` } : {};
        const url = tab === 'attendance'
          ? `/api/geo/attendance?days=${days}&limit=1000`
          : `/api/geo/access?days=${days}&limit=1000`;
        const res = await authedFetch(url, { headers });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (abort) return;
        if (tab === 'attendance') setAttendance(Array.isArray(data) ? data : []);
        else setAccess(Array.isArray(data) ? data : []);
      } catch (e) {
        if (!abort) setError(e.message || 'Failed to load');
      } finally {
        if (!abort) setLoading(false);
      }
    }
    load();
    return () => { abort = true; };
  }, [tab, days]);

  const points = useMemo(() => {
    if (tab === 'attendance') {
      return attendance.map(r => ({
        id: `${r.attendance_id}-${r.event}`,
        lat: r.lat, lng: r.lng,
        title: r.staff_name || r.employee_id || 'Staff',
        subtitle: r.event === 'clock_in' ? 'Clock In' : 'Clock Out',
        when: r.at,
        address: r.address,
        accuracy: r.accuracy,
        color: r.event === 'clock_in' ? '#16a34a' : '#dc2626',
      })).filter(p => typeof p.lat === 'number' && typeof p.lng === 'number');
    }
    return access.map(r => ({
      id: r.id,
      lat: r.lat, lng: r.lng,
      title: r.user_name || r.user_email || 'User',
      subtitle: r.action || 'access',
      when: r.at,
      address: r.address,
      accuracy: r.accuracy,
      ip: r.ip_address,
      color: '#2563eb',
    })).filter(p => typeof p.lat === 'number' && typeof p.lng === 'number');
  }, [tab, attendance, access]);

  // Render markers when points change.
  useEffect(() => {
    const L = window.L;
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!L || !map || !layer) return;
    layer.clearLayers();
    if (!points.length) return;
    const bounds = [];
    points.forEach(p => {
      const marker = L.circleMarker([p.lat, p.lng], {
        radius: 7,
        color: p.color,
        weight: 2,
        fillColor: p.color,
        fillOpacity: 0.55,
      });
      const popup = `
        <div style="min-width:200px">
          <div style="font-weight:700">${escapeHtml(p.title)}</div>
          <div style="color:#64748b;font-size:12px">${escapeHtml(p.subtitle)}</div>
          <div style="margin-top:4px;font-size:12px">${escapeHtml(fmtTime(p.when))}</div>
          ${p.address ? `<div style="margin-top:4px;font-size:12px">${escapeHtml(p.address)}</div>` : ''}
          ${p.ip ? `<div style="margin-top:2px;font-size:11px;color:#64748b">IP: ${escapeHtml(p.ip)}</div>` : ''}
          ${typeof p.accuracy === 'number' ? `<div style="font-size:11px;color:#64748b">±${Math.round(p.accuracy)} m</div>` : ''}
        </div>`;
      marker.bindPopup(popup);
      marker.addTo(layer);
      bounds.push([p.lat, p.lng]);
    });
    if (bounds.length === 1) {
      map.setView(bounds[0], 14);
    } else if (bounds.length > 1) {
      try { map.fitBounds(bounds, { padding: [30, 30] }); } catch {}
    }
  }, [points]);

  return (
    <div className="content">
      <div className="content-header">
        <h2>Geomap — Locations</h2>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <div className="btn-group" role="group">
            <button
              className={`btn ${tab === 'attendance' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setTab('attendance')}
            >Staff Attendance</button>
            <button
              className={`btn ${tab === 'access' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setTab('access')}
            >Access Log</button>
          </div>
          <label style={{ fontSize: 13 }}>
            Days:
            <select
              value={days}
              onChange={e => setDays(parseInt(e.target.value, 10))}
              style={{ marginLeft: 6 }}
            >
              <option value={1}>1</option>
              <option value={7}>7</option>
              <option value={30}>30</option>
              <option value={90}>90</option>
            </select>
          </label>
        </div>
      </div>

      {error && <div className="alert alert-danger" style={{ marginBottom: 8 }}>{error}</div>}
      <div style={{ fontSize: 13, color: '#64748b', marginBottom: 8 }}>
        {loading ? 'Loading…' : `${points.length} geo-tagged ${tab === 'attendance' ? 'attendance events' : 'access events'} in the last ${days} day(s).`}
      </div>

      <div
        ref={mapElRef}
        style={{
          width: '100%',
          height: '70vh',
          minHeight: 480,
          borderRadius: 8,
          overflow: 'hidden',
          border: '1px solid #e2e8f0',
        }}
      />

      <div style={{ marginTop: 12, maxHeight: 240, overflow: 'auto', border: '1px solid #e2e8f0', borderRadius: 6 }}>
        <table className="data-table" style={{ width: '100%' }}>
          <thead>
            <tr>
              <th>{tab === 'attendance' ? 'Staff' : 'User'}</th>
              <th>{tab === 'attendance' ? 'Event' : 'Action'}</th>
              <th>When</th>
              <th>Address</th>
              {tab === 'access' && <th>IP</th>}
            </tr>
          </thead>
          <tbody>
            {points.length === 0 && (
              <tr><td colSpan={tab === 'access' ? 5 : 4} style={{ textAlign: 'center', padding: 12, color: '#94a3b8' }}>No geo-tagged events yet.</td></tr>
            )}
            {points.map(p => (
              <tr key={p.id}>
                <td>{p.title}</td>
                <td>{p.subtitle}</td>
                <td>{fmtTime(p.when)}</td>
                <td style={{ fontSize: 12 }}>{p.address || <span style={{ color: '#94a3b8' }}>—</span>}</td>
                {tab === 'access' && <td style={{ fontSize: 12 }}>{p.ip || ''}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
