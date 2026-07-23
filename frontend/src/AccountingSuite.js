// Accounting Suite — the enterprise UI for the double-entry accounting package.
//
// A self-contained module (mounted from AppMain when activeModule==='accounting')
// built on the Bonnesante design system (src/ui). Every figure is read from the
// ledger; this component originates nothing. Posting is gated on the server, so
// ledger reports may read empty until it is switched on — that is correct, not a
// bug.

import React, { useState, useEffect, useCallback } from 'react';
import { authedFetch } from './utils/api';
import { color, space, radius, font, naira } from './ui/theme';
import {
  Icon, Card, SectionTitle, KpiCard, Chip, Btn, DataTable,
  SkeletonCards, Skeleton, Banner, ErrorBox,
} from './ui/kit';

// ---------------------------------------------------------------------------
// data helpers
// ---------------------------------------------------------------------------

const todayISO = () => new Date().toISOString().slice(0, 10);
const monthStartISO = () => { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10); };
const yearStartISO = () => { const d = new Date(); return new Date(d.getFullYear(), 0, 1).toISOString().slice(0, 10); };

async function req(url, opts) {
  const res = await authedFetch(url, opts);
  if (!res.ok) {
    let d = `Request failed (${res.status})`;
    try { const j = await res.json(); d = j.detail || j.message || d; } catch {}
    throw new Error(d);
  }
  return res.json();
}
const getJSON = (u) => req(u);
const postJSON = (u, b) => req(u, { method: 'POST', body: JSON.stringify(b || {}) });
const putJSON = (u, b) => req(u, { method: 'PUT', body: JSON.stringify(b || {}) });

// shared bits ---------------------------------------------------------------

const Loading = () => <div style={{ padding: space(3), color: color.textMuted, fontSize: 13 }}>Loading…</div>;

function DateRange({ start, end, setStart, setEnd, onGo }) {
  const inp = { padding: '9px 10px', border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, fontSize: 13, fontFamily: font.family, color: color.text };
  return (
    <div style={{ display: 'flex', gap: space(1.5), alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: space(2) }}>
      <label style={{ fontSize: 12, color: color.textSecondary, fontWeight: 500 }}>From<br /><input type="date" value={start} onChange={(e) => setStart(e.target.value)} style={{ ...inp, marginTop: 4 }} /></label>
      <label style={{ fontSize: 12, color: color.textSecondary, fontWeight: 500 }}>To<br /><input type="date" value={end} onChange={(e) => setEnd(e.target.value)} style={{ ...inp, marginTop: 4 }} /></label>
      <Btn variant="accent" size="sm" icon="refresh" onClick={onGo}>Run</Btn>
    </div>
  );
}

const money = (v) => naira(v);
const statusTone = (s) => {
  const t = String(s || '').toLowerCase();
  if (['paid', 'pass', 'approved', 'posted', 'active'].includes(t)) return 'success';
  if (['pending', 'draft', 'unpaid', 'filed'].includes(t)) return 'warning';
  if (['fail', 'cancelled', 'overdue', 'rejected'].includes(t)) return 'danger';
  return 'neutral';
};

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

