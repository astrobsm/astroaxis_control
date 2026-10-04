// The delivery desk: runs that need closing, and goods that need putting back.
//
// Mounted from AppMain when activeModule === 'deliveryDesk'.
//
// WHY THESE TWO LISTS AND NOTHING ELSE
// ------------------------------------
// The manifest screens already exist and work. What had no home at all was
// the aftermath: a van that went out and was never closed, and goods that
// came back from a failed delivery and are missing from stock.
//
// Both are invisible everywhere else in the system, and both cost money
// quietly — the first because nobody can say whether a customer got their
// goods, the second because the stock reappears months later at a count as an
// unexplained surplus.

import React, { useCallback, useEffect, useState } from 'react';
import { authedFetch } from './utils/api';
import { color, radius, space } from './ui/theme';
import { Banner, Btn, Card, Chip, ErrorBox, SkeletonCards } from './ui/kit';

async function req(url, opts) {
  const res = await authedFetch(url, opts);
  if (!res.ok) {
    let d = `Request failed (${res.status})`;
    try { const j = await res.json(); d = j.detail || j.message || d; } catch { /* keep */ }
    throw new Error(typeof d === 'string' ? d : 'Request failed');
  }
  return res.json();
}

const inputStyle = {
  width: '100%', padding: '9px 11px', borderRadius: radius.sm, fontSize: 13.5,
  border: `1px solid ${color.borderStrong}`, fontFamily: 'inherit',
  boxSizing: 'border-box', background: '#fff', color: color.text,
};

function CloseRunDialog({ run, onClose, onDone, notify }) {
  const [drops, setDrops] = useState(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [reasons, setReasons] = useState({});

  const load = useCallback(async () => {
    try {
      const m = await req(`/api/logistics/manifests/${run.manifest_id}`);
      setDrops(m.customers || m.drops || []);
      setErr('');
    } catch (e) { setErr(e.message); }
  }, [run.manifest_id]);

  useEffect(() => { load(); }, [load]);

  const mark = async (dropId, status) => {
    setBusy(true); setErr('');
    try {
      await req(`/api/delivery/drops/${dropId}/status`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status, reason: reasons[dropId] || null }),
      });
      await load(); onDone();
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  const closeRun = async () => {
    setBusy(true); setErr('');
    try {
      await req(`/api/delivery/manifests/${run.manifest_id}/status`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'completed' }),
      });
      if (notify) notify(`${run.manifest_number} closed.`, 'success');
      onDone(); onClose();
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  const open = (drops || []).filter(
    (d) => d.status === 'pending' || d.status === 'out_for_delivery');

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.55)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 2000, padding: 16,
    }} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={{
        background: '#fff', borderRadius: radius.lg, width: 'min(640px, 100%)',
        maxHeight: '90vh', overflow: 'auto', padding: space(3),
      }}>
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 800,
          color: color.navy }}>{run.manifest_number}</h3>
        <div style={{ fontSize: 12.5, color: color.textSecondary, marginTop: 4 }}>
          Dispatched {run.delivery_date} · {run.days_old} days ago
          {run.driver ? ` · ${run.driver}` : ''}
        </div>

        {err && <ErrorBox msg={err} />}

        <div style={{ margin: `${space(2)} 0` }}>
          <Banner tone="warning" title="Say what happened at each stop">
            Each of these is a customer who may or may not have received their
            goods. The run cannot be closed until every one has an outcome.
          </Banner>
        </div>

        {!drops ? <SkeletonCards n={2} /> : open.length === 0 ? (
          <div style={{ fontSize: 13, color: color.success, padding: space(2),
            textAlign: 'center' }}>
            Every stop has an outcome. The run can be closed.
          </div>
        ) : open.map((d) => (
          <div key={d.id} style={{ padding: '12px 0',
            borderBottom: `1px solid ${color.border}` }}>
            <div style={{ fontSize: 13.5, fontWeight: 600 }}>
              {d.customer_name}
            </div>
            <div style={{ fontSize: 11.5, color: color.textSecondary,
              marginBottom: 8 }}>
              {d.delivery_address || d.city || ''}
            </div>
            <input style={{ ...inputStyle, marginBottom: 8 }}
              placeholder="Reason, if it failed"
              value={reasons[d.id] || ''}
              onChange={(e) => setReasons((r) => ({ ...r, [d.id]: e.target.value }))} />
            <div style={{ display: 'flex', gap: 8 }}>
              <Btn size="sm" variant="accent" disabled={busy}
                onClick={() => mark(d.id, 'delivered')}>Delivered</Btn>
              <Btn size="sm" variant="danger" disabled={busy}
                onClick={() => mark(d.id, 'failed')}>Failed</Btn>
            </div>
          </div>
        ))}

        <div style={{ display: 'flex', gap: 10, marginTop: space(3),
          justifyContent: 'flex-end' }}>
          <Btn variant="secondary" onClick={onClose}>Close</Btn>
          <Btn onClick={closeRun} disabled={busy || open.length > 0}>
            Close the run
          </Btn>
        </div>
      </div>
    </div>
  );
}

