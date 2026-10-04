// Who to contact today, and why.
//
// Mounted from AppMain when activeModule === 'opportunities'.
//
// THE POINT OF THIS SCREEN
// ------------------------
// A sales officer opens it on Monday morning and works down it. That is the
// whole design brief, and it rules out most of what a dashboard usually does:
// there are no charts, no trend lines and no totals that nobody acts on. Every
// row is a customer, a reason, and a button that records what happened.
//
// WHY EVERY ROW SHOWS ITS WORKING
// -------------------------------
// "Ring Hospital A" is an instruction. "Usually orders every 30 days, 37 days
// since the last one" is a reason someone can act on, argue with, or dismiss
// as wrong — and being able to dismiss it as wrong is what stops the list
// being quietly ignored instead.
//
// NOTHING HERE IS SENT ANYWHERE
// -----------------------------
// This screen produces no messages. The queue is worked by people. Whether
// any of it is ever sent automatically is a decision that has not been taken.

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

const TYPE_LABEL = {
  UNPAID_INVOICE: 'Payment due',
  REORDER_DUE: 'Reorder due',
  DORMANT: 'Gone quiet',
  HIGH_VALUE_QUIET: 'Worth a call',
  CROSS_SELL: 'Cross-sell',
  SATISFACTION_CHECK: 'Follow-up',
};
const TYPE_TONE = {
  UNPAID_INVOICE: 'danger',
  REORDER_DUE: 'warning',
  DORMANT: 'warning',
  HIGH_VALUE_QUIET: 'info',
  CROSS_SELL: 'success',
  SATISFACTION_CHECK: 'neutral',
};

const inputStyle = {
  padding: '8px 11px', borderRadius: radius.sm, fontSize: 13.5,
  border: `1px solid ${color.borderStrong}`, fontFamily: 'inherit',
  background: '#fff', color: color.text, boxSizing: 'border-box',
};

function ActionDialog({ item, onClose, onDone }) {
  const [outcome, setOutcome] = useState('CONTACTED');
  const [note, setNote] = useState('');
  const [days, setDays] = useState(14);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const submit = async () => {
    setBusy(true); setErr('');
    try {
      const result = await req('/api/opportunities/actions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          opportunity_key: item.key, outcome,
          note: note.trim() || null,
          snooze_days: outcome === 'SNOOZED' ? Number(days) : null,
        }),
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
        background: '#fff', borderRadius: radius.lg, width: 'min(520px, 100%)',
        maxHeight: '90vh', overflow: 'auto', padding: space(3),
      }}>
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 800,
          color: color.navy }}>{item.customer_name}</h3>
        <div style={{ fontSize: 12.5, color: color.textSecondary, marginTop: 5,
          lineHeight: 1.6 }}>{item.reason}</div>

        {err && <ErrorBox msg={err} />}

        <div style={{ marginTop: space(2) }}>
          <label style={{ fontSize: 12, fontWeight: 600,
            color: color.textSecondary }}>What happened?</label>
          <select style={{ ...inputStyle, width: '100%', marginTop: 5 }}
            value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            <option value="CONTACTED">I contacted them</option>
            <option value="CONVERTED">They ordered / paid</option>
            <option value="DECLINED">They said no</option>
            <option value="NOT_RELEVANT">This was not a real opportunity</option>
            <option value="SNOOZED">Remind me later</option>
          </select>
        </div>

        {outcome === 'SNOOZED' && (
          <div style={{ marginTop: 12 }}>
            <label style={{ fontSize: 12, fontWeight: 600,
              color: color.textSecondary }}>Come back in</label>
            <select style={{ ...inputStyle, width: '100%', marginTop: 5 }}
              value={days} onChange={(e) => setDays(e.target.value)}>
              {[3, 7, 14, 30, 60, 90].map((d) => (
                <option key={d} value={d}>{d} days</option>
              ))}
            </select>
            <div style={{ fontSize: 11, color: color.textMuted, marginTop: 4 }}>
              There is no dismiss-forever. If it is still true then, it comes
              back.
            </div>
          </div>
        )}

        {outcome === 'NOT_RELEVANT' && (
          <div style={{ marginTop: 12 }}>
            <Banner tone="info" title="This is the useful answer">
              If a kind of suggestion is repeatedly wrong, that shows up in the
              performance figures and we stop surfacing it. Say why below if
              you can.
            </Banner>
          </div>
        )}

        <div style={{ marginTop: 12 }}>
          <label style={{ fontSize: 12, fontWeight: 600,
            color: color.textSecondary }}>Note (optional)</label>
          <textarea style={{ ...inputStyle, width: '100%', marginTop: 5,
            minHeight: 64, resize: 'vertical' }}
            value={note} onChange={(e) => setNote(e.target.value)} />
        </div>

        <div style={{ fontSize: 11.5, color: color.textSecondary,
          marginTop: 12, lineHeight: 1.6 }}>
          Recorded against your name and kept permanently. It cannot be edited
          afterwards &mdash; a correction is another entry.
        </div>

        <div style={{ display: 'flex', gap: 10, marginTop: space(3),
          justifyContent: 'flex-end' }}>
          <Btn variant="secondary" onClick={onClose}>Cancel</Btn>
          <Btn onClick={submit} disabled={busy}>
            {busy ? 'Saving…' : 'Record'}
          </Btn>
        </div>
      </div>
    </div>
  );
}

