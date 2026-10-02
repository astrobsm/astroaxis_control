// Salary & Payroll — choosing who to pay, paying them, and saying so.
//
// Mounted from AppMain as the Salary & Payroll module.
//
// WHY THIS REPLACED THE OLD SCREEN
// --------------------------------
// The screen this supersedes read /api/staff/payroll/dashboard, which returns
// `net_pay = gross_pay  // No deductions for now`. Every figure on it was a
// gross. Building "mark as paid" on top of that would have meant a payment
// workflow that pays people their gross and withholds no PAYE, no pension, no
// NHF and no NHIA — and under-deducted PAYE is recoverable from the COMPANY,
// not from the employee. The same calculator was already retired in the
// backend for exactly this reason.
//
// So this screen drives /api/payroll instead: the engine with rate configs,
// progressive tax bands and statutory deductions, which produces a real net.
//
// THE SEQUENCE, AND WHY IT HAS FOUR STEPS
// ---------------------------------------
//   1. Choose a period      — see who actually worked it
//   2. Process selected     — creates a DRAFT run. Nobody is paid.
//   3. Approve              — posts the debt: Dr Salaries, Cr Salary Payable
//   4. Record payment       — posts the settlement: Dr Payable, Cr Bank
//
// Steps 3 and 4 are separate because they are different facts. "We owe our
// staff this" and "we have paid our staff" are not the same statement, and
// until this screen existed the system could only ever make the first one.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
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
const getJSON = (u) => req(u);
const postJSON = (u, b) => req(u, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(b || {}),
});

async function download(url, filename) {
  const res = await authedFetch(url);
  if (!res.ok) {
    let d = `Download failed (${res.status})`;
    try { const j = await res.json(); d = j.detail || d; } catch { /* keep */ }
    throw new Error(d);
  }
  const blob = await res.blob();
  const href = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = href; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  window.URL.revokeObjectURL(href);
}

const inputStyle = {
  padding: '8px 11px', borderRadius: radius.sm, fontSize: 13.5,
  border: `1px solid ${color.borderStrong}`, fontFamily: 'inherit',
  background: '#fff', color: color.text, boxSizing: 'border-box',
};

const monthBounds = () => {
  const n = new Date();
  return {
    start: new Date(n.getFullYear(), n.getMonth(), 1).toISOString().slice(0, 10),
    end: new Date(n.getFullYear(), n.getMonth() + 1, 0).toISOString().slice(0, 10),
  };
};

const STATE_TONE = {
  PAID: 'success', APPROVED: 'info', DRAFT: 'warning',
  NOT_PROCESSED: 'neutral', CANCELLED: 'danger',
};
const STATE_LABEL = {
  PAID: 'Paid', APPROVED: 'Approved — not yet paid', DRAFT: 'Draft',
  NOT_PROCESSED: 'Not processed', CANCELLED: 'Cancelled',
};