function DashboardTab() {
  const [d, setD] = useState(null); const [err, setErr] = useState(''); const [loading, setLoading] = useState(true);
  useEffect(() => { setLoading(true); getJSON('/api/dashboard/executive').then(setD).catch((e) => setErr(e.message)).finally(() => setLoading(false)); }, []);
  if (loading) return <div><SkeletonCards n={6} /><div style={{ height: 16 }} /><SkeletonCards n={3} /></div>;
  if (err) return <ErrorBox msg={err} />;
  if (!d) return null;
  const pm = d.profitability.month, py = d.profitability.year_to_date;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: space(2) }}>
      {d.warnings?.length > 0 && (
        <Banner tone="warning" title={`${d.warnings.length} item${d.warnings.length > 1 ? 's need' : ' needs'} attention`}>
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>{d.warnings.map((w, i) => <li key={i} style={{ marginTop: 2 }}>{w}</li>)}</ul>
        </Banner>
      )}
      {/* KPI row */}
      <div style={{ display: 'flex', gap: space(2), flexWrap: 'wrap' }}>
        <KpiCard icon="bank" tone="success" label="Revenue · MTD" value={naira(pm.income, { compact: true })} sub="This month" />
        <KpiCard icon="wallet" tone="royal" label="Cash on hand" value={naira(d.liquidity.cash_on_hand, { compact: true })} sub="All accounts" />
        <KpiCard icon="receivable" tone="info" label="Receivables" value={naira(d.working_capital.receivables, { compact: true })} sub="Owed to you" />
        <KpiCard icon="tag" tone="warning" label="Payables" value={naira(d.working_capital.payables, { compact: true })} sub="You owe" />
        <KpiCard icon={pm.net_profit >= 0 ? 'trendUp' : 'trendDown'} tone={pm.net_profit >= 0 ? 'success' : 'danger'} label="Net profit · MTD" value={naira(pm.net_profit, { compact: true })} sub="This month" />
        <KpiCard icon="budget" tone="info" label="Working capital" value={naira(d.liquidity.net_working_capital, { compact: true })} sub="Cash + AR − AP" />
      </div>
      {/* position + liquidity */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(280px,1fr))', gap: space(2) }}>
        <Card>
          <SectionTitle right={<Chip tone={d.position.balanced ? 'success' : 'danger'}>{d.position.balanced ? 'Balanced' : 'Unbalanced'}</Chip>}>Financial position</SectionTitle>
          <Row2 label="Assets" value={money(d.position.assets)} />
          <Row2 label="Liabilities" value={money(d.position.liabilities)} />
          <Row2 label="Total equity" value={money(d.position.total_equity)} strong />
        </Card>
        <Card>
          <SectionTitle>Year to date</SectionTitle>
          <Row2 label="Income" value={money(py.income)} />
          <Row2 label="Expenses" value={money(py.expenses)} />
          <Row2 label="Net profit" value={money(py.net_profit)} strong tone={py.net_profit >= 0 ? 'success' : 'danger'} />
        </Card>
        <Card>
          <SectionTitle>Overdue receivables</SectionTitle>
          <div style={{ fontSize: 30, fontWeight: 700, color: d.working_capital.overdue_receivables.amount > 0 ? color.danger : color.text, letterSpacing: '-0.02em' }}>
            {money(d.working_capital.overdue_receivables.amount)}
          </div>
          <div style={{ fontSize: 12.5, color: color.textSecondary, marginTop: 4 }}>{d.working_capital.overdue_receivables.count} invoice(s) past due</div>
        </Card>
      </div>
      {d.top_cost_centres?.length > 0 && (
        <Card>
          <SectionTitle>Top cost centres · this month</SectionTitle>
          <DataTable cols={[{ key: 'cost_centre', label: 'Cost centre' }, { key: 'spend', label: 'Spend', align: 'right' }]}
            rows={d.top_cost_centres} render={(r, c) => c.key === 'spend' ? <strong>{money(r.spend)}</strong> : <Chip tone="info">{r.cost_centre}</Chip>} />
        </Card>
      )}
    </div>
  );
}

