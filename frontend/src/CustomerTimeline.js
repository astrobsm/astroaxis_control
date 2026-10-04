// One customer, everything that has ever happened, in order.
//
// Opened from the opportunity queue and from the customer list.
//
// WHY THIS SCREEN EXISTS
// ----------------------
// Answering "what has happened with this hospital?" previously meant opening
// orders, invoices, payments, deliveries and the call log separately, and
// still missing the phone call somebody made last Tuesday.
//
// Everything here is assembled live from those same sources. Nothing is
// stored, so a cancelled order or a paid invoice changes this the moment it
// changes there.

import React, { useCallback, useEffect, useState } from 'react';
import { authedFetch } from './utils/api';
import { color, naira, radius, space } from './ui/theme';
import { Banner, Btn, Card, Chip, ErrorBox, SkeletonCards } from './ui/kit';

async function req(url) {
  const res = await authedFetch(url);
  if (!res.ok) {
    let d = `Request failed (${res.status})`;
    try { const j = await res.json(); d = j.detail || j.message || d; } catch { /* keep */ }
    throw new Error(typeof d === 'string' ? d : 'Request failed');
  }
  return res.json();
}

const KIND_LABEL = {
  ORDER: 'Order', INVOICE: 'Invoice', PAYMENT: 'Payment',
  QUOTATION: 'Quotation', DELIVERY: 'Delivery', CALL: 'Call',
  VISIT: 'Visit', MESSAGE: 'Message', CONSENT: 'Consent',
  RECALL: 'Recall', MERGE: 'Merge', OUTREACH: 'Outreach',
};

const TONE_COLOUR = {
  success: color.success, danger: color.danger,
  warning: '#B45309', info: color.medical, neutral: color.textSecondary,
};

function Tile({ label, value, sub, tone }) {
  return (
    <div style={{ background: '#F8FAFC', borderRadius: radius.md,
      padding: '12px 14px', border: `1px solid ${color.border}` }}>
      <div style={{ fontSize: 16.5, fontWeight: 800,
        color: tone || color.navy }}>{value}</div>
      <div style={{ fontSize: 11, color: color.textSecondary, marginTop: 2 }}>
        {label}
      </div>
      {sub && (
        <div style={{ fontSize: 10.5, color: color.textMuted, marginTop: 1 }}>
          {sub}
        </div>
      )}
    </div>
  );
}