function Tile({ label, value, tone = 'default', sub }) {
  const fg = { success: color.success, warning: '#B45309',
    danger: color.danger, default: color.navy }[tone];
  return (
    <div style={{ background: '#F8FAFC', borderRadius: radius.md,
      padding: '13px 15px', border: `1px solid ${color.border}` }}>
      <div style={{ fontSize: 17, fontWeight: 800, color: fg }}>{value}</div>
      <div style={{ fontSize: 11.5, color: color.textSecondary, marginTop: 3 }}>
        {label}
      </div>
      {sub && (
        <div style={{ fontSize: 10.5, color: color.textMuted, marginTop: 2 }}>
          {sub}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Recording a payment
// ---------------------------------------------------------------------------

function PaymentDialog({ runId, runNumber, payslips, onClose, onDone }) {
  const [form, setForm] = useState({
    paid_on: new Date().toISOString().slice(0, 10),
    method: 'BANK_TRANSFER', bank_reference: '', notes: '',
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const total = payslips.reduce((a, p) => a + Number(p.net_pay || 0), 0);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const submit = async () => {
    setBusy(true); setErr('');
    try {
      const result = await postJSON(`/api/payroll/runs/${runId}/payments`, {
        payslip_ids: payslips.map((p) => p.payslip_id),
        paid_on: form.paid_on, method: form.method,
        bank_reference: form.bank_reference || null,
        notes: form.notes || null,
      });
      onDone(result);
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
        background: '#fff', borderRadius: radius.lg, width: 'min(560px, 100%)',
        maxHeight: '90vh', overflow: 'auto', padding: space(3),
      }}>
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 800,
          color: color.navy }}>Record a payment</h3>
        <div style={{ fontSize: 12.5, color: color.textSecondary,
          marginTop: 5, lineHeight: 1.6 }}>
          {payslips.length} staff on run {runNumber}. This records that the
          money has left and clears the same amount from Staff Salary Payable.
        </div>

        <div style={{ background: color.navy, color: '#fff',
          borderRadius: radius.md, padding: '12px 15px',
          margin: `${space(2)} 0` }}>
          <div style={{ fontSize: 11, opacity: 0.85 }}>Total being paid</div>
          <div style={{ fontSize: 22, fontWeight: 800 }}>{naira(total)}</div>
        </div>

        {err && <ErrorBox msg={err} />}

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr',
          gap: 12 }}>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600,
              color: color.textSecondary }}>Date the money left</label>
            <input type="date" style={{ ...inputStyle, width: '100%', marginTop: 5 }}
              value={form.paid_on} onChange={set('paid_on')} />
          </div>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600,
              color: color.textSecondary }}>Method</label>
            <select style={{ ...inputStyle, width: '100%', marginTop: 5 }}
              value={form.method} onChange={set('method')}>
              <option value="BANK_TRANSFER">Bank transfer</option>
              <option value="CHEQUE">Cheque</option>
              <option value="MOBILE_MONEY">Mobile money</option>
              <option value="CASH">Cash</option>
              <option value="OTHER">Other</option>
            </select>
          </div>
        </div>

        <div style={{ marginTop: 12 }}>
          <label style={{ fontSize: 12, fontWeight: 600,
            color: color.textSecondary }}>Bank reference</label>
          <input style={{ ...inputStyle, width: '100%', marginTop: 5 }}
            value={form.bank_reference} onChange={set('bank_reference')}
            placeholder="e.g. FT26100212345" />
          <div style={{ fontSize: 11, color: color.textMuted, marginTop: 4 }}>
            Whatever ties this to the bank statement. Without it the payment
            cannot be reconciled to the account later.
          </div>
        </div>

        <div style={{ marginTop: 12 }}>
          <label style={{ fontSize: 12, fontWeight: 600,
            color: color.textSecondary }}>Notes</label>
          <textarea style={{ ...inputStyle, width: '100%', marginTop: 5,
            minHeight: 60, resize: 'vertical' }}
            value={form.notes} onChange={set('notes')} />
        </div>

        {form.method === 'CASH' && (
          <div style={{ marginTop: 12 }}>
            <Banner tone="warning" title="Cash leaves the cash account">
              A cash payment is posted against Petty Cash / Cash, not the bank,
              so the bank reconciliation stays correct.
            </Banner>
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, marginTop: space(3),
          justifyContent: 'flex-end' }}>
          <Btn variant="secondary" onClick={onClose}>Cancel</Btn>
          <Btn onClick={submit} disabled={busy || !form.paid_on}>
            {busy ? 'Recording…' : `Record payment of ${naira(total)}`}
          </Btn>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