function Row2({ label, value, strong, tone }) {
  const c = tone === 'success' ? color.success : tone === 'danger' ? color.danger : color.text;
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '9px 0', borderBottom: `1px solid #F1F5F9` }}>
      <span style={{ fontSize: 13, color: color.textSecondary }}>{label}</span>
      <span style={{ fontSize: strong ? 16 : 14, fontWeight: strong ? 700 : 500, color: c }}>{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Financial reports
// ---------------------------------------------------------------------------

function ReportsTab() {
  const [start, setStart] = useState(monthStartISO()); const [end, setEnd] = useState(todayISO());
  const [tb, setTb] = useState(null); const [pnl, setPnl] = useState(null); const [bs, setBs] = useState(null); const [cf, setCf] = useState(null);
  const [err, setErr] = useState(''); const [loading, setLoading] = useState(false);
  const run = useCallback(() => {
    setLoading(true); setErr('');
    Promise.all([
      getJSON(`/api/accounting/trial-balance?start=${start}&end=${end}`),
      getJSON(`/api/accounting/profit-and-loss?start=${start}&end=${end}`),
      getJSON(`/api/accounting/balance-sheet?as_at=${end}`),
      getJSON(`/api/cash/flow?start=${start}&end=${end}`).catch(() => null),
    ]).then(([a, b, c, e]) => { setTb(a); setPnl(b); setBs(c); setCf(e); }).catch((e) => setErr(e.message)).finally(() => setLoading(false));
  }, [start, end]);
  useEffect(() => { run(); }, []); // eslint-disable-line
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: space(2) }}>
      <DateRange start={start} end={end} setStart={setStart} setEnd={setEnd} onGo={run} />
      <ErrorBox msg={err} />
      {loading && <Loading />}
      {pnl && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(240px,1fr))', gap: space(2) }}>
          <KpiCard icon="bank" tone="success" label="Income" value={naira(pnl.total_income, { compact: true })} sub={`${start} → ${end}`} />
          <KpiCard icon="tag" tone="warning" label="Expenses" value={naira(pnl.total_expenses, { compact: true })} sub="Period total" />
          <KpiCard icon={pnl.net_profit >= 0 ? 'trendUp' : 'trendDown'} tone={pnl.net_profit >= 0 ? 'success' : 'danger'} label="Net profit" value={naira(pnl.net_profit, { compact: true })} sub="Income − expenses" />
        </div>
      )}
      {bs && (
        <Card>
          <SectionTitle right={<Chip tone={bs.balanced ? 'success' : 'danger'}>{bs.balanced ? 'Balanced' : `Diff ${money(bs.difference)}`}</Chip>}>Balance sheet · as at {end}</SectionTitle>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(160px,1fr))', gap: space(2) }}>
            <Metric label="Assets" value={money(bs.assets)} />
            <Metric label="Liabilities" value={money(bs.liabilities)} />
            <Metric label="Equity" value={money(bs.total_equity)} />
          </div>
        </Card>
      )}
      {tb && (
        <Card>
          <SectionTitle right={<span style={{ fontSize: 12.5, color: tb.balanced ? color.success : color.danger, fontWeight: 600 }}>Dr {money(tb.total_debit)} · Cr {money(tb.total_credit)}</span>}>Trial balance</SectionTitle>
          <DataTable maxHeight={420}
            cols={[{ key: 'code', label: 'Code' }, { key: 'name', label: 'Account' }, { key: 'debit', label: 'Debit', align: 'right' }, { key: 'credit', label: 'Credit', align: 'right' }]}
            rows={tb.accounts || []} render={(r, c) => (c.key === 'debit' || c.key === 'credit') ? money(r[c.key]) : c.key === 'code' ? <span style={{ fontFamily: font.mono, color: color.textSecondary }}>{r.code}</span> : r[c.key]} />
        </Card>
      )}
      {cf && (
        <Card>
          <SectionTitle right={<Chip tone={cf.reconciles ? 'success' : 'danger'}>{cf.reconciles ? 'Reconciles' : 'Does not reconcile'}</Chip>}>Cash flow · direct method</SectionTitle>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: space(2) }}>
            <Metric label="Operating" value={money(cf.operating?.total)} />
            <Metric label="Investing" value={money(cf.investing?.total)} />
            <Metric label="Financing" value={money(cf.financing?.total)} />
            <Metric label="Net movement" value={money(cf.net_movement)} />
          </div>
        </Card>
      )}
    </div>
  );
}

