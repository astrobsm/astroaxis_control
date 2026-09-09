// My Operational Wallet — the staff-facing half of the accountability module.
//
// Mounted from AppMain when activeModule === 'wallet'.
//
// The design constraint that shaped this screen: a factory supervisor standing
// at a fuel station, on a phone, in the sun, must be able to record what they
// just spent in under a minute. So the expense form is four fields and a
// camera button, it opens as a full-height sheet rather than a page the user
// has to navigate back from, and the receipt photograph is taken inline.
//
// Every figure shown here comes from the server. Nothing is computed in the
// browser and nothing is cached across a reload -- a balance is the kind of
// number that must never be a stale local guess.

import React, { useCallback, useEffect, useState } from 'react';
import { authedFetch } from './utils/api';
import { color, font, naira, radius, space } from './ui/theme';
import {
  Banner, Btn, Card, Chip, DataTable, ErrorBox, Icon, SectionTitle, Skeleton,
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

const todayISO = () => new Date().toISOString().slice(0, 10);
const money = (v) => naira(v);

const STATUS_TONE = {
  APPROVED: 'success', PENDING: 'warning', REJECTED: 'danger',
  REVERSED: 'neutral', CONFIRMED: 'success', DECLARED: 'warning',
  FUNDED: 'success', PARTIALLY_APPROVED: 'warning', PAID: 'success',
  ACTIVE: 'success', SUSPENDED: 'warning', FROZEN: 'danger', CLOSED: 'neutral',
};
const tone = (s) => STATUS_TONE[String(s || '').toUpperCase()] || 'neutral';

const inputStyle = {
  padding: '11px 12px', border: `1px solid ${color.borderStrong}`,
  borderRadius: radius.sm, fontSize: 16, fontFamily: font.family,
  color: color.text, background: '#fff', width: '100%', boxSizing: 'border-box',
};
// 16px, not 13px: anything smaller makes iOS Safari zoom the whole page on
// focus, which on a one-handed form is genuinely disorienting.

function Field({ label, hint, children }) {
  return (
    <label style={{ display: 'block', fontSize: 12.5, color: color.textSecondary, fontWeight: 600, marginBottom: space(2) }}>
      {label}
      <div style={{ marginTop: 5 }}>{children}</div>
      {hint && <div style={{ marginTop: 4, fontSize: 11.5, color: color.textMuted, fontWeight: 400, lineHeight: 1.45 }}>{hint}</div>}
    </label>
  );
}

/** Full-height bottom sheet. Reachable with a thumb; dismissible by tapping away. */
function Sheet({ title, onClose, children, footer }) {
  useEffect(() => {
    const onEsc = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onEsc);
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onEsc);
      document.body.style.overflow = '';
    };
  }, [onClose]);

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)',
        zIndex: 1000, display: 'flex', alignItems: 'flex-end',
        justifyContent: 'center',
      }}>
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: '#fff', width: '100%', maxWidth: 560,
          maxHeight: '92vh', display: 'flex', flexDirection: 'column',
          borderRadius: `${radius.lg} ${radius.lg} 0 0`,
          boxShadow: '0 -8px 32px rgba(15,23,42,0.18)',
        }}>
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: `${space(2)} ${space(2.5)}`, borderBottom: `1px solid ${color.border}`,
          flexShrink: 0,
        }}>
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: color.text }}>{title}</h3>
          <button onClick={onClose} aria-label="Close" style={{
            border: 'none', background: 'transparent', fontSize: 26, lineHeight: 1,
            color: color.textMuted, cursor: 'pointer', padding: '0 4px',
          }}>&times;</button>
        </div>
        <div style={{ padding: space(2.5), overflowY: 'auto', flex: 1 }}>{children}</div>
        {footer && (
          <div style={{
            padding: space(2.5), borderTop: `1px solid ${color.border}`,
            flexShrink: 0, background: '#FbFcFe',
          }}>{footer}</div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Add expense
// ---------------------------------------------------------------------------

function AddExpense({ wallet, categories, onClose, onDone }) {
  const [amount, setAmount] = useState('');
  const [categoryId, setCategoryId] = useState('');
  const [purpose, setPurpose] = useState('');
  const [spentOn, setSpentOn] = useState(todayISO());
  const [vendor, setVendor] = useState('');
  const [file, setFile] = useState(null);
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  // Generated once per open, so a double-tap on a slow connection is
  // recognised by the server as the same expense rather than a second one.
  const [idem] = useState(() => `exp-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`);

  const category = categories.find((c) => c.id === categoryId);
  const needsReceipt = category ? category.requires_receipt : true;

  const submit = async () => {
    setErr('');
    if (!categoryId) { setErr('Choose what the money was spent on.'); return; }
    if (!amount || Number(amount) <= 0) { setErr('Enter the amount you spent.'); return; }
    if (!purpose.trim()) { setErr('Say briefly what it was for.'); return; }

    setBusy(true);
    try {
      // Position is offered, never demanded: a denied or unavailable location
      // must not stop someone recording money they have already spent.
      let coords = {};
      try {
        coords = await new Promise((resolve) => {
          if (!navigator.geolocation) return resolve({});
          const done = (p) => resolve({
            latitude: p.coords.latitude, longitude: p.coords.longitude,
            gps_accuracy: p.coords.accuracy,
          });
          navigator.geolocation.getCurrentPosition(done, () => resolve({}),
            { timeout: 4000, maximumAge: 60000 });
        });
      } catch { coords = {}; }

      const created = await postJSON(`/api/wallet/wallets/${wallet.id}/expenses`, {
        category_id: categoryId,
        amount: Number(amount),
        purpose: purpose.trim(),
        spent_on: spentOn,
        vendor: vendor.trim() || null,
        idempotency_key: idem,
        ...coords,
      });

      if (file) {
        const form = new FormData();
        form.append('file', file);
        // The expense is already recorded at this point. If the photograph
        // fails to upload, say so plainly rather than implying the whole
        // record was lost -- the money is accounted for either way, and the
        // receipt can be attached again from the list.
        try {
          await req(`/api/wallet/expenses/${created.id}/receipt`,
            { method: 'POST', body: form });
        } catch (e) {
          onDone(created, `Expense ${created.expense_reference} was saved, but `
            + `the receipt did not upload: ${e.message} You can attach it again `
            + `from your transaction list.`);
          return;
        }
      }
      onDone(created, null);
    } catch (e) {
      setErr(e.message);
      setBusy(false);
    }
  };

  return (
    <Sheet
      title="Add expense"
      onClose={busy ? () => {} : onClose}
      footer={
        <Btn variant="accent" onClick={submit} disabled={busy} icon="check"
          style={{ width: '100%' }}>
          {busy ? 'Saving…' : 'Submit expense'}
        </Btn>
      }>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <Field label="Amount spent">
        <input type="number" inputMode="decimal" min="0" step="0.01"
          value={amount} onChange={(e) => setAmount(e.target.value)}
          placeholder="0.00" style={{ ...inputStyle, fontSize: 22, fontWeight: 700 }} />
      </Field>

      <Field label="What was it for?">
        <select value={categoryId} onChange={(e) => setCategoryId(e.target.value)}
          style={inputStyle}>
          <option value="">Choose a category…</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
      </Field>

      <Field label="Purpose"
        hint="A few words is enough — 'diesel for the generator'.">
        <input value={purpose} onChange={(e) => setPurpose(e.target.value)}
          placeholder="Diesel for the generator" style={inputStyle} />
      </Field>

      <Field label="Receipt photograph"
        hint={needsReceipt
          ? 'Required for this category. Point the camera at the receipt.'
          : 'Optional for this category, but always worth having.'}>
        <input type="file" accept="image/*,application/pdf" capture="environment"
          onChange={(e) => setFile(e.target.files ? e.target.files[0] : null)}
          style={{ ...inputStyle, padding: '9px 10px' }} />
        {file && (
          <div style={{ marginTop: 6, fontSize: 12, color: color.success, fontWeight: 600 }}>
            {file.name} ({(file.size / 1024).toFixed(0)} KB) attached
          </div>
        )}
      </Field>

      {!more ? (
        <button onClick={() => setMore(true)} style={{
          background: 'transparent', border: 'none', color: color.medical,
          fontSize: 13, fontWeight: 600, cursor: 'pointer', padding: 0,
        }}>+ Add date or vendor</button>
      ) : (
        <>
          <Field label="Date spent">
            <input type="date" value={spentOn} max={todayISO()}
              onChange={(e) => setSpentOn(e.target.value)} style={inputStyle} />
          </Field>
          <Field label="Paid to (vendor)">
            <input value={vendor} onChange={(e) => setVendor(e.target.value)}
              placeholder="Total filling station" style={inputStyle} />
          </Field>
        </>
      )}
    </Sheet>
  );
}

// ---------------------------------------------------------------------------
// Request funds / return funds / reconcile
// ---------------------------------------------------------------------------

function RequestFunds({ wallet, onClose, onDone }) {
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const [urgency, setUrgency] = useState('NORMAL');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const submit = async () => {
    setErr('');
    if (!amount || Number(amount) <= 0) { setErr('Enter how much you need.'); return; }
    if (reason.trim().length < 3) { setErr('Say what the money is needed for.'); return; }
    setBusy(true);
    try {
      const r = await postJSON(`/api/wallet/wallets/${wallet.id}/fund-requests`, {
        amount: Number(amount), reason: reason.trim(), urgency,
      });
      onDone(`Request ${r.request_reference} sent to management.`);
    } catch (e) { setErr(e.message); setBusy(false); }
  };

  return (
    <Sheet title="Request more funds" onClose={busy ? () => {} : onClose}
      footer={<Btn variant="accent" onClick={submit} disabled={busy} style={{ width: '100%' }}>
        {busy ? 'Sending…' : 'Send request'}</Btn>}>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}
      <Banner tone="info" title="You do not need to telephone anyone">
        Management sees this request with your current balance attached, and can
        approve all of it, part of it, or ask you a question.
      </Banner>
      <div style={{ height: space(2) }} />
      <Field label="Amount needed">
        <input type="number" inputMode="decimal" min="0" step="0.01" value={amount}
          onChange={(e) => setAmount(e.target.value)} placeholder="0.00"
          style={{ ...inputStyle, fontSize: 22, fontWeight: 700 }} />
      </Field>
      <Field label="What is it for?"
        hint="Be specific — this is what management reads before deciding.">
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3}
          placeholder="Emergency repair to the sealing machine"
          style={{ ...inputStyle, resize: 'vertical' }} />
      </Field>
      <Field label="How urgent?">
        <select value={urgency} onChange={(e) => setUrgency(e.target.value)} style={inputStyle}>
          <option value="NORMAL">Normal</option>
          <option value="URGENT">Urgent — needed today</option>
          <option value="EMERGENCY">Emergency — work is stopped</option>
        </select>
      </Field>
    </Sheet>
  );
}

