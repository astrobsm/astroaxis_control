// Batches, quarantine and recall.
//
// THE SCREEN THIS REFUSES TO DRAW
// -------------------------------
// A traceability dial reading "94%". What that number means is that some
// quantity of medical goods is somewhere nobody can name, and a percentage
// dressed in green makes it look like a pass mark. The traceability panel shows
// two absolute quantities side by side and names the untraceable one.
//
// The recall screen shows two lists for the same reason: stock still held can
// be stopped with a phone call to a warehouse, stock already despatched has to
// be chased to a named customer. Merging them into a total would hide which
// work is urgent.

import React, { useCallback, useEffect, useState } from 'react';
import { authedFetch } from './utils/api';
import { color, radius, space } from './ui/theme';
import {
  Banner, Btn, Card, Chip, DataTable, ErrorBox, KpiCard, SectionTitle,
  SkeletonCards,
} from './ui/kit';

async function req(url, opts) {
  const res = await authedFetch(url, opts);
  if (!res.ok) {
    let d = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      d = typeof j.detail === 'string' ? j.detail : (j.detail?.message || j.message || d);
    } catch { /* keep the status */ }
    throw new Error(d);
  }
  return res.json();
}
const getJSON = (u) => req(u);
const postJSON = (u, b) => req(u, { method: 'POST', body: JSON.stringify(b || {}) });

const input = {
  width: '100%', padding: '9px 11px', borderRadius: radius.sm,
  border: `1px solid ${color.borderStrong}`, fontSize: 13.5,
  fontFamily: 'inherit', boxSizing: 'border-box', background: '#fff',
};

const STATUS_TONE = {
  AVAILABLE: 'success', QUARANTINED: 'warning', RECALLED: 'danger',
  WITHDRAWN: 'danger', CONSUMED: 'neutral',
};

const qty = (v) => Number(v).toLocaleString('en-NG', { maximumFractionDigits: 2 });

function Field({ label, hint, children }) {
  return (
    <label style={{ display: 'block', marginBottom: space(1.5) }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: color.textSecondary,
        marginBottom: 5 }}>{label}</div>
      {children}
      {hint && (
        <div style={{ fontSize: 11, color: color.textMuted, marginTop: 4,
          lineHeight: 1.5 }}>{hint}</div>
      )}
    </label>
  );
}

// ---------------------------------------------------------------------------
// Recall
// ---------------------------------------------------------------------------

function TracePanel({ trace }) {
  const held = Number(trace.quantity_still_held || 0);
  const gone = Number(trace.quantity_despatched || 0);

  return (
    <div>
      <div style={{ display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
        gap: space(2), marginBottom: space(2) }}>
        <KpiCard icon="asset" label="Still on our shelves" value={qty(held)}
          sub="Can be stopped now" tone={held > 0 ? 'warning' : 'success'} />
        <KpiCard icon="alert" label="Already despatched" value={qty(gone)}
          sub={`${trace.recipients} recipient(s) to contact`}
          tone={gone > 0 ? 'danger' : 'success'} />
      </div>

      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <SectionTitle>Where it still is — stop these</SectionTitle>
        <DataTable
          cols={[
            { key: 'name', label: 'Location', wrap: true },
            { key: 'warehouse_kind', label: 'Type' },
            { key: 'distributor', label: 'Held by', wrap: true },
            { key: 'on_hand', label: 'Quantity', align: 'right' },
          ]}
          rows={trace.still_held}
          empty="None on hand anywhere."
          render={(r, c) => {
            if (c.key === 'on_hand') return qty(r.on_hand);
            if (c.key === 'warehouse_kind') {
              return (
                <Chip tone={r.distributor_id ? 'info' : 'neutral'}>
                  {r.distributor_id ? 'Distributor' : 'Company'}
                </Chip>
              );
            }
            return r[c.key] || '—';
          }}
        />
      </Card>

      <Card pad={2.5}>
        <SectionTitle>Who was already sent it — these must be contacted</SectionTitle>
        <DataTable
          cols={[
            { key: 'customer', label: 'Recipient', wrap: true },
            { key: 'phone', label: 'Phone' },
            { key: 'order_number', label: 'Order' },
            { key: 'quantity', label: 'Quantity', align: 'right' },
            { key: 'order_date', label: 'Sent' },
          ]}
          rows={trace.despatched_to}
          empty="Nothing has left the building."
          render={(r, c) => {
            if (c.key === 'quantity') return qty(r.quantity);
            if (c.key === 'order_date') {
              return r.order_date
                ? new Date(r.order_date).toLocaleDateString() : '—';
            }
            if (c.key === 'customer') {
              return (
                <span>{r.customer}
                  {r.distributor && (
                    <div style={{ fontSize: 11, color: color.textMuted }}>
                      via {r.distributor}
                    </div>
                  )}
                </span>
              );
            }
            return r[c.key] || '—';
          }}
        />
        {trace.despatched_to.length > 0 && (
          <div style={{ marginTop: space(1.5) }}>
            <Banner tone="danger" title="This list is the recall">
              Each of these people has the affected goods. The app has blocked
              further despatch; contacting them is work only a person can do.
            </Banner>
          </div>
        )}
      </Card>
    </div>
  );
}