function Metric({ label, value }) {
  return (
    <div style={{ background: color.bg, borderRadius: radius.sm, padding: space(2) }}>
      <div style={{ fontSize: 11.5, color: color.textSecondary, textTransform: 'uppercase', letterSpacing: '0.04em', fontWeight: 500 }}>{label}</div>
      <div style={{ fontSize: 19, fontWeight: 700, color: color.text, marginTop: 4, letterSpacing: '-0.01em' }}>{value}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// General ledger
// ---------------------------------------------------------------------------

function LedgerTab() {
  const [accounts, setAccounts] = useState([]); const [err, setErr] = useState('');
  const [sel, setSel] = useState(null); const [ledger, setLedger] = useState(null);
  useEffect(() => { getJSON('/api/accounting/accounts').then((d) => setAccounts(d.accounts || d)).catch((e) => setErr(e.message)); }, []);
  const open = (code) => { setSel(code); setLedger(null); getJSON(`/api/accounting/accounts/${code}/ledger`).then(setLedger).catch((e) => setErr(e.message)); };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: space(2) }}>
      <ErrorBox msg={err} />
      <Card>
        <SectionTitle>Chart of accounts</SectionTitle>
        <DataTable maxHeight={440}
          cols={[{ key: 'code', label: 'Code' }, { key: 'name', label: 'Name' }, { key: 'account_type', label: 'Type' }, { key: 'act', label: '', align: 'right' }]}
          rows={accounts}
          render={(r, c) => c.key === 'act' ? <Btn size="sm" variant="secondary" icon="ledger" onClick={() => open(r.code)}>Ledger</Btn>
            : c.key === 'code' ? <span style={{ fontFamily: font.mono, color: color.textSecondary }}>{r.code}</span>
              : c.key === 'account_type' ? <Chip tone="neutral">{r.account_type}</Chip> : r[c.key]} />
      </Card>
      {sel && (
        <Card>
          <SectionTitle right={ledger && <span style={{ fontSize: 13, color: color.textSecondary }}>Closing <strong style={{ color: color.text }}>{money(ledger.closing_balance)}</strong></span>}>
            Account ledger · {sel} {ledger?.account?.name ? `— ${ledger.account.name}` : ''}
          </SectionTitle>
          {!ledger ? <Loading /> : (
            <DataTable maxHeight={360}
              cols={[{ key: 'date', label: 'Date' }, { key: 'description', label: 'Description', wrap: true }, { key: 'debit', label: 'Debit', align: 'right' }, { key: 'credit', label: 'Credit', align: 'right' }, { key: 'balance', label: 'Balance', align: 'right' }]}
              rows={ledger.entries || []} render={(r, c) => ['debit', 'credit', 'balance'].includes(c.key) ? money(r[c.key]) : r[c.key]} />
          )}
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Product costs
// ---------------------------------------------------------------------------

function CostsTab() {
  const [kind, setKind] = useState('products'); const [rows, setRows] = useState([]); const [edits, setEdits] = useState({});
  const [err, setErr] = useState(''); const [msg, setMsg] = useState(''); const [loading, setLoading] = useState(true); const [saving, setSaving] = useState(false); const [onlyMissing, setOnlyMissing] = useState(false);
  const load = useCallback(() => { setLoading(true); setErr(''); setEdits({}); getJSON(`/api/costs/${kind}`).then(setRows).catch((e) => setErr(e.message)).finally(() => setLoading(false)); }, [kind]);
  useEffect(() => { load(); }, [load]);
  const costKey = kind === 'products' ? 'cost_price' : 'unit_cost';
  const save = async () => {
    const updates = Object.entries(edits).filter(([, v]) => v !== '' && v != null && !Number.isNaN(Number(v))).map(([id, v]) => ({ id, cost: Number(v) }));
    if (!updates.length) { setMsg('No changes to save.'); return; }
    setSaving(true); setErr(''); setMsg('');
    try { const r = await putJSON(`/api/costs/${kind}`, { updates }); setMsg(`Saved ${r.updated} cost${r.updated === 1 ? '' : 's'}.`); load(); }
    catch (e) { setErr(e.message); } finally { setSaving(false); }
  };
  const shown = rows.filter((r) => !onlyMissing || r.missing_cost);
  const missing = rows.filter((r) => r.missing_cost).length;
  const pending = Object.keys(edits).filter((k) => edits[k] !== '' && edits[k] != null).length;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: space(2) }}>
      <ErrorBox msg={err} />
      {msg && <Banner tone="success">{msg}</Banner>}
      <Banner tone="info">Enter the unit <strong>cost</strong> (what it costs you) for each item — this feeds stock valuation and profit. {kind === 'products' && 'A cost above the selling price shows a negative margin, flagging a likely typo.'}</Banner>
      <Card>
        <div style={{ display: 'flex', gap: space(1), alignItems: 'center', flexWrap: 'wrap', marginBottom: space(2) }}>
          <Btn size="sm" variant={kind === 'products' ? 'primary' : 'secondary'} icon="box" onClick={() => setKind('products')}>Products</Btn>
          <Btn size="sm" variant={kind === 'materials' ? 'primary' : 'secondary'} icon="tag" onClick={() => setKind('materials')}>Raw Materials</Btn>
          <Chip tone={missing ? 'warning' : 'success'}>{missing} of {rows.length} missing a cost</Chip>
          <label style={{ marginLeft: 'auto', fontSize: 13, color: color.textSecondary, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <input type="checkbox" checked={onlyMissing} onChange={(e) => setOnlyMissing(e.target.checked)} /> Show only missing
          </label>
        </div>
        {loading ? <div><Skeleton h={40} /><Skeleton h={40} style={{ marginTop: 8 }} /><Skeleton h={40} style={{ marginTop: 8 }} /></div> : (
          <DataTable maxHeight={460}
            cols={[
              { key: 'name', label: 'Item' }, { key: 'unit', label: 'Unit' }, { key: 'on_hand', label: 'On hand', align: 'right' },
              ...(kind === 'products' ? [{ key: 'selling_price', label: 'Selling', align: 'right' }] : [{ key: 'stock_value', label: 'Stock value', align: 'right' }]),
              { key: 'cost', label: 'Unit cost', align: 'right' },
              ...(kind === 'products' ? [{ key: 'margin', label: 'Margin', align: 'right' }] : []),
            ]}
            rows={shown}
            render={(r, c) => {
              if (c.key === 'unit') return <span style={{ color: color.textSecondary }}>{r.unit || '—'}</span>;
              if (c.key === 'on_hand') return (r.on_hand || 0).toLocaleString();
              if (c.key === 'selling_price') return r.selling_price != null ? money(r.selling_price) : '—';
              if (c.key === 'stock_value') return <span style={{ color: r.stock_value > 5000000 ? color.danger : color.text, fontWeight: r.stock_value > 5000000 ? 600 : 400 }}>{money(r.stock_value)}</span>;
              if (c.key === 'cost') return (
                <input type="number" min="0" step="any" defaultValue={r[costKey] ?? ''} onChange={(e) => setEdits((p) => ({ ...p, [r.id]: e.target.value }))}
                  style={{ width: 120, padding: '7px 9px', textAlign: 'right', border: `1px solid ${r.missing_cost ? color.warning : color.borderStrong}`, borderRadius: radius.sm, fontFamily: font.family, fontSize: 13 }} />
              );
              if (c.key === 'margin') {
                const ev = edits[r.id] !== undefined && edits[r.id] !== '' ? Number(edits[r.id]) : r.cost_price;
                const m = r.selling_price && ev != null ? Math.round((r.selling_price - ev) / r.selling_price * 1000) / 10 : null;
                return m == null ? <span style={{ color: color.textMuted }}>—</span> : <Chip tone={m < 0 ? 'danger' : m < 10 ? 'warning' : 'success'}>{m}%</Chip>;
              }
              return <span style={{ fontWeight: 500, background: r.missing_cost ? color.warningBg : 'transparent', padding: r.missing_cost ? '2px 6px' : 0, borderRadius: 4 }}>{r.name}</span>;
            }} />
        )}
        <div style={{ marginTop: space(2), display: 'flex', gap: space(1.5), alignItems: 'center' }}>
          <Btn variant="primary" icon="check" onClick={save} disabled={saving}>{saving ? 'Saving…' : `Save costs${pending ? ` (${pending})` : ''}`}</Btn>
          <span style={{ fontSize: 12, color: color.textMuted }}>Changes apply when you click Save.</span>
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Fixed assets
// ---------------------------------------------------------------------------

function AssetsTab() {
  const [assets, setAssets] = useState([]); const [err, setErr] = useState(''); const [msg, setMsg] = useState('');
  const load = useCallback(() => { getJSON('/api/assets/').then((d) => setAssets(d.assets || d)).catch((e) => setErr(e.message)); }, []);
  useEffect(() => { load(); }, [load]);
  const runDep = async () => { setErr(''); setMsg(''); try { const p = monthStartISO(); await postJSON('/api/assets/depreciation/run', { period: p }); setMsg(`Depreciation run for ${p}.`); load(); } catch (e) { setErr(e.message); } };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: space(2) }}>
      <ErrorBox msg={err} />
      {msg && <Banner tone="success">{msg}</Banner>}
      <Card>
        <SectionTitle right={<Btn size="sm" variant="accent" icon="refresh" onClick={runDep}>Run monthly depreciation</Btn>}>Fixed asset register</SectionTitle>
        <DataTable maxHeight={460}
          cols={[{ key: 'asset_number', label: 'No.' }, { key: 'name', label: 'Asset' }, { key: 'category', label: 'Category' }, { key: 'cost', label: 'Cost', align: 'right' }, { key: 'status', label: 'Status' }]}
          rows={assets} render={(r, c) => c.key === 'cost' ? money(r.cost) : c.key === 'status' ? <Chip tone={statusTone(r.status)}>{r.status}</Chip> : r[c.key]} />
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Budgeting
// ---------------------------------------------------------------------------

function BudgetingTab() {
  const [centres, setCentres] = useState([]); const [report, setReport] = useState(null);
  const [start, setStart] = useState(monthStartISO()); const [end, setEnd] = useState(todayISO());
  const [err, setErr] = useState(''); const [nc, setNc] = useState({ code: '', name: '' });
  const loadC = useCallback(() => { getJSON('/api/budgeting/cost-centres').then(setCentres).catch((e) => setErr(e.message)); }, []);
  useEffect(() => { loadC(); }, [loadC]);
  const run = () => { setErr(''); getJSON(`/api/budgeting/cost-centres/report?start=${start}&end=${end}`).then(setReport).catch((e) => setErr(e.message)); };
  const add = async () => { setErr(''); try { await postJSON('/api/budgeting/cost-centres', nc); setNc({ code: '', name: '' }); loadC(); } catch (e) { setErr(e.message); } };
  const inp = { padding: '9px 10px', border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, fontSize: 13, fontFamily: font.family };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: space(2) }}>
      <ErrorBox msg={err} />
      <Card>
        <SectionTitle>Cost centres</SectionTitle>
        <div style={{ display: 'flex', gap: space(1), marginBottom: space(2), flexWrap: 'wrap' }}>
          <input placeholder="Code" value={nc.code} onChange={(e) => setNc({ ...nc, code: e.target.value })} style={{ ...inp, width: 110 }} />
          <input placeholder="Name" value={nc.name} onChange={(e) => setNc({ ...nc, name: e.target.value })} style={{ ...inp, width: 220 }} />
          <Btn variant="primary" size="sm" icon="check" onClick={add} disabled={!nc.code || !nc.name}>Add</Btn>
        </div>
        <DataTable cols={[{ key: 'code', label: 'Code' }, { key: 'name', label: 'Name' }, { key: 'category', label: 'Category' }]} rows={centres}
          render={(r, c) => c.key === 'code' ? <Chip tone="info">{r.code}</Chip> : r[c.key]} />
      </Card>
      <Card>
        <SectionTitle>Cost centre report</SectionTitle>
        <DateRange start={start} end={end} setStart={setStart} setEnd={setEnd} onGo={run} />
        {report && (
          <>
            {report.unallocated_warning && <Banner tone="warning">{report.unallocated_warning}</Banner>}
            <DataTable cols={[{ key: 'cost_centre', label: 'Centre' }, { key: 'name', label: 'Name' }, { key: 'income', label: 'Income', align: 'right' }, { key: 'expenditure', label: 'Expenditure', align: 'right' }, { key: 'net', label: 'Net', align: 'right' }]}
              rows={report.cost_centres || []} render={(r, c) => ['income', 'expenditure', 'net'].includes(c.key) ? money(r[c.key]) : c.key === 'cost_centre' ? <Chip tone="info">{r.cost_centre}</Chip> : r[c.key]} />
          </>
        )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// VAT
// ---------------------------------------------------------------------------

function VatTab() {
  const [start, setStart] = useState(monthStartISO()); const [end, setEnd] = useState(todayISO());
  const [computed, setComputed] = useState(null); const [returns, setReturns] = useState([]);
  const [err, setErr] = useState(''); const [msg, setMsg] = useState('');
  const loadR = useCallback(() => { getJSON('/api/tax/vat/returns').then(setReturns).catch(() => {}); }, []);
  useEffect(() => { loadR(); }, [loadR]);
  const compute = () => { setErr(''); getJSON(`/api/tax/vat/compute?start=${start}&end=${end}`).then(setComputed).catch((e) => setErr(e.message)); };
  const file = async () => {
    setErr(''); setMsg(''); const filed_by = window.prompt('File this VAT return — your name for the record:'); if (!filed_by) return;
    try { await postJSON('/api/tax/vat/returns', { period_start: start, period_end: end, filed_by }); setMsg('VAT return filed.'); loadR(); } catch (e) { setErr(e.message); }
  };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: space(2) }}>
      <ErrorBox msg={err} />
      {msg && <Banner tone="success">{msg}</Banner>}
      <Card>
        <SectionTitle>Compute VAT position</SectionTitle>
        <DateRange start={start} end={end} setStart={setStart} setEnd={setEnd} onGo={compute} />
        {computed && (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: space(2), marginBottom: space(2) }}>
              <Metric label="Output VAT" value={money(computed.output_vat)} />
              <Metric label="Input VAT" value={money(computed.input_vat)} />
              <Metric label="Net payable" value={money(computed.net_payable)} />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: space(1.5), flexWrap: 'wrap' }}>
              <Chip tone={computed.position === 'PAYABLE' ? 'warning' : computed.position === 'CREDIT' ? 'success' : 'neutral'}>{computed.position}</Chip>
              <Btn variant="primary" size="sm" icon="vat" onClick={file}>File this return</Btn>
            </div>
            {(computed.notes || []).map((n, i) => <div key={i} style={{ color: '#B45309', fontSize: 12.5, marginTop: 6 }}>• {n}</div>)}
          </>
        )}
      </Card>
      <Card>
        <SectionTitle>Filed returns</SectionTitle>
        <DataTable cols={[{ key: 'period_start', label: 'From' }, { key: 'period_end', label: 'To' }, { key: 'output_vat', label: 'Output', align: 'right' }, { key: 'input_vat', label: 'Input', align: 'right' }, { key: 'net_payable', label: 'Net', align: 'right' }, { key: 'status', label: 'Status' }]}
          rows={returns} render={(r, c) => ['output_vat', 'input_vat', 'net_payable'].includes(c.key) ? money(r[c.key]) : c.key === 'status' ? <Chip tone={statusTone(r.status)}>{r.status}</Chip> : r[c.key]} />
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// QC costing
// ---------------------------------------------------------------------------

