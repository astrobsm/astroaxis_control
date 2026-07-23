// Accounting Suite — the UI for the double-entry accounting package.
//
// A self-contained module (mounted from AppMain when activeModule==='accounting')
// with sub-tabs over the backend accounting APIs. Every figure shown here is
// read from the ledger; this component originates nothing.
//
// Posting is gated OFF on the server by default, so the ledger reports may be
// empty until ACCOUNTING_POSTING_ENABLED is set. That is expected, not a bug —
// the reports are correct reflections of an empty ledger.

import React, { useState, useEffect, useCallback } from 'react';
import { authedFetch } from './utils/api';

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

const NGN = (v) =>
  '₦' + Number(v || 0).toLocaleString('en-NG', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });

const todayISO = () => new Date().toISOString().slice(0, 10);
const monthStartISO = () => {
  const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1)
    .toISOString().slice(0, 10);
};
const yearStartISO = () => {
  const d = new Date(); return new Date(d.getFullYear(), 0, 1)
    .toISOString().slice(0, 10);
};

async function getJSON(url) {
  const res = await authedFetch(url);
  if (!res.ok) {
    let d = `Request failed (${res.status})`;
    try { const j = await res.json(); d = j.detail || j.message || d; } catch {}
    throw new Error(d);
  }
  return res.json();
}

async function postJSON(url, body) {
  const res = await authedFetch(url, { method: 'POST', body: JSON.stringify(body || {}) });
  if (!res.ok) {
    let d = `Request failed (${res.status})`;
    try { const j = await res.json(); d = j.detail || j.message || d; } catch {}
    throw new Error(d);
  }
  return res.json();
}

// small presentational atoms -------------------------------------------------

const Card = ({ title, children, accent }) => (
  <div style={{
    background: '#fff', borderRadius: 10, padding: 16, boxShadow: '0 1px 4px rgba(0,0,0,0.08)',
    borderTop: `3px solid ${accent || '#2563eb'}`, marginBottom: 16,
  }}>
    {title && <h3 style={{ margin: '0 0 12px', fontSize: 15, color: '#1e293b' }}>{title}</h3>}
    {children}
  </div>
);

const Stat = ({ label, value, color }) => (
  <div style={{ flex: '1 1 160px', minWidth: 160, background: '#f8fafc', borderRadius: 8, padding: '12px 14px' }}>
    <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, color: color || '#0f172a' }}>{value}</div>
  </div>
);

const Loading = () => <div style={{ padding: 20, color: '#64748b' }}>Loading…</div>;
const ErrorBox = ({ msg }) => msg ? (
  <div style={{ padding: 12, background: '#fef2f2', color: '#b91c1c', borderRadius: 8, marginBottom: 12, fontSize: 13 }}>{msg}</div>
) : null;