function BatchDetail({ batchId, onClose, onChanged }) {
  const [data, setData] = useState(null);
  const [trace, setTrace] = useState(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [d, t] = await Promise.all([
        getJSON(`/api/batches/${batchId}`),
        getJSON(`/api/batches/${batchId}/trace`),
      ]);
      setData(d); setTrace(t); setErr('');
    } catch (e) { setErr(e.message); }
  }, [batchId]);

  useEffect(() => { load(); }, [load]);

  if (err && !data) return <ErrorBox msg={err} />;
  if (!data) return <SkeletonCards n={3} />;

  const act = async (status, prompt) => {
    const reason = window.prompt(prompt);
    if (!reason || reason.trim().length < 3) return;
    setBusy(true);
    try {
      await postJSON(`/api/batches/${batchId}/status`,
        { status, reason: reason.trim() });
      await load();
      onChanged && onChanged();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  return (
    <div>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center',
          flexWrap: 'wrap' }}>
          <strong style={{ fontSize: 16 }}>{data.batch_number}</strong>
          <Chip tone={STATUS_TONE[data.status] || 'neutral'}>{data.status}</Chip>
          {data.expired && <Chip tone="danger">Expired</Chip>}
          <Btn size="sm" variant="ghost" style={{ marginLeft: 'auto' }}
            onClick={onClose}>Close</Btn>
        </div>
        <div style={{ marginTop: 6, fontSize: 13.5, color: color.textSecondary }}>
          {data.product_name} · {data.sku}
        </div>
        <div style={{ marginTop: space(1.5), display: 'flex', gap: space(3),
          flexWrap: 'wrap', fontSize: 13 }}>
          <div><strong>{qty(data.on_hand)}</strong> on hand</div>
          <div>Expires {data.expiry_date || 'not recorded'}</div>
          <div>Made {data.manufactured_on || 'not recorded'}</div>
          <div>{data.origin}</div>
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: space(2),
          flexWrap: 'wrap' }}>
          {data.status === 'AVAILABLE' && (
            <Btn size="sm" variant="secondary" disabled={busy}
              onClick={() => act('QUARANTINED',
                'Hold this batch. Why? (this is the record the decision is judged on)')}>
              Quarantine
            </Btn>
          )}
          {data.status === 'QUARANTINED' && (
            <Btn size="sm" variant="accent" disabled={busy}
              onClick={() => act('AVAILABLE',
                'Release this batch back to stock. On what basis?')}>
              Release
            </Btn>
          )}
          {data.status !== 'RECALLED' && (
            <Btn size="sm" variant="danger" disabled={busy}
              onClick={() => act('RECALLED',
                'Recall this batch. This cannot be undone. Why?')}>
              Recall
            </Btn>
          )}
        </div>
        {data.status !== 'RECALLED' && (
          <div style={{ fontSize: 11.5, color: color.textMuted, marginTop: 10,
            lineHeight: 1.6 }}>
            A recall is permanent — a recalled batch can never be returned to
            sale. If a recall is later found to be unnecessary, that is a
            decision recorded against new stock, not an edit to this record.
          </div>
        )}
      </Card>

      {trace && <TracePanel trace={trace} />}

      <Card pad={2.5} style={{ marginTop: space(2) }}>
        <SectionTitle>What was decided, and by whom</SectionTitle>
        <DataTable
          cols={[
            { key: 'created_at', label: 'When' },
            { key: 'to_status', label: 'Became' },
            { key: 'decided_by', label: 'Decided by' },
            { key: 'reason', label: 'Why', wrap: true },
          ]}
          rows={data.history}
          empty="No history."
          render={(r, c) => {
            if (c.key === 'created_at') {
              return new Date(r.created_at).toLocaleString();
            }
            if (c.key === 'to_status') {
              return <Chip tone={STATUS_TONE[r.to_status] || 'neutral'}>
                {r.to_status}</Chip>;
            }
            return r[c.key] || '—';
          }}
        />
        <div style={{ fontSize: 11, color: color.textMuted, marginTop: 10 }}>
          Append-only. This is the record a recall is judged on.
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main screen
// ---------------------------------------------------------------------------

