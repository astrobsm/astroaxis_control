// Meetings, for people who are signed in.
//
// WHAT REPLACED WHAT
// ------------------
// The screen this supersedes asked for a room name, prefixed it with
// "AstroBSM_", and mounted the public meet.jit.si. Anyone who guessed
// "AstroBSM_weekly-standup" was in the management meeting, and nothing about
// the meeting was ever written down.
//
// So this screen exists for the parts Jitsi does not do: scheduling, who is
// invited and as what, the shareable link and its life, the waiting room, and
// the attendance record afterwards. The media is still Jitsi's.
//
// THE LINK IS SHOWN ONCE
// ----------------------
// Only its fingerprint is stored, exactly as the distributor ordering link
// works. A host who loses it regenerates, which invalidates the old one. That
// trade is deliberate: a link that can be re-read from the database is a link
// that leaks with the database.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { authedFetch } from './utils/api';
import { color, radius, space } from './ui/theme';
import {
  Banner, Btn, Card, Chip, DataTable, ErrorBox, SectionTitle, SkeletonCards,
} from './ui/kit';
import MeetingRoom from './MeetingRoom';

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

const input = {
  width: '100%', padding: '9px 11px', borderRadius: radius.sm,
  border: `1px solid ${color.borderStrong}`, fontSize: 13.5,
  fontFamily: 'inherit', boxSizing: 'border-box', background: '#fff',
};

const TONES = {
  SCHEDULED: 'info', LIVE: 'success', ENDED: 'neutral', CANCELLED: 'danger',
};
const tone = (s) => TONES[String(s || '').toUpperCase()] || 'neutral';

// Who is signed in. For filtering the invite list only -- every permission
// here is decided by the server, per meeting.
function currentUserId() {
  try {
    const raw = localStorage.getItem('user') || localStorage.getItem('currentUser');
    return String(JSON.parse(raw || '{}').id || '');
  } catch { return ''; }
}

const when = (v) => (v ? new Date(v).toLocaleString(undefined, {
  day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
}) : '—');

const minutes = (seconds) => (seconds === null || seconds === undefined
  ? '—' : `${Math.max(1, Math.round(seconds / 60))} min`);

function Field({ label, hint, children }) {
  return (
    <label style={{ display: 'block', marginBottom: space(1.5) }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: color.textSecondary,
        marginBottom: 5 }}>{label}</div>
      {children}
      {hint && (
        <div style={{ fontSize: 11.5, color: color.textMuted, marginTop: 4,
          lineHeight: 1.5 }}>{hint}</div>
      )}
    </label>
  );
}

// ---------------------------------------------------------------------------
// The link, after it has been issued
// ---------------------------------------------------------------------------

function LinkPanel({ joinUrl, invitation, expiresAt, securedBy }) {
  const [copied, setCopied] = useState('');

  const copy = async (what, value) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(what);
      setTimeout(() => setCopied(''), 2500);
    } catch {
      window.prompt('Copy this:', value);
    }
  };

  const share = async () => {
    if (!navigator.share) { copy('invitation', invitation); return; }
    try { await navigator.share({ text: invitation }); } catch { /* cancelled */ }
  };

  return (
    <Card pad={2.5} style={{ marginBottom: space(2) }}>
      <SectionTitle>Meeting link</SectionTitle>

      <Banner tone={securedBy === 'signature' ? 'success' : 'warning'}
        title={securedBy === 'signature'
          ? 'Protected by a signature' : 'Protected by being unguessable'}>
        {securedBy === 'signature'
          ? 'The meeting service checks a signature this server produces, so a '
            + 'forwarded link is worth nothing without it.'
          : 'Anyone holding this link can join. It cannot be guessed, but it '
            + 'can be forwarded — send it only to the people you want in the '
            + 'meeting, and revoke it if it goes astray.'}
      </Banner>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap',
        marginTop: space(1.5) }}>
        <input readOnly value={joinUrl} onFocus={(e) => e.target.select()}
          style={{ ...input, flex: '1 1 300px', fontFamily: 'monospace',
            fontSize: 12 }} />
        <Btn size="sm" variant="accent" onClick={() => copy('link', joinUrl)}>
          {copied === 'link' ? 'Copied' : 'Copy link'}
        </Btn>
        <Btn size="sm" variant="secondary"
          onClick={() => copy('invitation', invitation)}>
          {copied === 'invitation' ? 'Copied' : 'Copy invitation'}
        </Btn>
        {typeof navigator !== 'undefined' && navigator.share && (
          <Btn size="sm" variant="ghost" onClick={share}>Share</Btn>
        )}
      </div>

      <pre style={{
        marginTop: space(1.5), padding: space(1.5), background: '#F8FAFC',
        border: `1px solid ${color.border}`, borderRadius: radius.sm,
        fontSize: 12, lineHeight: 1.6, whiteSpace: 'pre-wrap',
        fontFamily: 'inherit', color: color.textSecondary,
      }}>{invitation}</pre>

      <div style={{ fontSize: 12, color: color.textMuted, marginTop: space(1),
        lineHeight: 1.6 }}>
        Shown once — only a fingerprint is stored. Expires {when(expiresAt)}.
        If you lose it, regenerate: the old link stops working immediately.
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Scheduling
// ---------------------------------------------------------------------------

