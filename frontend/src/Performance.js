// Distributor performance — run-rate, history, reviews, scorecard.
//
// THE TRAFFIC LIGHT THIS SCREEN WILL NOT SHOW
// -------------------------------------------
// A coloured band for a month that has barely started. Four days into January a
// distributor with one slow week projects to miss by 60%, and a red light gets
// them phoned about what was never evidence of anything.
//
// So when the server returns TOO_EARLY this renders as plain text saying how
// much of the month has actually passed — not a grey dot in the same row as the
// green and red ones, because a grey dot in a traffic light still reads as a
// verdict. The absence of a light is the message.
//
// And there is no overall score anywhere. Performance, compliance and evidence
// quality are three readings; averaging them would let a good sales month
// outvote an expired licence.

import React, { useCallback, useEffect, useState } from 'react';
import { authedFetch } from './utils/api';
import { color, naira, radius, space } from './ui/theme';
import {
  Banner, Btn, Card, Chip, DataTable, ErrorBox, KpiCard, SectionTitle,
  SkeletonCards,
} from './ui/kit';

async function req(url, opts) {
  const res = await authedFetch(url, opts);
  if (!res.ok) {
    let d = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      d = typeof j.detail === 'string' ? j.detail : (j.detail?.message || j.message || d);
    } catch { /* keep the status */ }
    throw new Error(d);
  }
  return res.json();
}
const getJSON = (u) => req(u);
const postJSON = (u, b) => req(u, { method: 'POST', body: JSON.stringify(b || {}) });

const BAND_TONE = {
  ON_TARGET: 'success', BEHIND: 'warning', WELL_BEHIND: 'danger',
  NO_TARGET: 'neutral', TOO_EARLY: 'neutral',
};
const BAND_LABEL = {
  ON_TARGET: 'On target', BEHIND: 'Behind', WELL_BEHIND: 'Well behind',
  NO_TARGET: 'No target set', TOO_EARLY: 'Too early to say',
};
const CONFIDENCE_LABEL = {
  HIGH: 'most of the month has passed',
  MODERATE: 'about half the month has passed',
  LOW: 'early in the month — treat as an indication',
  NONE: '',
};

const pct = (v) => (v === null || v === undefined ? '—' : `${Number(v).toFixed(1)}%`);

// The current month, rendered honestly. TOO_EARLY is not a band here; it is a
// sentence explaining that the question cannot be answered yet.
function CurrentMonth({ rate }) {
  if (rate.band === 'TOO_EARLY') {
    return (
      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <SectionTitle>This month</SectionTitle>
        <div style={{ fontSize: 14, lineHeight: 1.8 }}>
          <strong>{rate.selling_days_elapsed}</strong> of{' '}
          <strong>{rate.selling_days_total}</strong> selling days have passed.
          <div style={{ marginTop: 8 }}>
            Verified so far: <strong>{naira(rate.verified_to_date)}</strong>
            {Number(rate.target_amount) > 0 && (
              <> against a target of {naira(rate.target_amount)}</>
            )}
          </div>
        </div>
        <div style={{ marginTop: space(1.5) }}>
          <Banner tone="info" title="No projection yet, on purpose">
            {rate.note}
          </Banner>
        </div>
      </Card>
    );
  }

  if (rate.band === 'NO_TARGET') {
    return (
      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <SectionTitle>This month</SectionTitle>
        <Banner tone="warning" title="No target is in force">
          {rate.note} Verified so far: {naira(rate.verified_to_date)}.
        </Banner>
      </Card>
    );
  }

  return (
    <Card pad={2.5} style={{ marginBottom: space(2) }}>
      <SectionTitle right={<Chip tone={BAND_TONE[rate.band]}>
        {BAND_LABEL[rate.band]}</Chip>}>This month</SectionTitle>
      <div style={{ display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
        gap: space(2), marginBottom: space(1.5) }}>
        <div>
          <div style={{ fontSize: 11, color: color.textMuted }}>
            Verified to date
          </div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>
            {naira(rate.verified_to_date)}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: color.textMuted }}>
            Projected full month
          </div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>
            {naira(rate.projection)}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: color.textMuted }}>Target</div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>
            {naira(rate.target_amount)}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: color.textMuted }}>
            Projected achievement
          </div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>
            {pct(rate.projected_achievement_pct)}
          </div>
        </div>
      </div>
      <div style={{ fontSize: 11.5, color: color.textMuted, lineHeight: 1.7 }}>
        {rate.selling_days_elapsed} of {rate.selling_days_total} selling days —{' '}
        {CONFIDENCE_LABEL[rate.confidence]}. {rate.note}
      </div>
    </Card>
  );
}

