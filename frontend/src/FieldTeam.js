// The Bonnesante side of the field portal.
//
// Mounted as a pane in the distributor dossier. Three jobs:
//
//   * issue the joining link that lets a distributor's marketers create
//     accounts,
//   * set that distributor's price list — which is also, deliberately, the
//     range their marketers can see,
//   * read what the team has been doing, including where they were.
//
// ON THE TRAIL TAB
// ----------------
// A named individual's movements are personal data. The panel says out loud
// who agreed to be located and who did not, because a marketer showing no
// trail has usually declined rather than sat still, and somebody reading this
// screen should not have to guess which.

import React, { useCallback, useEffect, useState } from 'react';
import { authedFetch, isAdmin } from './utils/api';
import { color, naira, radius, space } from './ui/theme';
import { Banner, Btn, Card, Chip, DataTable, ErrorBox, SkeletonCards } from './ui/kit';

async function req(url, opts) {
  const res = await authedFetch(url, opts);
  if (!res.ok) {
    let d = `Request failed (${res.status})`;
    try { const j = await res.json(); d = j.detail || j.message || d; } catch { /* keep */ }
    throw new Error(typeof d === 'string' ? d : 'Request failed');
  }
  return res.json();
}
const getJSON = (u) => req(u);
const postJSON = (u, b) => req(u, { method: 'POST', body: JSON.stringify(b || {}) });

const inputStyle = {
  width: '100%', padding: '9px 11px', borderRadius: radius.sm, fontSize: 13.5,
  border: `1px solid ${color.borderStrong}`, fontFamily: 'inherit',
  boxSizing: 'border-box', background: '#fff', color: color.text,
};