function ReturnFunds({ wallet, onClose, onDone }) {
  const [amount, setAmount] = useState(wallet.balance || '');
  const [method, setMethod] = useState('CASH');
  const [reference, setReference] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const submit = async () => {
    setErr('');
    if (!amount || Number(amount) <= 0) { setErr('Enter how much you handed back.'); return; }
    setBusy(true);
    try {
      const r = await postJSON(`/api/wallet/wallets/${wallet.id}/returns`, {
        amount: Number(amount), method, reference: reference.trim() || null,
        returned_on: todayISO(),
      });
      onDone(`Return ${r.return_reference} recorded. Your balance changes once `
        + `finance confirms the money is back.`);
    } catch (e) { setErr(e.message); setBusy(false); }
  };

  return (
    <Sheet title="Return unused funds" onClose={busy ? () => {} : onClose}
      footer={<Btn variant="accent" onClick={submit} disabled={busy} style={{ width: '100%' }}>
        {busy ? 'Recording…' : 'Record return'}</Btn>}>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}
      <Banner tone="warning" title="Your balance does not change yet">
        Finance confirms the money has actually been received before this comes
        off what you are accountable for. That protects you as much as the
        company — the record shows exactly when it was handed over and who
        confirmed it.
      </Banner>
      <div style={{ height: space(2) }} />
      <Field label="Amount returned" hint={`You currently hold ${money(wallet.balance)}.`}>
        <input type="number" inputMode="decimal" min="0" step="0.01" value={amount}
          onChange={(e) => setAmount(e.target.value)}
          style={{ ...inputStyle, fontSize: 22, fontWeight: 700 }} />
      </Field>
      <Field label="How did you return it?">
        <select value={method} onChange={(e) => setMethod(e.target.value)} style={inputStyle}>
          <option value="CASH">Cash</option>
          <option value="BANK_TRANSFER">Bank transfer</option>
          <option value="CHEQUE">Cheque</option>
          <option value="MOBILE_MONEY">Mobile money</option>
        </select>
      </Field>
      <Field label="Reference (optional)" hint="Transfer reference or who you gave the cash to.">
        <input value={reference} onChange={(e) => setReference(e.target.value)} style={inputStyle} />
      </Field>
    </Sheet>
  );
}