const Table = ({ cols, rows, render }) => (
  <div style={{ overflowX: 'auto' }}>
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
      <thead>
        <tr>{cols.map((c) => (
          <th key={c.key} style={{ textAlign: c.align || 'left', padding: '8px 10px', borderBottom: '2px solid #e2e8f0', color: '#475569', whiteSpace: 'nowrap' }}>{c.label}</th>
        ))}</tr>
      </thead>
      <tbody>
        {rows.length === 0 ? (
          <tr><td colSpan={cols.length} style={{ padding: 16, color: '#94a3b8', textAlign: 'center' }}>No records.</td></tr>
        ) : rows.map((r, i) => (
          <tr key={i} style={{ borderBottom: '1px solid #f1f5f9' }}>
            {cols.map((c) => (
              <td key={c.key} style={{ padding: '7px 10px', textAlign: c.align || 'left' }}>
                {render ? render(r, c) : r[c.key]}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

const DateRange = ({ start, end, setStart, setEnd, onGo }) => (
  <div style={{ display: 'flex', gap: 8, alignItems: 'end', flexWrap: 'wrap', marginBottom: 12 }}>
    <label style={{ fontSize: 12, color: '#475569' }}>From<br />
      <input type="date" value={start} onChange={(e) => setStart(e.target.value)} style={{ padding: 6 }} /></label>
    <label style={{ fontSize: 12, color: '#475569' }}>To<br />
      <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} style={{ padding: 6 }} /></label>
    <button onClick={onGo} style={btnPrimary}>Run</button>
  </div>
);

const btnPrimary = { background: '#2563eb', color: '#fff', border: 0, borderRadius: 6, padding: '8px 16px', cursor: 'pointer', fontWeight: 600 };
const btnGhost = { background: '#f1f5f9', color: '#334155', border: 0, borderRadius: 6, padding: '8px 14px', cursor: 'pointer' };

// ---------------------------------------------------------------------------
// tabs
// ---------------------------------------------------------------------------

function DashboardTab() {
  const [d, setD] = useState(null); const [err, setErr] = useState(''); const [loading, setLoading] = useState(true);
  useEffect(() => {
    setLoading(true);
    getJSON('/api/dashboard/executive').then(setD).catch((e) => setErr(e.message)).finally(() => setLoading(false));
  }, []);
  if (loading) return <Loading />;
  if (err) return <ErrorBox msg={err} />;
  if (!d) return null;
  const pm = d.profitability.month, py = d.profitability.year_to_date;
  return (
    <div>
      {d.warnings && d.warnings.length > 0 && (
        <div style={{ background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 8, padding: 12, marginBottom: 16 }}>
          <strong style={{ color: '#92400e', fontSize: 13 }}>Attention</strong>
          <ul style={{ margin: '6px 0 0', paddingLeft: 18, color: '#78350f', fontSize: 13 }}>
            {d.warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </div>
      )}
      <Card title="Profitability" accent="#16a34a">
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <Stat label="This month — Income" value={NGN(pm.income)} />
          <Stat label="This month — Expenses" value={NGN(pm.expenses)} />
          <Stat label="This month — Net profit" value={NGN(pm.net_profit)} color={pm.net_profit >= 0 ? '#16a34a' : '#dc2626'} />
          <Stat label="Year to date — Net profit" value={NGN(py.net_profit)} color={py.net_profit >= 0 ? '#16a34a' : '#dc2626'} />
        </div>
      </Card>
      <Card title="Financial position" accent="#2563eb">
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <Stat label="Assets" value={NGN(d.position.assets)} />
          <Stat label="Liabilities" value={NGN(d.position.liabilities)} />
          <Stat label="Equity" value={NGN(d.position.total_equity)} />
          <Stat label="Balanced?" value={d.position.balanced ? 'Yes' : 'NO'} color={d.position.balanced ? '#16a34a' : '#dc2626'} />
        </div>
      </Card>
      <Card title="Liquidity & working capital" accent="#7c3aed">
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <Stat label="Cash on hand" value={NGN(d.liquidity.cash_on_hand)} />
          <Stat label="Net working capital" value={NGN(d.liquidity.net_working_capital)} />
          <Stat label="Receivables" value={NGN(d.working_capital.receivables)} />
          <Stat label="Payables" value={NGN(d.working_capital.payables)} />
          <Stat label="Overdue receivables" value={NGN(d.working_capital.overdue_receivables.amount)} color="#dc2626" />
        </div>
      </Card>
      {d.top_cost_centres && d.top_cost_centres.length > 0 && (
        <Card title="Top cost centres this month" accent="#ea580c">
          <Table cols={[{ key: 'cost_centre', label: 'Cost centre' }, { key: 'spend', label: 'Spend', align: 'right' }]}
            rows={d.top_cost_centres} render={(r, c) => c.key === 'spend' ? NGN(r.spend) : r[c.key]} />
        </Card>
      )}
    </div>
  );
}

function ReportsTab() {
  const [start, setStart] = useState(monthStartISO());
  const [end, setEnd] = useState(todayISO());
  const [tb, setTb] = useState(null); const [pnl, setPnl] = useState(null);
  const [bs, setBs] = useState(null); const [cf, setCf] = useState(null);
  const [err, setErr] = useState(''); const [loading, setLoading] = useState(false);

  const run = useCallback(() => {
    setLoading(true); setErr('');
    Promise.all([
      getJSON(`/api/accounting/trial-balance?start=${start}&end=${end}`),
      getJSON(`/api/accounting/profit-and-loss?start=${start}&end=${end}`),
      getJSON(`/api/accounting/balance-sheet?as_at=${end}`),
      getJSON(`/api/cash/flow?start=${start}&end=${end}`).catch(() => null),
    ]).then(([a, b, c, e]) => { setTb(a); setPnl(b); setBs(c); setCf(e); })
      .catch((e) => setErr(e.message)).finally(() => setLoading(false));
  }, [start, end]);
  useEffect(() => { run(); }, []); // eslint-disable-line

  return (
    <div>
      <DateRange start={start} end={end} setStart={setStart} setEnd={setEnd} onGo={run} />
      <ErrorBox msg={err} />
      {loading && <Loading />}
      {pnl && (
        <Card title="Profit & Loss" accent="#16a34a">
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
            <Stat label="Income" value={NGN(pnl.total_income)} />
            <Stat label="Expenses" value={NGN(pnl.total_expenses)} />
            <Stat label="Net profit" value={NGN(pnl.net_profit)} color={pnl.net_profit >= 0 ? '#16a34a' : '#dc2626'} />
          </div>
          <Table cols={[{ key: 'name', label: 'Account' }, { key: 'amount', label: 'Amount', align: 'right' }]}
            rows={[...(pnl.income || []).map(x => ({ ...x, _t: 'Income' })), ...(pnl.expenses || []).map(x => ({ ...x, _t: 'Expense' }))]}
            render={(r, c) => c.key === 'amount' ? NGN(r.amount) : `${r.name} (${r._t})`} />
        </Card>
      )}
      {bs && (
        <Card title={`Balance Sheet as at ${end}`} accent="#2563eb">
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <Stat label="Assets" value={NGN(bs.assets)} />
            <Stat label="Liabilities" value={NGN(bs.liabilities)} />
            <Stat label="Equity" value={NGN(bs.total_equity)} />
            <Stat label="Balanced?" value={bs.balanced ? 'Yes' : 'NO — difference ' + NGN(bs.difference)} color={bs.balanced ? '#16a34a' : '#dc2626'} />
          </div>
        </Card>
      )}
      {tb && (
        <Card title="Trial Balance" accent="#7c3aed">
          <div style={{ marginBottom: 8, fontSize: 13, color: tb.balanced ? '#16a34a' : '#dc2626' }}>
            Debits {NGN(tb.total_debit)} · Credits {NGN(tb.total_credit)} · {tb.balanced ? 'Balanced ✓' : 'NOT balanced'}
          </div>
          <Table cols={[{ key: 'code', label: 'Code' }, { key: 'name', label: 'Account' }, { key: 'debit', label: 'Debit', align: 'right' }, { key: 'credit', label: 'Credit', align: 'right' }]}
            rows={tb.accounts || []} render={(r, c) => (c.key === 'debit' || c.key === 'credit') ? NGN(r[c.key]) : r[c.key]} />
        </Card>
      )}
      {cf && (
        <Card title="Cash Flow (direct method)" accent="#0891b2">
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <Stat label="Operating" value={NGN(cf.operating?.total)} />
            <Stat label="Investing" value={NGN(cf.investing?.total)} />
            <Stat label="Financing" value={NGN(cf.financing?.total)} />
            <Stat label="Net movement" value={NGN(cf.net_movement)} />
            <Stat label="Reconciles?" value={cf.reconciles ? 'Yes' : 'NO'} color={cf.reconciles ? '#16a34a' : '#dc2626'} />
          </div>
        </Card>
      )}
    </div>
  );
}

function LedgerTab() {
  const [accounts, setAccounts] = useState([]); const [err, setErr] = useState('');
  const [sel, setSel] = useState(null); const [ledger, setLedger] = useState(null);
  useEffect(() => { getJSON('/api/accounting/accounts').then((d) => setAccounts(d.accounts || d)).catch((e) => setErr(e.message)); }, []);
  const openLedger = (code) => {
    setSel(code); setLedger(null);
    getJSON(`/api/accounting/accounts/${code}/ledger`).then(setLedger).catch((e) => setErr(e.message));
  };
  return (
    <div>
      <ErrorBox msg={err} />
      <Card title="Chart of Accounts" accent="#2563eb">
        <Table cols={[{ key: 'code', label: 'Code' }, { key: 'name', label: 'Name' }, { key: 'account_type', label: 'Type' }, { key: 'act', label: '', align: 'right' }]}
          rows={accounts} render={(r, c) => c.key === 'act'
            ? <button style={btnGhost} onClick={() => openLedger(r.code)}>Ledger</button>
            : r[c.key]} />
      </Card>
      {sel && (
        <Card title={`Account ledger — ${sel}`} accent="#7c3aed">
          {!ledger ? <Loading /> : (
            <>
              <div style={{ marginBottom: 8, fontSize: 13, color: '#475569' }}>
                {ledger.account?.name} · closing balance <strong>{NGN(ledger.closing_balance)}</strong>
              </div>
              <Table cols={[{ key: 'date', label: 'Date' }, { key: 'description', label: 'Description' }, { key: 'debit', label: 'Debit', align: 'right' }, { key: 'credit', label: 'Credit', align: 'right' }, { key: 'balance', label: 'Balance', align: 'right' }]}
                rows={ledger.entries || []} render={(r, c) => ['debit', 'credit', 'balance'].includes(c.key) ? NGN(r[c.key]) : r[c.key]} />
            </>
          )}
        </Card>
      )}
    </div>
  );
}

function AssetsTab() {
  const [assets, setAssets] = useState([]); const [err, setErr] = useState(''); const [msg, setMsg] = useState('');
  const load = useCallback(() => { getJSON('/api/assets/').then((d) => setAssets(d.assets || d)).catch((e) => setErr(e.message)); }, []);
  useEffect(() => { load(); }, [load]);
  const runDep = async () => {
    setErr(''); setMsg('');
    try {
      const period = monthStartISO();
      const r = await postJSON('/api/assets/depreciation/run', { period });
      setMsg(`Depreciation run for ${period}: ${JSON.stringify(r).slice(0, 200)}`); load();
    } catch (e) { setErr(e.message); }
  };
  return (
    <div>
      <ErrorBox msg={err} />
      {msg && <div style={{ background: '#ecfdf5', color: '#065f46', padding: 10, borderRadius: 8, marginBottom: 12, fontSize: 13 }}>{msg}</div>}
      <div style={{ marginBottom: 12 }}><button style={btnPrimary} onClick={runDep}>Run monthly depreciation</button></div>
      <Card title="Fixed Asset Register" accent="#ea580c">
        <Table cols={[{ key: 'asset_number', label: 'No.' }, { key: 'name', label: 'Asset' }, { key: 'category', label: 'Category' }, { key: 'cost', label: 'Cost', align: 'right' }, { key: 'status', label: 'Status' }]}
          rows={assets} render={(r, c) => c.key === 'cost' ? NGN(r.cost) : r[c.key]} />
      </Card>
    </div>
  );
}

function BudgetingTab() {
  const [centres, setCentres] = useState([]); const [report, setReport] = useState(null);
  const [start, setStart] = useState(monthStartISO()); const [end, setEnd] = useState(todayISO());
  const [err, setErr] = useState(''); const [newCc, setNewCc] = useState({ code: '', name: '' });
  const loadCentres = useCallback(() => { getJSON('/api/budgeting/cost-centres').then(setCentres).catch((e) => setErr(e.message)); }, []);
  useEffect(() => { loadCentres(); }, [loadCentres]);
  const runReport = () => { setErr(''); getJSON(`/api/budgeting/cost-centres/report?start=${start}&end=${end}`).then(setReport).catch((e) => setErr(e.message)); };
  const addCc = async () => {
    setErr('');
    try { await postJSON('/api/budgeting/cost-centres', newCc); setNewCc({ code: '', name: '' }); loadCentres(); }
    catch (e) { setErr(e.message); }
  };
  return (
    <div>
      <ErrorBox msg={err} />
      <Card title="Cost Centres" accent="#2563eb">
        <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
          <input placeholder="Code" value={newCc.code} onChange={(e) => setNewCc({ ...newCc, code: e.target.value })} style={{ padding: 7, width: 100 }} />
          <input placeholder="Name" value={newCc.name} onChange={(e) => setNewCc({ ...newCc, name: e.target.value })} style={{ padding: 7, width: 200 }} />
          <button style={btnPrimary} onClick={addCc} disabled={!newCc.code || !newCc.name}>Add</button>
        </div>
        <Table cols={[{ key: 'code', label: 'Code' }, { key: 'name', label: 'Name' }, { key: 'category', label: 'Category' }]} rows={centres} />
      </Card>
      <Card title="Cost Centre Report" accent="#7c3aed">
        <DateRange start={start} end={end} setStart={setStart} setEnd={setEnd} onGo={runReport} />
        {report && (
          <>
            {report.unallocated_warning && <div style={{ color: '#b45309', fontSize: 13, marginBottom: 8 }}>{report.unallocated_warning}</div>}
            <Table cols={[{ key: 'cost_centre', label: 'Centre' }, { key: 'name', label: 'Name' }, { key: 'income', label: 'Income', align: 'right' }, { key: 'expenditure', label: 'Expenditure', align: 'right' }, { key: 'net', label: 'Net', align: 'right' }]}
              rows={report.cost_centres || []} render={(r, c) => ['income', 'expenditure', 'net'].includes(c.key) ? NGN(r[c.key]) : r[c.key]} />
          </>
        )}
      </Card>
    </div>
  );
}

function VatTab() {
  const [start, setStart] = useState(monthStartISO()); const [end, setEnd] = useState(todayISO());
  const [computed, setComputed] = useState(null); const [returns, setReturns] = useState([]);
  const [err, setErr] = useState(''); const [msg, setMsg] = useState('');
  const loadReturns = useCallback(() => { getJSON('/api/tax/vat/returns').then(setReturns).catch(() => {}); }, []);
  useEffect(() => { loadReturns(); }, [loadReturns]);
  const compute = () => { setErr(''); getJSON(`/api/tax/vat/compute?start=${start}&end=${end}`).then(setComputed).catch((e) => setErr(e.message)); };
  const file = async () => {
    setErr(''); setMsg('');
    const filed_by = window.prompt('File this VAT return — your name for the record:');
    if (!filed_by) return;
    try {
      await postJSON('/api/tax/vat/returns', { period_start: start, period_end: end, filed_by });
      setMsg('VAT return filed.'); loadReturns();
    } catch (e) { setErr(e.message); }
  };
  return (
    <div>
      <ErrorBox msg={err} />
      {msg && <div style={{ background: '#ecfdf5', color: '#065f46', padding: 10, borderRadius: 8, marginBottom: 12, fontSize: 13 }}>{msg}</div>}
      <Card title="Compute VAT Position" accent="#0891b2">
        <DateRange start={start} end={end} setStart={setStart} setEnd={setEnd} onGo={compute} />
        {computed && (
          <>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
              <Stat label="Output VAT" value={NGN(computed.output_vat)} />
              <Stat label="Input VAT" value={NGN(computed.input_vat)} />
              <Stat label="Net payable" value={NGN(computed.net_payable)} color={computed.net_payable > 0 ? '#dc2626' : '#16a34a'} />
              <Stat label="Position" value={computed.position} />
            </div>
            {(computed.notes || []).map((n, i) => <div key={i} style={{ color: '#b45309', fontSize: 12 }}>• {n}</div>)}
            <button style={{ ...btnPrimary, marginTop: 10 }} onClick={file}>File this return</button>
          </>
        )}
      </Card>
      <Card title="Filed Returns" accent="#2563eb">
        <Table cols={[{ key: 'period_start', label: 'From' }, { key: 'period_end', label: 'To' }, { key: 'output_vat', label: 'Output', align: 'right' }, { key: 'input_vat', label: 'Input', align: 'right' }, { key: 'net_payable', label: 'Net', align: 'right' }, { key: 'status', label: 'Status' }]}
          rows={returns} render={(r, c) => ['output_vat', 'input_vat', 'net_payable'].includes(c.key) ? NGN(r[c.key]) : r[c.key]} />
      </Card>
    </div>
  );
}

function QcTab() {
  const [rows, setRows] = useState([]); const [err, setErr] = useState('');
  const [form, setForm] = useState({ subject: '', cost: '', inspection_type: 'in_process', result: 'pending', post_cost: true });
  const load = useCallback(() => { getJSON('/api/maintenance/qc-inspections').then(setRows).catch((e) => setErr(e.message)); }, []);
  useEffect(() => { load(); }, [load]);
  const add = async () => {
    setErr('');
    try {
      await postJSON('/api/maintenance/qc-inspections', { ...form, cost: Number(form.cost || 0) });
      setForm({ subject: '', cost: '', inspection_type: 'in_process', result: 'pending', post_cost: true }); load();
    } catch (e) { setErr(e.message); }
  };
  return (
    <div>
      <ErrorBox msg={err} />
      <Card title="Record QC Inspection" accent="#16a34a">
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
          <input placeholder="Subject / batch" value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} style={{ padding: 7, width: 220 }} />
          <input placeholder="Cost" type="number" value={form.cost} onChange={(e) => setForm({ ...form, cost: e.target.value })} style={{ padding: 7, width: 110 }} />
          <select value={form.inspection_type} onChange={(e) => setForm({ ...form, inspection_type: e.target.value })} style={{ padding: 7 }}>
            <option value="incoming">Incoming</option><option value="in_process">In-process</option>
            <option value="finished">Finished</option><option value="stability">Stability</option>
          </select>
          <select value={form.result} onChange={(e) => setForm({ ...form, result: e.target.value })} style={{ padding: 7 }}>
            <option value="pending">Pending</option><option value="pass">Pass</option>
            <option value="fail">Fail</option><option value="conditional">Conditional</option>
          </select>
          <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 4 }}>
            <input type="checkbox" checked={form.post_cost} onChange={(e) => setForm({ ...form, post_cost: e.target.checked })} /> Post cost to ledger
          </label>
          <button style={btnPrimary} onClick={add} disabled={!form.subject}>Add</button>
        </div>
      </Card>
      <Card title="QC Inspections" accent="#2563eb">
        <Table cols={[{ key: 'inspection_number', label: 'No.' }, { key: 'inspection_date', label: 'Date' }, { key: 'subject', label: 'Subject' }, { key: 'inspection_type', label: 'Type' }, { key: 'result', label: 'Result' }, { key: 'cost', label: 'Cost', align: 'right' }, { key: 'posted', label: 'Posted' }]}
          rows={rows} render={(r, c) => c.key === 'cost' ? NGN(r.cost) : c.key === 'posted' ? (r.posted ? '✓' : '—') : r[c.key]} />
      </Card>
    </div>
  );
}

function PayrollTab() {
  const [runs, setRuns] = useState([]); const [configs, setConfigs] = useState([]);
  const [liab, setLiab] = useState(null); const [err, setErr] = useState('');
  useEffect(() => {
    getJSON('/api/payroll/runs').then((d) => setRuns(d.runs || d)).catch((e) => setErr(e.message));
    getJSON('/api/payroll/rate-configs').then((d) => setConfigs(d.configs || d)).catch(() => {});
    getJSON('/api/payroll/statutory-liabilities').then(setLiab).catch(() => {});
  }, []);
  const anyUnconfirmed = Array.isArray(configs) && configs.some((c) => c.confirmed === false || c.status === 'UNCONFIRMED');
  return (
    <div>
      <ErrorBox msg={err} />
      {anyUnconfirmed && (
        <div style={{ background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 8, padding: 12, marginBottom: 16, color: '#78350f', fontSize: 13 }}>
          Payroll rate configuration is <strong>UNCONFIRMED</strong>. The engine will refuse to run payroll until an accountant confirms the tax bands against current Nigerian law.
        </div>
      )}
      <Card title="Payroll Runs" accent="#2563eb">
        <Table cols={[{ key: 'period', label: 'Period' }, { key: 'status', label: 'Status' }, { key: 'gross', label: 'Gross', align: 'right' }, { key: 'net', label: 'Net', align: 'right' }]}
          rows={Array.isArray(runs) ? runs : []} render={(r, c) => ['gross', 'net'].includes(c.key) ? NGN(r[c.key] || r[`total_${c.key}`]) : (r[c.key] || r.period_month || '')} />
      </Card>
      {liab && (
        <Card title="Statutory Liabilities Outstanding" accent="#dc2626">
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            {Object.entries(liab).filter(([, v]) => typeof v === 'number').map(([k, v]) => <Stat key={k} label={k.replace(/_/g, ' ')} value={NGN(v)} />)}
          </div>
        </Card>
      )}
    </div>
  );
}

function CostsTab() {
  const [kind, setKind] = useState('products'); // 'products' | 'materials'
  const [rows, setRows] = useState([]); const [edits, setEdits] = useState({});
  const [err, setErr] = useState(''); const [msg, setMsg] = useState('');
  const [loading, setLoading] = useState(true); const [saving, setSaving] = useState(false);
  const [filter, setFilter] = useState('all'); // all | missing

  const load = useCallback(() => {
    setLoading(true); setErr(''); setEdits({});
    getJSON(`/api/costs/${kind}`).then(setRows).catch((e) => setErr(e.message)).finally(() => setLoading(false));
  }, [kind]);
  useEffect(() => { load(); }, [load]);

  const costKey = kind === 'products' ? 'cost_price' : 'unit_cost';
  const setEdit = (id, v) => setEdits((p) => ({ ...p, [id]: v }));

  const save = async () => {
    const updates = Object.entries(edits)
      .filter(([, v]) => v !== '' && v != null && !Number.isNaN(Number(v)))
      .map(([id, v]) => ({ id, cost: Number(v) }));
    if (updates.length === 0) { setMsg('No changes to save.'); return; }
    setSaving(true); setErr(''); setMsg('');
    try {
      const r = await postPut(`/api/costs/${kind}`, { updates });
      setMsg(`Saved ${r.updated} cost${r.updated === 1 ? '' : 's'}.`); load();
    } catch (e) { setErr(e.message); } finally { setSaving(false); }
  };

  const shown = rows.filter((r) => filter === 'all' || r.missing_cost);
  const missingCount = rows.filter((r) => r.missing_cost).length;
  const pendingCount = Object.keys(edits).filter((k) => edits[k] !== '' && edits[k] != null).length;

  return (
    <div>
      <ErrorBox msg={err} />
      {msg && <div style={{ background: '#ecfdf5', color: '#065f46', padding: 10, borderRadius: 8, marginBottom: 12, fontSize: 13 }}>{msg}</div>}
      <div style={{ background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 8, padding: 12, marginBottom: 14, fontSize: 13, color: '#1e3a5f' }}>
        Enter the unit <strong>cost</strong> (what it costs you) for each item. These feed stock valuation and
        profit. {kind === 'products' && 'A cost above the selling price shows a negative margin — a sign of a typo.'}
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
        <button onClick={() => setKind('products')} style={kind === 'products' ? btnPrimary : btnGhost}>Products</button>
        <button onClick={() => setKind('materials')} style={kind === 'materials' ? btnPrimary : btnGhost}>Raw Materials</button>
        <span style={{ marginLeft: 12, fontSize: 13, color: missingCount ? '#b45309' : '#16a34a' }}>
          {missingCount} of {rows.length} missing a cost
        </span>
        <label style={{ fontSize: 13, marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 4 }}>
          <input type="checkbox" checked={filter === 'missing'} onChange={(e) => setFilter(e.target.checked ? 'missing' : 'all')} />
          Show only missing
        </label>
      </div>
      {loading ? <Loading /> : (
        <Card accent="#16a34a">
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead><tr>
                <th style={thL}>Item</th><th style={thL}>Unit</th>
                <th style={thR}>On hand</th>
                {kind === 'products' && <th style={thR}>Selling price</th>}
                {kind === 'materials' && <th style={thR}>Current stock value</th>}
                <th style={thR}>Unit cost</th>
                {kind === 'products' && <th style={thR}>Margin</th>}
              </tr></thead>
              <tbody>
                {shown.map((r) => {
                  const editing = edits[r.id];
                  const effectiveCost = editing !== undefined && editing !== '' ? Number(editing) : r[costKey];
                  const margin = kind === 'products' && r.selling_price && effectiveCost != null
                    ? Math.round((r.selling_price - effectiveCost) / r.selling_price * 1000) / 10 : null;
                  return (
                    <tr key={r.id} style={{ borderBottom: '1px solid #f1f5f9', background: r.missing_cost ? '#fffbeb' : 'transparent' }}>
                      <td style={{ padding: '6px 10px' }}>{r.name}</td>
                      <td style={{ padding: '6px 10px', color: '#64748b' }}>{r.unit || '—'}</td>
                      <td style={tdR}>{r.on_hand?.toLocaleString()}</td>
                      {kind === 'products' && <td style={tdR}>{r.selling_price != null ? NGN(r.selling_price) : '—'}</td>}
                      {kind === 'materials' && <td style={{ ...tdR, color: r.stock_value > 5000000 ? '#dc2626' : '#334155' }}>{NGN(r.stock_value)}</td>}
                      <td style={tdR}>
                        <input type="number" min="0" step="any"
                          defaultValue={r[costKey] ?? ''}
                          onChange={(e) => setEdit(r.id, e.target.value)}
                          style={{ width: 110, padding: 6, textAlign: 'right', border: `1px solid ${r.missing_cost ? '#f59e0b' : '#cbd5e1'}`, borderRadius: 5 }} />
                      </td>
                      {kind === 'products' && (
                        <td style={{ ...tdR, color: margin == null ? '#94a3b8' : margin < 0 ? '#dc2626' : margin < 10 ? '#b45309' : '#16a34a', fontWeight: 600 }}>
                          {margin == null ? '—' : `${margin}%`}
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div style={{ marginTop: 14, display: 'flex', gap: 10, alignItems: 'center' }}>
            <button style={{ ...btnPrimary, opacity: saving ? 0.6 : 1 }} onClick={save} disabled={saving}>
              {saving ? 'Saving…' : `Save costs${pendingCount ? ` (${pendingCount})` : ''}`}
            </button>
            <span style={{ fontSize: 12, color: '#64748b' }}>Changes are applied when you click Save.</span>
          </div>
        </Card>
      )}
    </div>
  );
}

const thL = { textAlign: 'left', padding: '8px 10px', borderBottom: '2px solid #e2e8f0', color: '#475569' };
const thR = { textAlign: 'right', padding: '8px 10px', borderBottom: '2px solid #e2e8f0', color: '#475569' };
const tdR = { padding: '6px 10px', textAlign: 'right' };

async function postPut(url, body) {
  const res = await authedFetch(url, { method: 'PUT', body: JSON.stringify(body) });
  if (!res.ok) {
    let d = `Request failed (${res.status})`;
    try { const j = await res.json(); d = j.detail || j.message || d; } catch {}
    throw new Error(d);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// shell
// ---------------------------------------------------------------------------

const TABS = [
  ['dashboard', 'Dashboard', DashboardTab],
  ['reports', 'Financial Reports', ReportsTab],
  ['ledger', 'General Ledger', LedgerTab],
  ['costs', 'Product Costs', CostsTab],
  ['assets', 'Fixed Assets', AssetsTab],
  ['budgeting', 'Budgeting', BudgetingTab],
  ['vat', 'VAT Returns', VatTab],
  ['qc', 'QC Costing', QcTab],
  ['payroll', 'Payroll', PayrollTab],
];

export default function AccountingSuite() {
  const [tab, setTab] = useState('dashboard');
  const Active = (TABS.find((t) => t[0] === tab) || TABS[0])[2];
  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <h2 style={{ margin: 0, fontSize: 22, color: '#0f172a' }}>Accounting</h2>
        <span style={{ fontSize: 12, color: '#64748b' }}>Double-entry ledger · reports · tax · payroll</span>
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 16, borderBottom: '1px solid #e2e8f0', paddingBottom: 8 }}>
        {TABS.map(([id, label]) => (
          <button key={id} onClick={() => setTab(id)}
            style={{
              border: 0, borderRadius: 6, padding: '8px 14px', cursor: 'pointer', fontWeight: 600, fontSize: 13,
              background: tab === id ? '#2563eb' : '#f1f5f9', color: tab === id ? '#fff' : '#334155',
            }}>{label}</button>
        ))}
      </div>
      <Active />
    </div>
  );
}
