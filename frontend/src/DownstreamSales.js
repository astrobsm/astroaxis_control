// Downstream sales — what a distributor sold onward, and how well that is known.
//
// THE NUMBER THIS SCREEN WILL NOT DRAW
// ------------------------------------
// A single "sell-through" total. Almost all of this data is self-reported by
// the distributor, and a claim shown next to a confirmed fact in the same
// typeface becomes a fact by Tuesday. Verified and reported are two tiles, two
// colours, never added.
//
// The verified figure is the one that drives performance decisions, so it leads.
// The reported figure sits beside it labelled as unchecked, because hiding it
// would be its own kind of dishonesty — the company should see what is being
// claimed as well as what has been confirmed.

import React, { useCallback, useEffect, useState } from 'react';
import { authedFetch } from './utils/api';
import { color, naira, radius, space } from './ui/theme';
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

const PROVENANCE_TONE = {
  VERIFIED: 'success', REPORTED: 'warning', DISPUTED: 'danger',
};
const PROVENANCE_LABEL = {
  VERIFIED: 'Verified',
  REPORTED: 'Claimed, unchecked',
  DISPUTED: 'Disputed',
};

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
// One sale
// ---------------------------------------------------------------------------

function SaleDetail({ saleId, onClose, onChanged }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');

  const load = useCallback(async () => {
    try { setData(await getJSON(`/api/downstream/sales/${saleId}`)); setErr(''); }
    catch (e) { setErr(e.message); }
  }, [saleId]);

  useEffect(() => { load(); }, [load]);

  if (err && !data) return <ErrorBox msg={err} />;
  if (!data) return <SkeletonCards n={2} />;

  const s = data.sale;

  const upload = async (file) => {
    if (!file) return;
    setBusy(true);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('evidence_type', 'INVOICE');
      const res = await authedFetch(
        `/api/downstream/sales/${saleId}/evidence`,
        { method: 'POST', body: form });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.detail || 'Upload failed');
      }
      await load();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  const act = async (path, body) => {
    setBusy(true);
    try {
      await postJSON(`/api/downstream/sales/${saleId}/${path}`, body);
      await load();
      onChanged && onChanged();
      setNote('');
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  return (
    <div>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center',
          flexWrap: 'wrap' }}>
          <strong style={{ fontSize: 16 }}>{naira(s.total_amount)}</strong>
          <Chip tone={PROVENANCE_TONE[s.provenance]}>
            {PROVENANCE_LABEL[s.provenance]}
          </Chip>
          <code style={{ fontSize: 11, color: color.textMuted }}>
            {s.sale_reference}
          </code>
          <Btn size="sm" variant="ghost" style={{ marginLeft: 'auto' }}
            onClick={onClose}>Close</Btn>
        </div>
        <div style={{ marginTop: space(1), fontSize: 13.5, lineHeight: 1.8 }}>
          {s.legal_name} sold to <strong>{s.outlet || 'an unnamed outlet'}</strong>
          {s.outlet_type && ` (${s.outlet_type.toLowerCase()})`} on {s.sold_on}
          {s.marketer && <> · reported by marketer {s.marketer}</>}
        </div>
        <div style={{ marginTop: space(1.5) }}>
          <Banner tone={PROVENANCE_TONE[s.provenance]}
            title={PROVENANCE_LABEL[s.provenance]}>
            {data.provenance_note}
            {s.verified_by_name && (
              <div style={{ marginTop: 4 }}>
                By {s.verified_by_name}
                {s.verified_at && ` on ${new Date(s.verified_at).toLocaleDateString()}`}
                {s.verification_note && ` — ${s.verification_note}`}
              </div>
            )}
          </Banner>
        </div>

        {s.stock_discrepancy && (
          <Banner tone="danger" title="More was reported sold than we recorded shipping">
            {s.discrepancy_note}
            <div style={{ marginTop: 6 }}>
              Either the shipment record is incomplete or the sales report is
              inflated. Both are worth finding out. Stock was not adjusted.
            </div>
          </Banner>
        )}
      </Card>

      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <SectionTitle>What was sold</SectionTitle>
        <DataTable
          cols={[
            { key: 'product', label: 'Product', wrap: true },
            { key: 'batch_number', label: 'Batch' },
            { key: 'quantity', label: 'Qty', align: 'right' },
            { key: 'unit_price', label: 'Unit', align: 'right' },
            { key: 'line_total', label: 'Total', align: 'right' },
          ]}
          rows={data.lines}
          empty="No lines."
          render={(r, c) => {
            if (c.key === 'unit_price' || c.key === 'line_total') {
              return r[c.key] ? naira(r[c.key]) : '—';
            }
            if (c.key === 'batch_number') {
              return r.batch_number
                ? <code style={{ fontSize: 11.5 }}>{r.batch_number}</code>
                : <span style={{ color: color.textMuted }}>not recorded</span>;
            }
            if (c.key === 'quantity') {
              return `${Number(r.quantity)} ${r.unit || ''}`.trim();
            }
            return r[c.key] || '—';
          }}
        />
        {data.lines.some((l) => !l.batch_number) && (
          <div style={{ fontSize: 11.5, color: color.textMuted, marginTop: 10,
            lineHeight: 1.6 }}>
            Lines without a batch cannot be reached by a recall. Recording the
            batch at the point of sale is what lets the company name the outlet
            that bought it.
          </div>
        )}
      </Card>

      <Card pad={2.5}>
        <SectionTitle>Evidence</SectionTitle>
        <DataTable
          cols={[
            { key: 'filename', label: 'File', wrap: true },
            { key: 'evidence_type', label: 'Type' },
            { key: 'uploaded_by', label: 'Uploaded by' },
            { key: 'act', label: '', align: 'right' },
          ]}
          rows={data.evidence}
          empty="Nothing attached. A sale cannot be verified without evidence."
          render={(r, c) => {
            if (c.key === 'act') {
              return (
                <Btn size="sm" variant="ghost"
                  onClick={() => window.open(
                    `/api/downstream/evidence/${r.id}`, '_blank')}>View</Btn>
              );
            }
            return r[c.key] || '—';
          }}
        />

        <div style={{ marginTop: space(2) }}>
          <Field label="Attach an invoice, receipt or photograph">
            <input type="file" style={input} disabled={busy}
              accept="image/*,application/pdf"
              onChange={(e) => upload(e.target.files?.[0])} />
          </Field>
        </div>

        {s.provenance === 'REPORTED' && (
          <>
            <Field label="Note">
              <input style={input} value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="What did you check it against?" />
            </Field>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <Btn size="sm" variant="danger" disabled={busy}
                onClick={() => {
                  const reason = window.prompt('What was found to be wrong?');
                  if (reason && reason.trim().length >= 3) {
                    act('dispute', { reason: reason.trim() });
                  }
                }}>Dispute</Btn>
              <Btn size="sm" variant="accent"
                disabled={busy || data.evidence.length === 0}
                onClick={() => act('verify', { note: note || null })}>
                Verify
              </Btn>
            </div>
            <div style={{ fontSize: 11.5, color: color.textMuted, marginTop: 8,
              lineHeight: 1.6 }}>
              Verifying requires evidence on the record, and you cannot verify a
              sale you reported yourself. Once verified the figures are fixed.
            </div>
          </>
        )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The panel shown in the distributor dossier
// ---------------------------------------------------------------------------

export function DownstreamPanel({ distributorId }) {
  const [figures, setFigures] = useState(null);
  const [sales, setSales] = useState([]);
  const [marketers, setMarketers] = useState([]);
  const [outlets, setOutlets] = useState([]);
  const [products, setProducts] = useState([]);
  const [open, setOpen] = useState(null);
  const [recording, setRecording] = useState(null);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    try {
      const [f, s, m, o, p] = await Promise.all([
        getJSON(`/api/downstream/sell-through?distributor_id=${distributorId}`),
        getJSON(`/api/downstream/sales?distributor_id=${distributorId}`),
        getJSON(`/api/downstream/${distributorId}/by-marketer`),
        getJSON(`/api/downstream/${distributorId}/outlets`),
        getJSON('/api/products/?limit=500').catch(() => ({ items: [] })),
      ]);
      setFigures(f);
      setSales(s.sales || []);
      setMarketers(m.marketers || []);
      setOutlets(o.outlets || []);
      setProducts(p.items || p.data || []);
      setErr('');
    } catch (e) { setErr(e.message); }
  }, [distributorId]);

  useEffect(() => { load(); }, [load]);

  if (!figures) return <SkeletonCards n={3} />;

  if (open) {
    return (
      <div>
        <Btn size="sm" variant="ghost" style={{ marginBottom: space(2) }}
          onClick={() => { setOpen(null); load(); }}>← Back</Btn>
        <SaleDetail saleId={open} onClose={() => { setOpen(null); load(); }}
          onChanged={load} />
      </div>
    );
  }

  const record = async () => {
    try {
      await postJSON('/api/downstream/sales', {
        distributor_id: distributorId,
        sold_on: recording.sold_on,
        outlet_id: recording.outlet_id || null,
        marketer_id: recording.marketer_id || null,
        lines: [{
          product_id: recording.product_id,
          quantity: Number(recording.quantity),
          unit_price: recording.unit_price ? Number(recording.unit_price) : null,
        }],
      });
      setRecording(null);
      await load();
    } catch (e) { setErr(e.message); }
  };

  return (
    <div>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      {/* Two tiles. Never one. */}
      <div style={{ display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))',
        gap: space(2), marginBottom: space(2) }}>
        <KpiCard icon="check" label="Verified sell-through"
          value={naira(figures.verified_amount, { compact: true })}
          sub={`${figures.verified_count} sale(s) · counts toward performance`}
          tone="success" />
        <KpiCard icon="alert" label="Claimed, unchecked"
          value={naira(figures.reported_amount, { compact: true })}
          sub={`${figures.reported_count} sale(s) · counts toward nothing`}
          tone={Number(figures.reported_amount) > 0 ? 'warning' : 'neutral'} />
        {Number(figures.disputed_amount) > 0 && (
          <KpiCard icon="alert" label="Disputed"
            value={naira(figures.disputed_amount, { compact: true })}
            sub={`${figures.disputed_count} sale(s)`} tone="danger" />
        )}
        {figures.stock_discrepancies > 0 && (
          <KpiCard icon="alert" label="Stock discrepancies"
            value={figures.stock_discrepancies}
            sub="Reported more sold than shipped" tone="danger" />
        )}
      </div>

      <div style={{ fontSize: 12, color: color.textMuted, marginBottom: space(2),
        lineHeight: 1.7 }}>
        {figures.note}
      </div>

      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <SectionTitle right={!recording && (
          <Btn size="sm" variant="secondary"
            onClick={() => setRecording({
              sold_on: new Date().toISOString().slice(0, 10),
              product_id: '', quantity: '', unit_price: '',
              outlet_id: '', marketer_id: '',
            })}>Record a reported sale</Btn>
        )}>Sales reported</SectionTitle>

        {recording ? (
          <div>
            <Banner tone="info" title="This is recorded as a claim">
              It will be stored as reported by the distributor and will count
              toward nothing until somebody other than you checks it against
              evidence.
            </Banner>
            <Field label="Date sold">
              <input type="date" style={input} value={recording.sold_on}
                onChange={(e) => setRecording({ ...recording, sold_on: e.target.value })} />
            </Field>
            <Field label="Product">
              <select style={input} value={recording.product_id}
                onChange={(e) => setRecording({ ...recording, product_id: e.target.value })}>
                <option value="">Choose…</option>
                {products.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </Field>
            <Field label="Quantity">
              <input style={input} inputMode="decimal" value={recording.quantity}
                onChange={(e) => setRecording({ ...recording, quantity: e.target.value })} />
            </Field>
            <Field label="Unit price">
              <input style={input} inputMode="decimal" value={recording.unit_price}
                onChange={(e) => setRecording({ ...recording, unit_price: e.target.value })} />
            </Field>
            <Field label="Outlet">
              <select style={input} value={recording.outlet_id}
                onChange={(e) => setRecording({ ...recording, outlet_id: e.target.value })}>
                <option value="">Not recorded</option>
                {outlets.map((o) => (
                  <option key={o.id} value={o.id}>{o.name}</option>
                ))}
              </select>
            </Field>
            <Field label="Marketer">
              <select style={input} value={recording.marketer_id}
                onChange={(e) => setRecording({ ...recording, marketer_id: e.target.value })}>
                <option value="">Not recorded</option>
                {marketers.filter((m) => m.is_active).map((m) => (
                  <option key={m.id} value={m.id}>{m.full_name}</option>
                ))}
              </select>
            </Field>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <Btn size="sm" variant="ghost"
                onClick={() => setRecording(null)}>Cancel</Btn>
              <Btn size="sm" variant="accent"
                disabled={!recording.product_id || !recording.quantity}
                onClick={record}>Record</Btn>
            </div>
          </div>
        ) : (
          <DataTable
            cols={[
              { key: 'sold_on', label: 'Date' },
              { key: 'outlet', label: 'Sold to', wrap: true },
              { key: 'total_amount', label: 'Value', align: 'right' },
              { key: 'provenance', label: 'Status' },
              { key: 'evidence_count', label: 'Evidence', align: 'right' },
              { key: 'act', label: '', align: 'right' },
            ]}
            rows={sales}
            empty="Nothing reported yet."
            render={(r, c) => {
              if (c.key === 'total_amount') return naira(r.total_amount);
              if (c.key === 'provenance') {
                return (
                  <span>
                    <Chip tone={PROVENANCE_TONE[r.provenance]}>
                      {PROVENANCE_LABEL[r.provenance]}
                    </Chip>
                    {r.stock_discrepancy && (
                      <div style={{ marginTop: 3 }}>
                        <Chip tone="danger">Stock mismatch</Chip>
                      </div>
                    )}
                  </span>
                );
              }
              if (c.key === 'outlet') {
                return r.outlet || (
                  <span style={{ color: color.textMuted }}>not recorded</span>
                );
              }
              if (c.key === 'act') {
                return <Btn size="sm" variant="ghost"
                  onClick={() => setOpen(r.id)}>Open</Btn>;
              }
              return r[c.key] ?? '—';
            }}
          />
        )}
      </Card>

      <Card pad={2.5}>
        <SectionTitle>Marketers</SectionTitle>
        <DataTable
          cols={[
            { key: 'full_name', label: 'Marketer', wrap: true },
            { key: 'verified_amount', label: 'Verified', align: 'right' },
            { key: 'reported_amount', label: 'Claimed', align: 'right' },
            { key: 'disputed_count', label: 'Disputed', align: 'right' },
            { key: 'sales', label: 'Sales', align: 'right' },
          ]}
          rows={marketers}
          empty="No marketers recorded."
          render={(r, c) => {
            if (c.key === 'verified_amount') {
              return <strong>{naira(r.verified_amount)}</strong>;
            }
            if (c.key === 'reported_amount') {
              return <span style={{ color: color.textMuted }}>
                {naira(r.reported_amount)}</span>;
            }
            if (c.key === 'disputed_count') {
              return Number(r.disputed_count) > 0
                ? <Chip tone="danger">{r.disputed_count}</Chip> : '—';
            }
            return r[c.key] ?? '—';
          }}
        />
        <div style={{ fontSize: 11, color: color.textMuted, marginTop: 10,
          lineHeight: 1.6 }}>
          Ordered by verified value, not by what was claimed. Ranking on
          self-reported figures rewards optimistic paperwork.
        </div>
      </Card>
    </div>
  );
}

export default DownstreamPanel;
