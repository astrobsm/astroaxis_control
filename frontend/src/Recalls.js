// Recalls and product complaints.
//
// THE NUMBER THIS SCREEN PUTS IN THE LARGEST TYPE
// -----------------------------------------------
// Unaccounted. Every other recall dashboard leads with "recovered: 94%", which
// is the one figure nobody needs — what matters is the units that were never
// found, because those are still out there and somebody has to decide what to
// do about them. So the reconciliation shows four quantities and gives the
// unaccounted one its own card, in red, whether or not it is convenient.
//
// The adverse-event warning is repeated on every complaint that carries the
// flag, not shown once when it is first ticked. A legal duty that was explained
// in a dialog three weeks ago has not been discharged by having been explained.

import React, { useCallback, useEffect, useState } from 'react';
import { authedFetch } from './utils/api';
import { color, radius, space } from './ui/theme';
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
      d = typeof j.detail === 'string' ? j.detail : (j.detail?.message || d);
    } catch { /* keep the status */ }
    throw new Error(d);
  }
  return res.json();
}
const getJSON = (u) => req(u);
const postJSON = (u, b) => req(u, { method: 'POST', body: JSON.stringify(b || {}) });
const patchJSON = (u, b) => req(u, { method: 'PATCH', body: JSON.stringify(b || {}) });

const input = {
  width: '100%', padding: '9px 11px', borderRadius: radius.sm,
  border: `1px solid ${color.borderStrong}`, fontSize: 13.5,
  fontFamily: 'inherit', boxSizing: 'border-box', background: '#fff',
};

const SEVERITY_TONE = {
  URGENT: 'danger', ROUTINE: 'warning', PRECAUTIONARY: 'info',
  CRITICAL: 'danger', HIGH: 'warning', MEDIUM: 'info', LOW: 'neutral',
};

function Field({ label, hint, children }) {
  return (
    <label style={{ display: 'block', marginBottom: space(1.5) }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: color.textSecondary,
        marginBottom: 5 }}>{label}</div>
      {children}
      {hint && (
        <div style={{ fontSize: 11, color: color.textMuted, marginTop: 4,
          lineHeight: 1.5 }}>{hint}</div>
      )}
    </label>
  );
}

// ---------------------------------------------------------------------------
// One recall
// ---------------------------------------------------------------------------

