// Territory applications — request, review, decide.
//
// WHAT THIS SCREEN WILL NOT DO
// ----------------------------
// It will not reduce a reviewer's decision to one number. Eligibility, the
// compliance position and the conflicts over the ground itself are shown as
// three separate answers, because each can be fine while another is fatal, and
// a combined score is exactly how a missing licence or a double-promised LGA
// gets averaged away.
//
// It will not offer an override that cannot work. Where two exclusive
// territories cover the same LGA the database refuses the grant outright, so
// the approve button is not shown with a confirmation — it is not shown. Where
// the overlap is legal (neither side exclusive) it IS grantable, and there the
// reviewer is asked to confirm they mean it. A button that always fails teaches
// people to distrust every other button on the page.

import React, { useCallback, useEffect, useState } from 'react';
import { authedFetch } from './utils/api';
import { color, naira, radius, space } from './ui/theme';
import {
  Banner, Btn, Card, Chip, DataTable, ErrorBox, SectionTitle, SkeletonCards,
} from './ui/kit';

async function req(url, opts) {
  const res = await authedFetch(url, opts);
  if (!res.ok) {
    let d = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      d = typeof j.detail === 'string' ? j.detail : (j.detail?.message || j.message || d);
    } catch { /* keep the status */ }
    const err = new Error(d);
    err.status = res.status;
    throw err;
  }
  return res.json();
}
const getJSON = (u) => req(u);
const postJSON = (u, b) => req(u, { method: 'POST', body: JSON.stringify(b || {}) });

const input = {
  width: '100%', padding: '9px 11px', borderRadius: radius.sm,
  border: `1px solid ${color.borderStrong}`, fontSize: 13.5, fontFamily: 'inherit',
  boxSizing: 'border-box', background: '#fff',
};

const TONES = {
  APPROVED: 'success', SUBMITTED: 'info', UNDER_REVIEW: 'warning',
  REJECTED: 'danger', WITHDRAWN: 'neutral', DRAFT: 'neutral',
  ELIGIBLE: 'success', CONDITIONALLY_ELIGIBLE: 'warning', NOT_ELIGIBLE: 'danger',
};
const tone = (s) => TONES[String(s || '').toUpperCase()] || 'neutral';

function Field({ label, hint, children }) {
  return (
    <label style={{ display: 'block', marginBottom: space(1.5) }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: color.textSecondary, marginBottom: 5 }}>
        {label}
      </div>
      {children}
      {hint && (
        <div style={{ fontSize: 11, color: color.textMuted, marginTop: 4, lineHeight: 1.5 }}>
          {hint}
        </div>
      )}
    </label>
  );
}

// ---------------------------------------------------------------------------
// Conflicts
// ---------------------------------------------------------------------------

function ConflictList({ conflicts, blocking }) {
  if (!conflicts || conflicts.length === 0) return null;
  // Group by LGA — the reviewer cares about the ground, not the row count.
  const byLga = {};
  conflicts.forEach((c) => { (byLga[c.lga] = byLga[c.lga] || []).push(c); });

  return (
    <Banner tone={blocking ? 'danger' : 'warning'}
      title={blocking
        ? 'This ground is already promised exclusively'
        : 'Overlapping coverage — permitted, but intended?'}>
      <div style={{ lineHeight: 1.7 }}>
        {Object.entries(byLga).map(([lga, rows]) => (
          <div key={lga}>
            <strong>{lga}</strong> — held by{' '}
            {rows.map((r) => `${r.legal_name} (${r.territory_code})`).join(', ')}
          </div>
        ))}
        <div style={{ marginTop: 6 }}>
          {blocking
            ? 'Exclusivity is enforced per LGA, not per territory, so this '
              + 'cannot be granted. End the other assignment or narrow one '
              + 'territory’s coverage first.'
            : 'Neither side holds this ground exclusively, so both distributors '
              + 'may sell into it.'}
        </div>
      </div>
    </Banner>
  );
}

// ---------------------------------------------------------------------------
// Review
// ---------------------------------------------------------------------------