function Reconcile({ wallet, onClose, onDone }) {
  const firstOfMonth = () => {
    const d = new Date();
    return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10);
  };
  const [start, setStart] = useState(firstOfMonth());
  const [end, setEnd] = useState(todayISO());
  const [cash, setCash] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [result, setResult] = useState(null);

  const submit = async () => {
    setErr('');
    setBusy(true);
    try {
      const r = await postJSON(`/api/wallet/wallets/${wallet.id}/reconciliations`, {
        period_start: start, period_end: end,
        declared_cash_on_hand: cash === '' ? null : Number(cash),
        note: note.trim() || null,
      });
      setResult(r);
      setBusy(false);
    } catch (e) { setErr(e.message); setBusy(false); }
  };

  if (result) {
    const variance = Number(result.variance || 0);
    return (
      <Sheet title="Reconciliation submitted" onClose={() => onDone(
        `Reconciliation ${result.reconciliation_reference} sent for review.`)}
        footer={<Btn variant="accent" style={{ width: '100%' }} onClick={() => onDone(
          `Reconciliation ${result.reconciliation_reference} sent for review.`)}>Done</Btn>}>
        <Card pad={2.5}>
          {[['Opening balance', result.opening_balance],
            ['Funds received', result.total_funded],
            ['Expenses recorded', result.total_expenses],
            ['Funds returned', result.total_returned],
            ['Closing balance', result.closing_balance]].map(([label, v]) => (
            <div key={label} style={{
              display: 'flex', justifyContent: 'space-between', padding: '7px 0',
              fontSize: 14, borderBottom: `1px solid #F1F5F9`,
            }}>
              <span style={{ color: color.textSecondary }}>{label}</span>
              <strong>{money(v)}</strong>
            </div>
          ))}
          {result.variance != null && (
            <div style={{
              display: 'flex', justifyContent: 'space-between', paddingTop: 10,
              fontSize: 14, fontWeight: 700,
              color: variance === 0 ? color.success : color.danger,
            }}>
              <span>Difference from what you hold</span>
              <span>{money(result.variance)}</span>
            </div>
          )}
        </Card>
        {variance !== 0 && result.variance != null && (
          <div style={{ marginTop: space(2) }}>
            <Banner tone="warning" title="There is a difference to explain">
              This is not an accusation — differences happen for ordinary
              reasons, like a receipt not yet entered. Management will ask you
              about it.
            </Banner>
          </div>
        )}
      </Sheet>
    );
  }

  return (
    <Sheet title="Submit reconciliation" onClose={busy ? () => {} : onClose}
      footer={<Btn variant="accent" onClick={submit} disabled={busy} style={{ width: '100%' }}>
        {busy ? 'Calculating…' : 'Calculate and submit'}</Btn>}>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}
      <Field label="Period from"><input type="date" value={start}
        onChange={(e) => setStart(e.target.value)} style={inputStyle} /></Field>
      <Field label="Period to"><input type="date" value={end} max={todayISO()}
        onChange={(e) => setEnd(e.target.value)} style={inputStyle} /></Field>
      <Field label="Cash you still physically hold"
        hint="Count it and enter the real figure. The system compares this with its own calculation and shows management the difference.">
        <input type="number" inputMode="decimal" min="0" step="0.01" value={cash}
          onChange={(e) => setCash(e.target.value)} placeholder="0.00"
          style={{ ...inputStyle, fontSize: 20, fontWeight: 700 }} />
      </Field>
      <Field label="Anything to note?">
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2}
          style={{ ...inputStyle, resize: 'vertical' }} />
      </Field>
    </Sheet>
  );
}