function Row({ item, onAction }) {
  return (
    <div style={{
      background: '#fff', border: `1px solid ${color.border}`,
      borderRadius: radius.md, padding: '13px 15px', marginBottom: 8,
      display: 'flex', gap: 14, alignItems: 'flex-start', flexWrap: 'wrap',
    }}>
      <div style={{ minWidth: 220, flex: 1 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center',
          flexWrap: 'wrap' }}>
          <span style={{ fontSize: 14.5, fontWeight: 700, color: color.navy }}>
            {item.customer_name}
          </span>
          <Chip tone={TYPE_TONE[item.type]}>{TYPE_LABEL[item.type]}</Chip>
          {item.snoozed_until && <Chip tone="neutral">Snoozed</Chip>}
        </div>

        {/* The working. Without this the row is just an instruction. */}
        <div style={{ fontSize: 13, color: color.text, marginTop: 5,
          lineHeight: 1.55 }}>{item.reason}</div>

        <div style={{ fontSize: 11.5, color: color.textSecondary, marginTop: 4 }}>
          {item.customer_code}
          {item.phone ? ` · ${item.phone}` : ''}
          {item.detail && item.detail.basis
            ? ` · ${item.detail.basis}` : ''}
        </div>
      </div>

      <div style={{ textAlign: 'right', minWidth: 130 }}>
        {item.potential_value > 0 && (
          <>
            <div style={{ fontSize: 15, fontWeight: 800, color: color.navy }}>
              {naira(item.potential_value)}
            </div>
            <div style={{ fontSize: 10.5, color: color.textMuted,
              marginBottom: 7 }}>
              {item.type === 'UNPAID_INVOICE' ? 'owed' : 'estimate'}
            </div>
          </>
        )}
        <div style={{ fontSize: 12, fontWeight: 600, color: color.royal,
          marginBottom: 7 }}>{item.recommended_action}</div>
        <Btn size="sm" onClick={() => onAction(item)}>Record outcome</Btn>
      </div>
    </div>
  );
}