function QcTab() {
  const [rows, setRows] = useState([]); const [err, setErr] = useState('');
  const [form, setForm] = useState({ subject: '', cost: '', inspection_type: 'in_process', result: 'pending', post_cost: true });
  const load = useCallback(() => { getJSON('/api/maintenance/qc-inspections').then(setRows).catch((e) => setErr(e.message)); }, []);
  useEffect(() => { load(); }, [load]);
  const add = async () => { setErr(''); try { await postJSON('/api/maintenance/qc-inspections', { ...form, cost: Number(form.cost || 0) }); setForm({ subject: '', cost: '', inspection_type: 'in_process', result: 'pending', post_cost: true }); load(); } catch (e) { setErr(e.message); } };
  const inp = { padding: '9px 10px', border: `1px solid ${color.borderStrong}`, borderRadius: radius.sm, fontSize: 13, fontFamily: font.family };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: space(2) }}>
      <ErrorBox msg={err} />
      <Card>
        <SectionTitle>Record QC inspection</SectionTitle>
        <div style={{ display: 'flex', gap: space(1), flexWrap: 'wrap', alignItems: 'center' }}>
          <input placeholder="Subject / batch" value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} style={{ ...inp, width: 220 }} />
          <input placeholder="Cost" type="number" value={form.cost} onChange={(e) => setForm({ ...form, cost: e.target.value })} style={{ ...inp, width: 110 }} />
          <select value={form.inspection_type} onChange={(e) => setForm({ ...form, inspection_type: e.target.value })} style={inp}><option value="incoming">Incoming</option><option value="in_process">In-process</option><option value="finished">Finished</option><option value="stability">Stability</option></select>
          <select value={form.result} onChange={(e) => setForm({ ...form, result: e.target.value })} style={inp}><option value="pending">Pending</option><option value="pass">Pass</option><option value="fail">Fail</option><option value="conditional">Conditional</option></select>
          <label style={{ fontSize: 13, display: 'inline-flex', alignItems: 'center', gap: 5, color: color.textSecondary }}><input type="checkbox" checked={form.post_cost} onChange={(e) => setForm({ ...form, post_cost: e.target.checked })} /> Post cost</label>
          <Btn variant="primary" size="sm" icon="check" onClick={add} disabled={!form.subject}>Add</Btn>
        </div>
      </Card>
      <Card>
        <SectionTitle>QC inspections</SectionTitle>
        <DataTable cols={[{ key: 'inspection_number', label: 'No.' }, { key: 'inspection_date', label: 'Date' }, { key: 'subject', label: 'Subject' }, { key: 'inspection_type', label: 'Type' }, { key: 'result', label: 'Result' }, { key: 'cost', label: 'Cost', align: 'right' }, { key: 'posted', label: 'Posted' }]}
          rows={rows} render={(r, c) => c.key === 'cost' ? money(r.cost) : c.key === 'result' ? <Chip tone={statusTone(r.result)}>{r.result}</Chip> : c.key === 'posted' ? <Chip tone={r.posted ? 'success' : 'neutral'}>{r.posted ? 'Posted' : 'Not posted'}</Chip> : r[c.key]} />
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Payroll
// ---------------------------------------------------------------------------