function ReturnDialog({ drop, onClose, onDone, notify }) {
  const [warehouses, setWarehouses] = useState([]);
  const [warehouse, setWarehouse] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    req('/api/warehouses/?limit=100')
      .then((r) => {
        const list = r.items || r.warehouses || [];
        setWarehouses(list);
        if (list.length === 1) setWarehouse(list[0].id);
      })
      .catch(() => {});
  }, []);

  const submit = async () => {
    setBusy(true); setErr('');
    try {
      const result = await req(
        `/api/delivery/drops/${drop.drop_id}/return-to-stock`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ warehouse_id: warehouse }),
        });
      if (notify) notify(result.note, 'success');
      onDone(); onClose();
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.55)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 2000, padding: 16,
    }} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={{
        background: '#fff', borderRadius: radius.lg, width: 'min(520px, 100%)',
        padding: space(3),
      }}>
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 800,
          color: color.navy }}>Put the goods back on the shelf</h3>
        <div style={{ fontSize: 13, marginTop: 6 }}>
          {drop.customer} · {drop.lines} line{drop.lines === 1 ? '' : 's'}
        </div>
        <div style={{ fontSize: 12, color: color.textSecondary, marginTop: 4 }}>
          Failed {drop.failed_at
            ? new Date(drop.failed_at).toLocaleDateString() : ''} — {drop.reason}
        </div>

        {err && <ErrorBox msg={err} />}

        <div style={{ marginTop: space(2) }}>
          <label style={{ fontSize: 12, fontWeight: 600,
            color: color.textSecondary }}>Which warehouse took them back?</label>
          <select style={{ ...inputStyle, marginTop: 5 }} value={warehouse}
            onChange={(e) => setWarehouse(e.target.value)}>
            <option value="">Choose…</option>
            {warehouses.map((w) => (
              <option key={w.id} value={w.id}>{w.name}</option>
            ))}
          </select>
        </div>

        <div style={{ fontSize: 11.5, color: color.textSecondary,
          marginTop: space(2), lineHeight: 1.6 }}>
          This adds the stock back and records a movement. It runs once — the
          invoice is unchanged, and a credit note is a separate decision for
          whoever handles the account.
        </div>

        <div style={{ display: 'flex', gap: 10, marginTop: space(3),
          justifyContent: 'flex-end' }}>
          <Btn variant="secondary" onClick={onClose}>Cancel</Btn>
          <Btn onClick={submit} disabled={busy || !warehouse}>
            {busy ? 'Returning…' : 'Return to stock'}
          </Btn>
        </div>
      </div>
    </div>
  );
}