export default function Opportunities({ notify }) {
  const [data, setData] = useState(null);
  const [perf, setPerf] = useState(null);
  const [limit, setLimit] = useState(20);
  const [priority, setPriority] = useState('');
  const [acting, setActing] = useState(null);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    try {
      const q = `limit=${limit}${priority ? `&priority=${priority}` : ''}`;
      const [queue, performance] = await Promise.all([
        req(`/api/opportunities?${q}`),
        req('/api/opportunities/performance?days=90').catch(() => null),
      ]);
      setData(queue); setPerf(performance); setErr('');
    } catch (e) { setErr(e.message); }
  }, [limit, priority]);

  useEffect(() => { load(); }, [load]);

  const say = (m, kind) => { if (notify) notify(m, kind); };

  if (err) return <ErrorBox msg={err} />;
  if (!data) return <SkeletonCards n={4} />;

  const commercial = data.by_priority.COMMERCIAL || { count: 0, value: 0 };
  const relationship = data.by_priority.RELATIONSHIP || { count: 0, value: 0 };

  return (
    <div>
      {/* The two numbers the morning is actually about */}
      <div style={{ display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))',
        gap: 10, marginBottom: space(2) }}>
        <div style={{ background: color.navy, color: '#fff',
          borderRadius: radius.md, padding: '15px 17px' }}>
          <div style={{ fontSize: 21, fontWeight: 800 }}>
            {naira(commercial.value)}
          </div>
          <div style={{ fontSize: 11.5, opacity: 0.85, marginTop: 3 }}>
            across {commercial.count} commercial opportunities
          </div>
        </div>
        <div style={{ background: '#F8FAFC', border: `1px solid ${color.border}`,
          borderRadius: radius.md, padding: '15px 17px' }}>
          <div style={{ fontSize: 21, fontWeight: 800, color: color.navy }}>
            {relationship.count}
          </div>
          <div style={{ fontSize: 11.5, color: color.textSecondary,
            marginTop: 3 }}>relationship follow-ups</div>
        </div>
        {perf && perf.total_actioned > 0 && (
          <div style={{ background: '#F8FAFC',
            border: `1px solid ${color.border}`, borderRadius: radius.md,
            padding: '15px 17px' }}>
            <div style={{ fontSize: 21, fontWeight: 800,
              color: color.success }}>{perf.conversion_rate}%</div>
            <div style={{ fontSize: 11.5, color: color.textSecondary,
              marginTop: 3 }}>
              converted of {perf.total_actioned} worked, 90 days
            </div>
          </div>
        )}
      </div>

      {/* controls */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center',
        marginBottom: space(2), flexWrap: 'wrap' }}>
        {[['', 'Everything'], ['COMMERCIAL', 'Commercial'],
          ['RELATIONSHIP', 'Relationship']].map(([v, label]) => (
          <button key={v} onClick={() => setPriority(v)} style={{
            padding: '6px 13px', borderRadius: radius.pill, fontSize: 12.5,
            fontWeight: 600, cursor: 'pointer',
            border: `1px solid ${priority === v ? color.medical : color.borderStrong}`,
            background: priority === v ? color.infoBg : '#fff',
            color: priority === v ? color.royal : color.textSecondary,
          }}>{label}</button>
        ))}
        <select style={{ ...inputStyle, width: 'auto', marginLeft: 'auto' }}
          value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
          {[10, 20, 50, 100].map((n) => (
            <option key={n} value={n}>Top {n}</option>
          ))}
        </select>
        <Btn size="sm" variant="secondary" onClick={load}>Refresh</Btn>
      </div>

      {data.items.length === 0 ? (
        <Banner tone="success" title="Nothing needs a call today">
          No overdue invoices, no customer past their usual reorder, nobody
          gone quiet. That is a good morning, not a broken screen.
        </Banner>
      ) : (
        data.items.map((item) => (
          <Row key={item.key} item={item} onAction={setActing} />
        ))
      )}

      {data.not_shown > 0 && (
        <div style={{ fontSize: 12.5, color: color.textSecondary,
          textAlign: 'center', padding: space(2) }}>
          {data.not_shown} more below the top {data.shown}. Show more above.
        </div>
      )}

      <div style={{ fontSize: 11.5, color: color.textSecondary,
        marginTop: space(2), lineHeight: 1.7, maxWidth: 760 }}>
        {data.note}
        {data.snoozed_hidden > 0
          && ` ${data.snoozed_hidden} snoozed.`}
        {data.recently_actioned > 0
          && ` ${data.recently_actioned} worked in the last 7 days.`}
      </div>

      {acting && (
        <ActionDialog item={acting} onClose={() => setActing(null)}
          onDone={(r) => {
            setActing(null);
            say(r.note || 'Recorded.', 'success');
            load();
          }} />
      )}
    </div>
  );
}