export function PerformancePanel({ distributorId }) {
  const [card, setCard] = useState(null);
  const [rate, setRate] = useState(null);
  const [periods, setPeriods] = useState([]);
  const [reviews, setReviews] = useState([]);
  const [snaps, setSnaps] = useState([]);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [c, r, h, rv, sn] = await Promise.all([
        getJSON(`/api/performance/${distributorId}/scorecard`),
        getJSON(`/api/performance/${distributorId}/run-rate`),
        getJSON(`/api/performance/${distributorId}/history?months=12`),
        getJSON(`/api/performance/reviews?distributor_id=${distributorId}`),
        getJSON(`/api/performance/${distributorId}/snapshots`),
      ]);
      setCard(c); setRate(r); setPeriods(h.periods || []);
      setReviews(rv.reviews || []); setSnaps(sn.snapshots || []);
      setErr('');
    } catch (e) { setErr(e.message); }
  }, [distributorId]);

  useEffect(() => { load(); }, [load]);

  if (!card || !rate) return <SkeletonCards n={3} />;

  const due = card.review;

  const openReview = async () => {
    const reason = due.due ? null : window.prompt(
      'Nothing has triggered a review. Why are you opening one?');
    if (!due.due && (!reason || reason.trim().length < 3)) return;
    setBusy(true);
    try {
      await postJSON(`/api/performance/${distributorId}/reviews`,
        { reason: reason ? reason.trim() : null });
      await load();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  const closeReview = async (review) => {
    const outcome = window.prompt(
      'Outcome — one of: SUPPORTED, TARGET_RESET, WARNED, TERMINATED.\n\n'
      + 'TARGET_RESET is a real answer: sometimes the target was wrong, not '
      + 'the distributor.');
    if (!outcome) return;
    const note = window.prompt('What was decided, and on what basis?');
    if (!note || note.trim().length < 3) return;
    setBusy(true);
    try {
      await postJSON(`/api/performance/reviews/${review.id}/close`,
        { outcome: outcome.trim().toUpperCase(), note: note.trim() });
      await load();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  const snapshot = async () => {
    const last = periods.find((p) => p.complete);
    if (!last) return;
    const [y, m] = last.period_start.split('-');
    setBusy(true);
    try {
      await postJSON(`/api/performance/${distributorId}/snapshots`,
        { year: Number(y), month: Number(m), note: 'Month-end close' });
      await load();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  return (
    <div>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      {card.blocking_conditions.length > 0 && (
        <Banner tone="warning" title="Outstanding conditions">
          <ul style={{ margin: '6px 0 0', paddingLeft: 18, lineHeight: 1.8 }}>
            {card.blocking_conditions.map((b) => <li key={b}>{b}</li>)}
          </ul>
        </Banner>
      )}

      <CurrentMonth rate={rate} />

      {/* Three readings. Never one number. */}
      <div style={{ display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))',
        gap: space(2), marginBottom: space(2) }}>
        <KpiCard icon="chart" label="Last complete month"
          value={pct(card.performance.last_complete_month.achievement_pct)}
          sub={BAND_LABEL[card.performance.last_complete_month.band]}
          tone={BAND_TONE[card.performance.last_complete_month.band]} />
        <KpiCard icon="check" label="Share of claims evidenced"
          value={card.evidence.share_evidenced_pct
            ? pct(card.evidence.share_evidenced_pct) : '—'}
          sub="Verified as a share of all reported"
          tone={Number(card.evidence.share_evidenced_pct || 0) >= 70
            ? 'success' : 'warning'} />
        <KpiCard icon="alert" label="Months below threshold"
          value={due.consecutive_months_below}
          sub={`A review is raised at ${due.months_before_review}`}
          tone={due.consecutive_months_below >= due.months_before_review
            ? 'danger' : 'neutral'} />
      </div>

      <div style={{ fontSize: 12, color: color.textMuted, marginBottom: space(2),
        lineHeight: 1.7 }}>
        {card.note} {card.evidence.note}
      </div>

      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <SectionTitle right={(
          <div style={{ display: 'flex', gap: 6 }}>
            <Btn size="sm" variant="ghost" disabled={busy} onClick={snapshot}>
              Freeze last month
            </Btn>
            {!due.already_open && (
              <Btn size="sm" variant={due.due ? 'danger' : 'secondary'}
                disabled={busy} onClick={openReview}>
                Open a review
              </Btn>
            )}
          </div>
        )}>Month by month</SectionTitle>
        <DataTable
          cols={[
            { key: 'period_start', label: 'Month' },
            { key: 'target_amount', label: 'Target', align: 'right' },
            { key: 'verified_amount', label: 'Verified', align: 'right' },
            { key: 'reported_amount', label: 'Claimed', align: 'right' },
            { key: 'achievement_pct', label: 'Achieved', align: 'right' },
            { key: 'band', label: '' },
          ]}
          rows={periods}
          empty="No history."
          render={(r, c) => {
            if (c.key === 'period_start') {
              return new Date(r.period_start).toLocaleDateString('en-NG',
                { month: 'short', year: 'numeric' });
            }
            if (c.key === 'target_amount' || c.key === 'verified_amount') {
              return naira(r[c.key]);
            }
            if (c.key === 'reported_amount') {
              return <span style={{ color: color.textMuted }}>
                {naira(r.reported_amount)}</span>;
            }
            if (c.key === 'achievement_pct') return pct(r.achievement_pct);
            if (c.key === 'band') {
              return <Chip tone={BAND_TONE[r.band]}>{BAND_LABEL[r.band]}</Chip>;
            }
            return r[c.key] ?? '—';
          }}
        />
        <div style={{ fontSize: 11, color: color.textMuted, marginTop: 10,
          lineHeight: 1.6 }}>
          Each month is measured against the target that was in force then, not
          against today's. The claimed column is shown greyed because it counts
          toward nothing.
        </div>
      </Card>

      {reviews.length > 0 && (
        <Card pad={2.5} style={{ marginBottom: space(2) }}>
          <SectionTitle>Performance reviews</SectionTitle>
          <DataTable
            cols={[
              { key: 'opened_on', label: 'Opened' },
              { key: 'trigger_reason', label: 'Why', wrap: true },
              { key: 'status', label: 'Status' },
              { key: 'outcome', label: 'Outcome' },
              { key: 'act', label: '', align: 'right' },
            ]}
            rows={reviews}
            empty="None."
            render={(r, c) => {
              if (c.key === 'status') {
                return <Chip tone={r.status === 'CLOSED' ? 'neutral' : 'warning'}>
                  {r.status}</Chip>;
              }
              if (c.key === 'outcome') {
                return r.outcome
                  ? <span>{r.outcome.replace('_', ' ')}
                      {r.outcome_note && (
                        <div style={{ fontSize: 11, color: color.textMuted }}>
                          {r.outcome_note}
                        </div>
                      )}
                    </span>
                  : '—';
              }
              if (c.key === 'act') {
                return r.status !== 'CLOSED'
                  ? <Btn size="sm" variant="accent" disabled={busy}
                      onClick={() => closeReview(r)}>Close</Btn>
                  : null;
              }
              return r[c.key] ?? '—';
            }}
          />
        </Card>
      )}

      {snaps.length > 0 && (
        <Card pad={2.5}>
          <SectionTitle>Frozen month-ends</SectionTitle>
          <DataTable
            cols={[
              { key: 'period_start', label: 'Month' },
              { key: 'verified_amount', label: 'Verified then', align: 'right' },
              { key: 'achievement_pct', label: 'Achieved', align: 'right' },
              { key: 'band', label: '' },
              { key: 'computed_at', label: 'Taken' },
            ]}
            rows={snaps}
            empty="None."
            render={(r, c) => {
              if (c.key === 'period_start') {
                return new Date(r.period_start).toLocaleDateString('en-NG',
                  { month: 'short', year: 'numeric' });
              }
              if (c.key === 'verified_amount') return naira(r.verified_amount);
              if (c.key === 'achievement_pct') return pct(r.achievement_pct);
              if (c.key === 'band') {
                return <Chip tone={BAND_TONE[r.band]}>{BAND_LABEL[r.band]}</Chip>;
              }
              if (c.key === 'computed_at') {
                return new Date(r.computed_at).toLocaleDateString();
              }
              return r[c.key] ?? '—';
            }}
          />
          <div style={{ fontSize: 11, color: color.textMuted, marginTop: 10,
            lineHeight: 1.6 }}>
            These are the figures as they stood when each was taken, which may
            differ from the live table above — sales get verified weeks later.
            A decision taken in April was taken on April's numbers.
          </div>
        </Card>
      )}
    </div>
  );
}

export default PerformancePanel;