export default function DeliveryDesk({ notify }) {
  const [unclosed, setUnclosed] = useState(null);
  const [awaiting, setAwaiting] = useState(null);
  const [err, setErr] = useState('');
  const [closing, setClosing] = useState(null);
  const [returning, setReturning] = useState(null);

  const load = useCallback(async () => {
    try {
      const [u, a] = await Promise.all([
        req('/api/delivery/unclosed-runs'),
        req('/api/delivery/awaiting-return'),
      ]);
      setUnclosed(u); setAwaiting(a); setErr('');
    } catch (e) { setErr(e.message); }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (err) return <ErrorBox msg={err} />;
  if (!unclosed || !awaiting) return <SkeletonCards n={3} />;

  return (
    <div>
      <Card style={{ marginBottom: space(2) }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center',
          flexWrap: 'wrap', marginBottom: space(1) }}>
          <span style={{ fontSize: 14.5, fontWeight: 800, color: color.navy }}>
            Runs that went out and were never closed
          </span>
          {unclosed.count > 0 && (
            <Chip tone="warning">
              {unclosed.open_drops} stop{unclosed.open_drops === 1 ? '' : 's'}
            </Chip>
          )}
        </div>

        {unclosed.count === 0 ? (
          <div style={{ fontSize: 13, color: color.textSecondary,
            padding: space(2), textAlign: 'center' }}>
            Every dispatched run has been closed.
          </div>
        ) : (
          <>
            <div style={{ fontSize: 12, color: color.textSecondary,
              marginBottom: space(2), lineHeight: 1.6 }}>{unclosed.note}</div>
            {unclosed.runs.map((r) => (
              <div key={r.manifest_id} style={{ display: 'flex', gap: 12,
                alignItems: 'center', flexWrap: 'wrap', padding: '10px 0',
                borderBottom: `1px solid ${color.border}` }}>
                <div style={{ minWidth: 180, flex: 1 }}>
                  <div style={{ fontSize: 13.5, fontWeight: 600 }}>
                    {r.manifest_number}
                  </div>
                  <div style={{ fontSize: 11.5, color: color.textSecondary }}>
                    {r.delivery_date} · {r.days_old} days ago
                    {r.driver ? ` · ${r.driver}` : ''}
                  </div>
                </div>
                <Chip tone={r.days_old > 30 ? 'danger' : 'warning'}>
                  {r.open_drops} of {r.total_drops} unrecorded
                </Chip>
                <Btn size="sm" onClick={() => setClosing(r)}>Record outcomes</Btn>
              </div>
            ))}
          </>
        )}
      </Card>

      <Card>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center',
          flexWrap: 'wrap', marginBottom: space(1) }}>
          <span style={{ fontSize: 14.5, fontWeight: 800, color: color.navy }}>
            Goods back in the building, missing from stock
          </span>
          {awaiting.count > 0 && <Chip tone="danger">{awaiting.count}</Chip>}
        </div>

        {awaiting.count === 0 ? (
          <div style={{ fontSize: 13, color: color.textSecondary,
            padding: space(2), textAlign: 'center' }}>
            Nothing is waiting to be put back.
          </div>
        ) : (
          <>
            <div style={{ fontSize: 12, color: color.textSecondary,
              marginBottom: space(2), lineHeight: 1.6 }}>{awaiting.note}</div>
            {awaiting.drops.map((d) => (
              <div key={d.drop_id} style={{ display: 'flex', gap: 12,
                alignItems: 'center', flexWrap: 'wrap', padding: '10px 0',
                borderBottom: `1px solid ${color.border}` }}>
                <div style={{ minWidth: 180, flex: 1 }}>
                  <div style={{ fontSize: 13.5, fontWeight: 600 }}>
                    {d.customer}
                  </div>
                  <div style={{ fontSize: 11.5, color: '#B45309' }}>
                    {d.reason}
                  </div>
                  <div style={{ fontSize: 11, color: color.textMuted }}>
                    {d.manifest_number} · {d.lines} line
                    {d.lines === 1 ? '' : 's'}
                  </div>
                </div>
                <Btn size="sm" variant="accent"
                  onClick={() => setReturning(d)}>Return to stock</Btn>
              </div>
            ))}
          </>
        )}
      </Card>

      {closing && (
        <CloseRunDialog run={closing} notify={notify}
          onClose={() => setClosing(null)} onDone={load} />
      )}
      {returning && (
        <ReturnDialog drop={returning} notify={notify}
          onClose={() => setReturning(null)} onDone={load} />
      )}
    </div>
  );
}
