// Duplicate customers, and combining them.
//
// Rendered as a pane inside the Who to Contact screen, because that is where
// the damage shows: before deduplication one person appeared seven times in
// the live book, which produced seven identical "gone quiet" findings.
//
// THE SCREEN'S JOB IS TO MAKE THE DECISION SAFE, NOT QUICK
// --------------------------------------------------------
// A merge moves order history across twelve tables and cannot be undone
// cleanly. So every record in a group shows its orders, its invoices and its
// value, the survivor is chosen explicitly rather than assumed, and the
// confirm button says how many orders are about to move and where.
//
// Two records on one phone number may be a hospital and the nurse who orders
// for it. The screen is built to let somebody notice that.

import React, { useCallback, useEffect, useState } from 'react';
import { authedFetch, isAdmin } from './utils/api';
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

function Group({ group, onMerged, notify }) {
  const [survivor, setSurvivor] = useState(group.suggested_survivor_id);
  const [chosen, setChosen] = useState(
    () => new Set(group.members.map((m) => m.customer_id)));
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [confirming, setConfirming] = useState(false);

  const toMerge = group.members.filter(
    (m) => m.customer_id !== survivor && chosen.has(m.customer_id));
  const movingOrders = toMerge.reduce((a, m) => a + m.orders, 0);
  const movingInvoices = toMerge.reduce((a, m) => a + m.invoices, 0);
  const survivorRow = group.members.find((m) => m.customer_id === survivor);

  const toggle = (id) => setChosen((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });

  const submit = async () => {
    setBusy(true); setErr('');
    try {
      const result = await req('/api/customers/merge', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          surviving_id: survivor,
          merged_ids: toMerge.map((m) => m.customer_id),
          reason: reason.trim(),
        }),
      });
      if (notify) notify(result.note, 'success');
      onMerged();
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); setConfirming(false); }
  };

  return (
    <Card style={{ marginBottom: space(2) }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center',
        flexWrap: 'wrap', marginBottom: space(2) }}>
        <span style={{ fontSize: 14, fontWeight: 700, color: color.navy }}>
          {group.members.length} records
        </span>
        {group.reasons.map((r) => <Chip key={r} tone="warning">{r}</Chip>)}
      </div>

      {err && <ErrorBox msg={err} />}

      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse',
          fontSize: 13 }}>
          <thead>
            <tr style={{ background: '#FbFcFe' }}>
              {['Keep', 'Merge in', 'Customer', 'Phone', 'Orders', 'Invoices',
                'Value', 'Last order'].map((h, i) => (
                <th key={h} style={{
                  padding: '9px 10px', fontSize: 10.5, textAlign:
                    i >= 4 && i <= 6 ? 'right' : 'left',
                  color: color.textSecondary, fontWeight: 600,
                  textTransform: 'uppercase', letterSpacing: '0.04em',
                  borderBottom: `1px solid ${color.border}`,
                  whiteSpace: 'nowrap',
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {group.members.map((m) => {
              const isSurvivor = m.customer_id === survivor;
              return (
                <tr key={m.customer_id} style={{
                  background: isSurvivor ? '#ECFDF5' : '#fff',
                }}>
                  <td style={{ padding: '9px 10px',
                    borderBottom: '1px solid #F1F5F9' }}>
                    <input type="radio" name={`s-${group.suggested_survivor_id}`}
                      checked={isSurvivor}
                      aria-label={`Keep ${m.name}`}
                      onChange={() => setSurvivor(m.customer_id)} />
                  </td>
                  <td style={{ padding: '9px 10px',
                    borderBottom: '1px solid #F1F5F9' }}>
                    {!isSurvivor && (
                      <input type="checkbox" checked={chosen.has(m.customer_id)}
                        aria-label={`Merge in ${m.name}`}
                        onChange={() => toggle(m.customer_id)} />
                    )}
                  </td>
                  <td style={{ padding: '9px 10px',
                    borderBottom: '1px solid #F1F5F9' }}>
                    <div style={{ fontWeight: isSurvivor ? 700 : 500 }}>
                      {m.name}
                    </div>
                    <div style={{ fontSize: 11, color: color.textSecondary }}>
                      {m.customer_code}
                      {!m.is_active && ' · inactive'}
                      {m.email ? ` · ${m.email}` : ''}
                    </div>
                  </td>
                  <td style={{ padding: '9px 10px',
                    borderBottom: '1px solid #F1F5F9', whiteSpace: 'nowrap' }}>
                    {m.phone || '—'}
                  </td>
                  <td style={{ padding: '9px 10px', textAlign: 'right',
                    borderBottom: '1px solid #F1F5F9',
                    fontWeight: m.orders > 0 ? 700 : 400 }}>{m.orders}</td>
                  <td style={{ padding: '9px 10px', textAlign: 'right',
                    borderBottom: '1px solid #F1F5F9' }}>{m.invoices}</td>
                  <td style={{ padding: '9px 10px', textAlign: 'right',
                    borderBottom: '1px solid #F1F5F9', whiteSpace: 'nowrap' }}>
                    {m.value ? naira(m.value) : '—'}
                  </td>
                  <td style={{ padding: '9px 10px',
                    borderBottom: '1px solid #F1F5F9', whiteSpace: 'nowrap',
                    color: color.textSecondary }}>{m.last_order || '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {!isAdmin() ? (
        <div style={{ fontSize: 12, color: color.textSecondary,
          marginTop: space(2) }}>
          Merging is admin-only. It rewrites order history across the system
          and cannot be undone cleanly.
        </div>
      ) : !confirming ? (
        <div style={{ marginTop: space(2) }}>
          <label style={{ fontSize: 12, fontWeight: 600,
            color: color.textSecondary }}>
            Why are these the same customer? — required
          </label>
          <input style={{ ...inputStyle, marginTop: 5 }} value={reason}
            placeholder="e.g. same hospital, name entered two ways"
            onChange={(e) => setReason(e.target.value)} />
          <div style={{ marginTop: space(2) }}>
            <Btn variant="danger" disabled={!toMerge.length || reason.trim().length < 3}
              onClick={() => setConfirming(true)}>
              Merge {toMerge.length} into {survivorRow ? survivorRow.name : ''}
            </Btn>
          </div>
        </div>
      ) : (
        <div style={{ marginTop: space(2) }}>
          <Banner tone="danger" title="This cannot be undone">
            {movingOrders} order{movingOrders === 1 ? '' : 's'} and{' '}
            {movingInvoices} invoice{movingInvoices === 1 ? '' : 's'} will move
            onto <strong>{survivorRow && survivorRow.name}</strong>. The other
            record{toMerge.length === 1 ? ' is' : 's are'} kept and marked as
            merged, so old invoices and links still resolve &mdash; but the
            history does not come back apart.
          </Banner>
          <div style={{ display: 'flex', gap: 10, marginTop: space(2) }}>
            <Btn variant="secondary" onClick={() => setConfirming(false)}>
              Go back
            </Btn>
            <Btn variant="danger" onClick={submit} disabled={busy}>
              {busy ? 'Merging…' : 'Yes, merge them'}
            </Btn>
          </div>
        </div>
      )}
    </Card>
  );
}

export default function CustomerDuplicates({ notify }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    try { setData(await req('/api/customers/duplicates')); setErr(''); }
    catch (e) { setErr(e.message); }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (err) return <ErrorBox msg={err} />;
  if (!data) return <SkeletonCards n={2} />;

  return (
    <div>
      {data.group_count === 0 ? (
        <Banner tone="success" title="No duplicates found">
          Every customer record looks distinct on phone, email and name.
        </Banner>
      ) : (
        <>
          <Banner tone="warning"
            title={`${data.record_count} records look like ${data.group_count} customers`}>
            Each duplicate inflates the contact list and splits one
            customer&apos;s order history across several records, so their
            lifetime value and reorder pattern are both wrong. Nothing is
            merged until you confirm it.
          </Banner>
          {data.groups.map((g) => (
            <Group key={g.suggested_survivor_id} group={g}
              notify={notify} onMerged={load} />
          ))}
        </>
      )}
      <div style={{ fontSize: 11.5, color: color.textSecondary,
        marginTop: space(2), lineHeight: 1.7, maxWidth: 760 }}>
        {data.note}
      </div>
    </div>
  );
}
