// Company Calls — place a call, and keep a record of who was called and for
// how long.
//
// Mounted from AppMain when activeModule === 'calls'.
//
// HOW THIS WORKS, AND THE ONE THING IT CANNOT DO
// ----------------------------------------------
// Tapping Call hands off to the phone's dialer (tel:) or to WhatsApp. The
// browser is backgrounded at that moment and learns nothing about the call --
// there is no call-log API on Android or iOS, and there is not going to be one.
//
// So the app times how long it was in the background and, when the staff member
// returns, SHOWS them that figure and asks them to confirm or correct it. The
// record stores which of those happened. A duration is never displayed here
// without its provenance beside it, because an estimate rendered like a fact is
// worse than no estimate at all.
//
// Contacts come from two places: the company customer database (searchable,
// server-side) and the phone's own address book via the Contact Picker API,
// which exists on Chrome for Android and nowhere else. The picker button only
// appears where it will actually work.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { authedFetch } from './utils/api';
import { color, font, radius, space } from './ui/theme';
import {
  Banner, Btn, Card, Chip, DataTable, ErrorBox, Icon, SectionTitle, Skeleton,
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

/** Seconds -> "4m 12s". Durations here are minutes, not hours. */
const hms = (s) => {
  const n = Math.max(0, Math.round(Number(s) || 0));
  const m = Math.floor(n / 60);
  const r = n % 60;
  if (m >= 60) return `${Math.floor(m / 60)}h ${m % 60}m`;
  return m ? `${m}m ${r}s` : `${r}s`;
};

// How a duration was arrived at. Shown next to every figure.
const SOURCE_LABEL = {
  CONFIRMED: ['Confirmed', 'success'],
  MEASURED: ['Timed by app', 'info'],
  MANUAL: ['Typed in', 'warning'],
  UNKNOWN: ['Not known', 'neutral'],
  VERIFIED: ['Network verified', 'success'],
};
const SourceChip = ({ s }) => {
  const [label, t] = SOURCE_LABEL[s] || SOURCE_LABEL.UNKNOWN;
  return <Chip tone={t}>{label}</Chip>;
};

const inputStyle = {
  padding: '11px 12px', border: `1px solid ${color.borderStrong}`,
  borderRadius: radius.sm, fontSize: 16, fontFamily: font.family,
  color: color.text, background: '#fff', width: '100%', boxSizing: 'border-box',
};

function Field({ label, hint, children }) {
  return (
    <label style={{ display: 'block', fontSize: 12.5, color: color.textSecondary, fontWeight: 600, marginBottom: space(2) }}>
      {label}
      <div style={{ marginTop: 5 }}>{children}</div>
      {hint && <div style={{ marginTop: 4, fontSize: 11.5, color: color.textMuted, fontWeight: 400, lineHeight: 1.45 }}>{hint}</div>}
    </label>
  );
}

function Sheet({ title, onClose, children, footer }) {
  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, []);
  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)', zIndex: 1000,
      display: 'flex', alignItems: 'flex-end', justifyContent: 'center',
    }}>
      <div onClick={(e) => e.stopPropagation()} style={{
        background: '#fff', width: '100%', maxWidth: 560, maxHeight: '92vh',
        display: 'flex', flexDirection: 'column',
        borderRadius: `${radius.lg} ${radius.lg} 0 0`,
        boxShadow: '0 -8px 32px rgba(15,23,42,0.18)',
      }}>
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: `${space(2)} ${space(2.5)}`, borderBottom: `1px solid ${color.border}`,
          flexShrink: 0,
        }}>
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>{title}</h3>
          <button onClick={onClose} aria-label="Close" style={{
            border: 'none', background: 'transparent', fontSize: 26, lineHeight: 1,
            color: color.textMuted, cursor: 'pointer', padding: '0 4px',
          }}>&times;</button>
        </div>
        <div style={{ padding: space(2.5), overflowY: 'auto', flex: 1 }}>{children}</div>
        {footer && <div style={{ padding: space(2.5), borderTop: `1px solid ${color.border}`, background: '#FbFcFe', flexShrink: 0 }}>{footer}</div>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pick who to call
// ---------------------------------------------------------------------------

const phonePickerAvailable = () =>
  typeof navigator !== 'undefined' && 'contacts' in navigator
  && navigator.contacts && typeof navigator.contacts.select === 'function';

function ContactPicker({ onPick, onClose }) {
  const [q, setQ] = useState('');
  const [contacts, setContacts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [manual, setManual] = useState('');

  const search = useCallback(async (term) => {
    setLoading(true);
    try {
      const d = await getJSON(`/api/calls/contacts?q=${encodeURIComponent(term)}`);
      setContacts(d.contacts || []);
      setErr('');
    } catch (e) { setErr(e.message); }
    setLoading(false);
  }, []);

  useEffect(() => {
    const t = setTimeout(() => search(q), q ? 250 : 0);
    return () => clearTimeout(t);
  }, [q, search]);

  const pickFromPhone = async () => {
    try {
      const picked = await navigator.contacts.select(['name', 'tel'], { multiple: false });
      if (!picked || !picked.length) return;
      const c = picked[0];
      const tel = (c.tel && c.tel[0]) || '';
      if (!tel) { setErr('That contact has no phone number.'); return; }
      onPick({
        contact_phone: tel,
        contact_name: (c.name && c.name[0]) || tel,
        contact_source: 'PHONE_CONTACT',
      });
    } catch (e) {
      // A cancelled picker throws too; only report something worth reporting.
      if (e && e.name !== 'AbortError') setErr(`Could not open contacts: ${e.message}`);
    }
  };

  return (
    <Sheet title="Who are you calling?" onClose={onClose}>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <Field label="Search company customers">
        <input value={q} onChange={(e) => setQ(e.target.value)} autoFocus
          placeholder="Name, code or number" style={inputStyle} />
      </Field>

      {phonePickerAvailable() && (
        <div style={{ marginBottom: space(2) }}>
          <Btn variant="secondary" icon="users" onClick={pickFromPhone} style={{ width: '100%' }}>
            Choose from my phone contacts
          </Btn>
        </div>
      )}

      {loading ? <Skeleton h={120} /> : (
        <div style={{ display: 'grid', gap: 6, marginBottom: space(2) }}>
          {contacts.length === 0 && (
            <div style={{ color: color.textMuted, fontSize: 13, padding: space(2), textAlign: 'center' }}>
              {q ? 'No customer matches that.' : 'No customers with phone numbers yet.'}
            </div>
          )}
          {contacts.map((c) => (
            <button key={c.id} onClick={() => onPick({
              customer_id: c.id, contact_phone: c.phone,
              contact_name: c.name, contact_source: 'CUSTOMER',
            })} style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '11px 12px', border: `1px solid ${color.border}`,
              borderRadius: radius.sm, background: '#fff', cursor: 'pointer',
              textAlign: 'left', width: '100%', fontFamily: font.family,
            }}>
              <span>
                <span style={{ display: 'block', fontWeight: 600, fontSize: 14, color: color.text }}>{c.name}</span>
                <span style={{ fontSize: 12, color: color.textMuted }}>{c.phone}</span>
              </span>
              <Icon name="trendUp" size={16} color={color.medical} />
            </button>
          ))}
        </div>
      )}

      <Field label="Or type a number"
        hint="For someone not in the customer list and not in your phone.">
        <div style={{ display: 'flex', gap: 8 }}>
          <input value={manual} onChange={(e) => setManual(e.target.value)}
            placeholder="0803 123 4567" inputMode="tel" style={inputStyle} />
          <Btn variant="secondary" disabled={manual.trim().length < 4}
            onClick={() => onPick({
              contact_phone: manual.trim(), contact_name: manual.trim(),
              contact_source: 'MANUAL',
            })}>Use</Btn>
        </div>
      </Field>
    </Sheet>
  );
}