const BLANK = {
  title: '', description: '', scheduled_start: '', duration_minutes: 60,
  host_user_id: '', guest_access_enabled: true, waiting_room: true,
  guest_screen_share: false, guest_chat: true, max_participants: 50,
  passcode: '',
};

function ScheduleForm({ staff, onCreated, onCancel }) {
  // The person scheduling is already the host. Offering their own name in the
  // invite list is offering them a way to demote themselves -- which is
  // exactly what happened on the first real meeting: the host ticked their
  // own name, became a PARTICIPANT, and could not admit the people waiting
  // outside. The server now refuses to demote a host; this stops the screen
  // asking a question with only one wrong answer.
  const me = currentUserId();
  const [form, setForm] = useState(BLANK);
  const [invited, setInvited] = useState([]);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const set = (k) => (e) => {
    const v = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [k]: v }));
  };

  const submit = async () => {
    setBusy(true); setErr('');
    try {
      const body = {
        ...form,
        duration_minutes: Number(form.duration_minutes),
        max_participants: Number(form.max_participants),
        scheduled_start: new Date(form.scheduled_start).toISOString(),
        participants: invited.map((i) => ({ user_id: i.id, role: i.role })),
      };
      if (!body.passcode) delete body.passcode;
      if (!body.description) delete body.description;
      // Blank means "me", and the server defaults to the caller. Sending an
      // empty string would be sending a user id that is not one.
      if (!body.host_user_id) delete body.host_user_id;
      onCreated(await postJSON('/api/meetings', body));
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  const toggle = (person, role) => {
    setInvited((list) => {
      const found = list.find((i) => i.id === person.id);
      if (found && found.role === role) return list.filter((i) => i.id !== person.id);
      if (found) return list.map((i) => (i.id === person.id ? { ...i, role } : i));
      return [...list, { id: person.id, name: person.full_name, role }];
    });
  };

  return (
    <Card pad={2.5} style={{ marginBottom: space(2) }}>
      <SectionTitle>Schedule a meeting</SectionTitle>
      {err && <ErrorBox msg={err} />}

      <Field label="Title">
        <input style={input} value={form.title} onChange={set('title')}
          placeholder="Weekly Production Meeting" />
      </Field>

      <Field label="Agenda (internal)"
        hint="Kept for the people in the meeting. Never included in a guest invitation.">
        <textarea style={{ ...input, minHeight: 64, resize: 'vertical' }}
          value={form.description} onChange={set('description')} />
      </Field>

      <div style={{ display: 'flex', gap: space(2), flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 220px' }}>
          <Field label="Starts">
            <input style={input} type="datetime-local"
              value={form.scheduled_start} onChange={set('scheduled_start')} />
          </Field>
        </div>
        <div style={{ flex: '1 1 130px' }}>
          <Field label="Minutes">
            <input style={input} type="number" min="1" max="1440"
              value={form.duration_minutes} onChange={set('duration_minutes')} />
          </Field>
        </div>
      </div>

      <Field label="Host"
        hint="Whoever runs the meeting: admits people, ends it, manages the link. Choose somebody else and you stay on it as a co-host, so you can still change or cancel what you arranged.">
        <select style={input} value={form.host_user_id}
          onChange={set('host_user_id')}>
          <option value="">Me</option>
          {(staff || []).filter((person) => String(person.id) !== me)
            .map((person) => (
              <option key={person.id} value={person.id}>
                {person.full_name}
              </option>
            ))}
        </select>
      </Field>

      <Field label="How people get in">
        <div style={{ display: 'grid', gap: space(1) }}>
          {[
            [false, 'Let everyone join straight away',
              'Anybody with the link walks in. Simplest, and right for a team '
              + 'meeting or a training session.'],
            [true, 'Hold guests in a waiting room until I admit them',
              'You approve each outside guest as they arrive. Staff who are '
              + 'signed in always join directly.'],
          ].map(([value, label, hint]) => (
            <label key={String(value)} style={{
              display: 'flex', gap: 10, alignItems: 'flex-start',
              cursor: 'pointer', padding: space(1.5),
              border: `1px solid ${form.waiting_room === value
                ? color.medical : color.border}`,
              background: form.waiting_room === value ? color.infoBg : '#fff',
              borderRadius: radius.md,
            }}>
              <input type="radio" name="joinMode" style={{ marginTop: 3 }}
                checked={form.waiting_room === value}
                onChange={() => setForm((f) => ({ ...f, waiting_room: value }))} />
              <span>
                <span style={{ fontSize: 13.5, fontWeight: 600 }}>{label}</span>
                <span style={{ display: 'block', fontSize: 12,
                  color: color.textSecondary, marginTop: 2, lineHeight: 1.5 }}>
                  {hint}
                </span>
              </span>
            </label>
          ))}
        </div>
      </Field>

      <Field label="Invite staff"
        hint="Tap once to invite, again for co-host, a third time to remove. The host is already in the meeting.">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {(staff || []).filter((person) => String(person.id) !== me
            && String(person.id) !== String(form.host_user_id))
            .map((person) => {
            const mine = invited.find((i) => i.id === person.id);
            return (
              <button key={person.id} type="button"
                onClick={() => toggle(person, mine ? 'CO_HOST' : 'PARTICIPANT')}
                style={{
                  padding: '5px 12px', borderRadius: radius.pill, fontSize: 12.5,
                  cursor: 'pointer', fontWeight: 600,
                  border: `1px solid ${mine ? color.medical : color.borderStrong}`,
                  background: mine ? color.infoBg : '#fff',
                  color: mine ? color.royal : color.textSecondary,
                }}>
                {person.full_name}
                {mine && mine.role === 'CO_HOST' ? ' · co-host' : ''}
              </button>
            );
          })}
        </div>
      </Field>

      <SectionTitle>Guests from outside</SectionTitle>
      <div style={{ display: 'grid', gap: space(1) }}>
        {[
          ['guest_access_enabled', 'Allow people outside the company to join by link'],
          ['guest_chat', 'Let guests use the meeting chat'],
          ['guest_screen_share', 'Let guests share their screen'],
        ].map(([key, label]) => (
          <label key={key} style={{ display: 'flex', gap: 10, fontSize: 13.5,
            alignItems: 'center', cursor: 'pointer' }}>
            <input type="checkbox" checked={form[key]} onChange={set(key)}
              style={{ width: 17, height: 17 }} />
            <span>{label}</span>
          </label>
        ))}
      </div>

      <div style={{ display: 'flex', gap: space(2), flexWrap: 'wrap',
        marginTop: space(2) }}>
        <div style={{ flex: '1 1 150px' }}>
          <Field label="Maximum people">
            <input style={input} type="number" min="2" max="500"
              value={form.max_participants} onChange={set('max_participants')} />
          </Field>
        </div>
        <div style={{ flex: '1 1 200px' }}>
          <Field label="Passcode (optional)"
            hint="Send it separately from the link, or it protects nothing.">
            <input style={input} value={form.passcode} onChange={set('passcode')} />
          </Field>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <Btn size="sm" variant="ghost" onClick={onCancel}>Cancel</Btn>
        <Btn size="sm" variant="accent" onClick={submit}
          disabled={busy || form.title.trim().length < 2 || !form.scheduled_start}>
          {busy ? 'Creating…' : 'Create meeting'}
        </Btn>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Running a meeting
// ---------------------------------------------------------------------------

function HostControls({ meetingId, onError }) {
  const [waiting, setWaiting] = useState([]);
  const [inRoom, setInRoom] = useState([]);

  const load = useCallback(async () => {
    try {
      const body = await getJSON(`/api/meetings/${meetingId}/waiting`);
      setWaiting(body.waiting || []);
      setInRoom(body.in_meeting || []);
    } catch (e) { onError && onError(e.message); }
  }, [meetingId, onError]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);

  const decide = async (waitingId, admit) => {
    try {
      await postJSON(`/api/meetings/${meetingId}/waiting/${waitingId}`, { admit });
      await load();
    } catch (e) { onError && onError(e.message); }
  };

  const remove = async (attendanceId) => {
    try {
      await postJSON(
        `/api/meetings/${meetingId}/participants/${attendanceId}/remove`);
      await load();
    } catch (e) { onError && onError(e.message); }
  };

  return (
    <>
      {waiting.length > 0 && (
        <Card pad={2.5} style={{ marginBottom: space(2) }}>
          <SectionTitle>
            Waiting to be let in ({waiting.length})
          </SectionTitle>
          <div style={{ display: 'grid', gap: space(1) }}>
            {waiting.map((w) => (
              <div key={w.id} style={{
                display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
                padding: space(1.5), border: `1px solid ${color.border}`,
                borderRadius: radius.md, background: color.warningBg,
              }}>
                <strong style={{ fontSize: 13.5 }}>{w.display_name}</strong>
                <Chip tone={w.is_guest ? 'warning' : 'info'}>
                  {w.is_guest ? 'Outside guest' : 'Staff'}
                </Chip>
                <span style={{ fontSize: 12, color: color.textMuted }}>
                  asked at {when(w.requested_at)}
                </span>
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                  <Btn size="sm" variant="accent"
                    onClick={() => decide(w.id, true)}>Admit</Btn>
                  <Btn size="sm" variant="ghost"
                    onClick={() => decide(w.id, false)}>Reject</Btn>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <SectionTitle>In the meeting ({inRoom.length})</SectionTitle>
        <DataTable
          cols={[
            { key: 'display_name', label: 'Name', wrap: true },
            { key: 'is_guest', label: 'Type' },
            { key: 'role', label: 'Role' },
            { key: 'joined_at', label: 'Joined' },
            { key: 'act', label: '', align: 'right' },
          ]}
          rows={inRoom}
          empty="Nobody has joined yet."
          render={(r, c) => {
            if (c.key === 'is_guest') {
              return <Chip tone={r.is_guest ? 'warning' : 'info'}>
                {r.is_guest ? 'Guest' : 'Staff'}</Chip>;
            }
            if (c.key === 'joined_at') return when(r.joined_at);
            if (c.key === 'act') {
              return r.role === 'HOST' ? null : (
                <Btn size="sm" variant="ghost"
                  onClick={() => remove(r.id)}>Remove</Btn>
              );
            }
            return r[c.key] ?? '—';
          }}
        />
        <div style={{ fontSize: 11.5, color: color.textMuted,
          marginTop: space(1), lineHeight: 1.6 }}>
          Removing closes their attendance record. Ejecting somebody from the
          live call is done with the host controls inside the meeting window.
        </div>
      </Card>
    </>
  );
}

// ---------------------------------------------------------------------------
// Host controls, INSIDE the meeting
// ---------------------------------------------------------------------------
//
// This sits above the conference window because that is the only place it is
// any use. The meeting fills the screen, so a waiting-room control anywhere
// else is a control the host cannot reach at the one moment it matters --
// while somebody is knocking.
//
// Closed it is a single button. It opens itself when somebody new arrives in
// the lobby, because a host talking to the room is not watching a button, and
// a supplier left waiting outside a meeting they were invited to is the
// failure this whole panel exists to prevent.

function InMeetingHostPanel({ meetingId, onError }) {
  const [open, setOpen] = useState(false);
  const [waiting, setWaiting] = useState([]);
  const [inRoom, setInRoom] = useState([]);
  const [busy, setBusy] = useState(null);
  const announced = useRef(0);

  const load = useCallback(async () => {
    try {
      const body = await getJSON(`/api/meetings/${meetingId}/waiting`);
      const queue = body.waiting || [];
      setWaiting(queue);
      setInRoom(body.in_meeting || []);
      // Opens on a NEW arrival only. Re-opening a panel the host deliberately
      // closed, every five seconds, while the same person waits, would be
      // worse than saying nothing.
      if (queue.length > announced.current) setOpen(true);
      announced.current = queue.length;
    } catch (e) { onError && onError(e.message); }
  }, [meetingId, onError]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [load]);

  const decide = async (waitingId, admit) => {
    setBusy(waitingId);
    try {
      await postJSON(`/api/meetings/${meetingId}/waiting/${waitingId}`, { admit });
      await load();
    } catch (e) { onError && onError(e.message); }
    finally { setBusy(null); }
  };

  const remove = async (attendanceId, name) => {
    if (!window.confirm(`Remove ${name} from the meeting?`)) return;
    setBusy(attendanceId);
    try {
      await postJSON(
        `/api/meetings/${meetingId}/participants/${attendanceId}/remove`);
      await load();
    } catch (e) { onError && onError(e.message); }
    finally { setBusy(null); }
  };

  const pill = {
    position: 'fixed', top: 58, right: 14, zIndex: 10001,
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '9px 15px', borderRadius: 22, cursor: 'pointer',
    border: 'none', fontSize: 13.5, fontWeight: 700,
    fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
    background: waiting.length ? '#B45309' : 'rgba(255,255,255,0.18)',
    color: '#fff', boxShadow: '0 2px 10px rgba(0,0,0,0.3)',
  };

  if (!open) {
    return (
      <button style={pill} onClick={() => setOpen(true)}
        aria-label={waiting.length
          ? `${waiting.length} waiting to be admitted` : 'Host controls'}>
        {waiting.length > 0 && (
          <span aria-hidden="true" style={{
            width: 9, height: 9, borderRadius: '50%', background: '#fff',
          }} />
        )}
        {waiting.length > 0
          ? `${waiting.length} waiting` : `Participants (${inRoom.length})`}
      </button>
    );
  }

  return (
    <div style={{
      position: 'fixed', top: 0, right: 0, bottom: 0, zIndex: 10001,
      width: 'min(370px, 100vw)', background: '#fff',
      boxShadow: '-6px 0 24px rgba(0,0,0,0.3)', display: 'flex',
      flexDirection: 'column',
      fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', padding: '12px 14px',
        borderBottom: `1px solid ${color.border}`, flexShrink: 0,
      }}>
        <strong style={{ fontSize: 14.5 }}>Host controls</strong>
        <button onClick={() => setOpen(false)} aria-label="Close host controls"
          style={{
            marginLeft: 'auto', border: 'none', background: 'transparent',
            fontSize: 24, lineHeight: 1, cursor: 'pointer',
            color: color.textMuted,
          }}>&times;</button>
      </div>

      <div style={{ overflowY: 'auto', padding: 14, flex: 1 }}>
        <div style={{
          fontSize: 11.5, fontWeight: 700, color: color.textSecondary,
          textTransform: 'uppercase', letterSpacing: '0.05em',
          marginBottom: 8,
        }}>
          Waiting to be let in ({waiting.length})
        </div>

        {waiting.length === 0 ? (
          <div style={{ fontSize: 13, color: color.textMuted, marginBottom: 20 }}>
            Nobody is waiting.
          </div>
        ) : (
          <div style={{ display: 'grid', gap: 8, marginBottom: 20 }}>
            {waiting.map((w) => (
              <div key={w.id} style={{
                border: `1px solid ${color.border}`, borderRadius: 9,
                padding: 11, background: '#FFFBEB',
              }}>
                <div style={{ fontSize: 13.5, fontWeight: 600 }}>
                  {w.display_name}
                </div>
                <div style={{ fontSize: 11.5, color: color.textSecondary,
                  marginTop: 2 }}>
                  {w.is_guest ? 'Outside guest' : 'Staff'} &middot; asked{' '}
                  {when(w.requested_at)}
                </div>
                <div style={{ display: 'flex', gap: 7, marginTop: 9 }}>
                  <button disabled={busy === w.id}
                    onClick={() => decide(w.id, true)} style={{
                      flex: 1, padding: '8px 0', borderRadius: 7, border: 'none',
                      background: '#16A34A', color: '#fff', fontWeight: 700,
                      fontSize: 13, cursor: 'pointer',
                      opacity: busy === w.id ? 0.5 : 1,
                    }}>Admit</button>
                  <button disabled={busy === w.id}
                    onClick={() => decide(w.id, false)} style={{
                      flex: 1, padding: '8px 0', borderRadius: 7,
                      border: `1px solid ${color.borderStrong}`,
                      background: '#fff', color: color.textSecondary,
                      fontWeight: 600, fontSize: 13, cursor: 'pointer',
                      opacity: busy === w.id ? 0.5 : 1,
                    }}>Reject</button>
                </div>
              </div>
            ))}
          </div>
        )}

        <div style={{
          fontSize: 11.5, fontWeight: 700, color: color.textSecondary,
          textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 8,
        }}>
          In the meeting ({inRoom.length})
        </div>

        {inRoom.length === 0 ? (
          <div style={{ fontSize: 13, color: color.textMuted }}>
            Nobody has joined yet.
          </div>
        ) : (
          <div style={{ display: 'grid', gap: 6 }}>
            {inRoom.map((p) => (
              <div key={p.id} style={{
                display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px',
                border: `1px solid ${color.border}`, borderRadius: 8,
              }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 600,
                    overflow: 'hidden', textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap' }}>{p.display_name}</div>
                  <div style={{ fontSize: 11, color: color.textMuted }}>
                    {p.is_guest ? 'Guest' : 'Staff'} &middot; {p.role}
                  </div>
                </div>
                {p.role !== 'HOST' && (
                  <button disabled={busy === p.id}
                    onClick={() => remove(p.id, p.display_name)} style={{
                      marginLeft: 'auto', padding: '6px 11px', borderRadius: 7,
                      border: `1px solid ${color.borderStrong}`,
                      background: '#fff', color: '#B91C1C', fontSize: 12,
                      fontWeight: 600, cursor: 'pointer', flexShrink: 0,
                    }}>Remove</button>
                )}
              </div>
            ))}
          </div>
        )}

        <div style={{ fontSize: 11.5, color: color.textMuted, marginTop: 16,
          lineHeight: 1.6 }}>
          Removing closes that attendance record. Ejecting somebody from the
          live call is done with the participant controls inside the meeting
          window itself.
        </div>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// The screen
// ---------------------------------------------------------------------------

export default function Meetings() {
  const [config, setConfig] = useState(null);
  const [meetings, setMeetings] = useState([]);
  const [staff, setStaff] = useState([]);
  const [scheduling, setScheduling] = useState(false);
  const [issued, setIssued] = useState(null);
  const [active, setActive] = useState(null);
  const [detail, setDetail] = useState(null);
  const [includePast, setIncludePast] = useState(false);
  const [err, setErr] = useState('');
  const [toast, setToast] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [cfg, list] = await Promise.all([
        getJSON('/api/meetings/config'),
        getJSON(`/api/meetings?include_past=${includePast}`),
      ]);
      setConfig(cfg);
      setMeetings(list.meetings || []);
      setErr('');
    } catch (e) { setErr(e.message); }
    finally { setLoading(false); }
  }, [includePast]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    // Only needed when scheduling, so it is not fetched on every visit.
    if (!scheduling || staff.length) return;
    // Not /api/auth/users: that is admin-only, so everybody who is not an
    // administrator saw an empty list and could invite nobody.
    getJSON('/api/meetings/colleagues')
      .then((body) => setStaff(body.colleagues || []))
      .catch(() => setStaff([]));
  }, [scheduling, staff.length]);

  const flash = (m) => { setToast(m); setTimeout(() => setToast(''), 6000); };

  const join = async (meeting) => {
    try {
      const seat = await postJSON(`/api/meetings/${meeting.id}/join`);
      setActive(seat);
    } catch (e) { setErr(e.message); }
  };

  const leave = useCallback(async () => {
    if (active && active.attendance_id) {
      try {
        await postJSON(`/api/meetings/attendance/${active.attendance_id}/leave`);
      } catch { /* the meeting ending closes it anyway */ }
    }
    setActive(null);
    await load();
  }, [active, load]);

  const endMeeting = async (meeting) => {
    if (!window.confirm(`End "${meeting.title}" for everyone?`)) return;
    try {
      await postJSON(`/api/meetings/${meeting.id}/status`, { status: 'ENDED' });
      flash('Meeting ended. Attendance has been closed.');
      await load();
    } catch (e) { setErr(e.message); }
  };

  const cancelMeeting = async (meeting) => {
    const reason = window.prompt(`Cancel "${meeting.title}" — why?`);
    if (!reason || reason.trim().length < 3) return;
    try {
      await postJSON(`/api/meetings/${meeting.id}/status`,
        { status: 'CANCELLED', reason: reason.trim() });
      flash('Meeting cancelled.');
      await load();
    } catch (e) { setErr(e.message); }
  };

  const regenerate = async (meeting) => {
    try {
      const fresh = await postJSON(
        `/api/meetings/${meeting.id}/link/regenerate`, {});
      setIssued({ ...fresh, secured_by: config?.secured_by });
      flash('New link issued. The previous one no longer works.');
      await load();
    } catch (e) { setErr(e.message); }
  };

  const revoke = async (meeting) => {
    const reason = window.prompt('Revoke this link — why?');
    if (!reason || reason.trim().length < 3) return;
    try {
      await postJSON(`/api/meetings/${meeting.id}/link/revoke`,
        { reason: reason.trim() });
      flash('Link revoked. It stopped working immediately.');
      await load();
    } catch (e) { setErr(e.message); }
  };

  const openDetail = async (meeting) => {
    try { setDetail(await getJSON(`/api/meetings/${meeting.id}`)); }
    catch (e) { setErr(e.message); }
  };

  if (active) {
    // The host panel is rendered ON TOP of the meeting, not beside it.
    //
    // MeetingRoom covers the screen, so anything left on the page behind it is
    // unreachable -- which is where the admit and remove controls used to
    // live. A waiting-room control a host cannot reach while they are in the
    // meeting is a control that does not exist: the only moment it is needed
    // is the moment somebody is knocking.
    const canHost = ['HOST', 'CO_HOST'].includes(active.role);
    return (
      <>
        <MeetingRoom conference={active.conference} meeting={active.meeting}
          onLeave={leave} />
        {canHost && (
          <InMeetingHostPanel meetingId={active.meeting.id} onError={setErr} />
        )}
      </>
    );
  }

  if (loading) return <SkeletonCards n={3} />;

  return (
    <div>
      {toast && <div style={{ marginBottom: space(2) }}>
        <Banner tone="success" title="Done">{toast}</Banner></div>}
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      {config && !config.guest_access_available && (
        <Banner tone="warning" title="Guest links are switched off">
          MEETING_GUEST_SECRET is not set on the server, so people outside the
          company cannot join by link. Staff meetings work normally.
        </Banner>
      )}

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap',
        marginBottom: space(2.5) }}>
        <Btn size="sm" variant="accent"
          onClick={() => { setScheduling(true); setIssued(null); }}>
          Schedule a meeting
        </Btn>
        <Btn size="sm" variant="ghost"
          onClick={() => setIncludePast((v) => !v)}>
          {includePast ? 'Upcoming only' : 'Include past meetings'}
        </Btn>
        <Btn size="sm" variant="ghost" icon="refresh" onClick={load}>Refresh</Btn>
      </div>

      {issued && (
        <LinkPanel joinUrl={issued.join_url} invitation={issued.invitation}
          expiresAt={issued.link_expires_at}
          securedBy={issued.secured_by || config?.secured_by} />
      )}

      {scheduling && (
        <ScheduleForm staff={staff} onCancel={() => setScheduling(false)}
          onCreated={async (created) => {
            setScheduling(false);
            setIssued(created);
            flash('Meeting created. Copy the link now — it is shown once.');
            await load();
          }} />
      )}

      <Card pad={2.5}>
        <SectionTitle right={config && (
          <Chip tone={config.secured_by === 'signature' ? 'success' : 'warning'}>
            {config.secured_by === 'signature'
              ? 'Signed links' : 'Unguessable links'}
          </Chip>
        )}>Meetings</SectionTitle>

        <DataTable
          cols={[
            { key: 'title', label: 'Meeting', wrap: true },
            { key: 'scheduled_start', label: 'When' },
            { key: 'host_name', label: 'Host', wrap: true },
            { key: 'status', label: 'Status' },
            { key: 'live_now', label: 'In room', align: 'right' },
            { key: 'act', label: '', align: 'right' },
          ]}
          rows={meetings}
          empty="No meetings. Schedule one to begin."
          render={(r, c) => {
            if (c.key === 'title') {
              return (
                <span>
                  <button onClick={() => openDetail(r)} style={{
                    border: 'none', background: 'none', padding: 0,
                    font: 'inherit', fontWeight: 600, color: color.royal,
                    cursor: 'pointer', textAlign: 'left',
                  }}>{r.title}</button>
                  <br />
                  <span style={{ fontSize: 11, color: color.textMuted }}>
                    {r.meeting_code}
                    {r.waiting_count > 0 && ` · ${r.waiting_count} waiting`}
                  </span>
                </span>
              );
            }
            if (c.key === 'scheduled_start') return when(r.scheduled_start);
            if (c.key === 'status') {
              return <Chip tone={tone(r.status)}>{r.status}</Chip>;
            }
            if (c.key === 'act') {
              const canHost = r.my_role === 'HOST' || r.my_role === 'CO_HOST';
              if (['ENDED', 'CANCELLED'].includes(r.status)) {
                return <Btn size="sm" variant="ghost"
                  onClick={() => openDetail(r)}>Attendance</Btn>;
              }
              return (
                <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end',
                  flexWrap: 'wrap' }}>
                  <Btn size="sm" variant="accent"
                    onClick={() => join(r)}>Join</Btn>
                  {canHost && (
                    <Btn size="sm" variant="secondary"
                      onClick={() => regenerate(r)}>New link</Btn>
                  )}
                  {canHost && r.link_live && (
                    <Btn size="sm" variant="ghost"
                      onClick={() => revoke(r)}>Revoke</Btn>
                  )}
                  {canHost && (
                    <Btn size="sm" variant="ghost"
                      onClick={() => endMeeting(r)}>End</Btn>
                  )}
                  {canHost && r.status === 'SCHEDULED' && (
                    <Btn size="sm" variant="danger"
                      onClick={() => cancelMeeting(r)}>Cancel</Btn>
                  )}
                </div>
              );
            }
            return r[c.key] ?? '—';
          }}
        />
      </Card>

      {detail && (
        <div onClick={() => setDetail(null)} style={{
          position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)',
          zIndex: 2000, display: 'flex', alignItems: 'center',
          justifyContent: 'center', padding: space(2),
        }}>
          <div onClick={(e) => e.stopPropagation()} style={{
            background: '#fff', width: '100%', maxWidth: 780, maxHeight: '88vh',
            overflowY: 'auto', borderRadius: radius.lg, padding: space(2.5),
          }}>
            <div style={{ display: 'flex', alignItems: 'center',
              marginBottom: space(2) }}>
              <div>
                <h3 style={{ margin: 0, fontSize: 16 }}>{detail.title}</h3>
                <div style={{ fontSize: 12, color: color.textMuted, marginTop: 3 }}>
                  {detail.meeting_code} · {when(detail.scheduled_start)} ·
                  {' '}host {detail.host_name}
                </div>
              </div>
              <Btn size="sm" variant="ghost" style={{ marginLeft: 'auto' }}
                onClick={() => setDetail(null)}>Close</Btn>
            </div>

            {detail.description && (
              <Banner tone="info" title="Agenda (internal)">
                {detail.description}
              </Banner>
            )}

            {['HOST', 'CO_HOST'].includes(detail.my_role)
              && ['SCHEDULED', 'LIVE'].includes(detail.status) && (
              <HostControls meetingId={detail.id} onError={setErr} />
            )}

            <Card pad={2.5}>
              <SectionTitle>Attendance</SectionTitle>
              <DataTable
                cols={[
                  { key: 'display_name', label: 'Participant', wrap: true },
                  { key: 'is_guest', label: 'Type' },
                  { key: 'role', label: 'Role' },
                  { key: 'joined_at', label: 'Joined' },
                  { key: 'left_at', label: 'Left' },
                  { key: 'duration_seconds', label: 'Duration', align: 'right' },
                ]}
                rows={detail.attendance || []}
                empty="Nobody joined."
                render={(r, c) => {
                  if (c.key === 'is_guest') {
                    return <Chip tone={r.is_guest ? 'warning' : 'info'}>
                      {r.is_guest ? 'Guest' : 'Internal'}</Chip>;
                  }
                  if (c.key === 'joined_at') return when(r.joined_at);
                  if (c.key === 'left_at') {
                    return r.left_at ? when(r.left_at)
                      : <span style={{ color: color.textMuted }}>still in</span>;
                  }
                  if (c.key === 'duration_seconds') return minutes(r.duration_seconds);
                  return r[c.key] ?? '—';
                }}
              />
              <div style={{ fontSize: 11.5, color: color.textMuted,
                marginTop: space(1), lineHeight: 1.6 }}>
                A blank leaving time means that browser closed without telling
                us. Those rows are closed when the meeting ends.
              </div>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}