export default function PayrollDesk({ notify }) {
  const [period, setPeriod] = useState(monthBounds);
  const [applied, setApplied] = useState(monthBounds);
  const [data, setData] = useState(null);
  const [report, setReport] = useState(null);
  const [showIdle, setShowIdle] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState('');
  const [paying, setPaying] = useState(null);

  const say = useCallback((m, kind) => {
    if (notify) notify(m, kind); else if (kind === 'error') setErr(m);
  }, [notify]);

  const load = useCallback(async (p) => {
    setBusy('loading');
    try {
      const q = `period_start=${p.start}&period_end=${p.end}`;
      const [elig, rep] = await Promise.all([
        getJSON(`/api/payroll/eligibility?${q}`),
        getJSON(`/api/payroll/payment-report?${q}`),
      ]);
      setData(elig); setReport(rep); setApplied(p); setErr('');
      setSelected(new Set());
    } catch (e) { setErr(e.message); }
    finally { setBusy(''); }
  }, []);

  useEffect(() => { load(monthBounds()); }, [load]);

  const rows = useMemo(() => {
    if (!data) return [];
    return showIdle ? [...data.worked, ...data.not_worked] : data.worked;
  }, [data, showIdle]);

  const toggle = (id) => setSelected((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });

  const selectedRows = rows.filter((r) => selected.has(r.staff_id));
  const toProcess = selectedRows.filter((r) => r.state === 'NOT_PROCESSED');
  const toPay = selectedRows.filter(
    (r) => r.state === 'APPROVED' && r.payslip_id);
  const draftRuns = [...new Set(selectedRows
    .filter((r) => r.state === 'DRAFT').map((r) => r.run_id))];

  const q = `period_start=${applied.start}&period_end=${applied.end}`;

  // --- actions -----------------------------------------------------------

  const processSelected = async () => {
    setBusy('processing');
    try {
      const run = await postJSON('/api/payroll/runs', {
        period_start: applied.start, period_end: applied.end,
        staff_ids: toProcess.map((r) => r.staff_id),
      });
      if (!run.staff_paid) {
        say(`Nothing calculated. Skipped: ${
          run.staff_skipped?.join(', ') || 'no pay structure configured'}`,
        'error');
      } else {
        say(`Draft run ${run.run_number} — ${run.staff_paid} staff, net `
          + `${naira(run.net_total)}. Approve it to book the liability.`,
        'success');
      }
      await load(applied);
    } catch (e) { say(e.message, 'error'); setErr(e.message); }
    finally { setBusy(''); }
  };

  const approve = async (runId) => {
    setBusy('approving');
    try {
      const r = await postJSON(`/api/payroll/runs/${runId}/approve`,
        { approved_by: 'Payroll' });
      say(`Run ${r.run_number} approved${r.posted ? ' and posted' : ''}. `
        + 'Staff are now owed this money; record the payment when it leaves.',
      'success');
      await load(applied);
    } catch (e) { say(e.message, 'error'); setErr(e.message); }
    finally { setBusy(''); }
  };

  const bulkPayslips = async (onlySelected) => {
    setBusy('payslips');
    try {
      const ids = onlySelected
        ? selectedRows.filter((r) => r.payslip_id).map((r) => r.payslip_id)
        : null;
      await download(
        `/api/payroll/payslips.pdf?${q}${ids ? `&payslip_ids=${ids.join(',')}` : ''}`,
        `payslips_${applied.start}_${applied.end}.pdf`);
      say('Payslips downloaded — one page per person.', 'success');
    } catch (e) { say(e.message, 'error'); }
    finally { setBusy(''); }
  };

  const downloadReport = async () => {
    setBusy('report');
    try {
      await download(`/api/payroll/payment-report.pdf?${q}`,
        `payroll_report_${applied.start}_${applied.end}.pdf`);
      say('Payment report downloaded.', 'success');
    } catch (e) { say(e.message, 'error'); }
    finally { setBusy(''); }
  };

  // --- render ------------------------------------------------------------

  const allSelectable = rows.filter((r) => r.state !== 'PAID');
  const allChecked = allSelectable.length > 0
    && allSelectable.every((r) => selected.has(r.staff_id));

  return (
    <div>
      {/* period */}
      <Card style={{ marginBottom: space(2) }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end',
          flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 11.5, fontWeight: 600,
              color: color.textSecondary, marginBottom: 4 }}>Pay period from</div>
            <input type="date" style={inputStyle} value={period.start}
              onChange={(e) => setPeriod((p) => ({ ...p, start: e.target.value }))} />
          </div>
          <div>
            <div style={{ fontSize: 11.5, fontWeight: 600,
              color: color.textSecondary, marginBottom: 4 }}>to</div>
            <input type="date" style={inputStyle} value={period.end}
              onChange={(e) => setPeriod((p) => ({ ...p, end: e.target.value }))} />
          </div>
          <Btn onClick={() => load(period)} disabled={busy === 'loading'}>
            {busy === 'loading' ? 'Loading…' : 'Load period'}
          </Btn>
          <Btn variant="secondary" onClick={() => {
            const m = monthBounds(); setPeriod(m); load(m);
          }}>This month</Btn>
        </div>
      </Card>

      {err && <ErrorBox msg={err} />}

      {/* the money */}
      {report && (
        <div style={{ display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
          gap: 10, marginBottom: space(2) }}>
          <Tile label="Net payable to staff" value={naira(report.net_total)}
            sub={`${report.staff_count} processed`} />
          <Tile label="Already paid" value={naira(report.paid_total)}
            tone="success" sub={`${report.paid_count} staff`} />
          <Tile label="Still outstanding" value={naira(report.outstanding_total)}
            tone={report.outstanding_total > 0 ? 'warning' : 'default'}
            sub={`${report.unpaid_count} staff`} />
          <Tile label="Employer cost on top"
            value={naira(report.employer_cost_total)}
            sub="never deducted from staff" />
          <Tile label="Worked this period" value={data ? data.worked_count : '—'}
            sub={data ? `${data.not_worked_count} with no attendance` : ''} />
        </div>
      )}

      {/* bulk actions */}
      <Card style={{ marginBottom: space(2) }}>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap',
          alignItems: 'center' }}>
          <span style={{ fontSize: 12.5, fontWeight: 600,
            color: color.textSecondary }}>
            {selected.size} selected
          </span>

          {toProcess.length > 0 && (
            <Btn size="sm" onClick={processSelected} disabled={!!busy}>
              {busy === 'processing' ? 'Processing…'
                : `Process ${toProcess.length} staff`}
            </Btn>
          )}

          {draftRuns.length === 1 && (
            <Btn size="sm" variant="accent" onClick={() => approve(draftRuns[0])}
              disabled={!!busy}>
              {busy === 'approving' ? 'Approving…' : 'Approve draft run'}
            </Btn>
          )}

          {toPay.length > 0 && isAdmin() && (
            <Btn size="sm" variant="accent"
              onClick={() => setPaying({
                runId: toPay[0].run_id, runNumber: toPay[0].run_number,
                payslips: toPay.filter((r) => r.run_id === toPay[0].run_id),
              })}
              disabled={!!busy}>
              Mark {toPay.filter((r) => r.run_id === toPay[0].run_id).length} as paid
            </Btn>
          )}

          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8,
            flexWrap: 'wrap' }}>
            <Btn size="sm" variant="secondary" disabled={!!busy}
              onClick={() => bulkPayslips(selected.size > 0)}>
              {busy === 'payslips' ? 'Generating…'
                : selected.size > 0
                  ? `Payslips for ${selected.size} selected`
                  : 'All payslips (PDF)'}
            </Btn>
            <Btn size="sm" variant="secondary" onClick={downloadReport}
              disabled={!!busy}>
              {busy === 'report' ? 'Generating…' : 'Payment report (PDF)'}
            </Btn>
          </div>
        </div>

        {toPay.length > 0 && !isAdmin() && (
          <div style={{ fontSize: 11.5, color: color.textSecondary,
            marginTop: 8 }}>
            Recording a payment is admin-only. Everything else here is open
            to you.
          </div>
        )}
      </Card>

      {/* who worked */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10,
        marginBottom: 10, flexWrap: 'wrap' }}>
        <div style={{ fontSize: 13.5, fontWeight: 700, color: color.navy }}>
          {showIdle ? 'All active staff'
            : `Staff who worked ${applied.start} to ${applied.end}`}
        </div>
        {data && data.not_worked_count > 0 && (
          <button onClick={() => setShowIdle((v) => !v)} style={{
            padding: '5px 12px', borderRadius: radius.pill, fontSize: 11.5,
            fontWeight: 600, cursor: 'pointer', background: '#fff',
            border: `1px solid ${color.borderStrong}`, color: color.royal,
          }}>
            {showIdle ? 'Show only those who worked'
              : `Also show ${data.not_worked_count} with no attendance`}
          </button>
        )}
      </div>

      {!data ? <SkeletonCards n={3} /> : rows.length === 0 ? (
        <Banner tone="info" title="Nobody worked in this period">
          No completed attendance was recorded between these dates. If that is
          wrong, the gap is in attendance rather than payroll — and you can
          still show everyone with the button above.
        </Banner>
      ) : (
        <div style={{ overflowX: 'auto', border: `1px solid ${color.border}`,
          borderRadius: radius.md, background: '#fff' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse',
            fontSize: 13 }}>
            <thead>
              <tr style={{ background: '#FbFcFe' }}>
                <th style={{ padding: '10px 12px', width: 34 }}>
                  <input type="checkbox" checked={allChecked}
                    aria-label="Select everyone payable"
                    onChange={() => setSelected(allChecked ? new Set()
                      : new Set(allSelectable.map((r) => r.staff_id)))} />
                </th>
                {['Employee', 'Hours', 'Days', 'Gross', 'Deductions',
                  'Net pay', 'Status'].map((h, i) => (
                  <th key={h} style={{
                    padding: '10px 12px', fontSize: 11,
                    textAlign: i >= 1 && i <= 5 ? 'right' : 'left',
                    color: color.textSecondary, fontWeight: 600,
                    textTransform: 'uppercase', letterSpacing: '0.04em',
                    borderBottom: `1px solid ${color.border}`,
                    whiteSpace: 'nowrap',
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const idle = r.hours_worked <= 0;
                return (
                  <tr key={r.staff_id} style={{
                    background: selected.has(r.staff_id) ? color.royalBg
                      : r.state === 'PAID' ? '#F6FEF9' : '#fff',
                    opacity: idle ? 0.72 : 1,
                  }}>
                    <td style={{ padding: '9px 12px',
                      borderBottom: '1px solid #F1F5F9' }}>
                      <input type="checkbox"
                        checked={selected.has(r.staff_id)}
                        disabled={r.state === 'PAID'}
                        aria-label={`Select ${r.first_name} ${r.last_name}`}
                        onChange={() => toggle(r.staff_id)} />
                    </td>
                    <td style={{ padding: '9px 12px',
                      borderBottom: '1px solid #F1F5F9' }}>
                      <div style={{ fontWeight: 600 }}>
                        {r.first_name} {r.last_name}
                        {r.display_hidden && (
                          <span style={{ marginLeft: 7, fontSize: 10,
                            fontWeight: 700, color: '#B45309',
                            background: '#FFFBEB', padding: '2px 7px',
                            borderRadius: 10 }}
                            title={`Hidden from staff lists: ${r.hidden_reason}`}>
                            HIDDEN
                          </span>
                        )}
                      </div>
                      <div style={{ fontSize: 11, color: color.textSecondary }}>
                        {r.employee_id} · {r.position || 'Staff'}
                        {r.payment_mode === 'hourly'
                          ? ` · ${naira(r.hourly_rate)}/hr` : ''}
                      </div>
                    </td>
                    {[
                      r.hours_worked.toFixed(1),
                      r.days_worked,
                      r.gross_pay ? naira(r.gross_pay) : '—',
                      r.total_deductions ? naira(r.total_deductions) : '—',
                    ].map((v, i) => (
                      <td key={i} style={{ padding: '9px 12px',
                        textAlign: 'right', borderBottom: '1px solid #F1F5F9',
                        whiteSpace: 'nowrap',
                        color: idle && i < 2 ? color.textMuted : 'inherit' }}>
                        {v}
                      </td>
                    ))}
                    <td style={{ padding: '9px 12px', textAlign: 'right',
                      borderBottom: '1px solid #F1F5F9', fontWeight: 700,
                      whiteSpace: 'nowrap',
                      color: r.net_pay ? color.success : color.textMuted }}>
                      {r.net_pay ? naira(r.net_pay) : '—'}
                    </td>
                    <td style={{ padding: '9px 12px',
                      borderBottom: '1px solid #F1F5F9', whiteSpace: 'nowrap' }}>
                      <Chip tone={STATE_TONE[r.state] || 'neutral'}>
                        {STATE_LABEL[r.state] || r.state}
                      </Chip>
                      {r.paid_at && (
                        <div style={{ fontSize: 10.5, color: color.textSecondary,
                          marginTop: 3 }}>
                          {new Date(r.paid_at).toLocaleDateString()}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {data && (
        <div style={{ fontSize: 11.5, color: color.textSecondary,
          marginTop: 10, lineHeight: 1.7, maxWidth: 760 }}>
          {data.note}
        </div>
      )}

      {/* payments already recorded */}
      {report && report.batches && report.batches.length > 0 && (
        <Card style={{ marginTop: space(3) }}>
          <div style={{ fontSize: 13.5, fontWeight: 700, color: color.navy,
            marginBottom: 10 }}>Payments recorded for this period</div>
          {report.batches.map((b) => (
            <div key={b.batch_number} style={{
              display: 'flex', gap: 10, alignItems: 'baseline',
              padding: '8px 0', borderBottom: `1px solid ${color.border}`,
              flexWrap: 'wrap', fontSize: 12.5,
            }}>
              <div style={{ fontWeight: 600 }}>{b.paid_on}</div>
              <div style={{ color: color.textSecondary }}>
                {b.batch_number} · {b.method.replace(/_/g, ' ').toLowerCase()}
                {b.bank_reference ? ` · ref ${b.bank_reference}` : ''}
                {b.paid_by_name ? ` · recorded by ${b.paid_by_name}` : ''}
              </div>
              <div style={{ marginLeft: 'auto', fontWeight: 700 }}>
                {naira(b.total_net)}
                <span style={{ fontWeight: 400, color: color.textSecondary,
                  marginLeft: 6 }}>({b.staff_count} staff)</span>
              </div>
            </div>
          ))}
        </Card>
      )}

      {paying && (
        <PaymentDialog
          runId={paying.runId} runNumber={paying.runNumber}
          payslips={paying.payslips}
          onClose={() => setPaying(null)}
          onDone={(result) => {
            setPaying(null);
            say(`${result.staff_count} staff marked paid — ${
              naira(result.total_net)} on batch ${result.batch_number}.`
              + (result.outstanding_payslips
                ? ` ${result.outstanding_payslips} still outstanding on this run.`
                : ' This run is now fully paid.'),
            'success');
            load(applied);
          }} />
      )}
    </div>
  );
}