// ---------------------------------------------------------------------------
// Place the call, then confirm how long it took
// ---------------------------------------------------------------------------

function CallFlow({ contact, config, onClose, onDone }) {
  // Default to the company line where it exists: it is the only mode
  // whose duration the network vouches for.
  const [channel, setChannel] = useState(
    config && config.bridging_available ? 'BRIDGE' : 'PHONE');
  const [purpose, setPurpose] = useState('');
  const [stage, setStage] = useState('ready');   // ready -> dialling -> confirm
  const [call, setCall] = useState(null);
  const [measured, setMeasured] = useState(0);
  const [minutes, setMinutes] = useState('0');
  const [seconds, setSeconds] = useState('0');
  const [outcome, setOutcome] = useState('ANSWERED');
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const leftAt = useRef(null);
  const [bridge, setBridge] = useState(null);   // live status while bridging
  const [staffPhone, setStaffPhone] = useState((config && config.your_phone) || '');

  // The measurement. The app is backgrounded the moment the dialer opens, so
  // "time hidden" is the closest thing to a call duration a browser can know.
  // It is an upper bound on the call, never the call itself.
  useEffect(() => {
    if (stage !== 'dialling') return undefined;
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') {
        leftAt.current = Date.now();
      } else if (leftAt.current) {
        const away = Math.round((Date.now() - leftAt.current) / 1000);
        setMeasured(away);
        setMinutes(String(Math.floor(away / 60)));
        setSeconds(String(away % 60));
        setStage('confirm');
      }
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, [stage]);

  // Bridged calls need no timing and no confirmation: the network reports the
  // duration. Poll until it lands, then close.
  useEffect(() => {
    if (stage !== 'bridging' || !call) return undefined;
    let stop = false;
    const tick = async () => {
      try {
        const st = await getJSON(`/api/calls/${call.id}/status`);
        if (stop) return;
        setBridge(st);
        if (st.status === 'COMPLETED') {
          onDone(`Call logged: ${hms(st.duration_seconds)}, timed by the network.`);
        } else if (st.status === 'CANCELLED') {
          setErr(st.failure_reason || 'The call did not connect.');
          setStage('ready');
        }
      } catch { /* transient: keep polling */ }
    };
    const h = setInterval(tick, 4000);
    tick();
    return () => { stop = true; clearInterval(h); };
  }, [stage, call, onDone]);

  const placeBridged = async () => {
    setErr(''); setBusy(true);
    try {
      const created = await postJSON('/api/calls/bridge', {
        ...contact,
        purpose: purpose.trim() || null,
        staff_phone: staffPhone.trim() || null,
      });
      setCall(created);
      setStage('bridging');
      setBusy(false);
    } catch (e) { setErr(e.message); setBusy(false); }
  };

  const place = async () => {
    if (channel === 'BRIDGE') return placeBridged();
    setErr(''); setBusy(true);
    try {
      let coords = {};
      try {
        coords = await new Promise((resolve) => {
          if (!navigator.geolocation) return resolve({});
          navigator.geolocation.getCurrentPosition(
            (p) => resolve({ latitude: p.coords.latitude, longitude: p.coords.longitude }),
            () => resolve({}), { timeout: 3000, maximumAge: 60000 });
        });
      } catch { coords = {}; }

      const created = await postJSON('/api/calls', {
        ...contact, channel, purpose: purpose.trim() || null, ...coords,
      });
      setCall(created);
      setStage('dialling');
      setBusy(false);

      const num = created.contact_phone;
      if (channel === 'WHATSAPP') {
        // WhatsApp has no public deep link that STARTS a call. wa.me opens the
        // conversation; the staff member taps the call icon there. Promising
        // more than this would be a button that silently does nothing.
        window.location.href = `https://wa.me/${num.replace(/[^0-9]/g, '')}`;
      } else {
        window.location.href = `tel:${num}`;
      }
    } catch (e) { setErr(e.message); setBusy(false); }
  };

  const finish = async (source) => {
    setErr(''); setBusy(true);
    const total = (parseInt(minutes, 10) || 0) * 60 + (parseInt(seconds, 10) || 0);
    try {
      await postJSON(`/api/calls/${call.id}/complete`, {
        duration_seconds: total,
        duration_source: source,
        measured_seconds: measured || null,
        outcome, purpose: purpose.trim() || null,
        notes: notes.trim() || null,
      });
      onDone(`Call to ${contact.contact_name || contact.contact_phone} logged (${hms(total)}).`);
    } catch (e) { setErr(e.message); setBusy(false); }
  };

  const cancel = async () => {
    if (call) { try { await postJSON(`/api/calls/${call.id}/cancel`); } catch { /* record stays open; harmless */ } }
    onClose();
  };

  if (stage === 'confirm') {
    const edited = ((parseInt(minutes, 10) || 0) * 60 + (parseInt(seconds, 10) || 0)) !== measured;
    return (
      <Sheet title="How long was the call?" onClose={() => {}}
        footer={
          <Btn variant="accent" disabled={busy} style={{ width: '100%' }}
            onClick={() => finish(edited ? 'MANUAL' : 'CONFIRMED')}>
            {busy ? 'Saving…' : 'Save this call'}
          </Btn>
        }>
        {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

        <Banner tone="info" title={`You were away for ${hms(measured)}`}>
          That includes dialling and ringing, so the call itself was probably
          shorter. Correct it if you know better — the record notes whether the
          figure was confirmed or typed in.
        </Banner>
        <div style={{ height: space(2) }} />

        <Field label="Call duration">
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input type="number" min="0" value={minutes} inputMode="numeric"
              onChange={(e) => setMinutes(e.target.value)}
              style={{ ...inputStyle, textAlign: 'center' }} />
            <span style={{ fontSize: 13, color: color.textSecondary }}>min</span>
            <input type="number" min="0" max="59" value={seconds} inputMode="numeric"
              onChange={(e) => setSeconds(e.target.value)}
              style={{ ...inputStyle, textAlign: 'center' }} />
            <span style={{ fontSize: 13, color: color.textSecondary }}>sec</span>
          </div>
        </Field>

        <Field label="What happened?">
          <select value={outcome} onChange={(e) => setOutcome(e.target.value)} style={inputStyle}>
            <option value="ANSWERED">Answered</option>
            <option value="NO_ANSWER">No answer</option>
            <option value="BUSY">Busy</option>
            <option value="VOICEMAIL">Left a message</option>
            <option value="WRONG_NUMBER">Wrong number</option>
          </select>
        </Field>

        <Field label="Notes (optional)">
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3}
            placeholder="What was agreed?" style={{ ...inputStyle, resize: 'vertical' }} />
        </Field>
      </Sheet>
    );
  }

  if (stage === 'bridging') {
    const state = (bridge && bridge.bridge_state) || 'QUEUED';
    const WORDS = {
      QUEUED: 'Connecting to the network…',
      RINGING_STAFF: 'Your phone is ringing — answer it.',
      BRIDGING: 'Connecting you to the customer…',
      COMPLETED: 'Call finished.',
    };
    return (
      <Sheet title="Company line" onClose={() => {}}
        footer={
          <Btn variant="ghost" style={{ width: '100%' }}
            onClick={() => onDone('Call left running; its record will complete itself.')}>
            Close this and carry on
          </Btn>
        }>
        {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}
        <div style={{ textAlign: 'center', padding: space(3) }}>
          <Icon name="payroll" size={40} color={color.medical} />
          <h3 style={{ margin: `${space(2)} 0 6px`, fontSize: 17 }}>
            {contact.contact_name || contact.contact_phone}
          </h3>
          <p style={{ color: color.textSecondary, fontSize: 14, margin: 0, fontWeight: 600 }}>
            {WORDS[state] || state}
          </p>
          <p style={{ color: color.textMuted, fontSize: 12.5, lineHeight: 1.6, marginTop: space(2) }}>
            You do not need to time this call or type anything afterwards —
            the network reports how long it lasted.
          </p>
        </div>
      </Sheet>
    );
  }

  if (stage === 'dialling') {
    return (
      <Sheet title="Calling…" onClose={() => {}}
        footer={
          <div style={{ display: 'grid', gap: 8 }}>
            <Btn variant="accent" style={{ width: '100%' }}
              onClick={() => { setMeasured(0); setMinutes('0'); setSeconds('0'); setStage('confirm'); }}>
              I'm back — log this call
            </Btn>
            <Btn variant="ghost" style={{ width: '100%' }} onClick={cancel}>
              The call didn't happen
            </Btn>
          </div>
        }>
        <div style={{ textAlign: 'center', padding: space(3) }}>
          <Icon name="payroll" size={40} color={color.medical} />
          <h3 style={{ margin: `${space(2)} 0 4px`, fontSize: 17 }}>
            {contact.contact_name || contact.contact_phone}
          </h3>
          <p style={{ color: color.textSecondary, fontSize: 13.5, lineHeight: 1.6, margin: 0 }}>
            {channel === 'WHATSAPP'
              ? 'WhatsApp is opening. Tap the call icon there, and come back when you are done.'
              : 'Your phone is dialling. Come back to this app when the call ends and it will log itself.'}
          </p>
        </div>
      </Sheet>
    );
  }

  return (
    <Sheet title={`Call ${contact.contact_name || contact.contact_phone}`} onClose={onClose}
      footer={
        <Btn variant="accent" icon="payroll" disabled={busy} style={{ width: '100%' }}
          onClick={place}>{busy ? 'Starting…' : (
            channel === 'BRIDGE' ? 'Call on the company line'
              : channel === 'WHATSAPP' ? 'Call on WhatsApp' : 'Call on my phone')}</Btn>
      }>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <Card pad={2} style={{ marginBottom: space(2) }}>
        <div style={{ fontWeight: 700, fontSize: 15 }}>{contact.contact_name}</div>
        <div style={{ fontSize: 13, color: color.textSecondary }}>{contact.contact_phone}</div>
      </Card>

      <Field label="How do you want to call?">
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {[
            ...(config && config.bridging_available
              ? [['BRIDGE', 'Company line']] : []),
            ['PHONE', 'Phone call'], ['WHATSAPP', 'WhatsApp'],
          ].map(([k, label]) => (
            <button key={k} onClick={() => setChannel(k)} style={{
              flex: 1, padding: '11px 12px', borderRadius: radius.sm, fontSize: 14,
              fontWeight: 600, cursor: 'pointer', fontFamily: font.family,
              border: `1px solid ${channel === k ? color.medical : color.borderStrong}`,
              background: channel === k ? color.infoBg : '#fff',
              color: channel === k ? color.royal : color.textSecondary,
            }}>{label}</button>
          ))}
        </div>
      </Field>

      {channel === 'BRIDGE' ? (
        <>
          <Banner tone="success" title="The network will time this call">
            We ring your phone first, then connect you to the customer. The
            duration and cost come from the carrier, so nothing has to be
            confirmed afterwards.
          </Banner>
          <div style={{ height: space(2) }} />
          <Field label="Ring me on"
            hint="Your own line. Change it only if you are on a different phone today.">
            <input value={staffPhone} onChange={(e) => setStaffPhone(e.target.value)}
              placeholder="0803 123 4567" inputMode="tel" style={inputStyle} />
          </Field>
        </>
      ) : (
        <div style={{ marginBottom: space(2) }}>
          <Banner tone="warning" title="You will have to confirm the length">
            {channel === 'WHATSAPP'
              ? 'WhatsApp calls cannot be timed by any app. You will be asked how long it took.'
              : 'The app can only time how long you were away, so you will be asked to confirm it.'}
          </Banner>
        </div>
      )}

      <Field label="What is the call about?" hint="Optional, but it is what makes the log useful later.">
        <input value={purpose} onChange={(e) => setPurpose(e.target.value)}
          placeholder="Follow up on outstanding invoice" style={inputStyle} />
      </Field>
    </Sheet>
  );
}

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

