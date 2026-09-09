// Wallet Control — the management half of the accountability module.
//
// Mounted from AppMain when activeModule === 'walletAdmin'.
//
// Ordered the way the work is actually done: what is waiting for me, what the
// company's position is, who is holding what, what looks unusual, and finally
// the configuration that decides all of the above. Approving is the most
// frequent action, so it is first and needs no navigation.
//
// Every figure is read from the server. This screen originates no money; it
// records decisions about money that has already moved.

import React, { useCallback, useEffect, useState } from 'react';
import { authedFetch } from './utils/api';
import { color, font, naira, radius, space } from './ui/theme';
import {
  Banner, Btn, Card, Chip, DataTable, ErrorBox, Icon, KpiCard, SectionTitle,
  SkeletonCards,
} from './ui/kit';

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
const delJSON = (u) => req(u, { method: 'DELETE' });

const money = (v) => naira(v);
const todayISO = () => new Date().toISOString().slice(0, 10);

const STATUS_TONE = {
  APPROVED: 'success', PENDING: 'warning', REJECTED: 'danger',
  REVERSED: 'neutral', CONFIRMED: 'success', DECLARED: 'warning',
  ACTIVE: 'success', SUSPENDED: 'warning', FROZEN: 'danger', CLOSED: 'neutral',
  HIGH: 'danger', MEDIUM: 'warning', LOW: 'neutral',
  SUBMITTED: 'warning', ACCEPTED: 'success', PAID: 'success',
};
const tone = (s) => STATUS_TONE[String(s || '').toUpperCase()] || 'neutral';

const inputStyle = {
  padding: '9px 10px', border: `1px solid ${color.borderStrong}`,
  borderRadius: radius.sm, fontSize: 13, fontFamily: font.family,
  color: color.text, background: '#fff', width: '100%', boxSizing: 'border-box',
};