function PayrollTab() {
  const [runs, setRuns] = useState([]); const [configs, setConfigs] = useState([]); const [liab, setLiab] = useState(null); const [err, setErr] = useState('');
  useEffect(() => {
    getJSON('/api/payroll/runs').then((d) => setRuns(d.runs || d)).catch((e) => setErr(e.message));
    getJSON('/api/payroll/rate-configs').then((d) => setConfigs(d.configs || d)).catch(() => {});
    getJSON('/api/payroll/statutory-liabilities').then(setLiab).catch(() => {});
  }, []);
  const unconfirmed = Array.isArray(configs) && configs.some((c) => c.confirmed === false || c.status === 'UNCONFIRMED');
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: space(2) }}>
      <ErrorBox msg={err} />
      {unconfirmed && <Banner tone="warning" title="Payroll rates unconfirmed">The engine refuses to run payroll until an accountant confirms the tax bands against current Nigerian law.</Banner>}
      <Card>
        <SectionTitle>Payroll runs</SectionTitle>
        <DataTable cols={[{ key: 'period', label: 'Period' }, { key: 'status', label: 'Status' }, { key: 'gross', label: 'Gross', align: 'right' }, { key: 'net', label: 'Net', align: 'right' }]}
          rows={Array.isArray(runs) ? runs : []} render={(r, c) => ['gross', 'net'].includes(c.key) ? money(r[c.key] || r[`total_${c.key}`]) : c.key === 'status' ? <Chip tone={statusTone(r.status)}>{r.status || '—'}</Chip> : (r[c.key] || r.period_month || '—')} />
      </Card>
      {liab && (
        <Card>
          <SectionTitle>Statutory liabilities outstanding</SectionTitle>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: space(2) }}>
            {Object.entries(liab).filter(([, v]) => typeof v === 'number').map(([k, v]) => <Metric key={k} label={k.replace(/_/g, ' ')} value={money(v)} />)}
          </div>
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// shell
// ---------------------------------------------------------------------------