function ReviewPanel({ applicationId, onClose, onChanged }) {
  const [packet, setPacket] = useState(null);
  const [err, setErr] = useState('');
  const [note, setNote] = useState('');
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setPacket(await getJSON(`/api/geography/applications/${applicationId}`));
      setErr('');
    } catch (e) { setErr(e.message); }
  }, [applicationId]);

  useEffect(() => { load(); }, [load]);

  if (err && !packet) return <ErrorBox msg={err} />;
  if (!packet) return <SkeletonCards n={3} />;

  const a = packet.application;
  const el = packet.eligibility;
  const hard = packet.blocking_conflicts || [];
  const advisory = packet.advisory_conflicts || [];
  const open = ['SUBMITTED', 'UNDER_REVIEW'].includes(a.status);

  const act = async (path, body) => {
    setBusy(true);
    try {
      await postJSON(`/api/geography/applications/${applicationId}/${path}`, body);
      await load();
      onChanged && onChanged();
      setNote(''); setAck(false);
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  return (
    <div>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <strong style={{ fontSize: 15 }}>{a.legal_name}</strong>
          <Chip tone={tone(a.status)}>{a.status.replace('_', ' ')}</Chip>
          <code style={{ fontSize: 11, color: color.textMuted }}>{a.application_number}</code>
          <Btn size="sm" variant="ghost" style={{ marginLeft: 'auto' }}
            onClick={onClose}>Close</Btn>
        </div>
        <div style={{ marginTop: space(1), fontSize: 13.5, lineHeight: 1.7 }}>
          Applying for <strong>{a.territory_code}</strong> — {a.territory_name}
          {a.requested_exclusive ? ' (exclusive)' : ' (non-exclusive)'}
          {a.requested_from && <> from {a.requested_from}</>}
        </div>
        {a.applicant_statement && (
          <div style={{ marginTop: space(1), padding: space(1.5),
            background: '#FAFBFC', borderRadius: radius.sm, fontSize: 13,
            lineHeight: 1.6, border: `1px solid ${color.border}` }}>
            {a.applicant_statement}
          </div>
        )}
      </Card>

      {/* The three judgements, side by side and never combined. */}
      <ConflictList conflicts={hard} blocking />
      <ConflictList conflicts={advisory} blocking={false} />

      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <SectionTitle right={<Chip tone={tone(el.band)}>{el.band.replace(/_/g, ' ')}</Chip>}>
          Eligibility — {el.score}%
        </SectionTitle>
        <DataTable
          cols={[
            { key: 'factor', label: 'Factor' },
            { key: 'weight', label: 'Weight', align: 'right' },
            { key: 'earned', label: 'Earned', align: 'right' },
            { key: 'note', label: '' },
          ]}
          rows={el.breakdown}
          empty="Not assessed."
          render={(r, c) => (c.key === 'factor'
            ? String(r.factor).replace(/_/g, ' ') : r[c.key])}
        />
        {el.blocking_conditions.length > 0 && (
          <div style={{ marginTop: space(1.5) }}>
            <Banner tone="warning" title="These are not absorbed by the score">
              <ul style={{ margin: '6px 0 0', paddingLeft: 18, lineHeight: 1.7 }}>
                {el.blocking_conditions.map((b) => <li key={b}>{b}</li>)}
              </ul>
            </Banner>
          </div>
        )}
      </Card>

      {packet.compliance && (
        <Card pad={2.5} style={{ marginBottom: space(2) }}>
          <SectionTitle right={(
            <Chip tone={packet.compliance.fit_to_trade ? 'success' : 'warning'}>
              {packet.compliance.fit_to_trade ? 'Fit to trade' : 'Not yet fit to trade'}
            </Chip>
          )}>Compliance position</SectionTitle>
          {packet.compliance.blocking_conditions.length > 0 ? (
            <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.8, fontSize: 13 }}>
              {packet.compliance.blocking_conditions.map((b) => <li key={b}>{b}</li>)}
            </ul>
          ) : (
            <div style={{ fontSize: 13, color: color.textSecondary }}>
              Facility approved and an agreement is in force.
            </div>
          )}
        </Card>
      )}

      {a.status === 'UNDER_REVIEW' && a.eligibility_score !== null && (
        <div style={{ fontSize: 11.5, color: color.textMuted, marginBottom: space(2),
          lineHeight: 1.6 }}>
          The score on this application was taken when review began
          {a.reviewed_by_name ? ` by ${a.reviewed_by_name}` : ''} and is not
          recomputed. It is the figure the decision was made against.
        </div>
      )}

      {open && (
        <Card pad={2.5}>
          <SectionTitle>Decision</SectionTitle>

          {a.status === 'SUBMITTED' && (
            <div style={{ marginBottom: space(2) }}>
              <Btn size="sm" variant="secondary" disabled={busy}
                onClick={() => act('review')}>
                Take up for review
              </Btn>
              <div style={{ fontSize: 11, color: color.textMuted, marginTop: 6 }}>
                Records the eligibility score you will be working from.
              </div>
            </div>
          )}

          <Field label="Why"
            hint="Someone reads this when the decision is questioned. Say what it turned on.">
            <textarea style={{ ...input, minHeight: 70, resize: 'vertical' }}
              value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>

          {hard.length > 0 && (
            <Banner tone="danger" title="This cannot be granted">
              The ground is already held exclusively by another distributor.
              Refusing is the only decision available here — the database will
              not record a second exclusive promise over the same LGA.
            </Banner>
          )}

          {hard.length === 0 && advisory.length > 0 && (
            <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start',
              fontSize: 13, marginBottom: space(1.5), lineHeight: 1.5 }}>
              <input type="checkbox" checked={ack} style={{ marginTop: 3 }}
                onChange={(e) => setAck(e.target.checked)} />
              <span>
                I know another distributor already sells into this ground and
                intend to grant it anyway.
              </span>
            </label>
          )}

          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <Btn variant="danger" disabled={busy || note.trim().length < 3}
              onClick={() => act('decide', { approve: false, note: note.trim() })}>
              Refuse
            </Btn>
            {hard.length === 0 && (
              <Btn variant="accent"
                disabled={busy || note.trim().length < 3
                          || (advisory.length > 0 && !ack)}
                onClick={() => act('decide', {
                  approve: true, note: note.trim(),
                  acknowledge_conflicts: ack,
                })}>
                Grant territory
              </Btn>
            )}
          </div>
        </Card>
      )}

      {!open && (
        <Card pad={2.5}>
          <SectionTitle>Decision</SectionTitle>
          <div style={{ fontSize: 13.5, lineHeight: 1.8 }}>
            <Chip tone={tone(a.status)}>{a.status}</Chip>
            {a.decided_by_name && <> by {a.decided_by_name}</>}
            {a.decided_at && <> on {new Date(a.decided_at).toLocaleDateString()}</>}
            {a.decision_note && (
              <div style={{ marginTop: 8 }}>{a.decision_note}</div>
            )}
            {a.assignment_id && (
              <div style={{ marginTop: 8, fontSize: 12, color: color.textSecondary }}>
                This decision created the assignment that gives{' '}
                {a.legal_name} {a.territory_code}.
              </div>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// The queue
// ---------------------------------------------------------------------------

export function ApplicationQueue({ onChanged }) {
  const [rows, setRows] = useState(null);
  const [openOnly, setOpenOnly] = useState(true);
  const [reviewing, setReviewing] = useState(null);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    try {
      const r = await getJSON(
        `/api/geography/applications?open_only=${openOnly}`);
      setRows(r.applications || []);
      setErr('');
    } catch (e) { setErr(e.message); }
  }, [openOnly]);

  useEffect(() => { load(); }, [load]);

  if (reviewing) {
    return (
      <div>
        <Btn size="sm" variant="ghost" style={{ marginBottom: space(2) }}
          onClick={() => { setReviewing(null); load(); }}>← Back to queue</Btn>
        <ReviewPanel applicationId={reviewing}
          onClose={() => { setReviewing(null); load(); }}
          onChanged={() => { load(); onChanged && onChanged(); }} />
      </div>
    );
  }

  if (!rows) return <SkeletonCards n={2} />;

  return (
    <Card pad={2.5}>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}
      <SectionTitle right={(
        <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
          <input type="checkbox" checked={openOnly}
            onChange={(e) => setOpenOnly(e.target.checked)} />
          Awaiting a decision only
        </label>
      )}>Territory applications</SectionTitle>
      <DataTable
        cols={[
          { key: 'legal_name', label: 'Applicant', wrap: true },
          { key: 'territory_code', label: 'Territory', wrap: true },
          { key: 'state', label: 'State' },
          { key: 'status', label: 'Status' },
          { key: 'eligibility_score', label: 'Score', align: 'right' },
          { key: 'act', label: '', align: 'right' },
        ]}
        rows={rows}
        empty={openOnly
          ? 'Nothing awaiting a decision.'
          : 'No territory applications yet.'}
        render={(r, c) => {
          if (c.key === 'status') {
            return <Chip tone={tone(r.status)}>{r.status.replace('_', ' ')}</Chip>;
          }
          if (c.key === 'eligibility_score') {
            return r.eligibility_score === null || r.eligibility_score === undefined
              ? <span style={{ color: color.textMuted }}>not yet</span>
              : `${r.eligibility_score}%`;
          }
          if (c.key === 'legal_name') {
            return (
              <span>{r.legal_name}<br />
                <code style={{ fontSize: 11, color: color.textMuted }}>
                  {r.application_number}
                </code>
              </span>
            );
          }
          if (c.key === 'territory_code') {
            return (
              <span>{r.territory_code}<br />
                <span style={{ fontSize: 11, color: color.textMuted }}>
                  {r.territory_name}
                </span>
              </span>
            );
          }
          if (c.key === 'act') {
            return (
              <Btn size="sm" variant="accent" onClick={() => setReviewing(r.id)}>
                {['SUBMITTED', 'UNDER_REVIEW'].includes(r.status) ? 'Review' : 'Open'}
              </Btn>
            );
          }
          return r[c.key] || '—';
        }}
      />
    </Card>
  );
}

// ---------------------------------------------------------------------------
// What a distributor holds — for the dossier
// ---------------------------------------------------------------------------

export function TerritoryHoldings({ distributorId, territories, onChanged }) {
  const [held, setHeld] = useState(null);
  const [applying, setApplying] = useState(null);
  const [err, setErr] = useState('');
  const [conflicts, setConflicts] = useState(null);

  const load = useCallback(async () => {
    try {
      setHeld(await getJSON(
        `/api/geography/distributors/${distributorId}/territories`));
      setErr('');
    } catch (e) { setErr(e.message); }
  }, [distributorId]);

  useEffect(() => { load(); }, [load]);

  // Show the clash before the application is even made, not after.
  const previewConflicts = async (territoryId) => {
    setConflicts(null);
    if (!territoryId) return;
    try {
      const r = await getJSON(
        `/api/geography/territories/${territoryId}/conflicts`
        + `?distributor_id=${distributorId}`);
      setConflicts(r.conflicts || []);
    } catch { setConflicts(null); }
  };

  const apply = async () => {
    try {
      await postJSON(
        `/api/geography/territories/${applying.territory_id}/applications`,
        { distributor_id: distributorId, statement: applying.statement || null,
          requested_from: applying.requested_from || null });
      setApplying(null); setConflicts(null);
      await load();
      onChanged && onChanged();
    } catch (e) { setErr(e.message); }
  };

  if (!held) return <SkeletonCards n={2} />;

  const hard = (conflicts || []).filter((c) => c.blocking);
  const advisory = (conflicts || []).filter((c) => !c.blocking);

  return (
    <div>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <SectionTitle right={!applying && (
          <Btn size="sm" variant="secondary"
            onClick={() => setApplying({ territory_id: '', statement: '' })}>
            Apply for a territory
          </Btn>
        )}>Territory held now</SectionTitle>

        {applying ? (
          <div>
            <Field label="Territory">
              <select style={input} value={applying.territory_id}
                onChange={(e) => {
                  setApplying({ ...applying, territory_id: e.target.value });
                  previewConflicts(e.target.value);
                }}>
                <option value="">Choose…</option>
                {(territories || []).map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.code} — {t.name}{t.holder ? ` (held by ${t.holder})` : ''}
                  </option>
                ))}
              </select>
            </Field>

            <ConflictList conflicts={hard} blocking />
            <ConflictList conflicts={advisory} blocking={false} />

            <Field label="Why this distributor, for this ground"
              hint="Capacity, coverage, existing customers — whatever the reviewer should weigh.">
              <textarea style={{ ...input, minHeight: 70, resize: 'vertical' }}
                value={applying.statement}
                onChange={(e) => setApplying({ ...applying, statement: e.target.value })} />
            </Field>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <Btn size="sm" variant="ghost"
                onClick={() => { setApplying(null); setConflicts(null); }}>Cancel</Btn>
              <Btn size="sm" variant="accent" disabled={!applying.territory_id}
                onClick={apply}>Submit application</Btn>
            </div>
            {hard.length > 0 && (
              <div style={{ fontSize: 11.5, color: color.textMuted, marginTop: 8,
                lineHeight: 1.6 }}>
                You can still submit this — an application is a request, not a
                grant — but as things stand it cannot be approved.
              </div>
            )}
          </div>
        ) : (
          <DataTable
            cols={[
              { key: 'territory_code', label: 'Territory', wrap: true },
              { key: 'state', label: 'State' },
              { key: 'lga_count', label: 'LGAs', align: 'right' },
              { key: 'is_exclusive', label: 'Basis' },
              { key: 'assigned_from', label: 'Since' },
            ]}
            rows={held.current}
            empty="No territory held."
            render={(r, c) => {
              if (c.key === 'is_exclusive') {
                return (
                  <Chip tone={r.is_exclusive ? 'info' : 'neutral'}>
                    {r.is_exclusive ? 'Exclusive' : 'Shared'}
                  </Chip>
                );
              }
              if (c.key === 'territory_code') {
                return (
                  <span>{r.territory_code}<br />
                    <span style={{ fontSize: 11, color: color.textMuted }}>
                      {r.territory_name}
                    </span>
                  </span>
                );
              }
              return r[c.key] ?? '—';
            }}
          />
        )}
      </Card>

      <Card pad={2.5}>
        <SectionTitle>Territory held before</SectionTitle>
        <DataTable
          cols={[
            { key: 'territory_code', label: 'Territory' },
            { key: 'assigned_from', label: 'From' },
            { key: 'assigned_to', label: 'Until' },
            { key: 'end_reason', label: 'Why it ended', wrap: true },
          ]}
          rows={held.past}
          empty="Nothing ended."
          render={(r, c) => r[c.key] || '—'}
        />
        <div style={{ fontSize: 11, color: color.textMuted, marginTop: 10, lineHeight: 1.6 }}>
          Ended assignments are never deleted — sales made while a territory was
          held stay attached to whoever made them.
        </div>
      </Card>
    </div>
  );
}

export default ApplicationQueue;
