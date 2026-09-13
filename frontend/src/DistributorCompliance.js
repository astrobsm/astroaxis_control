// Distributor compliance — facilities, assessments, corrective actions,
// agreements. Rendered inside the distributor dossier and on the Compliance tab
// of Distribution.js.
//
// TWO THINGS THIS SCREEN REFUSES TO BLUR
// --------------------------------------
// 1. Every checklist requirement is labelled with WHO IMPOSES IT. A company
//    rule is shown as a company rule and a legal requirement names its
//    regulator. Telling a distributor that the company's own preference is the
//    law is not a UI detail; it is a false statement made in the company's
//    name.
//
// 2. The assessment shows the OUTCOME, not just the percentage. A store with no
//    quarantine area scores 95% and still fails, and the screen says so in
//    those words rather than showing a reassuring number.
//
// The signature panel hashes the contract text IN THE BROWSER and sends that
// hash. The server compares it with the text on record and refuses a mismatch.
// So the signature attaches to the words that were actually on the signer's
// screen, not merely to the fact that a button was pressed. Where the browser
// cannot hash (no secure context), signing is disabled rather than quietly
// falling back to a hash the signer never verified.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { authedFetch } from './utils/api';
import { color, radius, space } from './ui/theme';
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
const putJSON = (u, b) => req(u, { method: 'PUT', body: JSON.stringify(b || {}) });
const patchJSON = (u, b) => req(u, { method: 'PATCH', body: JSON.stringify(b || {}) });

const input = {
  width: '100%', padding: '9px 11px', borderRadius: radius.sm,
  border: `1px solid ${color.borderStrong}`, fontSize: 13.5, fontFamily: 'inherit',
  boxSizing: 'border-box', background: '#fff',
};

const STATUS_TONE = {
  APPROVED: 'success', ACTIVE: 'success', PASS: 'success', CLOSED: 'success',
  VERIFIED: 'success', COUNTERSIGNED: 'success', ACCEPTED: 'info',
  CONDITIONAL: 'warning', PENDING: 'warning', OPEN: 'warning',
  IN_PROGRESS: 'info', COMPLETED: 'info', DRAFT: 'neutral', ISSUED: 'info',
  REJECTED: 'danger', FAIL: 'danger', CRITICAL: 'danger', TERMINATED: 'danger',
  EXPIRED: 'danger', DECLINED: 'danger', HIGH: 'warning', MEDIUM: 'info',
  LOW: 'neutral', SUPERSEDED: 'neutral',
};
const tone = (s) => STATUS_TONE[String(s || '').toUpperCase()] || 'neutral';

// Who imposes a requirement. Shown on every item, every time.
const KIND_LABEL = {
  REGULATORY: 'Required by law',
  COMPANY: 'Company policy',
  COMMERCIAL: 'Commercial term',
};
const KIND_TONE = { REGULATORY: 'danger', COMPANY: 'info', COMMERCIAL: 'neutral' };

function KindChip({ kind, authority }) {
  const k = String(kind || 'COMPANY').toUpperCase();
  return (
    <Chip tone={KIND_TONE[k] || 'neutral'}>
      {KIND_LABEL[k] || k}{k === 'REGULATORY' && authority ? ` — ${authority}` : ''}
    </Chip>
  );
}

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