function Row({ label, children }) {
  return (
    <div style={{ marginBottom: space(2) }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: color.textSecondary,
        marginBottom: 5 }}>{label}</div>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------

function Invite({ distributorId }) {
  const [label, setLabel] = useState('');
  const [days, setDays] = useState(14);
  const [issued, setIssued] = useState(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const issue = async () => {
    setBusy(true); setErr('');
    try {
      setIssued(await postJSON(`/api/distributors/${distributorId}/field/invites`,
        { label: label.trim(), valid_days: Number(days) }));
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  const copy = () => {
    navigator.clipboard.writeText(issued.join_url)
      .then(() => { setCopied(true); setTimeout(() => setCopied(false), 2500); })
      .catch(() => { /* the link is on screen; they can select it */ });
  };

  if (!isAdmin()) {
    return (
      <Banner tone="info" title="Joining links are issued by an administrator">
        This creates logins on a distributor record, so it is kept to admins.
        Ask one to issue the link; everything else on this pane is open to you.
      </Banner>
    );
  }

  return (
    <Card>
      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: space(1) }}>
        Invite this distributor&apos;s marketers
      </div>
      <div style={{ fontSize: 12.5, color: color.textSecondary,
        lineHeight: 1.7, marginBottom: space(2) }}>
        One link onboards the whole team. Each marketer opens it, enters their
        name, phone and a password, and gets an account that reaches this
        distributor&apos;s price list and nothing else in the system.
      </div>

      {err && <ErrorBox msg={err} />}

      {!issued ? (
        <>
          <Row label="What is this link for?">
            <input style={inputStyle} value={label}
              placeholder="e.g. Enugu team, September intake"
              onChange={(e) => setLabel(e.target.value)} />
          </Row>
          <Row label="Valid for">
            <select style={inputStyle} value={days}
              onChange={(e) => setDays(e.target.value)}>
              {[7, 14, 30, 60, 90].map((d) => (
                <option key={d} value={d}>{d} days</option>
              ))}
            </select>
          </Row>
          <Btn onClick={issue} disabled={busy || label.trim().length < 3}>
            {busy ? 'Issuing…' : 'Issue joining link'}
          </Btn>
        </>
      ) : (
        <>
          <Banner tone="warning" title="Send this to your own team only">
            {issued.warning}
          </Banner>
          <div style={{
            background: color.bg, padding: '11px 13px',
            borderRadius: radius.sm, fontSize: 12.5, wordBreak: 'break-all',
            fontFamily: 'ui-monospace, monospace', marginBottom: space(2),
            border: `1px solid ${color.borderStrong}`,
          }}>{issued.join_url}</div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Btn size="sm" onClick={copy}>{copied ? 'Copied' : 'Copy link'}</Btn>
            <Btn size="sm" variant="ghost" onClick={() => setIssued(null)}>
              Issue another
            </Btn>
          </div>
          <div style={{ fontSize: 11.5, color: color.textSecondary,
            marginTop: space(2) }}>
            Expires {new Date(issued.expires_at).toLocaleDateString()}. This is
            the only time the link is shown in full — it is stored hashed.
          </div>
        </>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------

function Prices({ distributorId }) {
  const [prices, setPrices] = useState(null);
  const [products, setProducts] = useState([]);
  const [form, setForm] = useState({ product_id: '', unit: 'unit', price: '' });
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [p, prod] = await Promise.all([
        getJSON(`/api/distributors/${distributorId}/field/prices`),
        getJSON('/api/products/?size=1000').catch(() => ({ items: [] })),
      ]);
      setPrices(p.prices || []);
      setProducts(prod.items || prod.products || []);
      setErr('');
    } catch (e) { setErr(e.message); }
  }, [distributorId]);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setBusy(true); setErr('');
    try {
      await postJSON(`/api/distributors/${distributorId}/field/prices`, {
        product_id: form.product_id, unit: form.unit || 'unit',
        price: form.price,
      });
      setForm({ product_id: '', unit: 'unit', price: '' });
      load();
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  if (prices === null) return <SkeletonCards n={2} />;

  return (
    <>
      <Banner tone="info" title="The price list is also the range">
        A product priced here is one this distributor&apos;s marketers can see
        and quote. A product with no price does not appear in their catalogue
        at all — so this list is how you decide what they carry.
      </Banner>

      {err && <ErrorBox msg={err} />}

      <Card style={{ marginBottom: space(2) }}>
        <div style={{ display: 'grid',
          gridTemplateColumns: 'minmax(180px, 2fr) 100px 130px auto',
          gap: 10, alignItems: 'end' }}>
          <Row label="Product">
            <select style={inputStyle} value={form.product_id}
              onChange={(e) => setForm({ ...form, product_id: e.target.value })}>
              <option value="">Choose a product…</option>
              {products.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </Row>
          <Row label="Unit">
            <input style={inputStyle} value={form.unit}
              onChange={(e) => setForm({ ...form, unit: e.target.value })} />
          </Row>
          <Row label="Price (NGN)">
            <input style={inputStyle} type="number" min="0" step="0.01"
              value={form.price}
              onChange={(e) => setForm({ ...form, price: e.target.value })} />
          </Row>
          <div style={{ marginBottom: space(2) }}>
            <Btn onClick={save} disabled={busy || !form.product_id || form.price === ''}>
              {busy ? 'Saving…' : 'Set price'}
            </Btn>
          </div>
        </div>
        <div style={{ fontSize: 11.5, color: color.textSecondary }}>
          Setting a price for a product already listed closes the old price and
          opens a new one, so a visit logged last month still reads against the
          price that applied then.
        </div>
      </Card>

      <DataTable
        cols={[
          { key: 'product_name', label: 'Product', wrap: true },
          { key: 'unit', label: 'Unit' },
          { key: 'price', label: 'Price', align: 'right' },
          { key: 'effective_from', label: 'From' },
          { key: 'set_by_name', label: 'Set by' },
        ]}
        rows={prices}
        empty="No prices set. Until one is, this distributor's marketers see an empty catalogue."
        render={(r, c) => {
          if (c.key === 'price') return naira(r.price);
          if (c.key === 'effective_from') {
            return r.effective_from
              ? new Date(r.effective_from).toLocaleDateString() : '—';
          }
          return r[c.key] || '—';
        }} />
    </>
  );
}

// ---------------------------------------------------------------------------

function Trail({ distributorId, marketer, onBack }) {
  const [points, setPoints] = useState(null);
  const [note, setNote] = useState('');
  const [on, setOn] = useState(() => new Date().toISOString().slice(0, 10));
  const [err, setErr] = useState('');

  useEffect(() => {
    let alive = true;
    getJSON(`/api/distributors/${distributorId}/field/trail/${marketer.id}?on=${on}`)
      .then((b) => { if (alive) { setPoints(b.points || []); setNote(b.note || ''); setErr(''); } })
      .catch((e) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, [distributorId, marketer.id, on]);

  return (
    <Card>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10,
        marginBottom: space(2), flexWrap: 'wrap' }}>
        <Btn size="sm" variant="ghost" onClick={onBack}>← Team</Btn>
        <div style={{ fontSize: 14, fontWeight: 700 }}>{marketer.full_name}</div>
        <input type="date" style={{ ...inputStyle, width: 'auto', marginLeft: 'auto' }}
          value={on} onChange={(e) => setOn(e.target.value)} />
      </div>

      {err && <ErrorBox msg={err} />}

      {!marketer.consented && (
        <Banner tone="warning" title="This marketer has not agreed to location recording">
          There will be no trail, and that is their choice to make. Visits they
          log are still recorded; only the position is missing.
        </Banner>
      )}

      {points === null ? <SkeletonCards n={1} /> : points.length === 0 ? (
        <div style={{ padding: space(3), textAlign: 'center',
          color: color.textSecondary, fontSize: 13 }}>
          No positions recorded on this day.
        </div>
      ) : (
        <>
          <DataTable
            cols={[
              { key: 'recorded_at', label: 'Time' },
              { key: 'latitude', label: 'Latitude', align: 'right' },
              { key: 'longitude', label: 'Longitude', align: 'right' },
              { key: 'accuracy_m', label: 'Accuracy', align: 'right' },
              { key: 'captured_while_working', label: 'Working' },
            ]}
            rows={points}
            maxHeight={360}
            render={(p, c) => {
              if (c.key === 'recorded_at') {
                return new Date(p.recorded_at).toLocaleTimeString();
              }
              if (c.key === 'latitude' || c.key === 'longitude') {
                return Number(p[c.key]).toFixed(5);
              }
              if (c.key === 'accuracy_m') {
                return p.accuracy_m ? `${Math.round(p.accuracy_m)} m` : '—';
              }
              return p.captured_while_working ? 'Yes' : 'No';
            }} />
          <div style={{ marginTop: space(2) }}>
            <a href={`https://www.google.com/maps?q=${points[0].latitude},${points[0].longitude}`}
              target="_blank" rel="noreferrer"
              style={{ fontSize: 12.5, color: color.medical }}>
              Open the first position on a map
            </a>
          </div>
        </>
      )}

      {note && (
        <div style={{ fontSize: 11.5, color: color.textSecondary,
          marginTop: space(2), lineHeight: 1.6 }}>{note}</div>
      )}
    </Card>
  );
}

function Team({ distributorId }) {
  const [team, setTeam] = useState(null);
  const [days, setDays] = useState(30);
  const [viewing, setViewing] = useState(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    let alive = true;
    getJSON(`/api/distributors/${distributorId}/field/team?days=${days}`)
      .then((b) => { if (alive) { setTeam(b.team || []); setErr(''); } })
      .catch((e) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, [distributorId, days]);

  if (viewing) {
    return <Trail distributorId={distributorId} marketer={viewing}
      onBack={() => setViewing(null)} />;
  }

  if (err) return <ErrorBox msg={err} />;
  if (team === null) return <SkeletonCards n={2} />;

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10,
        marginBottom: space(2) }}>
        <div style={{ fontSize: 12.5, color: color.textSecondary }}>
          Activity over the last
        </div>
        <select style={{ ...inputStyle, width: 'auto' }} value={days}
          onChange={(e) => setDays(Number(e.target.value))}>
          {[7, 30, 90, 365].map((d) => <option key={d} value={d}>{d} days</option>)}
        </select>
      </div>

      <DataTable
        cols={[
          { key: 'full_name', label: 'Marketer', wrap: true },
          { key: 'account', label: 'Account' },
          { key: 'visits', label: 'Visits', align: 'right' },
          { key: 'order_value', label: 'Order value', align: 'right' },
          { key: 'customers', label: 'Customers', align: 'right' },
          { key: 'last_visit', label: 'Last visit' },
          { key: 'trail', label: '' },
        ]}
        rows={team}
        empty="No marketers on this distributor yet. Issue a joining link to start."
        render={(m, c) => {
          if (c.key === 'full_name') {
            return (
              <div>
                <div style={{ fontWeight: 600 }}>{m.full_name}</div>
                <div style={{ fontSize: 11.5, color: color.textSecondary }}>
                  {m.phone}
                </div>
              </div>
            );
          }
          if (c.key === 'account') {
            if (!m.has_account) return <Chip tone="neutral">Not signed up</Chip>;
            if (!m.is_active) return <Chip tone="danger">Inactive</Chip>;
            return m.consented
              ? <Chip tone="success">Sharing location</Chip>
              : <Chip tone="warning">Location off</Chip>;
          }
          if (c.key === 'order_value') return naira(m.order_value);
          if (c.key === 'last_visit') {
            return m.last_visit
              ? new Date(m.last_visit).toLocaleDateString() : '—';
          }
          if (c.key === 'trail') {
            // Offered only where there is something to show. A button that
            // always opens an empty page teaches people to stop pressing it.
            return m.consented
              ? <Btn size="sm" variant="ghost" onClick={() => setViewing(m)}>Trail</Btn>
              : <span style={{ fontSize: 11.5, color: color.textSecondary }}>—</span>;
          }
          return m[c.key];
        }} />
    </>
  );
}

// ---------------------------------------------------------------------------

export function FieldTeamPanel({ distributorId }) {
  const [pane, setPane] = useState('team');

  return (
    <div>
      <div style={{ display: 'flex', gap: 6, marginBottom: space(2),
        flexWrap: 'wrap' }}>
        {[['team', 'Team & activity'], ['prices', 'Price list'],
          ['invite', 'Joining link']].map(([k, label]) => (
          <button key={k} onClick={() => setPane(k)} style={{
            padding: '5px 12px', borderRadius: radius.pill, fontSize: 12,
            fontWeight: 600, cursor: 'pointer',
            border: `1px solid ${pane === k ? color.medical : color.borderStrong}`,
            background: pane === k ? color.infoBg : '#fff',
            color: pane === k ? color.royal : color.textSecondary,
          }}>{label}</button>
        ))}
      </div>

      {pane === 'team' && <Team distributorId={distributorId} />}
      {pane === 'prices' && <Prices distributorId={distributorId} />}
      {pane === 'invite' && <Invite distributorId={distributorId} />}
    </div>
  );
}

export default FieldTeamPanel;