export default function Batches() {
  const [tab, setTab] = useState('batches');
  const [rows, setRows] = useState(null);
  const [expiring, setExpiring] = useState(null);
  const [trace, setTrace] = useState(null);
  const [products, setProducts] = useState([]);
  const [open, setOpen] = useState(null);
  const [creating, setCreating] = useState(null);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    try {
      const [b, e, t, p] = await Promise.all([
        getJSON('/api/batches'),
        getJSON('/api/batches/expiring?within_days=90'),
        getJSON('/api/batches/traceability'),
        getJSON('/api/products/?limit=500').catch(() => ({ items: [] })),
      ]);
      setRows(b.batches || []);
      setExpiring(e);
      setTrace(t);
      setProducts(p.items || p.data || []);
      setErr('');
    } catch (ex) { setErr(ex.message); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const create = async () => {
    try {
      await postJSON('/api/batches', creating);
      setCreating(null);
      await load();
    } catch (e) { setErr(e.message); }
  };

  if (!rows) return <SkeletonCards n={4} />;

  if (open) {
    return (
      <div>
        <Btn size="sm" variant="ghost" style={{ marginBottom: space(2) }}
          onClick={() => { setOpen(null); load(); }}>← Back to batches</Btn>
        <BatchDetail batchId={open} onClose={() => { setOpen(null); load(); }}
          onChanged={load} />
      </div>
    );
  }

  const untraceable = Number(trace?.untraceable_quantity || 0);
  const traceable = Number(trace?.traceable_quantity || 0);

  return (
    <div>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <div style={{ display: 'flex', gap: space(1), flexWrap: 'wrap',
        marginBottom: space(2.5) }}>
        {[['batches', 'Batches'], ['expiring', 'Expiring'],
          ['traceability', 'Traceability']].map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)} style={{
            padding: '8px 15px', borderRadius: radius.pill, fontSize: 13,
            fontWeight: 600,
            border: `1px solid ${tab === k ? color.medical : color.borderStrong}`,
            background: tab === k ? color.infoBg : '#fff',
            color: tab === k ? color.royal : color.textSecondary,
            cursor: 'pointer',
          }}>{label}</button>
        ))}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <Btn size="sm" variant="accent" icon="asset"
            onClick={() => setCreating({ product_id: '', batch_number: '',
              expiry_date: '', manufactured_on: '', origin: 'PRODUCTION' })}>
            Register a batch
          </Btn>
          <Btn size="sm" variant="ghost" icon="refresh" onClick={load}>Refresh</Btn>
        </div>
      </div>

      {creating && (
        <Card pad={2.5} style={{ marginBottom: space(2) }}>
          <SectionTitle>Register a batch</SectionTitle>
          <Banner tone="info" title="This puts no stock anywhere">
            Registering a batch records that it exists. Receiving the goods into
            a warehouse is a separate step, so nothing can conjure inventory by
            filling in this form.
          </Banner>
          <Field label="Product">
            <select style={input} value={creating.product_id}
              onChange={(e) => setCreating({ ...creating, product_id: e.target.value })}>
              <option value="">Choose…</option>
              {products.map((p) => (
                <option key={p.id} value={p.id}>{p.name} ({p.sku})</option>
              ))}
            </select>
          </Field>
          <Field label="Batch number" hint="Exactly as printed on the goods.">
            <input style={input} value={creating.batch_number}
              onChange={(e) => setCreating({ ...creating, batch_number: e.target.value })} />
          </Field>
          <Field label="Expiry date"
            hint="Leave blank only if the product genuinely does not expire. Blank is recorded as unknown, which is not the same thing — and an unknown date sorts last when picking.">
            <input type="date" style={input} value={creating.expiry_date}
              onChange={(e) => setCreating({ ...creating, expiry_date: e.target.value })} />
          </Field>
          <Field label="Manufactured on">
            <input type="date" style={input} value={creating.manufactured_on}
              onChange={(e) => setCreating({ ...creating, manufactured_on: e.target.value })} />
          </Field>
          <Field label="Origin">
            <select style={input} value={creating.origin}
              onChange={(e) => setCreating({ ...creating, origin: e.target.value })}>
              <option value="PRODUCTION">Made here</option>
              <option value="PURCHASE">Bought in</option>
              <option value="OPENING">Existing stock being given a batch</option>
            </select>
          </Field>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <Btn size="sm" variant="ghost" onClick={() => setCreating(null)}>Cancel</Btn>
            <Btn size="sm" variant="accent"
              disabled={!creating.product_id || creating.batch_number.length < 2}
              onClick={create}>Register</Btn>
          </div>
        </Card>
      )}

      {tab === 'batches' && (
        <Card pad={2.5}>
          <SectionTitle>Batches with stock on hand</SectionTitle>
          <DataTable
            cols={[
              { key: 'batch_number', label: 'Batch' },
              { key: 'product', label: 'Product', wrap: true },
              { key: 'on_hand', label: 'On hand', align: 'right' },
              { key: 'expiry_date', label: 'Expires' },
              { key: 'status', label: 'Status' },
              { key: 'act', label: '', align: 'right' },
            ]}
            rows={rows}
            empty="No batches recorded yet."
            render={(r, c) => {
              if (c.key === 'on_hand') return qty(r.on_hand);
              if (c.key === 'status') {
                return <Chip tone={STATUS_TONE[r.status] || 'neutral'}>
                  {r.status}</Chip>;
              }
              if (c.key === 'expiry_date') {
                return r.expiry_date || (
                  <span style={{ color: color.textMuted }}>not recorded</span>
                );
              }
              if (c.key === 'act') {
                return <Btn size="sm" variant="ghost"
                  onClick={() => setOpen(r.id)}>Open</Btn>;
              }
              return r[c.key] || '—';
            }}
          />
        </Card>
      )}

      {tab === 'expiring' && expiring && (
        <>
          {expiring.expired.length > 0 && (
            <Banner tone="danger" title={`${expiring.expired.length} batch(es) have expired and still hold stock`}>
              These cannot be despatched — the app blocks it. They need writing
              off or returning.
            </Banner>
          )}
          <Card pad={2.5}>
            <SectionTitle>Expired and expiring within 90 days</SectionTitle>
            <DataTable
              cols={[
                { key: 'batch_number', label: 'Batch' },
                { key: 'product', label: 'Product', wrap: true },
                { key: 'on_hand', label: 'On hand', align: 'right' },
                { key: 'expiry_date', label: 'Expires' },
                { key: 'days_left', label: 'Days', align: 'right' },
              ]}
              rows={[...expiring.expired, ...expiring.expiring_soon]}
              empty="Nothing expiring in the next 90 days."
              render={(r, c) => {
                if (c.key === 'on_hand') return qty(r.on_hand);
                if (c.key === 'days_left') {
                  const n = Number(r.days_left);
                  return (
                    <span style={{ color: n < 0 ? color.danger : color.text,
                      fontWeight: n < 0 ? 700 : 400 }}>
                      {n < 0 ? `${Math.abs(n)} ago` : n}
                    </span>
                  );
                }
                return r[c.key] || '—';
              }}
            />
          </Card>
        </>
      )}

      {tab === 'traceability' && trace && (
        <>
          {/* Two quantities, side by side. Never one percentage. */}
          <div style={{ display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
            gap: space(2), marginBottom: space(2) }}>
            <KpiCard icon="check" label="Traceable to a batch"
              value={qty(traceable)} sub="Can be recalled" tone="success" />
            <KpiCard icon="alert" label="Not traceable"
              value={qty(untraceable)}
              sub="A recall cannot reach this"
              tone={untraceable > 0 ? 'danger' : 'success'} />
          </div>

          <Card pad={2.5}>
            <SectionTitle>What this means</SectionTitle>
            <div style={{ fontSize: 13.5, lineHeight: 1.8 }}>{trace.note}</div>
            {!trace.reconciles && (
              <div style={{ marginTop: space(2) }}>
                <Banner tone="danger" title="The movement history and the stock balances disagree">
                  {trace.reconciliation_note} Difference: {qty(trace.discrepancy)}.
                </Banner>
              </div>
            )}
            <div style={{ marginTop: space(2), fontSize: 12.5,
              color: color.textMuted, lineHeight: 1.7 }}>
              Stock balances total {qty(trace.stock_levels_total)}; the movement
              history totals {qty(trace.movements_total)}. Untraceable stock is
              not a fault to be fixed by assigning batch numbers retrospectively
              — that would be inventing a record. It clears as the stock sells
              through, or sooner if a physical count assigns real batch numbers
              to what is actually on the shelf.
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