/** SHA-256 of the exact string, hex. Null when the browser cannot do it. */
async function sha256Hex(s) {
  if (!(window.crypto && window.crypto.subtle)) return null;
  try {
    const buf = await window.crypto.subtle.digest(
      'SHA-256', new TextEncoder().encode(s));
    return Array.from(new Uint8Array(buf))
      .map((b) => b.toString(16).padStart(2, '0')).join('');
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Assessment
// ---------------------------------------------------------------------------

const RESULTS = [
  ['PASS', 'Pass', 'success'],
  ['REQUIRES_CORRECTION', 'Needs correction', 'warning'],
  ['FAIL', 'Fail', 'danger'],
  ['NOT_APPLICABLE', 'Not applicable', 'neutral'],
];

function AssessmentRunner({ assessmentId, checklist, documents, onClose, onDone }) {
  const [answers, setAnswers] = useState({});
  const [score, setScore] = useState(null);
  const [summary, setSummary] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const refreshScore = useCallback(async () => {
    try { setScore(await getJSON(`/api/distributors/assessments/${assessmentId}/score`)); }
    catch (e) { setErr(e.message); }
  }, [assessmentId]);

  const answer = async (item, result, extra = {}) => {
    const current = answers[item.id] || {};
    const next = { ...current, result, ...extra };
    // Evidence is required by the checklist itself, not by this screen.
    if (item.requires_evidence && ['PASS', 'REQUIRES_CORRECTION'].includes(result)
        && !next.evidence_document_id) {
      setAnswers((a) => ({ ...a, [item.id]: { ...next, pendingEvidence: true } }));
      setErr(`${item.code} needs a photograph or document before it can be recorded as `
             + `${result.toLowerCase().replace('_', ' ')}. Choose one below.`);
      return;
    }
    setErr('');
    try {
      await putJSON(`/api/distributors/assessments/${assessmentId}/items`, {
        checklist_item_id: item.id, result,
        note: next.note || null,
        evidence_document_id: next.evidence_document_id || null,
      });
      setAnswers((a) => ({ ...a, [item.id]: { ...next, pendingEvidence: false, saved: true } }));
      await refreshScore();
    } catch (e) { setErr(e.message); }
  };

  const submit = async () => {
    setBusy(true);
    try {
      const r = await postJSON(
        `/api/distributors/assessments/${assessmentId}/submit`,
        { summary: summary || null });
      onDone(r);
    } catch (e) { setErr(e.message); setBusy(false); }
  };

  const answered = Object.values(answers).filter((a) => a.saved).length;
  const failing = score && (score.critical_failures.length || score.regulatory_failures.length);

  const sections = useMemo(() => {
    const by = {};
    (checklist || []).forEach((i) => { (by[i.section] = by[i.section] || []).push(i); });
    return Object.entries(by);
  }, [checklist]);

  return (
    <div>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      {/* The outcome, never the percentage alone. */}
      {score && (
        <Card pad={2.5} style={{ marginBottom: space(2) }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: space(2), flexWrap: 'wrap' }}>
            <div>
              <div style={{ fontSize: 30, fontWeight: 800, color: color.text, lineHeight: 1 }}>
                {score.score}%
              </div>
              <div style={{ fontSize: 11, color: color.textMuted }}>
                {answered} of {(checklist || []).length} answered
              </div>
            </div>
            <Chip tone={tone(score.outcome)}>
              {score.outcome === 'PASS' ? 'Would pass'
                : score.outcome === 'CONDITIONAL' ? 'Conditional' : 'Would fail'}
            </Chip>
            {score.items_not_applicable > 0 && (
              <Chip tone="neutral">
                {score.items_not_applicable} not applicable — excluded from the score
              </Chip>
            )}
          </div>
          {failing ? (
            <div style={{ marginTop: space(1.5) }}>
              <Banner tone="danger" title="The score does not decide this">
                <div style={{ lineHeight: 1.6 }}>
                  {score.critical_failures.length > 0 && (
                    <div>
                      <strong>Critical requirements failed:</strong>{' '}
                      {score.critical_failures.map((f) => f.code).join(', ')}.
                    </div>
                  )}
                  {score.regulatory_failures.length > 0 && (
                    <div>
                      <strong>Legal requirements failed:</strong>{' '}
                      {score.regulatory_failures.map(
                        (f) => `${f.code}${f.authority ? ` (${f.authority})` : ''}`).join(', ')}.
                    </div>
                  )}
                  A facility that fails any of these fails the assessment
                  whatever the percentage says.
                </div>
              </Banner>
            </div>
          ) : null}
        </Card>
      )}

      {sections.map(([section, items]) => (
        <Card key={section} pad={2.5} style={{ marginBottom: space(2) }}>
          <SectionTitle>{section}</SectionTitle>
          <div style={{ display: 'grid', gap: space(1.5) }}>
            {items.map((item) => {
              const a = answers[item.id] || {};
              return (
                <div key={item.id} style={{
                  padding: space(1.5), borderRadius: radius.md,
                  border: `1px solid ${a.saved ? color.border : color.borderStrong}`,
                  background: a.saved ? '#FAFBFC' : '#fff',
                }}>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
                    <code style={{ fontSize: 11, color: color.textMuted }}>{item.code}</code>
                    <span style={{ fontSize: 13.5, fontWeight: 500, flex: 1, minWidth: 200 }}>
                      {item.requirement}
                    </span>
                    <KindChip kind={item.requirement_kind} authority={item.authority} />
                    {item.is_critical && <Chip tone="danger">Critical</Chip>}
                  </div>

                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: space(1) }}>
                    {RESULTS.map(([value, label]) => (
                      <button key={value} onClick={() => answer(item, value)} style={{
                        padding: '5px 11px', borderRadius: radius.pill, fontSize: 12,
                        fontWeight: 600, cursor: 'pointer',
                        border: `1px solid ${a.result === value ? color.medical : color.borderStrong}`,
                        background: a.result === value ? color.infoBg : '#fff',
                        color: a.result === value ? color.royal : color.textSecondary,
                      }}>{label}</button>
                    ))}
                  </div>

                  {(a.result === 'FAIL' || a.result === 'REQUIRES_CORRECTION') && (
                    <input style={{ ...input, marginTop: space(1) }}
                      placeholder="What exactly was found? This becomes the corrective action."
                      defaultValue={a.note || ''}
                      onBlur={(e) => answer(item, a.result, { note: e.target.value })} />
                  )}

                  {item.requires_evidence
                    && ['PASS', 'REQUIRES_CORRECTION'].includes(a.result) && (
                    <div style={{ marginTop: space(1) }}>
                      <select style={input} value={a.evidence_document_id || ''}
                        onChange={(e) => answer(item, a.result,
                          { evidence_document_id: e.target.value })}>
                        <option value="">Attach evidence…</option>
                        {(documents || []).map((d) => (
                          <option key={d.id} value={d.id}>
                            {d.doc_type} — {d.filename}
                          </option>
                        ))}
                      </select>
                      {(documents || []).length === 0 && (
                        <div style={{ fontSize: 11, color: color.warning, marginTop: 4 }}>
                          No documents are uploaded for this distributor yet. Upload
                          the photograph on the Documents panel first.
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </Card>
      ))}

      <Card pad={2.5}>
        <Field label="Assessor's summary (optional)">
          <textarea style={{ ...input, minHeight: 70, resize: 'vertical' }}
            value={summary} onChange={(e) => setSummary(e.target.value)}
            placeholder="Anything the checklist did not cover." />
        </Field>
        <Banner tone="info" title="Submitting freezes this assessment">
          The findings become a permanent record and cannot be edited afterwards.
          Every failure raises a corrective action with an owner. To change a
          finding later you carry out a new assessment; this one stays on the
          record.
        </Banner>
        <div style={{ display: 'flex', gap: 8, marginTop: space(2), justifyContent: 'flex-end' }}>
          <Btn variant="ghost" onClick={onClose}>Cancel</Btn>
          <Btn variant="accent" disabled={busy || answered === 0} onClick={submit}>
            {busy ? 'Submitting…' : `Submit ${answered} finding${answered === 1 ? '' : 's'}`}
          </Btn>
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Agreement
// ---------------------------------------------------------------------------

function AgreementView({ agreementId, onClose, onChanged }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState('');
  const [localHash, setLocalHash] = useState(null);
  const [signRole, setSignRole] = useState('');
  const [signName, setSignName] = useState('');
  const [meaning, setMeaning] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await getJSON(`/api/distributors/agreements/${agreementId}`);
      setData(d);
      setLocalHash(await sha256Hex(d.agreement.body || ''));
      setErr('');
    } catch (e) { setErr(e.message); }
  }, [agreementId]);

  useEffect(() => { load(); }, [load]);

  if (err && !data) return <ErrorBox msg={err} />;
  if (!data) return <SkeletonCards n={2} />;

  const a = data.agreement;
  const signed = (data.signatures || []).map((s) => s.signer_role);
  const canSign = ['ISSUED', 'ACCEPTED'].includes(a.status);
  const hashesAgree = localHash && localHash === a.body_sha256;

  const act = async (path, body) => {
    setBusy(true);
    try {
      await postJSON(`/api/distributors/agreements/${agreementId}/${path}`, body);
      await load();
      onChanged && onChanged();
      setMeaning(''); setSignName(''); setSignRole('');
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  return (
    <div>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <strong style={{ fontSize: 15 }}>{a.title}</strong>
          <Chip tone={tone(a.status)}>{a.status}</Chip>
          <code style={{ fontSize: 11, color: color.textMuted }}>{a.agreement_reference}</code>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
            {a.status === 'DRAFT' && (
              <Btn size="sm" variant="accent" disabled={busy}
                onClick={() => act('issue')}>Issue to distributor</Btn>
            )}
            {a.status === 'COUNTERSIGNED' && (
              <Btn size="sm" variant="accent" disabled={busy}
                onClick={() => act('activate')}>Bring into force</Btn>
            )}
            {['ISSUED', 'ACCEPTED', 'COUNTERSIGNED', 'ACTIVE'].includes(a.status) && (
              <Btn size="sm" variant="danger" disabled={busy} onClick={() => {
                const reason = window.prompt('Why is this agreement ending?');
                if (reason && reason.trim().length >= 3) {
                  act('end', { status: 'TERMINATED', reason: reason.trim() });
                }
              }}>Terminate</Btn>
            )}
            <Btn size="sm" variant="ghost" onClick={onClose}>Close</Btn>
          </div>
        </div>
        {a.status === 'DRAFT' && (
          <div style={{ marginTop: space(1.5) }}>
            <Banner tone="warning" title="Still a draft — have it reviewed">
              This text has not been checked by anyone qualified to check it.
              Issuing freezes it and presents it to the distributor for
              signature, so read it as the contract it will become.
            </Banner>
          </div>
        )}
      </Card>

      {/* The text itself. Nothing is summarised away. */}
      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <SectionTitle>The agreement as it stands</SectionTitle>
        <pre style={{
          whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 13,
          lineHeight: 1.65, fontFamily: 'inherit', margin: 0,
          maxHeight: 380, overflowY: 'auto', padding: space(1.5),
          background: '#fafbfc', borderRadius: radius.sm,
          border: `1px solid ${color.border}`,
        }}>{a.body}</pre>
        <div style={{ fontSize: 11, color: color.textMuted, marginTop: 8, lineHeight: 1.6 }}>
          Fingerprint of the text on record:{' '}
          <code style={{ wordBreak: 'break-all' }}>{a.body_sha256}</code>
          {localHash && (
            <div style={{ marginTop: 4, color: hashesAgree ? color.success : color.danger }}>
              {hashesAgree
                ? 'This browser hashed the text above and got the same fingerprint.'
                : 'The text shown does not match the fingerprint on record. Do not sign; '
                  + 'reload the page and report this.'}
            </div>
          )}
        </div>
      </Card>

      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <SectionTitle>Signatures</SectionTitle>
        <DataTable
          cols={[
            { key: 'signer_role', label: 'Party' },
            { key: 'signer_name', label: 'Signed by' },
            { key: 'meaning', label: 'What they signed to', wrap: true },
            { key: 'signed_at', label: 'When' },
          ]}
          rows={data.signatures}
          empty="Nobody has signed yet."
          render={(r, c) => (c.key === 'signed_at'
            ? new Date(r.signed_at).toLocaleString() : r[c.key])}
        />
        {a.status === 'COUNTERSIGNED' && (
          <div style={{ marginTop: space(1.5) }}>
            <Banner tone="info" title="Both parties have signed">
              The agreement is not yet in force. Bring it into force to apply its
              terms.
            </Banner>
          </div>
        )}
      </Card>

      {canSign && (
        <Card pad={2.5}>
          <SectionTitle>Add a signature</SectionTitle>
          {localHash === null ? (
            <Banner tone="danger" title="This browser cannot verify the document">
              Signing is disabled because this browser cannot compute the
              document fingerprint — usually because the page is not being served
              over HTTPS. A signature that nobody checked against the text is not
              evidence of agreement, so the app will not record one.
            </Banner>
          ) : !hashesAgree ? (
            <Banner tone="danger" title="Fingerprint mismatch">
              The text displayed does not match what is on record. Signing is
              blocked.
            </Banner>
          ) : (
            <>
              <Field label="Signing as">
                <select style={input} value={signRole}
                  onChange={(e) => setSignRole(e.target.value)}>
                  <option value="">Choose…</option>
                  {!signed.includes('DISTRIBUTOR') && (
                    <option value="DISTRIBUTOR">The distributor</option>
                  )}
                  {a.status === 'ACCEPTED' && !signed.includes('COMPANY') && (
                    <option value="COMPANY">Bonnesante Medicals (countersign)</option>
                  )}
                  {!signed.includes('WITNESS') && <option value="WITNESS">Witness</option>}
                </select>
              </Field>
              <Field label="Full name of the person signing">
                <input style={input} value={signName}
                  onChange={(e) => setSignName(e.target.value)}
                  placeholder="As it should appear on the record" />
              </Field>
              <Field label="What this signature means"
                hint="In the signer's own words. A signature with no stated meaning records only that a button was pressed.">
                <textarea style={{ ...input, minHeight: 60, resize: 'vertical' }}
                  value={meaning} onChange={(e) => setMeaning(e.target.value)}
                  placeholder="e.g. I have read these terms in full and accept them on behalf of the company." />
              </Field>
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <Btn variant="accent"
                  disabled={busy || !signRole || signName.trim().length < 2
                            || meaning.trim().length < 10}
                  onClick={() => act('sign', {
                    signer_name: signName.trim(), signer_role: signRole,
                    meaning: meaning.trim(), body_sha256: localHash,
                  })}>
                  {busy ? 'Recording…' : 'Sign'}
                </Btn>
              </div>
            </>
          )}
        </Card>
      )}
    </div>
  );
}

function NewAgreement({ distributorId, onClose, onDone }) {
  const [title, setTitle] = useState('Distribution Agreement');
  const [body, setBody] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const save = async () => {
    setBusy(true);
    try {
      const r = await postJSON(`/api/distributors/${distributorId}/agreements`,
        { title: title.trim(), body });
      onDone(r);
    } catch (e) { setErr(e.message); setBusy(false); }
  };

  return (
    <Card pad={2.5}>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}
      <Banner tone="warning" title="This app does not write your contracts">
        Paste the agreement your legal adviser has approved. The app records the
        lifecycle, freezes the text at issue and ties each signature to the exact
        words signed — it does not supply the terms, and nothing here is legal
        advice.
      </Banner>
      <div style={{ height: space(2) }} />
      <Field label="Title">
        <input style={input} value={title} onChange={(e) => setTitle(e.target.value)} />
      </Field>
      <Field label="The agreement text"
        hint="Exactly as it should be presented. Once issued this cannot be changed — you would supersede it with a new agreement instead.">
        <textarea style={{ ...input, minHeight: 260, resize: 'vertical', fontFamily: 'inherit' }}
          value={body} onChange={(e) => setBody(e.target.value)}
          placeholder="Paste the approved agreement here." />
      </Field>
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <Btn variant="ghost" onClick={onClose}>Cancel</Btn>
        <Btn variant="accent" disabled={busy || body.trim().length < 50} onClick={save}>
          {busy ? 'Saving…' : 'Save as draft'}
        </Btn>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// The panel shown inside the dossier
// ---------------------------------------------------------------------------

export function CompliancePanel({ distributorId, documents, onChanged }) {
  const [summary, setSummary] = useState(null);
  const [facilities, setFacilities] = useState([]);
  const [agreements, setAgreements] = useState([]);
  const [err, setErr] = useState('');
  const [view, setView] = useState(null);
  const [newFacility, setNewFacility] = useState(null);

  const load = useCallback(async () => {
    try {
      const [s, f, a] = await Promise.all([
        getJSON(`/api/distributors/${distributorId}/compliance`),
        getJSON(`/api/distributors/${distributorId}/facilities`),
        getJSON(`/api/distributors/${distributorId}/agreements`),
      ]);
      setSummary(s);
      setFacilities(f.facilities || []);
      setAgreements(a.agreements || []);
      setErr('');
    } catch (e) { setErr(e.message); }
  }, [distributorId]);

  useEffect(() => { load(); }, [load]);

  const startAssessment = async (facility) => {
    try {
      const r = await postJSON(
        `/api/distributors/facilities/${facility.id}/assessments`, {});
      setView({ kind: 'assessment', id: r.id, checklist: r.checklist,
        facility: facility.name, reference: r.assessment_reference });
    } catch (e) { setErr(e.message); }
  };

  const addFacility = async () => {
    try {
      await postJSON(`/api/distributors/${distributorId}/facilities`, newFacility);
      setNewFacility(null);
      await load();
    } catch (e) { setErr(e.message); }
  };

  if (!summary) return <SkeletonCards n={2} />;

  if (view?.kind === 'assessment') {
    return (
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: space(2) }}>
          <Btn size="sm" variant="ghost" onClick={() => setView(null)}>← Back</Btn>
          <strong>{view.facility}</strong>
          <code style={{ fontSize: 11, color: color.textMuted }}>{view.reference}</code>
        </div>
        <AssessmentRunner assessmentId={view.id} checklist={view.checklist}
          documents={documents}
          onClose={() => setView(null)}
          onDone={async (r) => {
            setView(null);
            await load();
            onChanged && onChanged();
            setErr('');
            window.alert(
              `${r.assessment_reference}: ${r.score}% — ${r.outcome}.\n`
              + `${r.corrective_actions_raised} corrective action(s) raised.\n`
              + `The facility is now ${r.facility_status}.`);
          }} />
      </div>
    );
  }

  if (view?.kind === 'agreement') {
    return (
      <div>
        <Btn size="sm" variant="ghost" style={{ marginBottom: space(2) }}
          onClick={() => { setView(null); load(); }}>← Back</Btn>
        <AgreementView agreementId={view.id}
          onClose={() => { setView(null); load(); }}
          onChanged={() => { load(); onChanged && onChanged(); }} />
      </div>
    );
  }

  if (view?.kind === 'newAgreement') {
    return (
      <div>
        <Btn size="sm" variant="ghost" style={{ marginBottom: space(2) }}
          onClick={() => setView(null)}>← Back</Btn>
        <NewAgreement distributorId={distributorId}
          onClose={() => setView(null)}
          onDone={async (r) => { await load(); setView({ kind: 'agreement', id: r.id }); }} />
      </div>
    );
  }

  return (
    <div>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <div style={{ marginBottom: space(2) }}>
        {summary.fit_to_trade ? (
          <Banner tone="success" title="Fit to trade">
            The facility is approved, an agreement is in force and no critical
            corrective action is outstanding.
          </Banner>
        ) : (
          <Banner tone="warning" title="Not yet fit to trade">
            <ul style={{ margin: '6px 0 0', paddingLeft: 18, lineHeight: 1.7 }}>
              {summary.blocking_conditions.map((b) => <li key={b}>{b}</li>)}
            </ul>
          </Banner>
        )}
      </div>

      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <SectionTitle right={!newFacility && (
          <Btn size="sm" variant="secondary"
            onClick={() => setNewFacility({ name: '', town: '', address: '',
              responsible_person: '' })}>Add facility</Btn>
        )}>Storage facilities</SectionTitle>

        {newFacility ? (
          <div>
            <Field label="Facility name">
              <input style={input} value={newFacility.name} autoFocus
                onChange={(e) => setNewFacility({ ...newFacility, name: e.target.value })}
                placeholder="e.g. Main store, Ikeja" />
            </Field>
            <Field label="Town">
              <input style={input} value={newFacility.town}
                onChange={(e) => setNewFacility({ ...newFacility, town: e.target.value })} />
            </Field>
            <Field label="Address">
              <input style={input} value={newFacility.address}
                onChange={(e) => setNewFacility({ ...newFacility, address: e.target.value })} />
            </Field>
            <Field label="Person responsible for the facility"
              hint="The checklist requires a named person. 'Somebody' is not an answer an inspector can verify.">
              <input style={input} value={newFacility.responsible_person}
                onChange={(e) => setNewFacility(
                  { ...newFacility, responsible_person: e.target.value })} />
            </Field>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <Btn size="sm" variant="ghost" onClick={() => setNewFacility(null)}>Cancel</Btn>
              <Btn size="sm" variant="accent" disabled={!newFacility.name.trim()}
                onClick={addFacility}>Save</Btn>
            </div>
          </div>
        ) : (
          <DataTable
            cols={[
              { key: 'name', label: 'Facility', wrap: true },
              { key: 'town', label: 'Town' },
              { key: 'status', label: 'Status' },
              { key: 'last_assessed', label: 'Last assessed' },
              { key: 'last_score', label: 'Score', align: 'right' },
              { key: 'act', label: '', align: 'right' },
            ]}
            rows={facilities}
            empty="No storage facility recorded. A distributor cannot be assessed without one."
            render={(r, c) => {
              if (c.key === 'status') return <Chip tone={tone(r.status)}>{r.status}</Chip>;
              if (c.key === 'last_score') {
                return r.last_score === null || r.last_score === undefined
                  ? '—' : `${r.last_score}%`;
              }
              if (c.key === 'act') {
                return (
                  <Btn size="sm" variant="accent" onClick={() => startAssessment(r)}>
                    {r.last_assessed ? 'Re-assess' : 'Assess'}
                  </Btn>
                );
              }
              return r[c.key] || '—';
            }}
          />
        )}
      </Card>

      <Card pad={2.5}>
        <SectionTitle right={(
          <Btn size="sm" variant="secondary"
            onClick={() => setView({ kind: 'newAgreement' })}>Draft agreement</Btn>
        )}>Agreements</SectionTitle>
        <DataTable
          cols={[
            { key: 'title', label: 'Agreement', wrap: true },
            { key: 'status', label: 'Status' },
            { key: 'signature_count', label: 'Signatures', align: 'right' },
            { key: 'effective_from', label: 'From' },
            { key: 'expires_on', label: 'Until' },
            { key: 'act', label: '', align: 'right' },
          ]}
          rows={agreements}
          empty="No agreement exists. A distributor cannot trade without one in force."
          render={(r, c) => {
            if (c.key === 'status') return <Chip tone={tone(r.status)}>{r.status}</Chip>;
            if (c.key === 'title') {
              return (
                <span>{r.title}<br />
                  <code style={{ fontSize: 11, color: color.textMuted }}>
                    {r.agreement_reference}
                  </code>
                </span>
              );
            }
            if (c.key === 'act') {
              return (
                <Btn size="sm" variant="ghost"
                  onClick={() => setView({ kind: 'agreement', id: r.id })}>Open</Btn>
              );
            }
            return r[c.key] || '—';
          }}
        />
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The company-wide corrective action queue, for the Compliance tab
// ---------------------------------------------------------------------------

export function CorrectiveActionQueue({ onChanged }) {
  const [rows, setRows] = useState(null);
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    try {
      const r = await getJSON(
        `/api/distributors/compliance/corrective-actions?overdue_only=${overdueOnly}`);
      setRows(r.corrective_actions || []);
      setErr('');
    } catch (e) { setErr(e.message); }
  }, [overdueOnly]);

  useEffect(() => { load(); }, [load]);

  const progress = async (row, status) => {
    const note = status === 'CLOSED'
      ? window.prompt('How did you verify this was actually fixed?')
      : null;
    if (status === 'CLOSED' && (!note || note.trim().length < 3)) return;
    try {
      await patchJSON(`/api/distributors/corrective-actions/${row.id}`,
        { status, note: note ? note.trim() : null });
      await load();
      onChanged && onChanged();
    } catch (e) { setErr(e.message); }
  };

  if (!rows) return <SkeletonCards n={2} />;

  return (
    <Card pad={2.5}>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}
      <SectionTitle right={(
        <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
          <input type="checkbox" checked={overdueOnly}
            onChange={(e) => setOverdueOnly(e.target.checked)} />
          Overdue only
        </label>
      )}>Open corrective actions</SectionTitle>
      <DataTable
        cols={[
          { key: 'legal_name', label: 'Distributor', wrap: true },
          { key: 'finding', label: 'Finding', wrap: true },
          { key: 'severity', label: 'Severity' },
          { key: 'responsible_person', label: 'Owner' },
          { key: 'deadline', label: 'Due' },
          { key: 'act', label: '', align: 'right' },
        ]}
        rows={rows}
        empty="Nothing outstanding."
        render={(r, c) => {
          if (c.key === 'severity') return <Chip tone={tone(r.severity)}>{r.severity}</Chip>;
          if (c.key === 'deadline') {
            if (!r.deadline) return <span style={{ color: color.warning }}>No deadline set</span>;
            return (
              <span style={{ color: r.overdue ? color.danger : color.text,
                fontWeight: r.overdue ? 700 : 400 }}>{r.deadline}</span>
            );
          }
          if (c.key === 'legal_name') {
            return (
              <span>{r.legal_name}<br />
                <code style={{ fontSize: 11, color: color.textMuted }}>
                  {r.action_reference}
                </code>
              </span>
            );
          }
          if (c.key === 'act') {
            return (
              <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                {r.status === 'OPEN' && (
                  <Btn size="sm" variant="ghost"
                    onClick={() => progress(r, 'IN_PROGRESS')}>Start</Btn>
                )}
                {['OPEN', 'IN_PROGRESS'].includes(r.status) && (
                  <Btn size="sm" variant="ghost"
                    onClick={() => progress(r, 'COMPLETED')}>Done</Btn>
                )}
                {r.status === 'COMPLETED' && (
                  <Btn size="sm" variant="accent"
                    onClick={() => progress(r, 'CLOSED')}>Verify &amp; close</Btn>
                )}
              </div>
            );
          }
          return r[c.key] || '—';
        }}
      />
      <div style={{ fontSize: 11, color: color.textMuted, marginTop: 10, lineHeight: 1.6 }}>
        A finding can only be closed by recording how the fix was verified, and
        the app records who verified it. A corrective action closed without that
        is just paperwork.
      </div>
    </Card>
  );
}

export default CompliancePanel;