export default function CustomerTimeline({ customerId, onClose }) {
  const [data, setData] = useState(null);
  const [summary, setSummary] = useState(null);
  const [kind, setKind] = useState('');
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    try {
      const [t, s] = await Promise.all([
        req(`/api/customers/${customerId}/timeline?limit=300`),
        req(`/api/customers/${customerId}/summary`).catch(() => null),
      ]);
      setData(t); setSummary(s); setErr('');
    } catch (e) { setErr(e.message); }
  }, [customerId]);

  useEffect(() => { load(); }, [load]);

  const events = data
    ? (kind ? data.events.filter((e) => e.kind === kind) : data.events)
    : [];

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.55)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 2000, padding: 16,
    }} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={{
        background: '#fff', borderRadius: radius.lg, width: 'min(780px, 100%)',
        maxHeight: '92vh', overflow: 'auto', padding: space(3),
      }}>
        {err && <ErrorBox msg={err} />}
        {!data ? <SkeletonCards n={3} /> : (
          <>
            <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start',
              flexWrap: 'wrap' }}>
              <div style={{ minWidth: 0, flex: 1 }}>
                <h3 style={{ margin: 0, fontSize: 17, fontWeight: 800,
                  color: color.navy }}>{data.customer.name}</h3>
                <div style={{ fontSize: 12, color: color.textSecondary,
                  marginTop: 3 }}>
                  {data.customer.customer_code}
                  {data.customer.phone ? ` · ${data.customer.phone}` : ''}
                  {data.customer.type ? ` · ${data.customer.type.toLowerCase()}` : ''}
                  {data.customer.assigned_rep
                    ? ` · rep ${data.customer.assigned_rep}` : ''}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {data.customer.do_not_contact && (
                  <Chip tone="danger">Do not contact</Chip>
                )}
                {data.customer.marketing_consent === 'OPTED_IN' && (
                  <Chip tone="success">Opted in</Chip>
                )}
                {!data.customer.is_active && <Chip tone="neutral">Inactive</Chip>}
              </div>
            </div>

            {data.customer.merged_into && (
              <div style={{ marginTop: space(2) }}>
                <Banner tone="warning" title="This record was merged away">
                  Its history now lives on another customer record. Anything
                  below is what happened before the merge.
                </Banner>
              </div>
            )}

            {summary && (
              <div style={{ display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(128px, 1fr))',
                gap: 9, margin: `${space(2)} 0` }}>
                <Tile label="Lifetime value"
                  value={naira(summary.lifetime_value)}
                  sub={`${summary.orders} order${summary.orders === 1 ? '' : 's'}`} />
                <Tile label="Average order"
                  value={naira(summary.average_order)} />
                <Tile label="Outstanding" value={naira(summary.outstanding)}
                  tone={summary.outstanding > 0 ? color.danger : undefined} />
                <Tile label="Last order"
                  value={summary.last_order || '—'}
                  sub={summary.days_since_last_order !== null
                    ? `${summary.days_since_last_order} days ago` : ''} />
                {summary.average_interval_days && (
                  <Tile label="Usually orders every"
                    value={`${summary.average_interval_days}d`} />
                )}
              </div>
            )}

            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap',
              marginBottom: space(2) }}>
              <button onClick={() => setKind('')} style={{
                padding: '4px 11px', borderRadius: radius.pill, fontSize: 11.5,
                fontWeight: 600, cursor: 'pointer',
                border: `1px solid ${!kind ? color.medical : color.borderStrong}`,
                background: !kind ? color.infoBg : '#fff',
                color: !kind ? color.royal : color.textSecondary,
              }}>All {data.total_events}</button>
              {Object.entries(data.counts).map(([k, n]) => (
                <button key={k} onClick={() => setKind(k === kind ? '' : k)}
                  style={{
                    padding: '4px 11px', borderRadius: radius.pill,
                    fontSize: 11.5, fontWeight: 600, cursor: 'pointer',
                    border: `1px solid ${kind === k ? color.medical : color.borderStrong}`,
                    background: kind === k ? color.infoBg : '#fff',
                    color: kind === k ? color.royal : color.textSecondary,
                  }}>{KIND_LABEL[k] || k} {n}</button>
              ))}
            </div>

            {events.length === 0 ? (
              <Banner tone="info" title="Nothing recorded yet">
                No orders, invoices, deliveries or conversations for this
                customer.
              </Banner>
            ) : (
              <div style={{ borderLeft: `2px solid ${color.border}`,
                paddingLeft: space(2), marginLeft: 6 }}>
                {events.map((e, i) => (
                  <div key={i} style={{ position: 'relative',
                    padding: '9px 0' }}>
                    <span style={{
                      position: 'absolute', left: -(space(2)) , top: 14,
                      marginLeft: -5, width: 8, height: 8, borderRadius: '50%',
                      background: TONE_COLOUR[e.tone] || color.textSecondary,
                    }} />
                    <div style={{ display: 'flex', gap: 8,
                      alignItems: 'baseline', flexWrap: 'wrap' }}>
                      <span style={{ fontSize: 13.5, fontWeight: 600,
                        color: color.navy }}>{e.title}</span>
                      {e.amount ? (
                        <span style={{ fontSize: 13, fontWeight: 700,
                          color: TONE_COLOUR[e.tone] || color.navy }}>
                          {naira(e.amount)}
                        </span>
                      ) : null}
                      <span style={{ marginLeft: 'auto', fontSize: 11,
                        color: color.textMuted, whiteSpace: 'nowrap' }}>
                        {new Date(e.at).toLocaleDateString()}
                      </span>
                    </div>
                    {e.detail && (
                      <div style={{ fontSize: 12.5, color: color.textSecondary,
                        marginTop: 2, lineHeight: 1.5 }}>{e.detail}</div>
                    )}
                    {e.actor && (
                      <div style={{ fontSize: 11, color: color.textMuted,
                        marginTop: 1 }}>{e.actor}</div>
                    )}
                  </div>
                ))}
              </div>
            )}

            <div style={{ fontSize: 11.5, color: color.textSecondary,
              marginTop: space(2), lineHeight: 1.6 }}>{data.note}</div>

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