function Field({ label, hint, children }) {
  return (
    <label style={{ display: 'block', fontSize: 12, color: color.textSecondary, fontWeight: 600 }}>
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

function Modal({ title, onClose, children, footer, width = 560 }) {
  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: space(2),
    }}>
      <div onClick={(e) => e.stopPropagation()} style={{
        background: '#fff', width: '100%', maxWidth: width, maxHeight: '88vh',
        display: 'flex', flexDirection: 'column', borderRadius: radius.lg,
        boxShadow: '0 24px 48px rgba(15,23,42,0.24)',
      }}>
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: `${space(2)} ${space(2.5)}`, borderBottom: `1px solid ${color.border}`,
        }}>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>{title}</h3>
          <button onClick={onClose} aria-label="Close" style={{
            border: 'none', background: 'transparent', fontSize: 24, lineHeight: 1,
            color: color.textMuted, cursor: 'pointer',
          }}>&times;</button>
        </div>
        <div style={{ padding: space(2.5), overflowY: 'auto' }}>{children}</div>
        {footer && <div style={{ padding: space(2.5), borderTop: `1px solid ${color.border}`, background: '#FbFcFe' }}>{footer}</div>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Record a funding — the "I gave John N100,000" moment
// ---------------------------------------------------------------------------

function FundWallet({ wallets, preselect, onClose, onDone }) {
  const [walletId, setWalletId] = useState(preselect || '');
  const [amount, setAmount] = useState('');
  const [purpose, setPurpose] = useState('');
  const [method, setMethod] = useState('BANK_TRANSFER');
  const [reference, setReference] = useState('');
  const [source, setSource] = useState('1200');
  const [fundedOn, setFundedOn] = useState(todayISO());
  const [accountBy, setAccountBy] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [idem] = useState(() => `fund-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`);

  const submit = async () => {
    setErr('');
    if (!walletId) { setErr('Choose whose wallet this is.'); return; }
    if (!amount || Number(amount) <= 0) { setErr('Enter the amount issued.'); return; }
    if (purpose.trim().length < 3) { setErr('State what the funds are for.'); return; }
    setBusy(true);
    try {
      const r = await postJSON(`/api/wallet/wallets/${walletId}/fundings`, {
        amount: Number(amount), purpose: purpose.trim(),
        disbursement_method: method,
        disbursement_reference: reference.trim() || null,
        source_account_code: source, funded_on: fundedOn,
        account_by: accountBy || null, idempotency_key: idem,
      });
      onDone(`${money(r.amount)} recorded as issued (${r.funding_reference}).`);
    } catch (e) { setErr(e.message); setBusy(false); }
  };

  return (
    <Modal title="Record funds issued to staff" onClose={busy ? () => {} : onClose}
      footer={<Btn variant="accent" onClick={submit} disabled={busy} style={{ width: '100%' }}>
        {busy ? 'Recording…' : 'Record the advance'}</Btn>}>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <Banner tone="info" title="This records money; it does not send money">
        Transfer the cash or make the bank transfer the way you always do. This
        screen records that the employee has been entrusted with it, so it can
        be tracked until it is spent or returned.
      </Banner>
      <div style={{ height: space(2) }} />

      <div style={{ display: 'grid', gap: space(2) }}>
        <Field label="Who is receiving the funds?">
          <select value={walletId} onChange={(e) => setWalletId(e.target.value)} style={inputStyle}>
            <option value="">Choose a wallet…</option>
            {wallets.filter((w) => w.status === 'ACTIVE').map((w) => (
              <option key={w.id} value={w.id}>
                {w.holder} — {w.wallet_type} ({w.wallet_number})
              </option>
            ))}
          </select>
        </Field>

        <Grid min={200}>
          <Field label="Amount issued">
            <input type="number" inputMode="decimal" min="0" step="0.01" value={amount}
              onChange={(e) => setAmount(e.target.value)} placeholder="100000.00"
              style={{ ...inputStyle, fontSize: 18, fontWeight: 700 }} />
          </Field>
          <Field label="Date issued">
            <input type="date" value={fundedOn} onChange={(e) => setFundedOn(e.target.value)} style={inputStyle} />
          </Field>
        </Grid>

        <Field label="What are the funds for?">
          <input value={purpose} onChange={(e) => setPurpose(e.target.value)}
            placeholder="Factory operational expenses" style={inputStyle} />
        </Field>

        <Grid min={200}>
          <Field label="How was the money given?">
            <select value={method} onChange={(e) => setMethod(e.target.value)} style={inputStyle}>
              <option value="BANK_TRANSFER">Bank transfer</option>
              <option value="CASH">Cash</option>
              <option value="COMPANY_CARD">Company card</option>
              <option value="CHEQUE">Cheque</option>
              <option value="MOBILE_MONEY">Mobile money</option>
              <option value="OTHER">Other</option>
            </select>
          </Field>
          <Field label="Taken from"
            hint="Which company account the money actually left.">
            <select value={source} onChange={(e) => setSource(e.target.value)} style={inputStyle}>
              <option value="1200">Bank account</option>
              <option value="1100">Cash</option>
              <option value="1110">Petty cash</option>
            </select>
          </Field>
        </Grid>

        <Field label="Transfer reference"
          hint="The bank reference or teller number. This is what someone reconciling the bank statement will search for.">
          <input value={reference} onChange={(e) => setReference(e.target.value)} style={inputStyle} />
        </Field>

        <Field label="Account for it by"
          hint="Leave empty if there is no deadline. After this date, any unspent balance is reported as outstanding.">
          <input type="date" value={accountBy} min={fundedOn}
            onChange={(e) => setAccountBy(e.target.value)} style={inputStyle} />
        </Field>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Approvals
// ---------------------------------------------------------------------------

function ApprovalQueue({ queue, onDecide, onView }) {
  const [note, setNote] = useState({});
  return (
    <Card>
      <SectionTitle right={<Chip tone={queue.length ? 'warning' : 'success'}>
        {queue.length} waiting</Chip>}>Expenses awaiting your approval</SectionTitle>
      <DataTable
        cols={[
          { key: 'submitted_by', label: 'Who' },
          { key: 'purpose', label: 'What for', wrap: true },
          { key: 'amount', label: 'Amount', align: 'right' },
          { key: 'receipts', label: 'Receipt', align: 'center' },
          { key: 'act', label: '', align: 'right' },
        ]}
        rows={queue}
        empty="Nothing is waiting for you. "
        render={(r, c) => {
          if (c.key === 'submitted_by') {
            return (
              <div>
                <div style={{ fontWeight: 600 }}>{r.submitted_by}</div>
                <div style={{ fontSize: 11.5, color: color.textMuted }}>
                  {r.wallet_number} · {r.spent_on}
                </div>
              </div>
            );
          }
          if (c.key === 'purpose') {
            return (
              <div style={{ maxWidth: 260, whiteSpace: 'normal' }}>
                <div>{r.purpose}</div>
                <div style={{ fontSize: 11.5, color: color.textMuted, marginTop: 2 }}>
                  {r.category}{r.vendor ? ` · ${r.vendor}` : ''}
                </div>
              </div>
            );
          }
          if (c.key === 'amount') {
            return (
              <div>
                <strong>{money(r.amount)}</strong>
                <div style={{ marginTop: 3 }}><Chip tone="info">{r.required_tier}</Chip></div>
              </div>
            );
          }
          if (c.key === 'receipts') {
            return r.receipts > 0
              ? <button onClick={() => onView(r)} style={{
                  border: 'none', background: 'transparent', cursor: 'pointer', padding: 0,
                }}><Icon name="check" size={17} color={color.success} /></button>
              : <Chip tone="warning">None</Chip>;
          }
          return (
            <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end', alignItems: 'center' }}>
              <input placeholder="Note" value={note[r.id] || ''}
                onChange={(e) => setNote({ ...note, [r.id]: e.target.value })}
                style={{ ...inputStyle, width: 120, padding: '6px 8px', fontSize: 12 }} />
              <Btn size="sm" variant="accent"
                onClick={() => onDecide(r, true, note[r.id])}>Approve</Btn>
              <Btn size="sm" variant="danger"
                onClick={() => onDecide(r, false, note[r.id])}>Reject</Btn>
            </div>
          );
        }}
      />
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Main screen
// ---------------------------------------------------------------------------

const TABS = [
  ['approvals', 'Approvals'],
  ['overview', 'Overview'],
  ['wallets', 'Wallets'],
  ['requests', 'Fund requests'],
  ['returns', 'Returns'],
  ['claims', 'Reimbursements'],
  ['reconciliations', 'Reconciliations'],
  ['flags', 'Flags'],
  ['config', 'Configuration'],
];

export default function WalletAdmin() {
  const [tab, setTab] = useState('approvals');
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [toast, setToast] = useState('');
  const [caps, setCaps] = useState({});
  const [dash, setDash] = useState(null);
  const [queue, setQueue] = useState([]);
  const [wallets, setWallets] = useState([]);
  const [requests, setRequests] = useState([]);
  const [returns, setReturns] = useState([]);
  const [claims, setClaims] = useState([]);
  const [recs, setRecs] = useState([]);
  const [flags, setFlags] = useState([]);
  const [rules, setRules] = useState([]);
  const [approvers, setApprovers] = useState([]);
  const [modal, setModal] = useState(null);

  const flash = (m) => { setToast(m); setTimeout(() => setToast(''), 6000); };

  const load = useCallback(async () => {
    setErr('');
    try {
      const me = await getJSON('/api/wallet/me');
      setCaps(me.capabilities || {});
      const jobs = [
        getJSON('/api/wallet/approvals/queue').then((d) => setQueue(d.queue || [])),
        getJSON('/api/wallet/wallets').then((d) => setWallets(d.wallets || [])),
        getJSON('/api/wallet/dashboard').then(setDash),
        getJSON('/api/wallet/fund-requests?status=PENDING').then((d) => setRequests(d.requests || [])),
        getJSON('/api/wallet/returns?status=DECLARED').then((d) => setReturns(d.returns || [])),
        getJSON('/api/wallet/reimbursements').then((d) => setClaims(d.reimbursements || [])),
        getJSON('/api/wallet/reconciliations').then((d) => setRecs(d.reconciliations || [])),
        getJSON('/api/wallet/flags?status=OPEN').then((d) => setFlags(d.flags || [])),
      ];
      if (me.capabilities && me.capabilities.is_admin) {
        jobs.push(
          getJSON('/api/wallet/admin/approval-rules').then((d) => setRules(d.rules || [])),
          getJSON('/api/wallet/admin/approvers').then((d) => setApprovers(d.approvers || [])),
        );
      }
      // allSettled, not all: one endpoint the user lacks rights for must not
      // blank the whole screen.
      await Promise.allSettled(jobs);
    } catch (e) { setErr(e.message); }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const decideExpense = async (row, approve, note) => {
    try {
      await postJSON(`/api/wallet/expenses/${row.id}/decision`, { approve, note: note || null });
      flash(`${row.expense_reference} ${approve ? 'approved' : 'rejected'}.`);
      await load();
    } catch (e) { setErr(e.message); }
  };

  const viewReceipts = async (row) => {
    try {
      const d = await getJSON(`/api/wallet/expenses/${row.id}/receipts`);
      setModal({ kind: 'receipts', receipts: d.receipts || [], row });
    } catch (e) { setErr(e.message); }
  };

  if (loading) return <SkeletonCards n={4} />;

  const t = dash ? dash.totals : {};
  const q = dash ? dash.queues : {};

  return (
    <div>
      {toast && <div style={{ marginBottom: space(2) }}><Banner tone="success" title="Done">{toast}</Banner></div>}
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <div style={{ display: 'flex', gap: space(1), flexWrap: 'wrap', marginBottom: space(2.5) }}>
        {TABS.filter(([k]) => k !== 'config' || caps.is_admin).map(([k, label]) => {
          const badge = { approvals: queue.length, requests: requests.length,
            returns: returns.length, flags: flags.length }[k];
          return (
            <button key={k} onClick={() => setTab(k)} style={{
              padding: '8px 15px', borderRadius: radius.pill, fontSize: 13, fontWeight: 600,
              border: `1px solid ${tab === k ? color.medical : color.borderStrong}`,
              background: tab === k ? color.infoBg : '#fff',
              color: tab === k ? color.royal : color.textSecondary, cursor: 'pointer',
              display: 'inline-flex', alignItems: 'center', gap: 6,
            }}>
              {label}
              {badge > 0 && (
                <span style={{
                  background: color.danger, color: '#fff', fontSize: 10.5, fontWeight: 700,
                  minWidth: 17, height: 17, borderRadius: 9, display: 'inline-flex',
                  alignItems: 'center', justifyContent: 'center', padding: '0 4px',
                }}>{badge}</span>
              )}
            </button>
          );
        })}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: space(1) }}>
          {caps.can_fund && (
            <Btn size="sm" variant="accent" icon="wallet"
              onClick={() => setModal({ kind: 'fund' })}>Record funds issued</Btn>
          )}
          <Btn size="sm" variant="ghost" icon="refresh" onClick={load}>Refresh</Btn>
        </div>
      </div>

      {tab === 'approvals' && (
        <ApprovalQueue queue={queue} onDecide={decideExpense} onView={viewReceipts} />
      )}

      {tab === 'overview' && dash && (
        <>
          <Grid min={210}>
            <KpiCard icon="wallet" label="Total issued" value={money(t.issued)}
              sub={`${t.wallets} wallet(s)`} tone="royal" />
            <KpiCard icon="tag" label="Total spent" value={money(t.spent)}
              sub="Approved expenses" tone="info" />
            <KpiCard icon="bank" label="Returned" value={money(t.returned)}
              sub="Confirmed back" tone="success" />
            <KpiCard icon="alert" label="Outstanding" value={money(t.outstanding)}
              sub="Past its accounting date"
              tone={Number(t.outstanding) > 0 ? 'danger' : 'success'} />
          </Grid>

          <div style={{ height: space(2.5) }} />

          <Grid min={240}>
            <Card pad={2.5}>
              <div style={{ fontSize: 12, color: color.textSecondary, fontWeight: 600 }}>Currently entrusted to staff</div>
              <div style={{ fontSize: 24, fontWeight: 700, marginTop: 4 }}>{money(t.balance)}</div>
              <div style={{ fontSize: 12, color: color.textMuted, marginTop: 4 }}>
                Money in employees' hands right now, spent or not.
              </div>
            </Card>
            <Card pad={2.5}>
              <div style={{ fontSize: 12, color: color.textSecondary, fontWeight: 600 }}>Waiting on someone</div>
              <div style={{ display: 'grid', gap: 6, marginTop: 8, fontSize: 13 }}>
                <span>{q.pending_expenses} expense(s) — {money(q.pending_expense_value)}</span>
                <span>{q.pending_fund_requests} fund request(s) — {money(q.pending_fund_request_value)}</span>
                <span>{q.pending_reimbursements} claim(s) — {money(q.pending_reimbursement_value)}</span>
                <span style={{ color: q.open_flags > 0 ? color.danger : color.textMuted }}>
                  {q.open_flags} open flag(s)
                </span>
              </div>
            </Card>
          </Grid>

          <div style={{ height: space(2.5) }} />

          <Card>
            <SectionTitle right={
              <Btn size="sm" variant="secondary" icon="reports" onClick={() => {
                const start = new Date(); start.setDate(1);
                window.open(`/api/wallet/reports/expenses.csv?date_from=${start.toISOString().slice(0, 10)}&date_to=${todayISO()}`, '_blank');
              }}>Export this month</Btn>
            }>By department</SectionTitle>
            <DataTable
              cols={[
                { key: 'department', label: 'Department' },
                { key: 'wallets', label: 'Wallets', align: 'right' },
                { key: 'issued', label: 'Issued', align: 'right' },
                { key: 'spent', label: 'Spent', align: 'right' },
                { key: 'returned', label: 'Returned', align: 'right' },
                { key: 'balance', label: 'Held now', align: 'right' },
                { key: 'outstanding', label: 'Outstanding', align: 'right' },
              ]}
              rows={dash.departments}
              empty="No wallets have been opened yet."
              render={(r, c) => {
                if (['issued', 'spent', 'returned', 'balance'].includes(c.key)) return money(r[c.key]);
                if (c.key === 'outstanding') {
                  const v = Number(r.outstanding);
                  return <span style={{ fontWeight: v > 0 ? 700 : 400, color: v > 0 ? color.danger : color.textMuted }}>{money(v)}</span>;
                }
                return r[c.key];
              }}
            />
          </Card>
        </>
      )}

      {tab === 'wallets' && (
        <Card>
          <SectionTitle right={caps.is_admin
            ? <Btn size="sm" variant="secondary" onClick={() => setModal({ kind: 'newWallet' })}>Open a wallet</Btn>
            : null}>Staff wallets</SectionTitle>
          <DataTable
            cols={[
              { key: 'holder', label: 'Holder' },
              { key: 'wallet_type', label: 'Type' },
              { key: 'department', label: 'Department' },
              { key: 'total_funded', label: 'Issued', align: 'right' },
              { key: 'total_spent', label: 'Spent', align: 'right' },
              { key: 'balance', label: 'Held now', align: 'right' },
              { key: 'status', label: 'Status' },
              { key: 'act', label: '', align: 'right' },
            ]}
            rows={wallets}
            empty="No wallets yet."
            render={(r, c) => {
              if (['total_funded', 'total_spent', 'balance'].includes(c.key)) return money(r[c.key]);
              if (c.key === 'status') return <Chip tone={tone(r.status)}>{r.status}</Chip>;
              if (c.key === 'holder') {
                return (
                  <div>
                    <div style={{ fontWeight: 600 }}>{r.holder}</div>
                    <div style={{ fontSize: 11.5, color: color.textMuted }}>{r.wallet_number}</div>
                  </div>
                );
              }
              if (c.key === 'act') {
                return (
                  <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                    <Btn size="sm" variant="ghost" onClick={async () => {
                      try {
                        const d = await getJSON(`/api/wallet/wallets/${r.id}/report`);
                        setModal({ kind: 'report', report: d });
                      } catch (e) { setErr(e.message); }
                    }}>Statement</Btn>
                    {caps.can_fund && (
                      <Btn size="sm" variant="secondary"
                        onClick={() => setModal({ kind: 'fund', preselect: r.id })}>Fund</Btn>
                    )}
                  </div>
                );
              }
              return r[c.key];
            }}
          />
        </Card>
      )}

      {tab === 'requests' && (
        <Card>
          <SectionTitle>Requests for additional funds</SectionTitle>
          <DataTable
            cols={[
              { key: 'requested_by', label: 'Who' },
              { key: 'reason', label: 'Reason', wrap: true },
              { key: 'balance_at_request', label: 'Had', align: 'right' },
              { key: 'amount_requested', label: 'Asked for', align: 'right' },
              { key: 'urgency', label: 'Urgency' },
              { key: 'act', label: '', align: 'right' },
            ]}
            rows={requests}
            empty="No open requests."
            render={(r, c) => {
              if (c.key === 'balance_at_request' || c.key === 'amount_requested') return money(r[c.key]);
              if (c.key === 'urgency') {
                return <Chip tone={r.urgency === 'EMERGENCY' ? 'danger' : r.urgency === 'URGENT' ? 'warning' : 'neutral'}>{r.urgency}</Chip>;
              }
              if (c.key === 'reason') {
                return <div style={{ maxWidth: 280, whiteSpace: 'normal' }}>{r.reason}</div>;
              }
              if (c.key === 'act') {
                return (
                  <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                    <Btn size="sm" variant="accent" onClick={async () => {
                      try {
                        await postJSON(`/api/wallet/fund-requests/${r.id}/decision`, { decision: 'APPROVED' });
                        flash(`${r.request_reference} approved. Record the funding once the money has actually been sent.`);
                        await load();
                      } catch (e) { setErr(e.message); }
                    }}>Approve</Btn>
                    <Btn size="sm" variant="danger" onClick={async () => {
                      try {
                        await postJSON(`/api/wallet/fund-requests/${r.id}/decision`, { decision: 'REJECTED' });
                        flash(`${r.request_reference} rejected.`);
                        await load();
                      } catch (e) { setErr(e.message); }
                    }}>Reject</Btn>
                  </div>
                );
              }
              return r[c.key];
            }}
          />
          <div style={{ marginTop: space(2) }}>
            <Banner tone="info" title="Approving is not funding">
              Approving authorises the payment. The wallet balance only changes
              once you record the funding, after the money has actually been
              transferred or handed over.
            </Banner>
          </div>
        </Card>
      )}

      {tab === 'returns' && (
        <Card>
          <SectionTitle>Funds staff say they have returned</SectionTitle>
          <DataTable
            cols={[
              { key: 'declared_by', label: 'Who' },
              { key: 'wallet_number', label: 'Wallet' },
              { key: 'amount', label: 'Amount', align: 'right' },
              { key: 'method', label: 'How' },
              { key: 'returned_on', label: 'Date' },
              { key: 'act', label: '', align: 'right' },
            ]}
            rows={returns}
            empty="Nothing waiting to be confirmed."
            render={(r, c) => {
              if (c.key === 'amount') return <strong>{money(r.amount)}</strong>;
              if (c.key === 'act') {
                return (
                  <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                    <Btn size="sm" variant="accent" onClick={async () => {
                      try {
                        await postJSON(`/api/wallet/returns/${r.id}/confirm`, { confirm: true });
                        flash(`${money(r.amount)} confirmed received; the balance has been reduced.`);
                        await load();
                      } catch (e) { setErr(e.message); }
                    }}>Confirm received</Btn>
                    <Btn size="sm" variant="danger" onClick={async () => {
                      try {
                        await postJSON(`/api/wallet/returns/${r.id}/confirm`, { confirm: false });
                        flash('Return rejected.');
                        await load();
                      } catch (e) { setErr(e.message); }
                    }}>Not received</Btn>
                  </div>
                );
              }
              return r[c.key];
            }}
          />
          <div style={{ marginTop: space(2) }}>
            <Banner tone="warning" title="Only confirm what you actually have">
              Confirming reduces what the employee is accountable for. Count the
              cash or check the bank credit first.
            </Banner>
          </div>
        </Card>
      )}

      {tab === 'claims' && (
        <Card>
          <SectionTitle>Reimbursement claims</SectionTitle>
          <div style={{ marginBottom: space(2) }}>
            <Banner tone="info" title="These are separate from wallet balances">
              The employee spent their own money, so nothing was entrusted and
              nothing is discharged. Approving creates a debt the company owes
              them; paying settles it.
            </Banner>
          </div>
          <DataTable
            cols={[
              { key: 'claimant', label: 'Who' },
              { key: 'purpose', label: 'What for', wrap: true },
              { key: 'amount', label: 'Claimed', align: 'right' },
              { key: 'spent_on', label: 'Date' },
              { key: 'status', label: 'Status' },
              { key: 'act', label: '', align: 'right' },
            ]}
            rows={claims}
            empty="No claims."
            render={(r, c) => {
              if (c.key === 'amount') return <strong>{money(r.amount)}</strong>;
              if (c.key === 'status') return <Chip tone={tone(r.status)}>{r.status}</Chip>;
              if (c.key === 'purpose') {
                return (
                  <div style={{ maxWidth: 260, whiteSpace: 'normal' }}>
                    <div>{r.purpose}</div>
                    <div style={{ fontSize: 11.5, color: color.textMuted }}>{r.category}</div>
                  </div>
                );
              }
              if (c.key === 'act') {
                if (r.status === 'PENDING' && caps.can_approve) {
                  return (
                    <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                      <Btn size="sm" variant="accent" onClick={async () => {
                        try {
                          await postJSON(`/api/wallet/reimbursements/${r.id}/decision`, { approve: true });
                          flash(`${r.reimbursement_reference} approved.`); await load();
                        } catch (e) { setErr(e.message); }
                      }}>Approve</Btn>
                      <Btn size="sm" variant="danger" onClick={async () => {
                        try {
                          await postJSON(`/api/wallet/reimbursements/${r.id}/decision`, { approve: false });
                          flash(`${r.reimbursement_reference} rejected.`); await load();
                        } catch (e) { setErr(e.message); }
                      }}>Reject</Btn>
                    </div>
                  );
                }
                if (r.status === 'APPROVED' && caps.can_reconcile) {
                  return (
                    <Btn size="sm" variant="secondary" onClick={async () => {
                      try {
                        await postJSON(`/api/wallet/reimbursements/${r.id}/pay`, {
                          paid_on: todayISO(), payment_method: 'BANK_TRANSFER',
                        });
                        flash(`${r.reimbursement_reference} marked paid.`); await load();
                      } catch (e) { setErr(e.message); }
                    }}>Mark paid</Btn>
                  );
                }
                return null;
              }
              return r[c.key];
            }}
          />
        </Card>
      )}

      {tab === 'reconciliations' && (
        <Card>
          <SectionTitle>Period reconciliations</SectionTitle>
          <DataTable
            cols={[
              { key: 'submitted_by', label: 'Who' },
              { key: 'period', label: 'Period' },
              { key: 'closing_balance', label: 'Should hold', align: 'right' },
              { key: 'declared_cash_on_hand', label: 'Says they hold', align: 'right' },
              { key: 'variance', label: 'Difference', align: 'right' },
              { key: 'status', label: 'Status' },
              { key: 'act', label: '', align: 'right' },
            ]}
            rows={recs}
            empty="No reconciliations submitted."
            render={(r, c) => {
              if (c.key === 'period') return `${r.period_start} → ${r.period_end}`;
              if (c.key === 'closing_balance' || c.key === 'declared_cash_on_hand') {
                return r[c.key] == null ? '—' : money(r[c.key]);
              }
              if (c.key === 'variance') {
                if (r.variance == null) return '—';
                const v = Number(r.variance);
                return <strong style={{ color: v === 0 ? color.success : color.danger }}>{money(v)}</strong>;
              }
              if (c.key === 'status') return <Chip tone={tone(r.status)}>{r.status}</Chip>;
              if (c.key === 'act' && r.status === 'SUBMITTED' && caps.can_reconcile) {
                return (
                  <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                    <Btn size="sm" variant="accent" onClick={async () => {
                      try {
                        await postJSON(`/api/wallet/reconciliations/${r.id}/review`, { accept: true });
                        flash(`${r.reconciliation_reference} accepted.`); await load();
                      } catch (e) { setErr(e.message); }
                    }}>Accept</Btn>
                    <Btn size="sm" variant="danger" onClick={async () => {
                      try {
                        await postJSON(`/api/wallet/reconciliations/${r.id}/review`, { accept: false });
                        flash(`${r.reconciliation_reference} sent back.`); await load();
                      } catch (e) { setErr(e.message); }
                    }}>Send back</Btn>
                  </div>
                );
              }
              return r[c.key];
            }}
          />
        </Card>
      )}

      {tab === 'flags' && (
        <Card>
          <SectionTitle right={caps.is_admin
            ? <Btn size="sm" variant="secondary" onClick={async () => {
                try {
                  const d = await postJSON('/api/wallet/flags/sweep');
                  flash(`${d.flagged} approved expense(s) are missing a receipt.`);
                  await load();
                } catch (e) { setErr(e.message); }
              }}>Re-scan for missing receipts</Btn>
            : null}>Things worth a look</SectionTitle>

          <div style={{ marginBottom: space(2) }}>
            <Banner tone="info" title="These are questions, not accusations">
              Every one of these patterns has an innocent explanation as often
              as not. Nothing here has blocked a transaction or changed anyone's
              status — it is here so a person can ask.
            </Banner>
          </div>

          <DataTable
            cols={[
              { key: 'severity', label: 'Level' },
              { key: 'holder', label: 'Who' },
              { key: 'message', label: 'What was noticed', wrap: true },
              { key: 'expense_reference', label: 'Expense' },
              { key: 'act', label: '', align: 'right' },
            ]}
            rows={flags}
            empty="Nothing has been flagged."
            render={(r, c) => {
              if (c.key === 'severity') return <Chip tone={tone(r.severity)}>{r.severity}</Chip>;
              if (c.key === 'message') {
                return <div style={{ maxWidth: 380, whiteSpace: 'normal' }}>{r.message}</div>;
              }
              if (c.key === 'holder') {
                return (
                  <div>
                    <div style={{ fontWeight: 600 }}>{r.holder}</div>
                    <div style={{ fontSize: 11.5, color: color.textMuted }}>{r.wallet_number}</div>
                  </div>
                );
              }
              if (c.key === 'expense_reference') {
                return r.expense_reference
                  ? <span>{r.expense_reference}<br /><span style={{ fontSize: 11.5, color: color.textMuted }}>{money(r.expense_amount)}</span></span>
                  : '—';
              }
              if (c.key === 'act') {
                return (
                  <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                    <Btn size="sm" variant="secondary" onClick={async () => {
                      try {
                        await postJSON(`/api/wallet/flags/${r.id}/review`, { status: 'REVIEWED' });
                        flash('Marked as reviewed.'); await load();
                      } catch (e) { setErr(e.message); }
                    }}>Looked into it</Btn>
                    <Btn size="sm" variant="ghost" onClick={async () => {
                      try {
                        await postJSON(`/api/wallet/flags/${r.id}/review`, { status: 'DISMISSED' });
                        flash('Dismissed.'); await load();
                      } catch (e) { setErr(e.message); }
                    }}>Not a concern</Btn>
                  </div>
                );
              }
              return r[c.key];
            }}
          />
        </Card>
      )}

      {tab === 'config' && caps.is_admin && (
        <>
          <Card style={{ marginBottom: space(2.5) }}>
            <SectionTitle right={<Btn size="sm" variant="secondary"
              onClick={() => setModal({ kind: 'newRule' })}>Add a band</Btn>}>
              Who has to approve what
            </SectionTitle>
            <DataTable
              cols={[
                { key: 'scope', label: 'Applies to' },
                { key: 'band', label: 'Amount band' },
                { key: 'tier', label: 'Needs approval from' },
                { key: 'is_active', label: 'Active' },
                { key: 'act', label: '', align: 'right' },
              ]}
              rows={rules}
              empty="No rules configured — everything falls back to management approval."
              render={(r, c) => {
                if (c.key === 'scope') {
                  return r.scope === 'GLOBAL' ? 'Every wallet'
                    : r.scope === 'WALLET_TYPE' ? `${r.wallet_type} wallets`
                    : r.wallet_number || 'One wallet';
                }
                if (c.key === 'band') {
                  return r.max_amount
                    ? `${money(r.min_amount)} – ${money(r.max_amount)}`
                    : `${money(r.min_amount)} and above`;
                }
                if (c.key === 'tier') {
                  return <Chip tone={r.tier === 'SELF' ? 'success' : r.tier === 'SUPERVISOR' ? 'warning' : 'danger'}>
                    {r.tier === 'SELF' ? 'Nobody — staff may spend' : r.tier}</Chip>;
                }
                if (c.key === 'is_active') return r.is_active ? 'Yes' : 'No';
                if (c.key === 'act' && r.is_active) {
                  return <Btn size="sm" variant="ghost" onClick={async () => {
                    try { await delJSON(`/api/wallet/admin/approval-rules/${r.id}`); flash('Band deactivated.'); await load(); }
                    catch (e) { setErr(e.message); }
                  }}>Deactivate</Btn>;
                }
                return null;
              }}
            />
          </Card>

          <Card>
            <SectionTitle right={<Btn size="sm" variant="secondary"
              onClick={() => setModal({ kind: 'newApprover' })}>Give someone authority</Btn>}>
              Who can approve, fund and reconcile
            </SectionTitle>
            <div style={{ marginBottom: space(2) }}>
              <Banner tone="info" title="Nobody approves their own spending">
                That rule has no exception, including for administrators. An
                administrator who holds a wallet still needs a second person for
                anything above their self-approval limit.
              </Banner>
            </div>
            <DataTable
              cols={[
                { key: 'full_name', label: 'Person' },
                { key: 'tier', label: 'Level' },
                { key: 'max_amount', label: 'Up to', align: 'right' },
                { key: 'scope', label: 'Scope' },
                { key: 'powers', label: 'Also can' },
                { key: 'act', label: '', align: 'right' },
              ]}
              rows={approvers}
              empty="Only administrators can approve at present."
              render={(r, c) => {
                if (c.key === 'max_amount') return r.max_amount ? money(r.max_amount) : 'No ceiling';
                if (c.key === 'scope') {
                  return r.wallet_number || r.department || 'Everything';
                }
                if (c.key === 'powers') {
                  const p = [r.can_fund && 'Fund', r.can_reconcile && 'Reconcile'].filter(Boolean);
                  return p.length ? p.join(', ') : '—';
                }
                if (c.key === 'full_name') {
                  return (
                    <div>
                      <div style={{ fontWeight: 600 }}>{r.full_name}</div>
                      <div style={{ fontSize: 11.5, color: color.textMuted }}>{r.email}</div>
                    </div>
                  );
                }
                if (c.key === 'act' && r.is_active) {
                  return <Btn size="sm" variant="ghost" onClick={async () => {
                    try { await delJSON(`/api/wallet/admin/approvers/${r.id}`); flash('Authority revoked.'); await load(); }
                    catch (e) { setErr(e.message); }
                  }}>Revoke</Btn>;
                }
                return r[c.key];
              }}
            />
          </Card>
        </>
      )}

      {modal && modal.kind === 'fund' && (
        <FundWallet wallets={wallets} preselect={modal.preselect}
          onClose={() => setModal(null)}
          onDone={async (msg) => { setModal(null); flash(msg); await load(); }} />
      )}

      {modal && modal.kind === 'receipts' && (
        <Modal title={`Receipts for ${modal.row.expense_reference}`} onClose={() => setModal(null)}>
          {modal.receipts.length === 0 && <p style={{ color: color.textMuted }}>No receipts attached.</p>}
          {modal.receipts.map((r) => (
            <div key={r.id} style={{ marginBottom: space(2) }}>
              <div style={{ fontSize: 12, color: color.textSecondary, marginBottom: 6 }}>
                {r.filename} · {(r.byte_size / 1024).toFixed(0)} KB
              </div>
              {r.content_type === 'application/pdf' ? (
                <a href={`/api/wallet/receipts/${r.id}`} target="_blank" rel="noreferrer"
                  style={{ color: color.medical, fontWeight: 600 }}>Open the PDF</a>
              ) : (
                <img src={`/api/wallet/receipts/${r.id}`} alt={r.filename}
                  style={{ maxWidth: '100%', borderRadius: radius.md, border: `1px solid ${color.border}` }} />
              )}
            </div>
          ))}
        </Modal>
      )}

      {modal && modal.kind === 'report' && (
        <WalletStatement report={modal.report} onClose={() => setModal(null)} />
      )}

      {modal && modal.kind === 'newWallet' && (
        <NewWallet onClose={() => setModal(null)}
          onDone={async (msg) => { setModal(null); flash(msg); await load(); }} />
      )}

      {modal && modal.kind === 'newRule' && (
        <NewRule wallets={wallets} onClose={() => setModal(null)}
          onDone={async (msg) => { setModal(null); flash(msg); await load(); }} />
      )}

      {modal && modal.kind === 'newApprover' && (
        <NewApprover onClose={() => setModal(null)}
          onDone={async (msg) => { setModal(null); flash(msg); await load(); }} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// One employee's complete money picture
// ---------------------------------------------------------------------------

function WalletStatement({ report, onClose }) {
  const s = report.summary;
  return (
    <Modal title={`${s.holder.full_name} — ${s.wallet_number}`} onClose={onClose} width={860}>
      <Grid min={150}>
        {[['Issued', s.total_funded], ['Spent', s.total_spent],
          ['Returned', s.total_returned], ['Held now', s.balance],
          ['Outstanding', s.outstanding]].map(([label, v]) => (
          <Card key={label} pad={2}>
            <div style={{ fontSize: 11, color: color.textSecondary, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em' }}>{label}</div>
            <div style={{ fontSize: 18, fontWeight: 700, marginTop: 3 }}>{money(v)}</div>
          </Card>
        ))}
      </Grid>

      {[['Funds issued', report.fundings, [
          { key: 'funding_reference', label: 'Reference' },
          { key: 'funded_on', label: 'Date' },
          { key: 'amount', label: 'Amount', align: 'right' },
          { key: 'purpose', label: 'Purpose', wrap: true },
          { key: 'account_by', label: 'Account by' },
          { key: 'status', label: 'Status' }]],
        ['Expenses', report.expenses, [
          { key: 'spent_on', label: 'Date' },
          { key: 'category', label: 'Category' },
          { key: 'purpose', label: 'Purpose', wrap: true },
          { key: 'amount', label: 'Amount', align: 'right' },
          { key: 'receipts', label: 'Receipt', align: 'center' },
          { key: 'status', label: 'Status' }]],
        ['Returns', report.returns, [
          { key: 'return_reference', label: 'Reference' },
          { key: 'returned_on', label: 'Date' },
          { key: 'amount', label: 'Amount', align: 'right' },
          { key: 'method', label: 'How' },
          { key: 'status', label: 'Status' }]],
        ['Requests for funds', report.fund_requests, [
          { key: 'request_reference', label: 'Reference' },
          { key: 'amount_requested', label: 'Asked', align: 'right' },
          { key: 'amount_approved', label: 'Approved', align: 'right' },
          { key: 'reason', label: 'Reason', wrap: true },
          { key: 'status', label: 'Status' }]],
        ['Reimbursement claims', report.reimbursements, [
          { key: 'reimbursement_reference', label: 'Reference' },
          { key: 'spent_on', label: 'Date' },
          { key: 'amount', label: 'Claimed', align: 'right' },
          { key: 'purpose', label: 'Purpose', wrap: true },
          { key: 'status', label: 'Status' }]],
        ['Flags raised', report.flags, [
          { key: 'created_at', label: 'When' },
          { key: 'flag_type', label: 'Pattern' },
          { key: 'severity', label: 'Level' },
          { key: 'message', label: 'Detail', wrap: true },
          { key: 'status', label: 'Status' }]],
      ].map(([title, rows, cols]) => (
        <div key={title} style={{ marginTop: space(3) }}>
          <SectionTitle>{title}</SectionTitle>
          <DataTable cols={cols} rows={rows || []} empty="Nothing recorded."
            maxHeight={280}
            render={(r, c) => {
              if (['amount', 'amount_requested', 'amount_approved'].includes(c.key)) {
                return r[c.key] == null ? '—' : money(r[c.key]);
              }
              if (c.key === 'status' || c.key === 'severity') {
                return <Chip tone={tone(r[c.key])}>{r[c.key]}</Chip>;
              }
              if (c.key === 'created_at') return String(r.created_at || '').slice(0, 10);
              if (c.wrap) return <div style={{ maxWidth: 260, whiteSpace: 'normal' }}>{r[c.key]}</div>;
              return r[c.key] == null ? '—' : r[c.key];
            }} />
        </div>
      ))}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Configuration forms
// ---------------------------------------------------------------------------

function NewWallet({ onClose, onDone }) {
  const [users, setUsers] = useState([]);
  const [form, setForm] = useState({
    user_id: '', wallet_type: 'INDIVIDUAL', department: '', purpose: '',
    single_txn_limit: '', daily_limit: '', monthly_limit: '', self_approve_limit: '',
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    // Reuses the existing user directory rather than introducing a second
    // list of people to keep in step.
    getJSON('/api/auth/users').then((d) => {
      const list = Array.isArray(d) ? d : (d.users || []);
      setUsers(list.filter((u) => u.is_active !== false));
    }).catch((e) => setErr(`Could not load the staff list: ${e.message}`));
  }, []);

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const num = (v) => (v === '' ? null : Number(v));

  const submit = async () => {
    setErr('');
    if (!form.user_id) { setErr('Choose the employee.'); return; }
    setBusy(true);
    try {
      const r = await postJSON('/api/wallet/wallets', {
        user_id: form.user_id, wallet_type: form.wallet_type,
        department: form.department || null, purpose: form.purpose || null,
        single_txn_limit: num(form.single_txn_limit),
        daily_limit: num(form.daily_limit),
        monthly_limit: num(form.monthly_limit),
        self_approve_limit: num(form.self_approve_limit),
      });
      onDone(`Wallet ${r.wallet_number} opened.`);
    } catch (e) { setErr(e.message); setBusy(false); }
  };

  return (
    <Modal title="Open a wallet" onClose={busy ? () => {} : onClose}
      footer={<Btn variant="accent" onClick={submit} disabled={busy} style={{ width: '100%' }}>
        {busy ? 'Opening…' : 'Open the wallet'}</Btn>}>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}
      <div style={{ display: 'grid', gap: space(2) }}>
        <Field label="Employee"
          hint="They must have a login: the wallet holder records their own spending.">
          <select value={form.user_id} onChange={set('user_id')} style={inputStyle}>
            <option value="">Choose…</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>{u.full_name} ({u.email})</option>
            ))}
          </select>
        </Field>
        <Grid min={200}>
          <Field label="Wallet type">
            <select value={form.wallet_type} onChange={set('wallet_type')} style={inputStyle}>
              {['INDIVIDUAL', 'FACTORY', 'MARKETING', 'SALES', 'LOGISTICS', 'PROCUREMENT', 'ADMIN']
                .map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </Field>
          <Field label="Department">
            <input value={form.department} onChange={set('department')}
              placeholder="Factory" style={inputStyle} />
          </Field>
        </Grid>
        <Field label="What is this wallet for?">
          <input value={form.purpose} onChange={set('purpose')}
            placeholder="Factory operational expenses" style={inputStyle} />
        </Field>
        <SectionTitle>Limits (leave empty for no limit)</SectionTitle>
        <Grid min={160}>
          <Field label="Per transaction">
            <input type="number" min="0" value={form.single_txn_limit} onChange={set('single_txn_limit')} style={inputStyle} />
          </Field>
          <Field label="Per day">
            <input type="number" min="0" value={form.daily_limit} onChange={set('daily_limit')} style={inputStyle} />
          </Field>
          <Field label="Per month">
            <input type="number" min="0" value={form.monthly_limit} onChange={set('monthly_limit')} style={inputStyle} />
          </Field>
        </Grid>
        <Field label="May spend without asking, up to"
          hint="Leave empty to follow the company approval ladder. Set 0 to require approval for everything.">
          <input type="number" min="0" value={form.self_approve_limit}
            onChange={set('self_approve_limit')} style={inputStyle} />
        </Field>
      </div>
    </Modal>
  );
}

function NewRule({ wallets, onClose, onDone }) {
  const [form, setForm] = useState({
    scope: 'GLOBAL', wallet_type: 'FACTORY', wallet_id: '',
    min_amount: '0', max_amount: '', tier: 'SUPERVISOR',
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const submit = async () => {
    setErr(''); setBusy(true);
    try {
      await postJSON('/api/wallet/admin/approval-rules', {
        scope: form.scope,
        wallet_type: form.scope === 'WALLET_TYPE' ? form.wallet_type : null,
        wallet_id: form.scope === 'WALLET' ? form.wallet_id : null,
        min_amount: Number(form.min_amount || 0),
        max_amount: form.max_amount === '' ? null : Number(form.max_amount),
        tier: form.tier,
      });
      onDone('Approval band added.');
    } catch (e) { setErr(e.message); setBusy(false); }
  };

  return (
    <Modal title="Add an approval band" onClose={busy ? () => {} : onClose}
      footer={<Btn variant="accent" onClick={submit} disabled={busy} style={{ width: '100%' }}>
        {busy ? 'Saving…' : 'Add the band'}</Btn>}>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}
      <div style={{ display: 'grid', gap: space(2) }}>
        <Field label="Applies to">
          <select value={form.scope} onChange={set('scope')} style={inputStyle}>
            <option value="GLOBAL">Every wallet</option>
            <option value="WALLET_TYPE">One type of wallet</option>
            <option value="WALLET">One specific wallet</option>
          </select>
        </Field>
        {form.scope === 'WALLET_TYPE' && (
          <Field label="Which type">
            <select value={form.wallet_type} onChange={set('wallet_type')} style={inputStyle}>
              {['INDIVIDUAL', 'FACTORY', 'MARKETING', 'SALES', 'LOGISTICS', 'PROCUREMENT', 'ADMIN']
                .map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </Field>
        )}
        {form.scope === 'WALLET' && (
          <Field label="Which wallet">
            <select value={form.wallet_id} onChange={set('wallet_id')} style={inputStyle}>
              <option value="">Choose…</option>
              {wallets.map((w) => <option key={w.id} value={w.id}>{w.holder} — {w.wallet_number}</option>)}
            </select>
          </Field>
        )}
        <Grid min={160}>
          <Field label="From amount">
            <input type="number" min="0" value={form.min_amount} onChange={set('min_amount')} style={inputStyle} />
          </Field>
          <Field label="Up to (empty = no ceiling)">
            <input type="number" min="0" value={form.max_amount} onChange={set('max_amount')} style={inputStyle} />
          </Field>
        </Grid>
        <Field label="Who must approve in this band">
          <select value={form.tier} onChange={set('tier')} style={inputStyle}>
            <option value="SELF">Nobody — staff may spend directly</option>
            <option value="SUPERVISOR">A supervisor</option>
            <option value="MANAGEMENT">Management</option>
          </select>
        </Field>
      </div>
    </Modal>
  );
}

function NewApprover({ onClose, onDone }) {
  const [users, setUsers] = useState([]);
  const [form, setForm] = useState({
    user_id: '', tier: 'SUPERVISOR', max_amount: '', department: '',
    can_fund: false, can_reconcile: false,
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    getJSON('/api/auth/users').then((d) => {
      const list = Array.isArray(d) ? d : (d.users || []);
      setUsers(list.filter((u) => u.is_active !== false));
    }).catch((e) => setErr(`Could not load the staff list: ${e.message}`));
  }, []);

  const submit = async () => {
    setErr('');
    if (!form.user_id) { setErr('Choose the person.'); return; }
    setBusy(true);
    try {
      await postJSON('/api/wallet/admin/approvers', {
        user_id: form.user_id, tier: form.tier,
        max_amount: form.max_amount === '' ? null : Number(form.max_amount),
        department: form.department || null,
        can_fund: form.can_fund, can_reconcile: form.can_reconcile,
      });
      onDone('Authority granted.');
    } catch (e) { setErr(e.message); setBusy(false); }
  };

  return (
    <Modal title="Give someone approval authority" onClose={busy ? () => {} : onClose}
      footer={<Btn variant="accent" onClick={submit} disabled={busy} style={{ width: '100%' }}>
        {busy ? 'Granting…' : 'Grant it'}</Btn>}>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}
      <div style={{ display: 'grid', gap: space(2) }}>
        <Field label="Person">
          <select value={form.user_id} onChange={(e) => setForm({ ...form, user_id: e.target.value })} style={inputStyle}>
            <option value="">Choose…</option>
            {users.map((u) => <option key={u.id} value={u.id}>{u.full_name} ({u.email})</option>)}
          </select>
        </Field>
        <Grid min={190}>
          <Field label="Level"
            hint="Finance outranks a supervisor but cannot approve management-tier spending.">
            <select value={form.tier} onChange={(e) => setForm({ ...form, tier: e.target.value })} style={inputStyle}>
              <option value="SUPERVISOR">Supervisor</option>
              <option value="FINANCE">Finance</option>
              <option value="MANAGEMENT">Management</option>
            </select>
          </Field>
          <Field label="Ceiling (empty = none)">
            <input type="number" min="0" value={form.max_amount}
              onChange={(e) => setForm({ ...form, max_amount: e.target.value })} style={inputStyle} />
          </Field>
        </Grid>
        <Field label="Limit to a department (empty = all)">
          <input value={form.department}
            onChange={(e) => setForm({ ...form, department: e.target.value })}
            placeholder="Factory" style={inputStyle} />
        </Field>
        <div style={{ display: 'grid', gap: 8 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
            <input type="checkbox" checked={form.can_fund}
              onChange={(e) => setForm({ ...form, can_fund: e.target.checked })} />
            Can record funds issued to staff, and decide fund requests
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
            <input type="checkbox" checked={form.can_reconcile}
              onChange={(e) => setForm({ ...form, can_reconcile: e.target.checked })} />
            Can confirm returned money, settle reconciliations and pay claims
          </label>
        </div>
      </div>
    </Modal>
  );
}
