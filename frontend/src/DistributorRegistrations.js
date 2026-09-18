// Data capture — the shareable registration link, and the queue it feeds.
//
// TWO KINDS OF LINK, AND WHY THEY LOOK DIFFERENT HERE
// ---------------------------------------------------
// An ordering link (Dossier → Ordering) is a credential for one distributor's
// account and must not be forwarded. A registration link is meant to be
// forwarded — trade fair, WhatsApp group, a rep's contact list — because it
// lets anyone APPLY and nothing more. So this screen encourages sharing where
// the ordering screen warns against it, and the copy says which is which.
//
// THE MATCHING HAPPENS HERE, NOT ON THE PUBLIC FORM
// ------------------------------------------------
// The public form can only confirm a full phone number against one masked
// account. This screen runs the existing duplicate matcher over everything the
// applicant typed — name, phone, email, CAC, TIN — and shows every candidate
// with the reason it matched, because the person who can judge identity is an
// employee, not an anonymous visitor. It also catches the common case: an
// applicant who does not realise they are already a customer under a slightly
// different name.
//
// LINKING IS NOT IMPORTING
// ------------------------
// Choosing "this is them" on approval attaches the EXISTING customer account
// rather than creating a second one, and the orders already recorded against it
// become visible under the distributor. Nothing is copied — those orders were
// always theirs. The number of orders and their value are shown before the
// decision, because that is the consequence the reviewer is agreeing to.

import React, { useCallback, useEffect, useState } from 'react';
import { authedFetch, isAdmin } from './utils/api';
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
  PENDING: 'warning', REVIEWING: 'info', APPROVED: 'success',
  REJECTED: 'danger', DUPLICATE: 'neutral',
};
const tone = (s) => TONES[String(s || '').toUpperCase()] || 'neutral';

const when = (v) => (v ? String(v).slice(0, 10) : '—');

function Field({ label, hint, children }) {
  return (
    <label style={{ display: 'block', marginBottom: space(1.5) }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: color.textSecondary, marginBottom: 5 }}>
        {label}
      </div>
      {children}
      {hint && (
        <div style={{ fontSize: 11.5, color: color.textMuted, marginTop: 4, lineHeight: 1.5 }}>
          {hint}
        </div>
      )}
    </label>
  );
}