const TABS = [
  ['dashboard', 'Dashboard', 'dashboard', DashboardTab],
  ['reports', 'Financial Reports', 'reports', ReportsTab],
  ['ledger', 'General Ledger', 'ledger', LedgerTab],
  ['costs', 'Product Costs', 'tag', CostsTab],
  ['assets', 'Fixed Assets', 'asset', AssetsTab],
  ['budgeting', 'Budgeting', 'budget', BudgetingTab],
  ['vat', 'VAT Returns', 'vat', VatTab],
  ['qc', 'QC Costing', 'qc', QcTab],
  ['payroll', 'Payroll', 'payroll', PayrollTab],
];

export default function AccountingSuite() {
  const [tab, setTab] = useState('dashboard');
  const Active = (TABS.find((t) => t[0] === tab) || TABS[0])[3];
  return (
    <div style={{ fontFamily: font.family, color: color.text, background: color.bg, minHeight: '100%', margin: -16, padding: space(3) }}>
      <style>{`@keyframes bmShimmer{0%{background-position:100% 0}100%{background-position:0 0}}
        .bm-acc *::-webkit-scrollbar{height:9px;width:9px}
        .bm-acc *::-webkit-scrollbar-thumb{background:#cbd5e1;border-radius:6px}`}</style>
      <div className="bm-acc">
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: space(1.5), marginBottom: space(3) }}>
          <div style={{ width: 44, height: 44, borderRadius: radius.md, background: color.navy, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Icon name="bank" size={22} color="#fff" />
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, letterSpacing: '-0.02em', color: color.navy }}>Accounting</h1>
            <div style={{ fontSize: 12.5, color: color.textSecondary, marginTop: 1 }}>Double-entry ledger · reporting · tax · payroll — Bonnesante Medicals</div>
          </div>
        </div>
        {/* Tab bar */}
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
