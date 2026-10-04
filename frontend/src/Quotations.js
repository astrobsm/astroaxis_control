// Quotations — preparing a price, standing behind it, turning it into an order.
//
// Mounted from AppMain when activeModule === 'quotations'.
//
// WHY THE EXPIRY IS SO PROMINENT
// ------------------------------
// A quotation is a price promise with a date on it. The date is the single
// most important thing on the screen after the total, because a quote that
// has quietly expired is the one that gets honoured by mistake — and this
// company sells into a currency that has moved a long way in a year.
//
// Expiry is computed by the server every time it is asked, not stored, so a
// quote that died on Friday reads as dead on Monday whether or not any job
// ran over the weekend.

import React, { useCallback, useEffect, useState } from 'react';
import { authedFetch } from './utils/api';
import { color, naira, radius, space } from './ui/theme';
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

const STATUS_TONE = {
  DRAFT: 'neutral', SENT: 'info', ACCEPTED: 'success',
  CONVERTED: 'success', DECLINED: 'danger', EXPIRED: 'warning',
  CANCELLED: 'neutral',
};

function Compose({ onClose, onDone, notify }) {
  const [customers, setCustomers] = useState([]);
  const [products, setProducts] = useState([]);
  const [form, setForm] = useState({
    customer_id: '', customer_type: 'retail', valid_days: '',
    discount_percent: '0', delivery_charge: '0', terms: '', notes: '',
  });
  const [lines, setLines] = useState([{ product_id: '', unit: 'unit', quantity: '1' }]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    // The same two lists the rest of the app uses; no second source of truth
    // for what a product is or who a customer is.
    Promise.all([
      req('/api/sales/customers?limit=1000&active_only=false')
        .catch(() => ({ items: [] })),
      req('/api/products/?size=1000').catch(() => ({ items: [] })),
    ]).then(([cust, prod]) => {
      setCustomers(cust.items || cust.customers || []);
      setProducts(prod.items || prod.products || []);
    });
  }, []);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const setLine = (i, k, v) => setLines((ls) =>
    ls.map((l, j) => (j === i ? { ...l, [k]: v } : l)));

  const submit = async () => {
    setBusy(true); setErr('');
    try {
      const result = await req('/api/quotations', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          customer_id: form.customer_id,
          customer_type: form.customer_type,
          valid_days: form.valid_days ? Number(form.valid_days) : null,
          discount_percent: Number(form.discount_percent || 0),
          delivery_charge: Number(form.delivery_charge || 0),
          terms: form.terms || null,
          notes: form.notes || null,
          items: lines.filter((l) => l.product_id).map((l) => ({
            product_id: l.product_id, unit: l.unit || 'unit',
            quantity: Number(l.quantity || 0),
          })),
        }),
      });
      if (notify) notify(result.note, 'success');
      onDone();
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  const ready = form.customer_id && lines.some(
    (l) => l.product_id && Number(l.quantity) > 0);

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.55)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 2000, padding: 16,
    }} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={{
        background: '#fff', borderRadius: radius.lg, width: 'min(680px, 100%)',
        maxHeight: '90vh', overflow: 'auto', padding: space(3),
      }}>
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 800,
          color: color.navy }}>New quotation</h3>

        {err && <ErrorBox msg={err} />}

        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr',
          gap: 10, marginTop: space(2) }}>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600,
              color: color.textSecondary }}>Customer</label>
            <select style={{ ...inputStyle, marginTop: 5 }}
              value={form.customer_id} onChange={set('customer_id')}>
              <option value="">Choose…</option>
              {customers.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600,
              color: color.textSecondary }}>Price list</label>
            <select style={{ ...inputStyle, marginTop: 5 }}
              value={form.customer_type} onChange={set('customer_type')}>
              <option value="retail">Retail</option>
              <option value="wholesale">Wholesale</option>
            </select>
          </div>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600,
              color: color.textSecondary }}>Valid for (days)</label>
            <input style={{ ...inputStyle, marginTop: 5 }} type="number"
              placeholder="default" value={form.valid_days}
              onChange={set('valid_days')} />
          </div>
        </div>

        <div style={{ marginTop: space(2) }}>
          <div style={{ fontSize: 12.5, fontWeight: 700, color: color.navy,
            marginBottom: 6 }}>Lines</div>
          {lines.map((l, i) => (
            <div key={i} style={{ display: 'grid',
              gridTemplateColumns: '3fr 1fr 1fr auto', gap: 8,
              marginBottom: 7 }}>
              <select style={inputStyle} value={l.product_id}
                onChange={(e) => setLine(i, 'product_id', e.target.value)}>
                <option value="">Choose a product…</option>
                {products.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
              <input style={inputStyle} value={l.unit} placeholder="unit"
                onChange={(e) => setLine(i, 'unit', e.target.value)} />
              <input style={inputStyle} type="number" min="0" value={l.quantity}
                onChange={(e) => setLine(i, 'quantity', e.target.value)} />
              <button onClick={() => setLines((ls) =>
                ls.filter((_, j) => j !== i))}
                disabled={lines.length === 1}
                style={{ border: 'none', background: 'none', cursor: 'pointer',
                  color: color.textSecondary, fontSize: 18 }}>×</button>
            </div>
          ))}
          <Btn size="sm" variant="secondary"
            onClick={() => setLines((ls) => [...ls,
              { product_id: '', unit: 'unit', quantity: '1' }])}>
            Add a line
          </Btn>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr',
          gap: 10, marginTop: space(2) }}>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600,
              color: color.textSecondary }}>Discount %</label>
            <input style={{ ...inputStyle, marginTop: 5 }} type="number"
              min="0" max="100" value={form.discount_percent}
              onChange={set('discount_percent')} />
          </div>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600,
              color: color.textSecondary }}>Delivery charge</label>
            <input style={{ ...inputStyle, marginTop: 5 }} type="number"
              min="0" value={form.delivery_charge}
              onChange={set('delivery_charge')} />
          </div>
        </div>

        <div style={{ marginTop: space(2) }}>
          <label style={{ fontSize: 12, fontWeight: 600,
            color: color.textSecondary }}>Terms</label>
          <textarea style={{ ...inputStyle, marginTop: 5, minHeight: 56,
            resize: 'vertical' }} value={form.terms} onChange={set('terms')} />
        </div>

        <div style={{ fontSize: 11.5, color: color.textSecondary,
          marginTop: space(2), lineHeight: 1.6 }}>
          Prices are taken from the price list now and fixed on the quotation.
          If the price list changes afterwards, this quotation does not.
        </div>

        <div style={{ display: 'flex', gap: 10, marginTop: space(3),
          justifyContent: 'flex-end' }}>
          <Btn variant="secondary" onClick={onClose}>Cancel</Btn>
          <Btn onClick={submit} disabled={busy || !ready}>
            {busy ? 'Preparing…' : 'Prepare quotation'}
          </Btn>
        </div>
      </div>
    </div>
  );
}

