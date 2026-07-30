// Payment Distribution (MAPD) — one customer payment, many destination accounts.
//
// A self-contained module (mounted from AppMain when activeModule==='distribution')
// built on the Bonnesante design system (src/ui). Every figure here is read from
// settlement records the engine actually wrote; this screen originates nothing.
//
// The screens are ordered the way the work is done: see what happened, fix what
// did not, then configure so it does not happen again.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { authedFetch } from './utils/api';
import { color, font, naira, radius, space } from './ui/theme';
import {
  Banner, Btn, Card, Chip, DataTable, ErrorBox, Icon, KpiCard, SectionTitle,
  Skeleton, SkeletonCards,
} from './ui/kit';

// ---------------------------------------------------------------------------
// data helpers
// ---------------------------------------------------------------------------

async function req(url, opts) {
  const res = await authedFetch(url, opts);
  if (!res.ok) {
    let d = `Request failed (${res.status})`;
    try { const j = await res.json(); d = j.detail || j.message || d; } catch { /* keep status */ }
    throw new Error(typeof d === 'string' ? d : JSON.stringify(d));
  }
  return res.json();
}
const getJSON = (u) => req(u);
const postJSON = (u, b) => req(u, { method: 'POST', body: JSON.stringify(b || {}) });
const putJSON = (u, b) => req(u, { method: 'PUT', body: JSON.stringify(b || {}) });
const patchJSON = (u) => req(u, { method: 'PATCH' });

const money = (v) => naira(v);
const todayISO = () => new Date().toISOString().slice(0, 10);
const monthStartISO = () => {
  const d = new Date();
  return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10);
};

const STATUS_TONE = {
  COMPLETED: 'success', PENDING: 'warning', SKIPPED: 'warning',
  FAILED: 'danger', REVERSED: 'neutral',
  ACTIVE: 'success', SUSPENDED: 'warning', CLOSED: 'danger',
};
const tone = (s) => STATUS_TONE[String(s || '').toUpperCase()] || 'neutral';

const inputStyle = {
  padding: '9px 10px', border: `1px solid ${color.borderStrong}`,
  borderRadius: radius.sm, fontSize: 13, fontFamily: font.family,
  color: color.text, background: '#fff', width: '100%', boxSizing: 'border-box',
};

function Field({ label, hint, children }) {
  return (
    <label style={{ display: 'block', fontSize: 12, color: color.textSecondary, fontWeight: 500 }}>
      {label}
      <div style={{ marginTop: 4 }}>{children}</div>
      {hint && <div style={{ marginTop: 3, fontSize: 11.5, color: color.textMuted, fontWeight: 400, lineHeight: 1.45 }}>{hint}</div>}
    </label>
  );
}