function RecallDetail({ recallId, onClose, onChanged }) {
  const [figures, setFigures] = useState(null);
  const [outstanding, setOutstanding] = useState(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [f, o] = await Promise.all([
        getJSON(`/api/recalls/${recallId}/reconciliation`),
        getJSON(`/api/recalls/${recallId}/outstanding`),
      ]);
      setFigures(f); setOutstanding(o); setErr('');
    } catch (e) { setErr(e.message); }
  }, [recallId]);

  useEffect(() => { load(); }, [load]);

  if (err && !figures) return <ErrorBox msg={err} />;
  if (!figures) return <SkeletonCards n={3} />;

  const unaccounted = Number(figures.unaccounted_quantity);

  const contact = async (target) => {
    const response = window.prompt(
      `What did ${target.name} say?\n\n`
      + 'Leave blank if nobody answered — an unanswered call is an attempt, '
      + 'not a notification, and they stay on the list.');
    if (response === null) return;
    setBusy(true);
    try {
      await postJSON(`/api/recalls/${recallId}/notifications`, {
        channel: 'PHONE',
        distributor_id: target.distributor_id || null,
        contact_name: target.name,
        acknowledged: response.trim().length > 0,
        response: response.trim() || 'No answer',
      });
      await load();
      onChanged && onChanged();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  const close = async () => {
    const note = window.prompt('What was done, and what was the outcome?');
    if (!note || note.trim().length < 10) return;
    let explanation = null;
    if (unaccounted > 0) {
      explanation = window.prompt(
        `${unaccounted} unit(s) were never found.\n\n`
        + 'What is believed to have happened to them? A recall closed with '
        + 'this unexplained reads as complete while product is still in use.');
      if (!explanation || explanation.trim().length < 10) return;
    }
    setBusy(true);
    try {
      await postJSON(`/api/recalls/${recallId}/close`, {
        closure_note: note.trim(),
        unaccounted_explanation: explanation ? explanation.trim() : null,
      });
      await load();
      onChanged && onChanged();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  return (
    <div>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center',
          flexWrap: 'wrap' }}>
          <strong style={{ fontSize: 16 }}>{figures.batch_number}</strong>
          <Chip tone={figures.status === 'CLOSED' ? 'neutral' : 'danger'}>
            {figures.status}
          </Chip>
          <code style={{ fontSize: 11, color: color.textMuted }}>
            {figures.recall_reference}
          </code>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
            {figures.status !== 'CLOSED' && (
              <Btn size="sm" variant="accent" disabled={busy} onClick={close}>
                Close recall
              </Btn>
            )}
            <Btn size="sm" variant="ghost" onClick={onClose}>Close</Btn>
          </div>
        </div>
        <div style={{ marginTop: 6, fontSize: 13.5, color: color.textSecondary }}>
          {figures.product} · raised {figures.raised_on}
        </div>
      </Card>

      {/* Four quantities, and the one that matters gets its own card. */}
      <div style={{ display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
        gap: space(2), marginBottom: space(2) }}>
        <KpiCard icon="asset" label="At risk when raised"
          value={figures.at_risk_quantity} sub="The denominator" tone="neutral" />
        <KpiCard icon="check" label="Returned"
          value={figures.recovered_quantity} sub="Came back to us"
          tone="success" />
        <KpiCard icon="alert" label="Destroyed"
          value={figures.destroyed_quantity} sub="Written off" tone="info" />
        <KpiCard icon="asset" label="Still on our shelves"
          value={figures.still_on_our_shelves} sub="Blocked from despatch"
          tone="warning" />
        <KpiCard icon="alert" label="Never found"
          value={figures.unaccounted_quantity}
          sub={unaccounted > 0 ? 'Still out there' : 'All accounted for'}
          tone={unaccounted > 0 ? 'danger' : 'success'} />
      </div>

      <Banner tone={unaccounted > 0 ? 'danger' : 'success'}
        title={unaccounted > 0
          ? `${figures.unaccounted_quantity} unit(s) never found`
          : 'Everything is accounted for'}>
        {figures.note}
        {figures.unaccounted_explanation && (
          <div style={{ marginTop: 6 }}>
            <strong>Believed to have happened:</strong>{' '}
            {figures.unaccounted_explanation}
          </div>
        )}
      </Banner>

      {figures.status !== 'CLOSED' && outstanding && (
        <Card pad={2.5}>
          <SectionTitle right={(
            <Chip tone={outstanding.count > 0 ? 'danger' : 'success'}>
              {outstanding.count} still to reach
            </Chip>
          )}>Who has to be contacted</SectionTitle>
          <DataTable
            cols={[
              { key: 'name', label: 'Who', wrap: true },
              { key: 'kind', label: 'Type' },
              { key: 'phone', label: 'Phone' },
              { key: 'holding', label: 'Has', align: 'right' },
              { key: 'act', label: '', align: 'right' },
            ]}
            rows={outstanding.still_to_contact}
            empty="Everyone on the list has acknowledged."
            render={(r, c) => {
              if (c.key === 'holding') return r.holding ?? r.received ?? '—';
              if (c.key === 'act') {
                return <Btn size="sm" variant="secondary" disabled={busy}
                  onClick={() => contact(r)}>Record contact</Btn>;
              }
              return r[c.key] || '—';
            }}
          />
          <div style={{ fontSize: 11.5, color: color.textMuted, marginTop: 10,
            lineHeight: 1.6 }}>
            {outstanding.note} Contacts attempted:{' '}
            {figures.contacts_attempted}; acknowledged:{' '}
            {figures.contacts_acknowledged}.
          </div>
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main screen
// ---------------------------------------------------------------------------

export default function Recalls() {
  const [tab, setTab] = useState('recalls');
  const [recalls, setRecalls] = useState(null);
  const [complaints, setComplaints] = useState(null);
  const [open, setOpen] = useState(null);
  const [logging, setLogging] = useState(null);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    try {
      const [r, c] = await Promise.all([
        getJSON('/api/recalls'),
        getJSON('/api/recalls/complaints/all'),
      ]);
      setRecalls(r.recalls || []); setComplaints(c); setErr('');
    } catch (e) { setErr(e.message); }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (!recalls || !complaints) return <SkeletonCards n={3} />;

  if (open) {
    return (
      <div>
        <Btn size="sm" variant="ghost" style={{ marginBottom: space(2) }}
          onClick={() => { setOpen(null); load(); }}>← Back</Btn>
        <RecallDetail recallId={open} onClose={() => { setOpen(null); load(); }}
          onChanged={load} />
      </div>
    );
  }

  const logComplaint = async () => {
    try {
      const r = await postJSON('/api/recalls/complaints', logging);
      setLogging(null);
      await load();
      if (r.warning) window.alert(r.warning);
    } catch (e) { setErr(e.message); }
  };

  return (
    <div>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <div style={{ display: 'flex', gap: space(1), flexWrap: 'wrap',
        marginBottom: space(2.5) }}>
        {[['recalls', 'Recalls'], ['complaints', 'Complaints']].map(
          ([k, label]) => (
            <button key={k} onClick={() => setTab(k)} style={{
              padding: '8px 15px', borderRadius: radius.pill, fontSize: 13,
              fontWeight: 600,
              border: `1px solid ${tab === k ? color.medical : color.borderStrong}`,
              background: tab === k ? color.infoBg : '#fff',
              color: tab === k ? color.royal : color.textSecondary,
              cursor: 'pointer',
            }}>{label}</button>
          ))}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <Btn size="sm" variant="accent"
            onClick={() => setLogging({ description: '', severity: 'MEDIUM',
              potential_adverse_event: false, received_from: '',
              contact_phone: '' })}>
            Log a complaint
          </Btn>
          <Btn size="sm" variant="ghost" icon="refresh" onClick={load}>
            Refresh
          </Btn>
        </div>
      </div>

      {complaints.warning && (
        <Banner tone="danger" title="Possible adverse events with no regulator record">
          {complaints.warning}
        </Banner>
      )}

      {logging && (
        <Card pad={2.5} style={{ marginBottom: space(2) }}>
          <SectionTitle>Log a complaint</SectionTitle>
          <Field label="What was reported"
            hint="In their words where you can. This cannot be edited afterwards — the investigation is recorded alongside it, not over it.">
            <textarea style={{ ...input, minHeight: 90, resize: 'vertical' }}
              value={logging.description}
              onChange={(e) => setLogging({ ...logging, description: e.target.value })} />
          </Field>
          <Field label="Who reported it">
            <input style={input} value={logging.received_from}
              onChange={(e) => setLogging({ ...logging, received_from: e.target.value })} />
          </Field>
          <Field label="Their phone">
            <input style={input} value={logging.contact_phone}
              onChange={(e) => setLogging({ ...logging, contact_phone: e.target.value })} />
          </Field>
          <Field label="Severity">
            <select style={input} value={logging.severity}
              onChange={(e) => setLogging({ ...logging, severity: e.target.value })}>
              {['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </Field>
          <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start',
            fontSize: 13, marginBottom: space(1.5), lineHeight: 1.6 }}>
            <input type="checkbox" checked={logging.potential_adverse_event}
              style={{ marginTop: 3 }}
              onChange={(e) => setLogging(
                { ...logging, potential_adverse_event: e.target.checked })} />
            <span>
              This may be an adverse event — harm, or possible harm, to a
              patient.
              <div style={{ fontSize: 11.5, color: color.textMuted,
                marginTop: 3 }}>
                Ticking this records that a reporting duty may have arisen. It
                does not notify anyone: this app cannot, and a person has to
                decide whether a regulator must be told.
              </div>
            </span>
          </label>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <Btn size="sm" variant="ghost"
              onClick={() => setLogging(null)}>Cancel</Btn>
            <Btn size="sm" variant="accent"
              disabled={logging.description.trim().length < 10}
              onClick={logComplaint}>Record</Btn>
          </div>
        </Card>
      )}

      {tab === 'recalls' && (
        <Card pad={2.5}>
          <SectionTitle>Recalls</SectionTitle>
          <DataTable
            cols={[
              { key: 'batch_number', label: 'Batch' },
              { key: 'product', label: 'Product', wrap: true },
              { key: 'severity', label: 'Severity' },
              { key: 'at_risk_quantity', label: 'At risk', align: 'right' },
              { key: 'recovered', label: 'Returned', align: 'right' },
              { key: 'status', label: 'Status' },
              { key: 'act', label: '', align: 'right' },
            ]}
            rows={recalls}
            empty="No recalls have been raised. Raise one from a batch on the Batches screen."
            render={(r, c) => {
              if (c.key === 'severity') {
                return <Chip tone={SEVERITY_TONE[r.severity]}>{r.severity}</Chip>;
              }
              if (c.key === 'status') {
                return <Chip tone={r.status === 'CLOSED' ? 'neutral' : 'danger'}>
                  {r.status}</Chip>;
              }
              if (c.key === 'act') {
                return <Btn size="sm" variant="ghost"
                  onClick={() => setOpen(r.id)}>Open</Btn>;
              }
              return r[c.key] ?? '—';
            }}
          />
        </Card>
      )}

      {tab === 'complaints' && (
        <>
          <div style={{ display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))',
            gap: space(2), marginBottom: space(2) }}>
            <KpiCard icon="alert" label="Possible adverse events"
              value={complaints.possible_adverse_events}
              sub="Flagged as patient harm"
              tone={complaints.possible_adverse_events > 0 ? 'danger' : 'success'} />
            <KpiCard icon="alert" label="No regulator record"
              value={complaints.adverse_events_with_no_regulator_record}
              sub="Nobody has recorded reporting these"
              tone={complaints.adverse_events_with_no_regulator_record > 0
                ? 'danger' : 'success'} />
          </div>

          <Card pad={2.5}>
            <SectionTitle>Complaints</SectionTitle>
            <DataTable
              cols={[
                { key: 'received_on', label: 'Received' },
                { key: 'description', label: 'What was reported', wrap: true },
                { key: 'severity', label: 'Severity' },
                { key: 'status', label: 'Status' },
                { key: 'regulator', label: 'Regulator' },
              ]}
              rows={complaints.complaints}
              empty="No complaints recorded."
              render={(r, c) => {
                if (c.key === 'severity') {
                  return (
                    <span>
                      <Chip tone={SEVERITY_TONE[r.severity]}>{r.severity}</Chip>
                      {r.potential_adverse_event && (
                        <div style={{ marginTop: 3 }}>
                          <Chip tone="danger">Possible adverse event</Chip>
                        </div>
                      )}
                    </span>
                  );
                }
                if (c.key === 'status') {
                  return <Chip tone={r.status === 'CLOSED' ? 'neutral' : 'warning'}>
                    {r.status}</Chip>;
                }
                if (c.key === 'description') {
                  return (
                    <span>
                      {r.description}
                      {r.outcome && (
                        <div style={{ fontSize: 11.5, color: color.textMuted,
                          marginTop: 4 }}>Outcome: {r.outcome}</div>
                      )}
                    </span>
                  );
                }
                if (c.key === 'regulator') {
                  if (!r.potential_adverse_event) return '—';
                  return r.regulator_notified_on
                    ? <span style={{ fontSize: 12 }}>
                        {r.regulator_notified_on}
                        <div style={{ color: color.textMuted, fontSize: 11 }}>
                          {r.regulator_reference}
                        </div>
                      </span>
                    : <Chip tone="danger">Not recorded</Chip>;
                }
                return r[c.key] || '—';
              }}
            />
            <div style={{ fontSize: 11.5, color: color.textMuted, marginTop: 12,
              lineHeight: 1.7 }}>
              “Not recorded” means nobody has written down that a regulator was
              told. This application notifies no regulator and cannot — the
              column records what a person did.
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
