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

import React, { useCallback, useEffect, useState } from 'react';
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
  guest_access_enabled: true, waiting_room: true, guest_screen_share: false,
  guest_chat: true, max_participants: 50, passcode: '',
};

function ScheduleForm({ staff, onCreated, onCancel }) {
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

      <Field label="Invite staff"
        hint="Tap once to invite, again for co-host, a third time to remove.">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {(staff || []).map((person) => {
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
          ['waiting_room', 'Hold them in a waiting room until I admit them'],
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
    getJSON('/api/auth/users').then((u) => setStaff(
      (Array.isArray(u) ? u : u.users || []).filter((x) => x.is_active !== false)
    )).catch(() => setStaff([]));
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
    return (
      <>
        <MeetingRoom conference={active.conference} meeting={active.meeting}
          onLeave={leave} />
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
          zIndex: 1000, display: 'flex', alignItems: 'center',
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