export default function CallModule() {
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [toast, setToast] = useState('');
  const [mine, setMine] = useState({ calls: [], today: { calls: 0, seconds: 0 } });
  const [picking, setPicking] = useState(false);
  const [contact, setContact] = useState(null);
  const [config, setConfig] = useState(null);

  const load = useCallback(async () => {
    try {
      // Asked before any Call button is drawn, so the company-line option is
      // only offered where it will actually work.
      const [m, c] = await Promise.all([
        getJSON('/api/calls/me'),
        getJSON('/api/calls/config').catch(() => null),
      ]);
      setMine(m);
      setConfig(c);
      setErr('');
    } catch (e) { setErr(e.message); }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const flash = (m) => { setToast(m); setTimeout(() => setToast(''), 5000); };

  if (loading) return <div style={{ padding: space(2) }}><Skeleton h={120} /></div>;

  return (
    <div style={{ maxWidth: 720, margin: '0 auto', paddingBottom: space(4) }}>
      {toast && <div style={{ marginBottom: space(2) }}><Banner tone="success" title="Logged">{toast}</Banner></div>}
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <Card pad={3} style={{
        background: `linear-gradient(135deg, ${color.navy} 0%, ${color.royal} 100%)`,
        border: 'none', color: '#fff', marginBottom: space(2),
      }}>
        <div style={{ fontSize: 12, opacity: 0.75, textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 600 }}>
          Calls today
        </div>
        <div style={{ fontSize: 34, fontWeight: 800, letterSpacing: '-0.02em', lineHeight: 1.15, marginTop: 4 }}>
          {mine.today.calls}
        </div>
        <div style={{ fontSize: 12.5, opacity: 0.85, marginTop: 4 }}>
          {hms(mine.today.seconds)} on the phone
        </div>
      </Card>

      <Btn variant="accent" icon="payroll" style={{ width: '100%', marginBottom: space(3) }}
        onClick={() => setPicking(true)}>Make a call</Btn>

      <Card pad={0} style={{ overflow: 'hidden' }}>
        <div style={{ padding: space(2.5), paddingBottom: 0 }}>
          <SectionTitle>My recent calls</SectionTitle>
        </div>
        <div style={{ padding: `0 ${space(2.5)} ${space(2.5)}` }}>
          <DataTable
            cols={[
              { key: 'contact', label: 'Who', wrap: true },
              { key: 'started_at', label: 'When' },
              { key: 'duration_seconds', label: 'Length', align: 'right' },
              { key: 'duration_source', label: 'Figure' },
            ]}
            rows={mine.calls}
            empty="No calls logged yet."
            render={(r, c) => {
              if (c.key === 'contact') {
                return (
                  <div style={{ maxWidth: 220, whiteSpace: 'normal' }}>
                    <div style={{ fontWeight: 600 }}>{r.customer_name || r.contact_name || r.contact_phone}</div>
                    <div style={{ fontSize: 11.5, color: color.textMuted }}>
                      {r.channel === 'WHATSAPP' ? 'WhatsApp' : 'Phone'}
                      {r.purpose ? ` · ${r.purpose}` : ''}
                    </div>
                  </div>
                );
              }
              if (c.key === 'started_at') {
                const d = new Date(r.started_at);
                return <span style={{ fontSize: 12.5 }}>{d.toLocaleDateString()}<br />{d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>;
              }
              if (c.key === 'duration_seconds') {
                if (r.status !== 'COMPLETED') return <Chip tone="neutral">{r.status === 'CANCELLED' ? 'Not made' : 'Open'}</Chip>;
                return <strong>{hms(r.duration_seconds)}</strong>;
              }
              if (c.key === 'duration_source') {
                return r.status === 'COMPLETED' ? <SourceChip s={r.duration_source} /> : null;
              }
              return r[c.key];
            }}
          />
        </div>
      </Card>

      <div style={{ marginTop: space(2) }}>
        {config && config.bridging_available ? (
          <Banner tone="success" title="Company line calls are timed by the network">
            Calls placed on the company line carry the carrier's own duration
            and cost — nobody here can alter them. Calls made straight from
            your phone or on WhatsApp are estimates you confirm yourself, and
            every length below says which it is.
          </Banner>
        ) : (
          <Banner tone="info" title="About these timings">
            The app cannot see your phone's call log — no web app can. It times
            how long you were away and asks you to confirm it, so every length
            here is marked with where the number came from.
          </Banner>
        )}
      </div>

      {picking && (
        <ContactPicker onClose={() => setPicking(false)}
          onPick={(c) => { setContact(c); setPicking(false); }} />
      )}
      {contact && (
        <CallFlow contact={contact} config={config} onClose={() => setContact(null)}
          onDone={async (msg) => { setContact(null); flash(msg); await load(); }} />
      )}
    </div>
  );
}