function Detail({ id, onClose, onChanged, notify }) {
  const [q, setQ] = useState(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');

  const load = useCallback(async () => {
    try { setQ(await req(`/api/quotations/${id}`)); setErr(''); }
    catch (e) { setErr(e.message); }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  const move = async (status) => {
    setBusy(true);
    try {
      await req(`/api/quotations/${id}/status`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status, note: note || null }),
      });
      setNote(''); await load(); onChanged();
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  const convert = async () => {
    setBusy(true);
    try {
      const r = await req(`/api/quotations/${id}/convert`, { method: 'POST' });
      if (notify) notify(r.note, 'success');
      await load(); onChanged();
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
        background: '#fff', borderRadius: radius.lg, width: 'min(700px, 100%)',
        maxHeight: '90vh', overflow: 'auto', padding: space(3),
      }}>
        {err && <ErrorBox msg={err} />}
        {!q ? <SkeletonCards n={2} /> : (
          <>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center',
              flexWrap: 'wrap' }}>
              <h3 style={{ margin: 0, fontSize: 16, fontWeight: 800,
                color: color.navy }}>{q.quotation_number}</h3>
              <Chip tone={STATUS_TONE[q.status]}>{q.status}</Chip>
              {q.is_expired && <Chip tone="warning">EXPIRED</Chip>}
            </div>
            <div style={{ fontSize: 13.5, marginTop: 6 }}>
              {q.customer.name}
              <span style={{ color: color.textSecondary }}>
                {' '}· {q.customer_type}
              </span>
            </div>

            {q.is_expired && (
              <div style={{ marginTop: space(2) }}>
                <Banner tone="warning" title={`Expired on ${q.valid_until}`}>
                  The price on this was only promised until that date. Prepare
                  a new quotation at current prices rather than honouring it.
                </Banner>
              </div>
            )}

            <div style={{ marginTop: space(2), overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse',
                fontSize: 13 }}>
                <thead>
                  <tr style={{ background: '#FbFcFe' }}>
                    {['Product', 'Unit', 'Qty', 'Price', 'Total'].map((h, i) => (
                      <th key={h} style={{ padding: '8px 10px', fontSize: 10.5,
                        textAlign: i >= 2 ? 'right' : 'left',
                        color: color.textSecondary, fontWeight: 600,
                        textTransform: 'uppercase',
                        borderBottom: `1px solid ${color.border}` }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {q.lines.map((l, i) => (
                    <tr key={i}>
                      <td style={{ padding: '8px 10px',
                        borderBottom: '1px solid #F1F5F9' }}>{l.product_name}</td>
                      <td style={{ padding: '8px 10px',
                        borderBottom: '1px solid #F1F5F9' }}>{l.unit}</td>
                      <td style={{ padding: '8px 10px', textAlign: 'right',
                        borderBottom: '1px solid #F1F5F9' }}>{l.quantity}</td>
                      <td style={{ padding: '8px 10px', textAlign: 'right',
                        borderBottom: '1px solid #F1F5F9' }}>
                        {naira(l.unit_price)}</td>
                      <td style={{ padding: '8px 10px', textAlign: 'right',
                        fontWeight: 600,
                        borderBottom: '1px solid #F1F5F9' }}>
                        {naira(l.line_total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div style={{ marginTop: space(2), textAlign: 'right',
              fontSize: 13 }}>
              <div style={{ color: color.textSecondary }}>
                Subtotal {naira(q.subtotal)}
              </div>
              {q.discount_amount > 0 && (
                <div style={{ color: color.textSecondary }}>
                  Discount {q.discount_percent}% − {naira(q.discount_amount)}
                  {q.discount_approved_by
                    && ` · approved by ${q.discount_approved_by}`}
                </div>
              )}
              {q.delivery_charge > 0 && (
                <div style={{ color: color.textSecondary }}>
                  Delivery {naira(q.delivery_charge)}
                </div>
              )}
              <div style={{ fontSize: 19, fontWeight: 800, color: color.navy,
                marginTop: 6 }}>{naira(q.total_amount)}</div>
              <div style={{ fontSize: 11.5, color: color.textSecondary }}>
                Valid until {q.valid_until}
                {!q.is_expired && q.days_left >= 0
                  && ` · ${q.days_left} day${q.days_left === 1 ? '' : 's'} left`}
              </div>
            </div>

            {q.converted_order && (
              <div style={{ marginTop: space(2) }}>
                <Banner tone="success" title="Converted">
                  Order {q.converted_order} was created from this quotation.
                </Banner>
              </div>
            )}

            {q.may_become.length > 0 && (
              <div style={{ marginTop: space(3) }}>
                <input style={inputStyle} value={note} placeholder="Note (required to decline)"
                  onChange={(e) => setNote(e.target.value)} />
                <div style={{ display: 'flex', gap: 8, marginTop: 10,
                  flexWrap: 'wrap' }}>
                  {q.may_become.map((s) => (
                    s === 'CONVERTED' ? (
                      <Btn key={s} onClick={convert} disabled={busy}>
                        Convert to order
                      </Btn>
                    ) : (
                      <Btn key={s} size="sm"
                        variant={s === 'DECLINED' || s === 'CANCELLED'
                          ? 'danger' : s === 'ACCEPTED' ? 'accent' : 'secondary'}
                        disabled={busy} onClick={() => move(s)}>
                        {{ SENT: 'Mark as sent', ACCEPTED: 'Customer accepted',
                          DECLINED: 'Customer declined',
                          CANCELLED: 'Withdraw' }[s] || s}
                      </Btn>
                    )
                  ))}
                </div>
              </div>
            )}

            {q.history.length > 0 && (
              <div style={{ marginTop: space(3) }}>
                <div style={{ fontSize: 12.5, fontWeight: 700,
                  color: color.navy, marginBottom: 6 }}>History</div>
                {q.history.map((h, i) => (
                  <div key={i} style={{ fontSize: 12,
                    color: color.textSecondary, padding: '3px 0' }}>
                    {new Date(h.at).toLocaleString()} — {h.to}
                    {h.actor ? ` by ${h.actor}` : ''}
                    {h.note ? ` · ${h.note}` : ''}
                  </div>
                ))}
              </div>
            )}

            <div style={{ fontSize: 11.5, color: color.textSecondary,
              marginTop: space(2), lineHeight: 1.6 }}>{q.note}</div>

            <div style={{ display: 'flex', justifyContent: 'flex-end',
              marginTop: space(2) }}>
              <Btn variant="secondary" onClick={onClose}>Close</Btn>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function Quotations({ notify }) {
  const [data, setData] = useState(null);
  const [status, setStatus] = useState('');
  const [composing, setComposing] = useState(false);
  const [viewing, setViewing] = useState(null);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    try {
      setData(await req(`/api/quotations${status ? `?status=${status}` : ''}`));
      setErr('');
    } catch (e) { setErr(e.message); }
  }, [status]);

  useEffect(() => { load(); }, [load]);

  if (err) return <ErrorBox msg={err} />;
  if (!data) return <SkeletonCards n={3} />;

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center',
        flexWrap: 'wrap', marginBottom: space(2) }}>
        <div style={{ background: color.navy, color: '#fff',
          borderRadius: radius.md, padding: '12px 16px' }}>
          <div style={{ fontSize: 19, fontWeight: 800 }}>
            {naira(data.open_value)}
          </div>
          <div style={{ fontSize: 11, opacity: 0.85 }}>
            live quotations awaiting a decision
          </div>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8,
          flexWrap: 'wrap', alignItems: 'center' }}>
          <select style={{ ...inputStyle, width: 'auto' }} value={status}
            onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>
            {['DRAFT', 'SENT', 'ACCEPTED', 'CONVERTED', 'DECLINED',
              'CANCELLED'].map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <Btn onClick={() => setComposing(true)}>New quotation</Btn>
        </div>
      </div>

      {data.quotations.length === 0 ? (
        <Banner tone="info" title="No quotations yet">
          A quotation fixes a price for a customer until a date you choose.
        </Banner>
      ) : (
        <div style={{ overflowX: 'auto', border: `1px solid ${color.border}`,
          borderRadius: radius.md, background: '#fff' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse',
            fontSize: 13 }}>
            <thead>
              <tr style={{ background: '#FbFcFe' }}>
                {['Number', 'Customer', 'Status', 'Valid until', 'Total',
                  'Prepared by'].map((h, i) => (
                  <th key={h} style={{ padding: '10px 12px', fontSize: 10.5,
                    textAlign: i === 4 ? 'right' : 'left',
                    color: color.textSecondary, fontWeight: 600,
                    textTransform: 'uppercase', letterSpacing: '0.04em',
                    borderBottom: `1px solid ${color.border}`,
                    whiteSpace: 'nowrap' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.quotations.map((q) => (
                <tr key={q.id} style={{ cursor: 'pointer' }}
                  onClick={() => setViewing(q.id)}>
                  <td style={{ padding: '10px 12px', fontWeight: 600,
                    borderBottom: '1px solid #F1F5F9', whiteSpace: 'nowrap' }}>
                    {q.quotation_number}</td>
                  <td style={{ padding: '10px 12px',
                    borderBottom: '1px solid #F1F5F9' }}>{q.customer}</td>
                  <td style={{ padding: '10px 12px',
                    borderBottom: '1px solid #F1F5F9', whiteSpace: 'nowrap' }}>
                    <Chip tone={STATUS_TONE[q.status]}>{q.status}</Chip>
                    {q.is_expired && <Chip tone="warning">EXPIRED</Chip>}
                  </td>
                  <td style={{ padding: '10px 12px',
                    borderBottom: '1px solid #F1F5F9', whiteSpace: 'nowrap',
                    color: q.is_expired ? '#B45309' : color.textSecondary }}>
                    {q.valid_until}
                    {!q.is_expired && q.days_left >= 0
                      && ` (${q.days_left}d)`}</td>
                  <td style={{ padding: '10px 12px', textAlign: 'right',
                    fontWeight: 700, borderBottom: '1px solid #F1F5F9',
                    whiteSpace: 'nowrap' }}>{naira(q.total_amount)}</td>
                  <td style={{ padding: '10px 12px',
                    borderBottom: '1px solid #F1F5F9',
                    color: color.textSecondary }}>{q.prepared_by || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ fontSize: 11.5, color: color.textSecondary,
        marginTop: space(2), lineHeight: 1.7, maxWidth: 760 }}>
        {data.note}
      </div>

      {composing && (
        <Compose notify={notify} onClose={() => setComposing(false)}
          onDone={() => { setComposing(false); load(); }} />
      )}
      {viewing && (
        <Detail id={viewing} notify={notify} onClose={() => setViewing(null)}
          onChanged={load} />
      )}
    </div>
  );
}