function Pair({ label, children }) {
  if (children === null || children === undefined || children === '') return null;
  return (
    <div>
      <div style={{ fontSize: 11, color: color.textMuted, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {label}
      </div>
      <div style={{ fontSize: 13.5, marginTop: 2 }}>{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Issuing a link
// ---------------------------------------------------------------------------

function NewLink({ onDone, onError }) {
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState('');
  const [campaign, setCampaign] = useState('');
  const [validDays, setValidDays] = useState(90);
  const [maxSubmissions, setMaxSubmissions] = useState('');
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState(null);
  const [copied, setCopied] = useState(false);

  const create = async () => {
    setBusy(true);
    try {
      const body = await postJSON('/api/distributors/registration-links', {
        label, campaign: campaign || null, valid_days: Number(validDays),
        max_submissions: maxSubmissions === '' ? null : Number(maxSubmissions),
      });
      setIssued(body);
      setLabel(''); setCampaign(''); setMaxSubmissions('');
      onDone();
    } catch (e) { onError(e.message); }
    finally { setBusy(false); }
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(issued.url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch { /* the field is selectable; copying by hand still works */ }
  };

  if (issued) {
    return (
      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <SectionTitle>Link ready to share</SectionTitle>
        <Banner tone="info" title="Ready to share">{issued.warning}</Banner>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: space(1.5) }}>
          <input readOnly value={issued.url} onFocus={(e) => e.target.select()}
            style={{ ...input, flex: '1 1 320px', fontFamily: 'monospace', fontSize: 12.5 }} />
          <Btn size="sm" variant="accent" icon="check" onClick={copy}>
            {copied ? 'Copied' : 'Copy'}
          </Btn>
          <Btn size="sm" variant="secondary" onClick={() => { setIssued(null); setOpen(false); }}>
            Done
          </Btn>
        </div>
        <div style={{ fontSize: 12, color: color.textMuted, marginTop: space(1.5), lineHeight: 1.6 }}>
          Expires {when(issued.expires_at)}. Send it by WhatsApp, print it as a QR
          code, put it on a flyer — it is meant to be passed around. You can copy
          it again from the list below at any time.
        </div>
      </Card>
    );
  }

  if (!open) {
    return (
      <div style={{ marginBottom: space(2) }}>
        <Btn size="sm" variant="accent" icon="tag" onClick={() => setOpen(true)}>
          New registration link
        </Btn>
      </div>
    );
  }

  return (
    <Card pad={2.5} style={{ marginBottom: space(2) }}>
      <SectionTitle>New registration link</SectionTitle>
      <Field label="Label" hint="Where it is going — 'Trade fair, Aba, March'. When it needs revoking you will be reading this list, not remembering.">
        <input style={input} value={label} onChange={(e) => setLabel(e.target.value)} />
      </Field>
      <Field label="Campaign (optional)" hint="Shown to the applicant at the top of the form.">
        <input style={input} value={campaign} onChange={(e) => setCampaign(e.target.value)} />
      </Field>
      <div style={{ display: 'flex', gap: space(2), flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 160px' }}>
          <Field label="Valid for (days)">
            <input style={input} type="number" min="1" max="730" value={validDays}
              onChange={(e) => setValidDays(e.target.value)} />
          </Field>
        </div>
        <div style={{ flex: '1 1 160px' }}>
          <Field label="Max applications" hint="Blank for no limit.">
            <input style={input} type="number" min="1" value={maxSubmissions}
              onChange={(e) => setMaxSubmissions(e.target.value)} />
          </Field>
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <Btn size="sm" variant="accent" disabled={busy || label.trim().length < 3}
          onClick={create}>{busy ? 'Creating…' : 'Create link'}</Btn>
        <Btn size="sm" variant="ghost" onClick={() => setOpen(false)}>Cancel</Btn>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Reviewing one application
// ---------------------------------------------------------------------------

function Review({ registrationId, onClose, onDecided }) {
  const [packet, setPacket] = useState(null);
  const [err, setErr] = useState('');
  const [note, setNote] = useState('');
  const [linkCustomer, setLinkCustomer] = useState('');
  const [acknowledge, setAcknowledge] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setPacket(await getJSON(`/api/distributors/registrations/${registrationId}`)); }
    catch (e) { setErr(e.message); }
  }, [registrationId]);

  useEffect(() => { load(); }, [load]);

  // Only a failure to LOAD replaces the screen. A refused decision leaves the
  // application on screen with the error above it -- wiping the form and the
  // note they had typed, on the one screen where the next step is to choose
  // differently, is how a reviewer gets stuck.
  if (err && !packet) return <ErrorBox msg={err} />;
  if (!packet) return <SkeletonCards n={2} />;

  const r = packet.registration;
  const candidates = packet.candidates || [];
  const customers = candidates.filter((c) => c.kind === 'customer');
  const distributorHits = candidates.filter((c) => c.kind === 'distributor');
  const chosen = customers.find((c) => c.id === linkCustomer);
  // Linking a customer answers the duplicate question for THAT customer and no
  // other. Mirrors services/registration.py, which decides the same way -- if
  // these two ever disagree the screen offers a button the server refuses.
  const unresolved = candidates.filter(
    (c) => c.strength >= 80 && !(chosen && c.kind === 'customer' && c.id === chosen.id));
  const settled = ['APPROVED', 'REJECTED', 'DUPLICATE'].includes(r.status);

  const decide = async (approve) => {
    setBusy(true);
    try {
      const body = await postJSON(
        `/api/distributors/registrations/${registrationId}/decide`,
        {
          approve, note,
          link_customer_id: approve && linkCustomer ? linkCustomer : null,
          acknowledge_duplicates: acknowledge,
        });
      onDecided(approve
        ? `${r.registration_reference} approved as ${body.distributor.distributor_code}`
          + (body.history
            ? `; ${body.history.orders_attributed} order(s) from the last `
              + `${Math.round((body.history.window_days || 365) / 30)} months attributed`
              + (body.history.older_left_with_customer
                ? `, ${body.history.older_left_with_customer} older left with the customer`
                : '')
            : '')
        : `${r.registration_reference} rejected`);
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: space(2) }}>
        <Chip tone={tone(r.status)}>{r.status}</Chip>
        <strong style={{ fontSize: 15 }}>{r.registration_reference}</strong>
        <span style={{ color: color.textSecondary, fontSize: 13.5 }}>{r.legal_name}</span>
        <div style={{ marginLeft: 'auto' }}>
          <Btn size="sm" variant="ghost" onClick={onClose}>Back to queue</Btn>
        </div>
      </div>

      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <Banner tone="warning" title="Unverified">{packet.note}</Banner>

      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <SectionTitle>What they told us</SectionTitle>
        <div style={{
          display: 'grid', gap: space(1.5),
          gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))',
        }}>
          <Pair label="Registered name">{r.legal_name}</Pair>
          <Pair label="Trading as">{r.trading_name}</Pair>
          <Pair label="Type">{r.entity_type}</Pair>
          <Pair label="Contact">{r.contact_name}</Pair>
          <Pair label="Phone">{r.phone}</Pair>
          <Pair label="WhatsApp">{r.whatsapp}</Pair>
          <Pair label="Email">{r.email}</Pair>
          <Pair label="CAC / RC">{r.cac_number}</Pair>
          <Pair label="TIN">{r.tin}</Pair>
          <Pair label="State">{r.state}</Pair>
          <Pair label="LGA">{r.lga}</Pair>
          <Pair label="Town">{r.town}</Pair>
          <Pair label="Business">{r.business_type}</Pair>
          <Pair label="Years trading">{r.years_in_operation}</Pair>
          <Pair label="Staff">{r.employee_count}</Pair>
          <Pair label="Marketers">{r.marketer_count}</Pair>
          <Pair label="Applied">{when(r.submitted_at)}</Pair>
          <Pair label="Came from">{r.came_from}</Pair>
        </div>
        {(r.business_address || r.storage_description || r.products_of_interest
          || r.applicant_note) && (
          <div style={{ marginTop: space(2), display: 'grid', gap: space(1.5) }}>
            <Pair label="Address">{r.business_address}</Pair>
            <Pair label="Storage and premises">{r.storage_description}</Pair>
            <Pair label="Products of interest">{r.products_of_interest}</Pair>
            <Pair label="Their note">{r.applicant_note}</Pair>
          </div>
        )}
      </Card>

      {r.claims_existing_customer && (
        <Banner tone="info" title="They say they already buy from us">
          {r.claimed_customer_name
            ? <>They confirmed the account <strong>{r.claimed_customer_name}</strong>
              {r.claimed_customer_code ? ` (${r.claimed_customer_code})` : ''} by phone
              number. Still your decision — confirm it below.</>
            : 'They could not identify the account themselves. The candidates below are the matches we found.'}
        </Banner>
      )}

      {distributorHits.length > 0 && (
        <Banner tone="danger" title="This may already be a distributor">
          {distributorHits.map((d) => (
            <div key={d.id} style={{ marginTop: 4 }}>
              <strong>{d.name}</strong>{d.code ? ` (${d.code})` : ''} — {d.reasons.join('; ')}
            </div>
          ))}
          <div style={{ marginTop: 6 }}>
            Approving creates a SECOND distributor record. Only tick the
            acknowledgement below if they really are different businesses.
          </div>
        </Banner>
      )}

      {!settled && (
        <Card pad={2.5} style={{ marginBottom: space(2) }}>
          <SectionTitle>Is this an existing customer?</SectionTitle>
          <div style={{ fontSize: 13, color: color.textSecondary, lineHeight: 1.65, marginBottom: space(1.5) }}>
            Linking attaches the account they already have instead of creating a
            second one, and their trading over the last twelve months becomes
            visible under the new distributor. Nothing is copied and no figure
            changes — those orders were always theirs, and anything older stays
            on the customer record untouched.
          </div>

          {customers.length === 0 ? (
            <div style={{ fontSize: 13, color: color.textMuted }}>
              No existing customer matched what they typed. Approving will create
              a new customer account for them.
            </div>
          ) : (
            <div style={{ display: 'grid', gap: space(1) }}>
              {[{ id: '', name: 'No — this is a new account', reasons: [] }]
                .concat(customers).map((c) => (
                <label key={c.id || 'none'} style={{
                  display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer',
                  border: `1px solid ${linkCustomer === c.id ? color.medical : color.border}`,
                  background: linkCustomer === c.id ? color.infoBg : '#fff',
                  borderRadius: radius.md, padding: space(1.5),
                }}>
                  <input type="radio" name="linkCustomer" checked={linkCustomer === c.id}
                    onChange={() => setLinkCustomer(c.id)} style={{ marginTop: 3 }} />
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 600, fontSize: 13.5 }}>{c.name}</div>
                    {c.reasons.length > 0 && (
                      <div style={{ fontSize: 12, color: color.textSecondary, marginTop: 2 }}>
                        {c.reasons.join('; ')}
                      </div>
                    )}
                    {c.history && (
                      <div style={{ fontSize: 12.5, color: color.textSecondary, marginTop: 4 }}>
                        {c.history.orders === 0 ? (
                          'No orders on record — nothing would be attributed.'
                        ) : (
                          <>
                            {/* What would actually move, not what exists. The
                                two differ, because attribution is bounded to a
                                year, and quoting the total here would promise
                                more than approval delivers. */}
                            <strong>{c.history.orders_in_window}</strong> order
                            {c.history.orders_in_window === 1 ? '' : 's'} worth{' '}
                            <strong>{naira(c.history.value_in_window)}</strong>
                            {' '}would be attributed (last{' '}
                            {Math.round((c.history.window_days || 365) / 30)} months).
                            {c.history.orders > c.history.orders_in_window && (
                              <div style={{ marginTop: 2 }}>
                                {c.history.orders} orders in total since{' '}
                                {c.history.first_order}; the older{' '}
                                {c.history.orders - c.history.orders_in_window} stay
                                with the customer, unchanged.
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    )}
                  </div>
                </label>
              ))}
            </div>
          )}
        </Card>
      )}

      {settled ? (
        <Card pad={2.5}>
          <SectionTitle>Decision</SectionTitle>
          <div style={{ display: 'grid', gap: space(1.5) }}>
            <Pair label="Outcome">{r.status}</Pair>
            <Pair label="Note">{r.review_note}</Pair>
            <Pair label="Decided">{when(r.reviewed_at)}</Pair>
            <Pair label="Orders attributed">
              {r.orders_attributed === null || r.orders_attributed === undefined
                ? null : String(r.orders_attributed)}
            </Pair>
          </div>
        </Card>
      ) : (
        <Card pad={2.5}>
          <SectionTitle>Decide</SectionTitle>
          <Field label="Why" hint="Recorded against the application. The applicant is entitled to a reason.">
            <textarea style={{ ...input, minHeight: 74, resize: 'vertical' }}
              value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>

          {unresolved.length > 0 && (
            <label style={{
              display: 'flex', gap: 10, alignItems: 'flex-start', marginBottom: space(1.5),
              fontSize: 13, lineHeight: 1.6, cursor: 'pointer',
            }}>
              <input type="checkbox" checked={acknowledge} style={{ marginTop: 3 }}
                onChange={(e) => setAcknowledge(e.target.checked)} />
              <span>
                I have seen {unresolved.length === 1 ? 'the match' : 'the matches'}
                {' '}above ({unresolved.map((c) => c.name).join(', ')}) and this
                is a different business.
              </span>
            </label>
          )}

          {unresolved.length > 0 && !acknowledge && (
            <div style={{
              fontSize: 12.5, color: color.textSecondary, marginBottom: space(1.5),
              lineHeight: 1.6,
            }}>
              Approving is blocked until either the matching account above is
              linked, or you confirm this is a different business.
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Btn size="sm" variant="accent"
              disabled={busy || note.trim().length < 3
                || (unresolved.length > 0 && !acknowledge)}
              onClick={() => decide(true)}>
              {busy ? 'Working…' : chosen ? `Approve and link ${chosen.name}` : 'Approve'}
            </Btn>
            <Btn size="sm" variant="danger" disabled={busy || note.trim().length < 3}
              onClick={() => decide(false)}>Reject</Btn>
          </div>
          <div style={{ fontSize: 12, color: color.textMuted, marginTop: space(1.5), lineHeight: 1.6 }}>
            Approving creates the distributor in DRAFT. It still has to go through
            the usual lifecycle — applied, reviewed, approved, active — before it
            can hold territory or order.
          </div>
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// The screen
// ---------------------------------------------------------------------------

export function RegistrationDesk({ onChanged }) {
  const [links, setLinks] = useState([]);
  const [registrations, setRegistrations] = useState([]);
  const [showDead, setShowDead] = useState(false);
  const [pendingOnly, setPendingOnly] = useState(true);
  const [reviewing, setReviewing] = useState(null);
  const [err, setErr] = useState('');
  const [toast, setToast] = useState('');
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(null);
  const admin = isAdmin();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [l, r] = await Promise.all([
        getJSON(`/api/distributors/registration-links?include_dead=${showDead}`),
        getJSON(`/api/distributors/registrations?pending_only=${pendingOnly}`),
      ]);
      setLinks(l.links || []);
      setRegistrations(r.registrations || []);
      setErr('');
    } catch (e) { setErr(e.message); }
    finally { setLoading(false); }
  }, [showDead, pendingOnly]);

  useEffect(() => { load(); }, [load]);

  const flash = (msg) => { setToast(msg); setTimeout(() => setToast(''), 6000); };

  const copyLink = async (link) => {
    try {
      await navigator.clipboard.writeText(link.url);
      setCopied(link.id);
      setTimeout(() => setCopied(null), 2500);
    } catch {
      // Clipboard is blocked in some browsers over plain HTTP, and on an
      // insecure origin it throws rather than failing quietly. Show the URL so
      // it can still be selected by hand.
      window.prompt('Copy this link:', link.url);
    }
  };

  const revoke = async (link) => {
    const reason = window.prompt(`Stop "${link.label}" accepting applications — why?`);
    if (!reason || reason.trim().length < 3) return;
    try {
      await postJSON(`/api/distributors/registration-links/${link.id}/revoke`,
        { reason: reason.trim() });
      flash(`${link.label} revoked`);
      await load();
    } catch (e) { setErr(e.message); }
  };

  if (reviewing) {
    return (
      <Review registrationId={reviewing} onClose={() => setReviewing(null)}
        onDecided={async (msg) => {
          setReviewing(null); flash(msg); await load();
          if (onChanged) onChanged();
        }} />
    );
  }

  if (loading) return <SkeletonCards n={3} />;

  return (
    <div>
      {toast && <div style={{ marginBottom: space(2) }}><Banner tone="success" title="Done">{toast}</Banner></div>}
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <Banner tone="info" title="What a registration link is">
        It lets anyone who has it APPLY to become a distributor, and nothing
        else — no account, no prices, no ordering. Share it as widely as you
        like. Every application lands in the queue below and becomes a
        distributor only when somebody here approves it.
      </Banner>

      {admin && <NewLink onDone={load} onError={setErr} />}

      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <SectionTitle right={
          <Btn size="sm" variant="ghost"
            onClick={() => setShowDead((v) => !v)}>
            {showDead ? 'Live links only' : 'Show expired and revoked'}
          </Btn>
        }>Links</SectionTitle>
        <DataTable
          cols={[
            { key: 'label', label: 'Label', wrap: true },
            { key: 'campaign', label: 'Campaign', wrap: true },
            { key: 'url', label: 'Link', wrap: true },
            { key: 'view_count', label: 'Opened', align: 'right' },
            { key: 'submission_count', label: 'Applications', align: 'right' },
            { key: 'expires_at', label: 'Expires' },
            { key: 'issued_by', label: 'Issued by', wrap: true },
            { key: 'act', label: '', align: 'right' },
          ]}
          rows={links}
          empty="No registration links. Create one to start collecting applications."
          render={(row, c) => {
            if (c.key === 'url') {
              // A link issued before the token was kept cannot be shown --
              // there was never anything stored but a fingerprint of it.
              if (!row.url) {
                return (
                  <span style={{ fontSize: 12, color: color.textMuted }}>
                    Not recoverable (…{row.token_hint}) — revoke and issue a new one
                  </span>
                );
              }
              return (
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <input readOnly value={row.url}
                    onFocus={(e) => e.target.select()}
                    style={{
                      ...input, width: 260, fontFamily: 'monospace',
                      fontSize: 11.5, padding: '5px 8px',
                    }} />
                  <Btn size="sm" variant="secondary" onClick={() => copyLink(row)}>
                    {copied === row.id ? 'Copied' : 'Copy'}
                  </Btn>
                </div>
              );
            }
            if (c.key === 'expires_at') {
              return row.is_live
                ? when(row.expires_at)
                : <Chip tone="neutral">{row.revoked_at ? 'Revoked' : 'Expired'}</Chip>;
            }
            if (c.key === 'submission_count') {
              return row.max_submissions
                ? `${row.submission_count} / ${row.max_submissions}`
                : row.submission_count;
            }
            if (c.key === 'act') {
              return admin && row.is_live
                ? <Btn size="sm" variant="ghost" onClick={() => revoke(row)}>Revoke</Btn>
                : null;
            }
            return row[c.key] ?? '—';
          }}
        />
        <div style={{ fontSize: 12, color: color.textMuted, marginTop: space(1.5), lineHeight: 1.6 }}>
          A registration link is meant to be published, so it stays here to be
          copied whenever you need it. If one has gone astray or has served its
          purpose, revoke it — that stops it accepting applications immediately.
        </div>
      </Card>

      <Card pad={2.5}>
        <SectionTitle right={
          <Btn size="sm" variant="ghost" onClick={() => setPendingOnly((v) => !v)}>
            {pendingOnly ? 'Show all applications' : 'Awaiting review only'}
          </Btn>
        }>Applications</SectionTitle>
        <DataTable
          cols={[
            { key: 'registration_reference', label: 'Reference' },
            { key: 'legal_name', label: 'Business', wrap: true },
            { key: 'phone', label: 'Phone' },
            { key: 'state', label: 'State' },
            { key: 'claims_existing_customer', label: 'Existing?' },
            { key: 'submitted_at', label: 'Applied' },
            { key: 'status', label: 'Status' },
            { key: 'act', label: '', align: 'right' },
          ]}
          rows={registrations}
          empty={pendingOnly ? 'Nothing awaiting review.' : 'No applications yet.'}
          render={(row, c) => {
            if (c.key === 'claims_existing_customer') {
              if (!row.claims_existing_customer) return '—';
              return <Chip tone="info">{row.claimed_customer || 'Says yes'}</Chip>;
            }
            if (c.key === 'submitted_at') return when(row.submitted_at);
            if (c.key === 'status') {
              return (
                <>
                  <Chip tone={tone(row.status)}>{row.status}</Chip>
                  {row.distributor_code && (
                    <span style={{ marginLeft: 6, fontSize: 12, color: color.textSecondary }}>
                      {row.distributor_code}
                    </span>
                  )}
                </>
              );
            }
            if (c.key === 'act') {
              return (
                <Btn size="sm" variant="secondary" onClick={() => setReviewing(row.id)}>
                  {['PENDING', 'REVIEWING'].includes(row.status) ? 'Review' : 'Open'}
                </Btn>
              );
            }
            return row[c.key] ?? '—';
          }}
        />
      </Card>
    </div>
  );
}

export default RegistrationDesk;