function Grid({ children, min = 220 }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(auto-fit, minmax(${min}px, 1fr))`, gap: space(2) }}>
      {children}
    </div>
  );
}

function DateRange({ start, end, setStart, setEnd, onGo }) {
  return (
    <div style={{ display: 'flex', gap: space(1.5), alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: space(2) }}>
      <div style={{ minWidth: 150 }}><Field label="From"><input type="date" value={start} onChange={(e) => setStart(e.target.value)} style={inputStyle} /></Field></div>
      <div style={{ minWidth: 150 }}><Field label="To"><input type="date" value={end} onChange={(e) => setEnd(e.target.value)} style={inputStyle} /></Field></div>
      <Btn variant="accent" size="sm" icon="refresh" onClick={onGo}>Run</Btn>
    </div>
  );
}

/** A horizontal share bar — how one account's take compares with the largest. */
function ShareBar({ value, max, tint = color.medical }) {
  const pct = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
  return (
    <div style={{ background: '#F1F5F9', borderRadius: radius.pill, height: 8, width: '100%', minWidth: 90 }}>
      <div style={{ width: `${pct}%`, height: '100%', background: tint, borderRadius: radius.pill }} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

function DashboardTab() {
  const [start, setStart] = useState(monthStartISO());
  const [end, setEnd] = useState(todayISO());
  const [d, setD] = useState(null);
  const [health, setHealth] = useState(null);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true); setErr('');
    Promise.all([
      getJSON(`/api/finance/dashboard?start=${start}&end=${end}`),
      getJSON('/api/payments/health').catch(() => null),
    ]).then(([dash, h]) => { setD(dash); setHealth(h); })
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
  }, [start, end]);

  useEffect(() => { load(); }, [load]);

  if (loading) return <div><SkeletonCards n={4} /><div style={{ height: 16 }} /><SkeletonCards n={2} /></div>;
  if (err) return <div><ErrorBox msg={err} /><Btn size="sm" icon="refresh" onClick={load}>Retry</Btn></div>;
  if (!d) return null;

  const t = d.totals;
  const maxAccount = Math.max(1, ...d.by_account.map((a) => a.total));
  const maxProduct = Math.max(1, ...d.by_product.map((p) => p.total));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: space(2) }}>
      <DateRange start={start} end={end} setStart={setStart} setEnd={setEnd} onGo={load} />

      {health && health.healthy === false && (
        <Banner tone="danger" title="Money received has not reached its accounts">
          {health.summary} Open the Exceptions tab to see which payments, fix the cause, then retry.
        </Banner>
      )}
      {t.unconfigured_products > 0 && (
        <Banner tone="warning" title={`${t.unconfigured_products} product${t.unconfigured_products > 1 ? 's have' : ' has'} no destination account`}>
          A payment covering these products cannot be distributed — the engine records the
          shortfall rather than guessing where the money should go. Map them under Products.
        </Banner>
      )}

      <div style={{ display: 'flex', gap: space(2), flexWrap: 'wrap' }}>
        <KpiCard icon="wallet" tone="success" label="Distributed" value={money(t.allocated)}
          sub={`${t.completed} settlement${t.completed === 1 ? '' : 's'}`} />
        <KpiCard icon="alert" tone={t.failed ? 'danger' : 'success'} label="Failed to distribute"
          value={money(t.failed_value)} sub={`${t.failed} payment${t.failed === 1 ? '' : 's'} awaiting retry`} />
        <KpiCard icon="ledger" tone="royal" label="Obligations accrued" value={money(t.obligations)}
          sub="Commissions & revenue shares" />
        <KpiCard icon="receivable" tone="warning" label="Outstanding receivables"
          value={money(t.outstanding_receivables)} sub="Invoiced, not yet collected" />
      </div>

      <Grid min={340}>
        <Card>
          <SectionTitle right={<span style={{ fontSize: 12, color: color.textMuted }}>{d.by_account.length} accounts</span>}>
            Revenue by destination account
          </SectionTitle>
          <DataTable
            cols={[
              { key: 'name', label: 'Account' },
              { key: 'business_unit', label: 'Unit' },
              { key: 'share', label: 'Share' },
              { key: 'total', label: 'Received', align: 'right' },
            ]}
            rows={d.by_account}
            empty="Nothing has been distributed in this period."
            render={(r, c) => {
              if (c.key === 'name') return <span style={{ fontWeight: 600 }}>{r.name} <span style={{ color: color.textMuted, fontWeight: 400 }}>({r.code})</span></span>;
              if (c.key === 'business_unit') return r.business_unit || '—';
              if (c.key === 'share') return <ShareBar value={r.total} max={maxAccount} />;
              return <strong>{money(r.total)}</strong>;
            }} />
        </Card>

        <Card>
          <SectionTitle>Revenue by product</SectionTitle>
          <DataTable
            cols={[
              { key: 'product', label: 'Product' },
              { key: 'share', label: 'Share' },
              { key: 'total', label: 'Allocated', align: 'right' },
            ]}
            rows={d.by_product}
            empty="No product allocations in this period."
            render={(r, c) => {
              if (c.key === 'product') return r.product || '—';
              if (c.key === 'share') return <ShareBar value={r.total} max={maxProduct} tint={color.royal} />;
              return <strong>{money(r.total)}</strong>;
            }} />
        </Card>
      </Grid>

      <Grid min={340}>
        <Card>
          <SectionTitle>Revenue by business unit</SectionTitle>
          <DataTable
            cols={[{ key: 'business_unit', label: 'Business unit' }, { key: 'total', label: 'Allocated', align: 'right' }]}
            rows={d.by_business_unit}
            empty="No allocations yet."
            render={(r, c) => (c.key === 'total' ? <strong>{money(r.total)}</strong> : r.business_unit)} />
        </Card>
        <Card>
          <SectionTitle>Daily collections distributed</SectionTitle>
          <DataTable
            cols={[{ key: 'date', label: 'Date' }, { key: 'total', label: 'Distributed', align: 'right' }]}
            rows={d.daily} maxHeight={280}
            empty="No settlements in this period."
            render={(r, c) => (c.key === 'total' ? money(r.total) : r.date)} />
        </Card>
      </Grid>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Settlements register
// ---------------------------------------------------------------------------

function SettlementsTab() {
  const [start, setStart] = useState(monthStartISO());
  const [end, setEnd] = useState(todayISO());
  const [status, setStatus] = useState('');
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true); setErr('');
    const q = new URLSearchParams({ start, end, ...(status ? { status } : {}) });
    getJSON(`/api/reports/settlements?${q}`)
      .then(setRows).catch((e) => setErr(e.message)).finally(() => setLoading(false));
  }, [start, end, status]);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <DateRange start={start} end={end} setStart={setStart} setEnd={setEnd} onGo={load} />
      <div style={{ display: 'flex', gap: space(1), marginBottom: space(2), flexWrap: 'wrap' }}>
        {['', 'COMPLETED', 'FAILED', 'SKIPPED', 'REVERSED'].map((s) => (
          <button key={s || 'all'} onClick={() => setStatus(s)}
            style={{
              border: `1px solid ${status === s ? color.navy : color.border}`,
              background: status === s ? color.navy : '#fff',
              color: status === s ? '#fff' : color.textSecondary,
              borderRadius: radius.pill, padding: '5px 13px', fontSize: 12.5,
              fontWeight: 600, cursor: 'pointer', fontFamily: font.family,
            }}>{s || 'All'}</button>
        ))}
      </div>
      <ErrorBox msg={err} />
      <Card>
        <SectionTitle right={<span style={{ fontSize: 12, color: color.textMuted }}>{rows.length} settlements</span>}>
          Settlement register
        </SectionTitle>
        {loading ? <Skeleton h={200} /> : (
          <DataTable
            cols={[
              { key: 'reference', label: 'Reference' },
              { key: 'invoice_number', label: 'Invoice' },
              { key: 'customer_name', label: 'Customer' },
              { key: 'status', label: 'Status' },
              { key: 'gross_amount', label: 'Received', align: 'right' },
              { key: 'allocated_amount', label: 'Distributed', align: 'right' },
              { key: 'destinations', label: 'Destinations', wrap: true },
            ]}
            rows={rows} maxHeight={560}
            empty="No settlements in this period."
            render={(r, c) => {
              if (c.key === 'status') return <Chip tone={tone(r.status)}>{r.status}</Chip>;
              if (c.key === 'gross_amount' || c.key === 'allocated_amount') return money(r[c.key]);
              if (c.key === 'destinations') {
                if (r.status === 'FAILED' || r.status === 'SKIPPED') {
                  return <span style={{ color: color.danger, fontSize: 12 }}>{r.failure_reason}</span>;
                }
                return <span style={{ fontFamily: font.mono, fontSize: 11.5, color: color.textSecondary }}>{r.destinations || '—'}</span>;
              }
              if (c.key === 'reference') {
                return <span style={{ fontFamily: font.mono, fontSize: 12 }}>{r.reference}</span>;
              }
              return r[c.key] || '—';
            }} />
        )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Exceptions — payments that have not reached their accounts
// ---------------------------------------------------------------------------

function ExceptionsTab() {
  const [rows, setRows] = useState([]);
  const [health, setHealth] = useState(null);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true); setErr('');
    Promise.all([
      getJSON('/api/payments/undistributed'),
      getJSON('/api/payments/health').catch(() => null),
    ]).then(([u, h]) => { setRows(u); setHealth(h); })
      .catch((e) => setErr(e.message)).finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const retryAll = async () => {
    setBusy(true); setErr(''); setMsg('');
    try {
      const r = await postJSON('/api/payments/distribute', { retry_failed: true, limit: 200 });
      setMsg(`${r.settled} of ${r.attempted} payment(s) distributed. ${r.still_failing} still failing.`);
      load();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  const retryOne = async (paymentId) => {
    setBusy(true); setErr(''); setMsg('');
    try {
      const r = await postJSON('/api/payments/distribute', { payment_id: paymentId });
      setMsg(r.status === 'COMPLETED'
        ? `Distributed ${money(r.allocated_amount)} across ${r.allocations.length} account(s).`
        : `${r.status}: ${r.reason}`);
      load();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: space(2) }}>
      <Banner tone="info" title="Why a payment lands here">
        The engine never leaves a payment half-distributed. If a destination account is
        suspended, or a product on the invoice has no account mapped, it records the
        failure and stops — the money is still banked and still shows as received.
        Fix the cause, then retry.
      </Banner>
      <ErrorBox msg={err} />
      {msg && <Banner tone="success" title="Done">{msg}</Banner>}

      {health && (
        <div style={{ display: 'flex', gap: space(2), flexWrap: 'wrap' }}>
          <KpiCard icon="alert" tone={health.failed_settlements ? 'danger' : 'success'}
            label="Failed distributions" value={health.failed_settlements ?? 0}
            sub={money(health.failed_value ?? 0)} />
          <KpiCard icon="wallet" tone={health.undistributed_payments ? 'warning' : 'success'}
            label="Undistributed payments" value={health.undistributed_payments ?? 0}
            sub={money(health.undistributed_value ?? 0)} />
          <KpiCard icon="check" tone={health.healthy ? 'success' : 'danger'}
            label="Engine health" value={health.healthy ? 'Reconciled' : 'Attention'}
            sub={health.summary} />
        </div>
      )}

      <Card>
        <SectionTitle right={<Btn size="sm" variant="accent" icon="refresh" disabled={busy} onClick={retryAll}>Retry all</Btn>}>
          Payments awaiting distribution
        </SectionTitle>
        {loading ? <Skeleton h={160} /> : (
          <DataTable
            cols={[
              { key: 'invoice_number', label: 'Invoice' },
              { key: 'customer_name', label: 'Customer' },
              { key: 'amount', label: 'Amount', align: 'right' },
              { key: 'payment_method', label: 'Method' },
              { key: 'last_failure', label: 'Why', wrap: true },
              { key: 'action', label: '', align: 'right' },
            ]}
            rows={rows}
            empty="Every payment received has reached its destination accounts."
            render={(r, c) => {
              if (c.key === 'amount') return <strong>{money(r.amount)}</strong>;
              if (c.key === 'last_failure') return <span style={{ fontSize: 12, color: color.danger }}>{r.last_failure || 'Not yet attempted'}</span>;
              if (c.key === 'action') return <Btn size="sm" variant="secondary" disabled={busy} onClick={() => retryOne(r.payment_id)}>Distribute</Btn>;
              return r[c.key] || '—';
            }} />
        )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Financial accounts
// ---------------------------------------------------------------------------

const BLANK_ACCOUNT = {
  code: '', name: '', account_kind: 'BANK', gl_account_code: '1200',
  contra_gl_account_code: '', bank_name: '', account_number: '',
  account_name: '', business_unit_id: '', status: 'ACTIVE', description: '',
};

function AccountsTab() {
  const [rows, setRows] = useState([]);
  const [units, setUnits] = useState([]);
  const [glAccounts, setGlAccounts] = useState([]);
  const [form, setForm] = useState(BLANK_ACCOUNT);
  const [showForm, setShowForm] = useState(false);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      getJSON('/api/finance/accounts'),
      getJSON('/api/finance/business-units').catch(() => []),
      getJSON('/api/accounting/accounts').catch(() => []),
    ]).then(([a, u, gl]) => {
      setRows(a); setUnits(u);
      setGlAccounts((gl || []).filter((g) => g.is_postable));
    }).catch((e) => setErr(e.message)).finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setErr(''); setMsg('');
    try {
      const body = { ...form };
      if (!body.business_unit_id) delete body.business_unit_id;
      if (!body.contra_gl_account_code) delete body.contra_gl_account_code;
      await postJSON('/api/finance/accounts', body);
      setMsg(`Account ${form.code} created.`);
      setForm(BLANK_ACCOUNT); setShowForm(false); load();
    } catch (e) { setErr(e.message); }
  };

  const setStatus = async (row, status) => {
    setErr(''); setMsg('');
    try {
      await putJSON(`/api/finance/accounts/${row.id}`, { status });
      setMsg(`${row.code} is now ${status}.`); load();
    } catch (e) { setErr(e.message); }
  };

  const isObligation = form.account_kind === 'OBLIGATION';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: space(2) }}>
      <ErrorBox msg={err} />
      {msg && <Banner tone="success" title="Saved">{msg}</Banner>}

      <Card>
        <SectionTitle right={<Btn size="sm" variant="accent" onClick={() => setShowForm(!showForm)}>{showForm ? 'Cancel' : 'New account'}</Btn>}>
          Destination accounts
        </SectionTitle>
        <div style={{ fontSize: 12.5, color: color.textSecondary, marginBottom: space(2), lineHeight: 1.5 }}>
          Where product revenue is sent. Each one maps onto a postable ledger account, so the
          distribution and the books can never tell different stories. Suspending an account
          pauses every settlement that would pay into it rather than allocating around it.
        </div>

        {showForm && (
          <div style={{ background: color.royalBg, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(2), marginBottom: space(2) }}>
            <Grid min={200}>
              <Field label="Code" hint="Short, stable, e.g. HERA">
                <input style={inputStyle} value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })} />
              </Field>
              <Field label="Name">
                <input style={inputStyle} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </Field>
              <Field label="Kind" hint={isObligation ? 'An obligation is a debt to a third party, not cash you hold.' : 'Where the money physically sits.'}>
                <select style={inputStyle} value={form.account_kind} onChange={(e) => setForm({ ...form, account_kind: e.target.value })}>
                  {['BANK', 'CASH', 'WALLET', 'VIRTUAL', 'OBLIGATION'].map((k) => <option key={k} value={k}>{k}</option>)}
                </select>
              </Field>
              <Field label={isObligation ? 'Expense ledger account' : 'Ledger account'}>
                <select style={inputStyle} value={form.gl_account_code} onChange={(e) => setForm({ ...form, gl_account_code: e.target.value })}>
                  {glAccounts.map((g) => <option key={g.code} value={g.code}>{g.code} — {g.name}</option>)}
                </select>
              </Field>
              {isObligation && (
                <Field label="Liability ledger account" hint="Credited when the expense above is debited.">
                  <select style={inputStyle} value={form.contra_gl_account_code} onChange={(e) => setForm({ ...form, contra_gl_account_code: e.target.value })}>
                    <option value="">Select…</option>
                    {glAccounts.filter((g) => g.account_type === 'LIABILITY').map((g) => <option key={g.code} value={g.code}>{g.code} — {g.name}</option>)}
                  </select>
                </Field>
              )}
              <Field label="Business unit">
                <select style={inputStyle} value={form.business_unit_id} onChange={(e) => setForm({ ...form, business_unit_id: e.target.value })}>
                  <option value="">Unassigned</option>
                  {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                </select>
              </Field>
              <Field label="Bank">
                <input style={inputStyle} value={form.bank_name} onChange={(e) => setForm({ ...form, bank_name: e.target.value })} />
              </Field>
              <Field label="Account number" hint="Encrypted at rest; only the last four digits are ever shown.">
                <input style={inputStyle} value={form.account_number} onChange={(e) => setForm({ ...form, account_number: e.target.value })} />
              </Field>
            </Grid>
            <div style={{ marginTop: space(2) }}>
              <Btn variant="primary" onClick={save} disabled={!form.code || !form.name}>Create account</Btn>
            </div>
          </div>
        )}

        {loading ? <Skeleton h={180} /> : (
          <DataTable
            cols={[
              { key: 'code', label: 'Code' },
              { key: 'name', label: 'Account' },
              { key: 'account_kind', label: 'Kind' },
              { key: 'gl_account_code', label: 'Ledger' },
              { key: 'business_unit', label: 'Unit' },
              { key: 'account_number_masked', label: 'Number' },
              { key: 'settled_total', label: 'Received', align: 'right' },
              { key: 'status', label: 'Status' },
              { key: 'action', label: '', align: 'right' },
            ]}
            rows={rows}
            empty="No destination accounts yet. Create one to start distributing."
            render={(r, c) => {
              if (c.key === 'status') return <Chip tone={tone(r.status)}>{r.status}</Chip>;
              if (c.key === 'settled_total') return <strong>{money(r.settled_total)}</strong>;
              if (c.key === 'gl_account_code') return <span style={{ fontFamily: font.mono, fontSize: 12 }}>{r.gl_account_code}</span>;
              if (c.key === 'action') {
                return r.status === 'ACTIVE'
                  ? <Btn size="sm" variant="ghost" onClick={() => setStatus(r, 'SUSPENDED')}>Suspend</Btn>
                  : <Btn size="sm" variant="ghost" onClick={() => setStatus(r, 'ACTIVE')}>Reactivate</Btn>;
              }
              return r[c.key] || '—';
            }} />
        )}
      </Card>

      <BusinessUnitsPanel units={units} onChange={load} />
    </div>
  );
}

function BusinessUnitsPanel({ units, onChange }) {
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [err, setErr] = useState('');

  const add = async () => {
    setErr('');
    try {
      await postJSON('/api/finance/business-units', { code, name });
      setCode(''); setName(''); onChange();
    } catch (e) { setErr(e.message); }
  };

  return (
    <Card>
      <SectionTitle>Business units</SectionTitle>
      <ErrorBox msg={err} />
      <div style={{ display: 'flex', gap: space(1.5), alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: space(2) }}>
        <div style={{ minWidth: 140 }}><Field label="Code"><input style={inputStyle} value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} /></Field></div>
        <div style={{ minWidth: 220 }}><Field label="Name"><input style={inputStyle} value={name} onChange={(e) => setName(e.target.value)} /></Field></div>
        <Btn size="sm" variant="secondary" onClick={add} disabled={!code || !name}>Add unit</Btn>
      </div>
      <DataTable
        cols={[{ key: 'code', label: 'Code' }, { key: 'name', label: 'Name' }, { key: 'product_count', label: 'Products', align: 'right' }]}
        rows={units} empty="No business units defined."
        render={(r, c) => r[c.key] ?? '—'} />
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Product → account mapping
// ---------------------------------------------------------------------------

function ProductsTab() {
  const [rows, setRows] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [units, setUnits] = useState([]);
  const [filter, setFilter] = useState('');
  const [onlyUnmapped, setOnlyUnmapped] = useState(false);
  const [edits, setEdits] = useState({});
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      getJSON('/api/finance/product-accounts'),
      getJSON('/api/finance/accounts?status=ACTIVE'),
      getJSON('/api/finance/business-units').catch(() => []),
    ]).then(([p, a, u]) => { setRows(p); setAccounts(a); setUnits(u); })
      .catch((e) => setErr(e.message)).finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const visible = useMemo(() => rows.filter((r) => {
    if (onlyUnmapped && r.settleable) return false;
    if (!filter) return true;
    const q = filter.toLowerCase();
    return (r.product_name || '').toLowerCase().includes(q) || (r.sku || '').toLowerCase().includes(q);
  }), [rows, filter, onlyUnmapped]);

  const save = async (row) => {
    setErr(''); setMsg('');
    const e = edits[row.product_id] || {};
    try {
      await putJSON('/api/finance/product-accounts', {
        product_id: row.product_id,
        default_financial_account_id: e.account ?? row.default_account_id ?? null,
        business_unit_id: e.unit ?? row.business_unit_id ?? null,
        tax_group: e.tax ?? row.tax_group ?? null,
        settlement_priority: row.settlement_priority ?? 100,
      });
      setMsg(`${row.product_name} mapped.`);
      setEdits({ ...edits, [row.product_id]: undefined });
      load();
    } catch (er) { setErr(er.message); }
  };

  const unmappedCount = rows.filter((r) => !r.settleable).length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: space(2) }}>
      <ErrorBox msg={err} />
      {msg && <Banner tone="success" title="Saved">{msg}</Banner>}
      {unmappedCount > 0 && (
        <Banner tone="warning" title={`${unmappedCount} product${unmappedCount > 1 ? 's' : ''} cannot be settled yet`}>
          Give each product a default account, or author a settlement rule for it. Until then
          a payment covering that product is recorded but not distributed.
        </Banner>
      )}
      <Card>
        <SectionTitle right={
          <div style={{ display: 'flex', gap: space(1), alignItems: 'center' }}>
            <label style={{ fontSize: 12.5, color: color.textSecondary, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <input type="checkbox" checked={onlyUnmapped} onChange={(e) => setOnlyUnmapped(e.target.checked)} />
              Unmapped only
            </label>
            <input placeholder="Search product or SKU" style={{ ...inputStyle, width: 220 }}
              value={filter} onChange={(e) => setFilter(e.target.value)} />
          </div>
        }>
          Product financial configuration
        </SectionTitle>
        {loading ? <Skeleton h={220} /> : (
          <DataTable
            cols={[
              { key: 'product_name', label: 'Product' },
              { key: 'sku', label: 'SKU' },
              { key: 'account', label: 'Destination account' },
              { key: 'unit', label: 'Business unit' },
              { key: 'tax', label: 'Tax group' },
              { key: 'rule_count', label: 'Rules', align: 'right' },
              { key: 'action', label: '', align: 'right' },
            ]}
            rows={visible} maxHeight={560}
            empty="No products match."
            render={(r, c) => {
              const e = edits[r.product_id] || {};
              const set = (patch) => setEdits({ ...edits, [r.product_id]: { ...e, ...patch } });
              if (c.key === 'product_name') {
                return (
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
                    {!r.settleable && <Icon name="alert" size={14} color={color.warning} />}
                    <span style={{ fontWeight: 600 }}>{r.product_name}</span>
                  </span>
                );
              }
              if (c.key === 'account') {
                return (
                  <select style={{ ...inputStyle, minWidth: 190 }}
                    value={e.account ?? r.default_account_id ?? ''}
                    onChange={(ev) => set({ account: ev.target.value || null })}>
                    <option value="">— none —</option>
                    {accounts.map((a) => <option key={a.id} value={a.id}>{a.code} — {a.name}</option>)}
                  </select>
                );
              }
              if (c.key === 'unit') {
                return (
                  <select style={{ ...inputStyle, minWidth: 150 }}
                    value={e.unit ?? r.business_unit_id ?? ''}
                    onChange={(ev) => set({ unit: ev.target.value || null })}>
                    <option value="">— none —</option>
                    {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                  </select>
                );
              }
              if (c.key === 'tax') {
                return <input style={{ ...inputStyle, width: 110 }} value={e.tax ?? r.tax_group ?? ''}
                  onChange={(ev) => set({ tax: ev.target.value })} placeholder="VAT" />;
              }
              if (c.key === 'rule_count') return r.rule_count || 0;
              if (c.key === 'action') return <Btn size="sm" variant="secondary" onClick={() => save(r)}>Save</Btn>;
              return r[c.key] || '—';
            }} />
        )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Settlement rules
// ---------------------------------------------------------------------------

const BLANK_SPLIT = { financial_account_id: '', allocation_type: 'CASH', percentage: '', is_residual: false };

function RulesTab() {
  const [rules, setRules] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [products, setProducts] = useState([]);
  const [units, setUnits] = useState([]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ code: '', name: '', scope: 'PRODUCT', product_id: '', business_unit_id: '', priority: 100 });
  const [splits, setSplits] = useState([{ ...BLANK_SPLIT }, { ...BLANK_SPLIT }]);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      getJSON('/api/finance/settlement-rules'),
      getJSON('/api/finance/accounts?status=ACTIVE'),
      getJSON('/api/finance/product-accounts'),
      getJSON('/api/finance/business-units').catch(() => []),
    ]).then(([r, a, p, u]) => { setRules(r); setAccounts(a); setProducts(p); setUnits(u); })
      .catch((e) => setErr(e.message)).finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const cashTotal = splits
    .filter((s) => s.allocation_type === 'CASH' && !s.is_residual && s.percentage !== '')
    .reduce((a, s) => a + Number(s.percentage || 0), 0);
  const hasResidual = splits.some((s) => s.allocation_type === 'CASH' && s.is_residual);
  const cashBalanced = hasResidual || Math.abs(cashTotal - 100) < 0.0001;

  const save = async () => {
    setErr(''); setMsg('');
    try {
      await postJSON('/api/finance/settlement-rules', {
        ...form,
        product_id: form.scope === 'PRODUCT' ? form.product_id : null,
        business_unit_id: form.scope === 'BUSINESS_UNIT' ? form.business_unit_id : null,
        splits: splits.filter((s) => s.financial_account_id).map((s) => ({
          financial_account_id: s.financial_account_id,
          allocation_type: s.allocation_type,
          is_residual: s.is_residual,
          percentage: s.is_residual ? null : Number(s.percentage),
        })),
      });
      setMsg(`Rule ${form.code} created.`);
      setShowForm(false);
      setForm({ code: '', name: '', scope: 'PRODUCT', product_id: '', business_unit_id: '', priority: 100 });
      setSplits([{ ...BLANK_SPLIT }, { ...BLANK_SPLIT }]);
      load();
    } catch (e) { setErr(e.message); }
  };

  const toggle = async (rule) => {
    setErr('');
    try {
      await patchJSON(`/api/finance/settlement-rules/${rule.id}/active?is_active=${!rule.is_active}`);
      load();
    } catch (e) { setErr(e.message); }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: space(2) }}>
      <ErrorBox msg={err} />
      {msg && <Banner tone="success" title="Saved">{msg}</Banner>}
      <Banner tone="info" title="How a rule is chosen">
        The most specific active rule wins: a product rule beats a business-unit rule, which
        beats a global one. Cash splits must account for 100% of the line — anything less and
        the rule is rejected here rather than failing on a live customer payment.
      </Banner>

      <Card>
        <SectionTitle right={<Btn size="sm" variant="accent" onClick={() => setShowForm(!showForm)}>{showForm ? 'Cancel' : 'New rule'}</Btn>}>
          Settlement rules
        </SectionTitle>

        {showForm && (
          <div style={{ background: color.royalBg, border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(2), marginBottom: space(2) }}>
            <Grid min={190}>
              <Field label="Code"><input style={inputStyle} value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })} /></Field>
              <Field label="Name"><input style={inputStyle} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
              <Field label="Scope">
                <select style={inputStyle} value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })}>
                  <option value="PRODUCT">Product</option>
                  <option value="BUSINESS_UNIT">Business unit</option>
                  <option value="GLOBAL">Global fallback</option>
                </select>
              </Field>
              {form.scope === 'PRODUCT' && (
                <Field label="Product">
                  <select style={inputStyle} value={form.product_id} onChange={(e) => setForm({ ...form, product_id: e.target.value })}>
                    <option value="">Select…</option>
                    {products.map((p) => <option key={p.product_id} value={p.product_id}>{p.product_name}</option>)}
                  </select>
                </Field>
              )}
              {form.scope === 'BUSINESS_UNIT' && (
                <Field label="Business unit">
                  <select style={inputStyle} value={form.business_unit_id} onChange={(e) => setForm({ ...form, business_unit_id: e.target.value })}>
                    <option value="">Select…</option>
                    {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                  </select>
                </Field>
              )}
              <Field label="Priority" hint="Lower wins within the same scope.">
                <input type="number" style={inputStyle} value={form.priority} onChange={(e) => setForm({ ...form, priority: Number(e.target.value) })} />
              </Field>
            </Grid>

            <div style={{ marginTop: space(2), fontSize: 12, fontWeight: 600, color: color.textSecondary, textTransform: 'uppercase', letterSpacing: '0.04em' }}>Splits</div>
            {splits.map((s, i) => (
              <div key={i} style={{ display: 'flex', gap: space(1), alignItems: 'flex-end', flexWrap: 'wrap', marginTop: space(1) }}>
                <div style={{ minWidth: 220, flex: '1 1 220px' }}>
                  <Field label="Destination">
                    <select style={inputStyle} value={s.financial_account_id}
                      onChange={(e) => setSplits(splits.map((x, j) => (j === i ? { ...x, financial_account_id: e.target.value } : x)))}>
                      <option value="">Select…</option>
                      {accounts.map((a) => <option key={a.id} value={a.id}>{a.code} — {a.name} ({a.account_kind})</option>)}
                    </select>
                  </Field>
                </div>
                <div style={{ width: 140 }}>
                  <Field label="Type">
                    <select style={inputStyle} value={s.allocation_type}
                      onChange={(e) => setSplits(splits.map((x, j) => (j === i ? { ...x, allocation_type: e.target.value, is_residual: false } : x)))}>
                      <option value="CASH">Cash</option>
                      <option value="OBLIGATION">Obligation</option>
                    </select>
                  </Field>
                </div>
                <div style={{ width: 110 }}>
                  <Field label="Percent">
                    <input type="number" style={inputStyle} disabled={s.is_residual} value={s.percentage}
                      onChange={(e) => setSplits(splits.map((x, j) => (j === i ? { ...x, percentage: e.target.value } : x)))} />
                  </Field>
                </div>
                {s.allocation_type === 'CASH' && (
                  <label style={{ fontSize: 12, color: color.textSecondary, display: 'inline-flex', alignItems: 'center', gap: 6, height: 38 }}>
                    <input type="checkbox" checked={s.is_residual}
                      onChange={(e) => setSplits(splits.map((x, j) => (j === i ? { ...x, is_residual: e.target.checked, percentage: '' } : x)))} />
                    Residual
                  </label>
                )}
                <Btn size="sm" variant="ghost" onClick={() => setSplits(splits.filter((_, j) => j !== i))}>Remove</Btn>
              </div>
            ))}
            <div style={{ marginTop: space(1.5), display: 'flex', gap: space(1), alignItems: 'center', flexWrap: 'wrap' }}>
              <Btn size="sm" variant="secondary" onClick={() => setSplits([...splits, { ...BLANK_SPLIT }])}>Add split</Btn>
              <Chip tone={cashBalanced ? 'success' : 'danger'}>
                Cash total {cashTotal}%{hasResidual ? ' + residual' : ''}
              </Chip>
              <Btn variant="primary" onClick={save} disabled={!form.code || !form.name || !cashBalanced}>Create rule</Btn>
            </div>
          </div>
        )}

        {loading ? <Skeleton h={200} /> : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: space(1.5) }}>
            {rules.length === 0 && <div style={{ padding: space(3), textAlign: 'center', color: color.textMuted }}>No settlement rules. Products fall back to their default account.</div>}
            {rules.map((r) => (
              <div key={r.id} style={{ border: `1px solid ${color.border}`, borderRadius: radius.md, padding: space(2), background: '#fff' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: space(1), flexWrap: 'wrap' }}>
                  <div>
                    <span style={{ fontWeight: 700 }}>{r.name}</span>
                    <span style={{ fontFamily: font.mono, fontSize: 12, color: color.textMuted, marginLeft: 8 }}>{r.code}</span>
                    <div style={{ fontSize: 12.5, color: color.textSecondary, marginTop: 2 }}>
                      {r.scope === 'PRODUCT' ? (r.product_name || 'Product') : r.scope === 'BUSINESS_UNIT' ? (r.business_unit || 'Unit') : 'All products'}
                      {' · priority '}{r.priority}
                      {' · from '}{r.effective_from}
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: space(1), alignItems: 'center' }}>
                    <Chip tone={r.is_active ? 'success' : 'neutral'}>{r.is_active ? 'Active' : 'Retired'}</Chip>
                    <Btn size="sm" variant="ghost" onClick={() => toggle(r)}>{r.is_active ? 'Retire' : 'Activate'}</Btn>
                  </div>
                </div>
                <div style={{ marginTop: space(1.5), display: 'flex', gap: space(1), flexWrap: 'wrap' }}>
                  {r.splits.map((s) => (
                    <div key={s.id} style={{
                      border: `1px solid ${s.allocation_type === 'OBLIGATION' ? color.warning : color.border}`,
                      background: s.allocation_type === 'OBLIGATION' ? color.warningBg : '#F8FAFC',
                      borderRadius: radius.sm, padding: '7px 11px', fontSize: 12.5,
                    }}>
                      <strong>{s.account_code}</strong>
                      <span style={{ color: color.textSecondary }}>
                        {' '}{s.is_residual ? 'residual' : s.percentage != null ? `${s.percentage}%` : s.fixed_amount != null ? naira(s.fixed_amount) : `${s.rate_per_unit}/unit`}
                      </span>
                      {s.allocation_type === 'OBLIGATION' && <span style={{ color: '#B45309', marginLeft: 6 }}>obligation</span>}
                      {s.account_status !== 'ACTIVE' && <span style={{ color: color.danger, marginLeft: 6 }}>{s.account_status}</span>}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Audit & refunds
// ---------------------------------------------------------------------------

function AuditTab() {
  const [log, setLog] = useState([]);
  const [refunds, setRefunds] = useState([]);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [loading, setLoading] = useState(true);
  const [refund, setRefund] = useState({
    settlement_id: '', amount: '', reason: '', approver_email: '', approver_password: '',
  });

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      getJSON('/api/finance/audit-log?limit=200').catch((e) => { setErr(e.message); return []; }),
      getJSON('/api/reports/refunds').catch(() => []),
    ]).then(([l, r]) => { setLog(l); setRefunds(r); }).finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const submitRefund = async () => {
    setErr(''); setMsg('');
    try {
      const body = { ...refund };
      if (!body.amount) delete body.amount; else body.amount = Number(body.amount);
      const r = await postJSON('/api/payments/refund', body);
      setMsg(`${r.refund_reference}: ${money(r.amount)} reversed on ${r.settlement_reference}, approved by ${r.approved_by}.`);
      setRefund({ settlement_id: '', amount: '', reason: '', approver_email: '', approver_password: '' });
      load();
    } catch (e) { setErr(e.message); }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: space(2) }}>
      <ErrorBox msg={err} />
      {msg && <Banner tone="success" title="Reversed">{msg}</Banner>}

      <Card>
        <SectionTitle>Reverse a distribution</SectionTitle>
        <div style={{ fontSize: 12.5, color: color.textSecondary, marginBottom: space(2), lineHeight: 1.5 }}>
          Every allocation is reversed proportionally, so no single account carries the whole
          clawback. Nothing is deleted — the original settlement and its reversal both stay on
          the record. A second administrator must authorise: reversing an allocation moves money
          back out of accounts other people are reconciling against.
          <br />
          This reverses the DISTRIBUTION. Refunding the customer and reversing the sale is the
          Returns module's job.
        </div>
        <Grid min={200}>
          <Field label="Settlement ID" hint="From the Settlements register.">
            <input style={inputStyle} value={refund.settlement_id} onChange={(e) => setRefund({ ...refund, settlement_id: e.target.value })} />
          </Field>
          <Field label="Amount" hint="Leave blank to reverse the whole settlement.">
            <input style={inputStyle} value={refund.amount} onChange={(e) => setRefund({ ...refund, amount: e.target.value })} />
          </Field>
          <Field label="Reason">
            <input style={inputStyle} value={refund.reason} onChange={(e) => setRefund({ ...refund, reason: e.target.value })} />
          </Field>
          <Field label="Approving administrator (email)">
            <input style={inputStyle} autoComplete="off" value={refund.approver_email} onChange={(e) => setRefund({ ...refund, approver_email: e.target.value })} />
          </Field>
          <Field label="Approver password">
            <input type="password" style={inputStyle} autoComplete="new-password" value={refund.approver_password} onChange={(e) => setRefund({ ...refund, approver_password: e.target.value })} />
          </Field>
        </Grid>
        <div style={{ marginTop: space(2) }}>
          <Btn variant="danger" onClick={submitRefund}
            disabled={!refund.settlement_id || refund.reason.length < 5 || !refund.approver_email || !refund.approver_password}>
            Reverse allocation
          </Btn>
        </div>
      </Card>

      <Card>
        <SectionTitle>Refunds</SectionTitle>
        <DataTable
          cols={[
            { key: 'refund_reference', label: 'Reference' },
            { key: 'settlement_reference', label: 'Settlement' },
            { key: 'invoice_number', label: 'Invoice' },
            { key: 'amount', label: 'Amount', align: 'right' },
            { key: 'is_full_reversal', label: 'Type' },
            { key: 'reason', label: 'Reason', wrap: true },
            { key: 'approved_by', label: 'Approved by' },
          ]}
          rows={refunds} empty="No allocations have been reversed."
          render={(r, c) => {
            if (c.key === 'amount') return money(r.amount);
            if (c.key === 'is_full_reversal') return <Chip tone={r.is_full_reversal ? 'danger' : 'warning'}>{r.is_full_reversal ? 'Full' : 'Partial'}</Chip>;
            return r[c.key] || '—';
          }} />
      </Card>

      <Card>
        <SectionTitle right={<Btn size="sm" variant="ghost" icon="refresh" onClick={load}>Refresh</Btn>}>
          Audit trail
        </SectionTitle>
        <div style={{ fontSize: 12.5, color: color.textSecondary, marginBottom: space(2) }}>
          Append-only. The database rejects any attempt to edit or delete these rows.
        </div>
        {loading ? <Skeleton h={200} /> : (
          <DataTable
            cols={[
              { key: 'created_at', label: 'When' },
              { key: 'event_type', label: 'Event' },
              { key: 'actor', label: 'Who' },
              { key: 'detail', label: 'Detail', wrap: true },
            ]}
            rows={log} maxHeight={520} empty="No events recorded yet."
            render={(r, c) => {
              if (c.key === 'created_at') return new Date(r.created_at).toLocaleString();
              if (c.key === 'event_type') return <Chip tone={r.event_type.includes('FAILED') ? 'danger' : r.event_type.includes('COMPLETED') ? 'success' : 'info'}>{r.event_type}</Chip>;
              if (c.key === 'detail') return <span style={{ fontFamily: font.mono, fontSize: 11, color: color.textSecondary }}>{JSON.stringify(r.detail)}</span>;
              return r[c.key] || '—';
            }} />
        )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// shell
// ---------------------------------------------------------------------------

const TABS = [
  ['dashboard', 'Dashboard', 'dashboard', DashboardTab],
  ['settlements', 'Settlements', 'ledger', SettlementsTab],
  ['exceptions', 'Exceptions', 'alert', ExceptionsTab],
  ['accounts', 'Accounts', 'bank', AccountsTab],
  ['products', 'Products', 'box', ProductsTab],
  ['rules', 'Rules', 'budget', RulesTab],
  ['audit', 'Audit & Refunds', 'qc', AuditTab],
];

export default function PaymentDistribution() {
  const [tab, setTab] = useState('dashboard');
  const Active = (TABS.find((t) => t[0] === tab) || TABS[0])[3];
  return (
    <div style={{ fontFamily: font.family, color: color.text, background: color.bg, minHeight: '100%', margin: -16, padding: space(3) }}>
      <style>{`@keyframes bmShimmer{0%{background-position:100% 0}100%{background-position:0 0}}
        .bm-mapd *::-webkit-scrollbar{height:9px;width:9px}
        .bm-mapd *::-webkit-scrollbar-thumb{background:#cbd5e1;border-radius:6px}`}</style>
      <div className="bm-mapd">
        <div style={{ display: 'flex', alignItems: 'center', gap: space(1.5), marginBottom: space(3) }}>
          <div style={{ width: 44, height: 44, borderRadius: radius.md, background: color.navy, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Icon name="wallet" size={22} color="#fff" />
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, letterSpacing: '-0.02em', color: color.navy }}>Payment Distribution</h1>
            <div style={{ fontSize: 12.5, color: color.textSecondary, marginTop: 1 }}>
              One invoice · one payment · every product's share in its own account — Bonnesante Medicals
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: space(3), background: '#fff', border: `1px solid ${color.border}`, borderRadius: radius.md, padding: 5 }}>
          {TABS.map(([id, label, icon]) => (
            <button key={id} onClick={() => setTab(id)}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 7, border: 0, borderRadius: radius.sm,
                padding: '9px 14px', cursor: 'pointer', fontFamily: font.family, fontSize: 13, fontWeight: 600,
                background: tab === id ? color.navy : 'transparent', color: tab === id ? '#fff' : color.textSecondary,
                transition: 'background .15s ease, color .15s ease',
              }}>
              <Icon name={icon} size={16} color={tab === id ? '#fff' : color.textMuted} />{label}
            </button>
          ))}
        </div>
        <Active />
      </div>
    </div>
  );
}