// ---------------------------------------------------------------------------
// Main screen
// ---------------------------------------------------------------------------

export default function StaffWallet() {
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [toast, setToast] = useState('');
  const [wallets, setWallets] = useState([]);
  const [active, setActive] = useState(0);
  const [categories, setCategories] = useState([]);
  const [inbox, setInbox] = useState({ items: [] });
  const [transactions, setTransactions] = useState([]);
  const [expenses, setExpenses] = useState([]);
  const [tab, setTab] = useState('activity');
  const [sheet, setSheet] = useState(null);

  const wallet = wallets[active];

  const loadWallets = useCallback(async () => {
    setErr('');
    try {
      const [mine, cats, box] = await Promise.all([
        getJSON('/api/wallet/me'),
        getJSON('/api/wallet/categories'),
        getJSON('/api/wallet/inbox'),
      ]);
      setWallets(mine.wallets || []);
      setCategories(cats.categories || []);
      setInbox(box || { items: [] });
    } catch (e) { setErr(e.message); }
    setLoading(false);
  }, []);

  const loadDetail = useCallback(async (walletId) => {
    if (!walletId) return;
    try {
      const [t, e] = await Promise.all([
        getJSON(`/api/wallet/wallets/${walletId}/transactions?limit=100`),
        getJSON(`/api/wallet/wallets/${walletId}/expenses?limit=100`),
      ]);
      setTransactions(t.transactions || []);
      setExpenses(e.expenses || []);
    } catch (e) { setErr(e.message); }
  }, []);

  useEffect(() => { loadWallets(); }, [loadWallets]);
  useEffect(() => { if (wallet) loadDetail(wallet.id); }, [wallet, loadDetail]);

  const refresh = async () => { await loadWallets(); if (wallet) await loadDetail(wallet.id); };

  const flash = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(''), 6000);
  };

  if (loading) {
    return <div style={{ padding: space(2) }}><Skeleton h={140} /></div>;
  }

  if (err && !wallets.length) return <ErrorBox msg={err} />;

  if (!wallets.length) {
    return (
      <Card pad={4} style={{ maxWidth: 560, margin: '0 auto', textAlign: 'center' }}>
        <Icon name="wallet" size={40} color={color.textMuted} />
        <h3 style={{ margin: `${space(2)} 0 6px`, fontSize: 17 }}>You do not have a wallet yet</h3>
        <p style={{ color: color.textSecondary, fontSize: 14, lineHeight: 1.6, margin: 0 }}>
          An administrator opens a wallet for you before you can be given
          operational funds. Once they do, everything you receive and spend
          appears here.
        </p>
      </Card>
    );
  }

  const outstanding = Number(wallet.outstanding || 0);
  const pending = Number(wallet.pending_approval || 0);

  return (
    <div style={{ maxWidth: 720, margin: '0 auto', paddingBottom: space(4) }}>
      {toast && (
        <div style={{ marginBottom: space(2) }}>
          <Banner tone="success" title="Done">{toast}</Banner>
        </div>
      )}
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      {inbox.items && inbox.items.length > 0 && (
        <div style={{ marginBottom: space(2) }}>
          {inbox.items.filter((i) => i.severity === 'HIGH').map((i, n) => (
            <Banner key={n} tone="warning" title="Needs your attention">{i.message}</Banner>
          ))}
        </div>
      )}

      {wallets.length > 1 && (
        <div style={{ display: 'flex', gap: space(1), marginBottom: space(2), flexWrap: 'wrap' }}>
          {wallets.map((w, i) => (
            <button key={w.id} onClick={() => setActive(i)} style={{
              padding: '7px 14px', borderRadius: radius.pill, fontSize: 13, fontWeight: 600,
              border: `1px solid ${i === active ? color.medical : color.borderStrong}`,
              background: i === active ? color.infoBg : '#fff',
              color: i === active ? color.royal : color.textSecondary, cursor: 'pointer',
            }}>{w.wallet_type}</button>
          ))}
        </div>
      )}

      {/* The balance, unmissable. */}
      <Card pad={3} style={{
        background: `linear-gradient(135deg, ${color.navy} 0%, ${color.royal} 100%)`,
        border: 'none', color: '#fff', marginBottom: space(2),
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <div style={{ fontSize: 12, opacity: 0.75, textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 600 }}>
              Available to spend
            </div>
            <div style={{ fontSize: 38, fontWeight: 800, letterSpacing: '-0.02em', lineHeight: 1.1, marginTop: 4 }}>
              {money(wallet.available)}
            </div>
            <div style={{ fontSize: 12.5, opacity: 0.8, marginTop: 6 }}>
              {wallet.wallet_number} · {wallet.department || 'No department'}
            </div>
          </div>
          <Chip tone={tone(wallet.status)}>{wallet.status}</Chip>
        </div>

        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: space(1.5),
          marginTop: space(3), paddingTop: space(2), borderTop: '1px solid rgba(255,255,255,0.18)',
        }}>
          {[['Received this month', wallet.funded_this_month],
            ['Spent this month', wallet.spent_this_month],
            ['Outstanding', wallet.outstanding]].map(([label, v], i) => (
            <div key={label}>
              <div style={{ fontSize: 10.5, opacity: 0.72, textTransform: 'uppercase', letterSpacing: '0.04em', fontWeight: 600 }}>{label}</div>
              <div style={{
                fontSize: 16, fontWeight: 700, marginTop: 2,
                color: i === 2 && outstanding > 0 ? '#FCA5A5' : '#fff',
              }}>{money(v)}</div>
            </div>
          ))}
        </div>

        {pending > 0 && (
          <div style={{ marginTop: space(2), fontSize: 12.5, opacity: 0.85 }}>
            {money(pending)} is awaiting approval and is already set aside from
            your available balance.
          </div>
        )}
      </Card>

      {/* Four actions, thumb-sized. */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
        gap: space(1.5), marginBottom: space(3),
      }}>
        <Btn variant="accent" icon="tag" onClick={() => setSheet('expense')}
          disabled={wallet.status !== 'ACTIVE'}>Add expense</Btn>
        <Btn variant="secondary" icon="wallet" onClick={() => setSheet('request')}
          disabled={wallet.status !== 'ACTIVE'}>Request funds</Btn>
        <Btn variant="secondary" icon="bank" onClick={() => setSheet('return')}
          disabled={Number(wallet.balance) <= 0}>Return funds</Btn>
        <Btn variant="secondary" icon="reports" onClick={() => setSheet('reconcile')}>
          Reconcile</Btn>
      </div>

      {wallet.status !== 'ACTIVE' && (
        <div style={{ marginBottom: space(2) }}>
          <Banner tone="danger" title={`This wallet is ${wallet.status.toLowerCase()}`}>
            You cannot record new spending until an administrator reactivates
            it. Any balance you hold is still yours to account for.
          </Banner>
        </div>
      )}

      <div style={{ display: 'flex', gap: space(1), marginBottom: space(2) }}>
        {[['activity', 'Transactions'], ['expenses', 'My expenses']].map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)} style={{
            padding: '8px 16px', borderRadius: radius.pill, fontSize: 13, fontWeight: 600,
            border: `1px solid ${tab === k ? color.medical : color.borderStrong}`,
            background: tab === k ? color.infoBg : '#fff',
            color: tab === k ? color.royal : color.textSecondary, cursor: 'pointer',
          }}>{label}</button>
        ))}
        <div style={{ marginLeft: 'auto' }}>
          <Btn variant="ghost" size="sm" icon="refresh" onClick={refresh}>Refresh</Btn>
        </div>
      </div>

      {tab === 'activity' && (
        <Card pad={0} style={{ overflow: 'hidden' }}>
          <div style={{ padding: space(2.5), paddingBottom: 0 }}>
            <SectionTitle>Every movement, oldest kept forever</SectionTitle>
          </div>
          <div style={{ padding: `0 ${space(2.5)} ${space(2.5)}` }}>
            <DataTable
              cols={[
                { key: 'occurred_on', label: 'Date' },
                { key: 'description', label: 'Detail', wrap: true },
                { key: 'amount', label: 'Amount', align: 'right' },
                { key: 'balance_after', label: 'Balance', align: 'right' },
              ]}
              rows={transactions}
              empty="Nothing yet. Once you are given funds, every movement appears here."
              render={(r, c) => {
                if (c.key === 'amount') {
                  const credit = r.direction === 'CREDIT';
                  return (
                    <span style={{ fontWeight: 700, color: credit ? color.success : color.text }}>
                      {credit ? '+' : '−'}{money(r.amount)}
                    </span>
                  );
                }
                if (c.key === 'balance_after') {
                  return <span style={{ color: color.textSecondary }}>{money(r.balance_after)}</span>;
                }
                if (c.key === 'description') {
                  return (
                    <div>
                      <div>{r.description}</div>
                      {r.entry_type === 'REVERSAL' && (
                        <div style={{ marginTop: 3 }}><Chip tone="neutral">Correction</Chip></div>
                      )}
                    </div>
                  );
                }
                return r[c.key];
              }}
            />
          </div>
        </Card>
      )}

      {tab === 'expenses' && (
        <Card pad={0} style={{ overflow: 'hidden' }}>
          <div style={{ padding: space(2.5), paddingBottom: 0 }}>
            <SectionTitle>What you have recorded</SectionTitle>
          </div>
          <div style={{ padding: `0 ${space(2.5)} ${space(2.5)}` }}>
            <DataTable
              cols={[
                { key: 'spent_on', label: 'Date' },
                { key: 'purpose', label: 'Purpose', wrap: true },
                { key: 'category', label: 'Category' },
                { key: 'amount', label: 'Amount', align: 'right' },
                { key: 'status', label: 'Status' },
                { key: 'receipts', label: 'Receipt', align: 'center' },
              ]}
              rows={expenses}
              empty="No expenses recorded yet."
              render={(r, c) => {
                if (c.key === 'amount') return <strong>{money(r.amount)}</strong>;
                if (c.key === 'status') {
                  return (
                    <div>
                      <Chip tone={tone(r.status)}>{r.status}</Chip>
                      {r.status === 'REJECTED' && r.decision_note && (
                        <div style={{ marginTop: 4, fontSize: 11.5, color: color.danger, whiteSpace: 'normal', maxWidth: 220 }}>
                          {r.decision_note}
                        </div>
                      )}
                    </div>
                  );
                }
                if (c.key === 'receipts') {
                  return r.receipts > 0
                    ? <Icon name="check" size={16} color={color.success} />
                    : <span style={{ color: color.warning, fontSize: 11.5, fontWeight: 600 }}>None</span>;
                }
                return r[c.key];
              }}
            />
          </div>
        </Card>
      )}

      {sheet === 'expense' && (
        <AddExpense wallet={wallet} categories={categories}
          onClose={() => setSheet(null)}
          onDone={async (created, warning) => {
            setSheet(null);
            flash(warning || (created.auto_approved
              ? `${money(created.amount)} recorded and approved (${created.expense_reference}).`
              : `${money(created.amount)} recorded (${created.expense_reference}). `
                + `It needs ${created.required_tier === 'MANAGEMENT' ? 'management' : 'supervisor'} approval.`));
            await refresh();
          }} />
      )}
      {sheet === 'request' && (
        <RequestFunds wallet={wallet} onClose={() => setSheet(null)}
          onDone={async (msg) => { setSheet(null); flash(msg); await refresh(); }} />
      )}
      {sheet === 'return' && (
        <ReturnFunds wallet={wallet} onClose={() => setSheet(null)}
          onDone={async (msg) => { setSheet(null); flash(msg); await refresh(); }} />
      )}
      {sheet === 'reconcile' && (
        <Reconcile wallet={wallet} onClose={() => setSheet(null)}
          onDone={async (msg) => { setSheet(null); flash(msg); await refresh(); }} />
      )}
    </div>
  );
}
