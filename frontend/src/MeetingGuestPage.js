// Joining a meeting from a link. NO LOGIN, and no way into the ERP.
//
// Rendered by App.js before the auth gate, for any path /meet/<token>.
//
// WHAT THIS PAGE IS ALLOWED TO KNOW
// ---------------------------------
// The meeting title, the host's name, when it starts. That is what the server
// sends, and it is all the invitation the guest is holding already told them.
// Not the agenda, not who else was invited, not one fact about the company —
// an invitation gets forwarded, so everything reaching this page is public.
//
// The room name is not here either. On the public Jitsi server the room IS the
// credential, so it arrives only after the server has decided this person may
// be in the meeting — after the host admits them, if there is a waiting room.
//
// Plain fetch, not authedFetch: there is no session here and never will be.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import MeetingRoom from './MeetingRoom';

const BRAND = '#0B1F4A';
const ACCENT = '#2D6CDF';
const OK = '#16A34A';
const WARN = '#B45309';
const DANGER = '#DC2626';
const LINE = '#E5E7EB';
const MUTED = '#64748B';

const wrap = {
  maxWidth: 520, margin: '0 auto', padding: '16px',
  fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
  color: '#0F172A', boxSizing: 'border-box',
};
const card = {
  background: '#fff', border: `1px solid ${LINE}`, borderRadius: 12,
  padding: 20, marginBottom: 14,
};
const btn = (variant = 'primary', disabled = false) => ({
  padding: '13px 20px', borderRadius: 10, fontSize: 15, fontWeight: 600,
  border: variant === 'ghost' ? `1px solid ${LINE}` : '1px solid transparent',
  background: variant === 'ghost' ? '#fff' : variant === 'accent' ? ACCENT : BRAND,
  color: variant === 'ghost' ? '#334155' : '#fff',
  cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.5 : 1,
  fontFamily: 'inherit', minHeight: 46, width: '100%',
});
const input = {
  width: '100%', padding: '12px', borderRadius: 9, fontSize: 16,
  border: `1px solid ${LINE}`, fontFamily: 'inherit', boxSizing: 'border-box',
  color: '#0F172A', background: '#fff', minHeight: 46,
};

function Notice({ tone = 'info', title, children }) {
  const c = {
    info: ['#EFF4FE', ACCENT, '#1E3A5F'],
    warning: ['#FFFBEB', WARN, '#78350F'],
    danger: ['#FEF2F2', DANGER, '#7F1D1D'],
    success: ['#ECFDF5', OK, '#065F46'],
  }[tone];
  return (
    <div style={{
      background: c[0], borderLeft: `4px solid ${c[1]}`, borderRadius: 8,
      padding: '12px 14px', marginBottom: 14, color: c[2], fontSize: 14,
      lineHeight: 1.6,
    }}>
      {title && <div style={{ fontWeight: 700, marginBottom: 3 }}>{title}</div>}
      {children}
    </div>
  );
}

// Said plainly, because "NotAllowedError" helps nobody standing in a corridor
// two minutes before a meeting.
function deviceProblem(err) {
  const name = err && err.name;
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'Your browser blocked the microphone and camera. Allow them for '
      + 'this site (the padlock in the address bar), then try again.';
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') {
    return 'No microphone or camera was found. You can still join to listen.';
  }
  if (name === 'NotReadableError') {
    return 'Another application is using your camera or microphone. Close it '
      + 'and try again.';
  }
  return 'Your microphone and camera could not be started. You can still join '
    + 'to listen.';
}

export default function MeetingGuestPage({ token }) {
  const [phase, setPhase] = useState('loading');   // loading|form|waiting|in|error
  const [error, setError] = useState('');
  const [opened, setOpened] = useState(null);
  const [name, setName] = useState('');
  const [passcode, setPasscode] = useState('');
  const [wantMic, setWantMic] = useState(true);
  const [wantCam, setWantCam] = useState(true);
  const [deviceNote, setDeviceNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [seat, setSeat] = useState(null);
  const [waitingId, setWaitingId] = useState(null);
  const pollRef = useRef(null);

  const call = useCallback(async (path, options) => {
    const res = await fetch(`/api/portal/meet/${token}${path}`, {
      headers: { 'Content-Type': 'application/json' }, ...options,
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || 'Something went wrong.');
    return body;
  }, [token]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const body = await call('');
        if (!alive) return;
        setOpened(body);
        setPhase('form');
      } catch (e) {
        if (!alive) return;
        setError(e.message);
        setPhase('error');
      }
    })();
    return () => { alive = false; };
  }, [call]);

  // Ask for the devices BEFORE joining, so a refusal is discovered here —
  // where it can be explained — rather than inside the meeting where it looks
  // like the call is broken.
  const warmUpDevices = async () => {
    if (!wantMic && !wantCam) return true;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setDeviceNote('This browser cannot use a microphone or camera. You can '
        + 'still join to listen.');
      return true;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: wantMic, video: wantCam,
      });
      // Released at once: Jitsi opens its own. Holding it would leave the
      // camera light on behind the meeting.
      stream.getTracks().forEach((t) => t.stop());
      setDeviceNote('');
      return true;
    } catch (err) {
      setDeviceNote(deviceProblem(err));
      return true;
    }
  };

  const join = async () => {
    setBusy(true);
    setError('');
    try {
      await warmUpDevices();
      const body = await call('/join', {
        method: 'POST',
        body: JSON.stringify({
          display_name: name.trim(),
          ...(passcode ? { passcode } : {}),
        }),
      });
      if (body.status === 'WAITING') {
        setWaitingId(body.waiting_id);
        setPhase('waiting');
      } else {
        setSeat(body);
        setPhase('in');
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  // While in the lobby, ask every few seconds whether the host has decided.
  useEffect(() => {
    if (phase !== 'waiting' || !waitingId) return undefined;
    let alive = true;
    const tick = async () => {
      try {
        const body = await call(`/waiting/${waitingId}`);
        if (!alive) return;
        if (body.status === 'ADMITTED') {
          setSeat(body);
          setPhase('in');
        } else if (body.status === 'REJECTED') {
          setError(body.note || 'The host did not admit you.');
          setPhase('error');
        }
      } catch (e) {
        if (!alive) return;
        setError(e.message);
        setPhase('error');
      }
    };
    pollRef.current = setInterval(tick, 4000);
    tick();
    return () => { alive = false; clearInterval(pollRef.current); };
  }, [phase, waitingId, call]);

  const leave = useCallback(() => {
    if (seat && seat.attendance_id) {
      // keepalive, because this fires as the tab is closing.
      try {
        fetch(`/api/portal/meet/${token}/leave/${seat.attendance_id}`,
          { method: 'POST', keepalive: true });
      } catch { /* the meeting ending will close the row anyway */ }
    }
  }, [seat, token]);

  const handleLeave = useCallback(() => {
    leave();
    setPhase('left');
  }, [leave]);

  useEffect(() => {
    window.addEventListener('pagehide', leave);
    return () => window.removeEventListener('pagehide', leave);
  }, [leave]);

  if (phase === 'loading') {
    return <div style={{ ...wrap, textAlign: 'center', paddingTop: 80, color: MUTED }}>
      Loading…
    </div>;
  }

  if (phase === 'error') {
    return (
      <div style={wrap}>
        <div style={{ ...card, marginTop: 48 }}>
          <Notice tone="danger" title="You cannot join this meeting">
            {error}
          </Notice>
          <div style={{ fontSize: 13, color: MUTED, lineHeight: 1.7 }}>
            Meeting links are issued for a period and can be withdrawn by the
            host. If you think this is a mistake, contact whoever invited you.
          </div>
        </div>
      </div>
    );
  }

  if (phase === 'in' && seat) {
    return (
      <MeetingRoom
        conference={seat.conference}
        meeting={seat.meeting}
        onLeave={handleLeave}
      />
    );
  }

  if (phase === 'left') {
    return (
      <div style={wrap}>
        <div style={{ ...card, marginTop: 48, textAlign: 'center' }}>
          <div style={{ fontSize: 18, fontWeight: 700, color: BRAND }}>
            You have left the meeting
          </div>
          <div style={{ fontSize: 13.5, color: MUTED, marginTop: 8, lineHeight: 1.7 }}>
            You can rejoin from the same link while the meeting is still running.
          </div>
          <div style={{ marginTop: 18 }}>
            <button style={btn('ghost')} onClick={() => window.location.reload()}>
              Rejoin
            </button>
          </div>
        </div>
      </div>
    );
  }

  const meeting = (opened && opened.meeting) || {};
  const when = meeting.scheduled_start
    ? new Date(meeting.scheduled_start).toLocaleString(undefined, {
      weekday: 'short', day: 'numeric', month: 'long',
      hour: '2-digit', minute: '2-digit',
    })
    : '';

  if (phase === 'waiting') {
    return (
      <div style={wrap}>
        <div style={{ ...card, marginTop: 48, textAlign: 'center' }}>
          <div style={{
            width: 54, height: 54, borderRadius: '50%', margin: '0 auto 16px',
            border: `3px solid ${LINE}`, borderTopColor: ACCENT,
            animation: 'bsmspin 0.9s linear infinite',
          }} />
          <style>{'@keyframes bsmspin{to{transform:rotate(360deg)}}'}</style>
          <div style={{ fontSize: 17, fontWeight: 700, color: BRAND }}>
            Waiting for the host to let you in
          </div>
          <div style={{ fontSize: 14, color: MUTED, marginTop: 8, lineHeight: 1.7 }}>
            {meeting.title}
            {meeting.host ? <><br />Host: {meeting.host}</> : null}
          </div>
          <div style={{ fontSize: 12.5, color: MUTED, marginTop: 16 }}>
            Keep this page open.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={wrap}>
      <div style={{ textAlign: 'center', paddingTop: 26, paddingBottom: 6 }}>
        <img src="/company-logo.png?v=20260118" alt="Bonnesante Medicals"
          onError={(e) => { e.target.style.display = 'none'; }}
          style={{ height: 52, width: 'auto', objectFit: 'contain' }} />
      </div>

      <div style={card}>
        <div style={{ fontSize: 12, color: MUTED, textTransform: 'uppercase',
          letterSpacing: '0.05em', fontWeight: 600 }}>
          You have been invited to
        </div>
        <h1 style={{ fontSize: 20, fontWeight: 800, color: BRAND, margin: '6px 0 10px' }}>
          {meeting.title}
        </h1>
        <div style={{ fontSize: 14, color: '#334155', lineHeight: 1.8 }}>
          {meeting.host && <div>Host: <strong>{meeting.host}</strong></div>}
          {when && <div>{when}</div>}
          {meeting.duration_minutes && (
            <div style={{ color: MUTED, fontSize: 13 }}>
              About {meeting.duration_minutes} minutes
            </div>
          )}
        </div>
      </div>

      {error && <Notice tone="danger" title="Not able to join">{error}</Notice>}
      {deviceNote && <Notice tone="warning">{deviceNote}</Notice>}

      <div style={card}>
        <label htmlFor="guest-name" style={{
          display: 'block', fontSize: 12.5, fontWeight: 600, color: '#334155',
          marginBottom: 6,
        }}>
          Your name
        </label>
        <input id="guest-name" style={input} value={name} autoFocus
          placeholder="The name others will see"
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && name.trim().length >= 2) join(); }} />

        {opened && opened.requires_passcode && (
          <div style={{ marginTop: 14 }}>
            <label htmlFor="guest-pass" style={{
              display: 'block', fontSize: 12.5, fontWeight: 600,
              color: '#334155', marginBottom: 6,
            }}>
              Meeting passcode
            </label>
            <input id="guest-pass" style={input} type="password" value={passcode}
              placeholder="Sent to you separately"
              onChange={(e) => setPasscode(e.target.value)} />
          </div>
        )}

        <div style={{ marginTop: 16, display: 'grid', gap: 10 }}>
          <label style={{ display: 'flex', gap: 10, alignItems: 'center',
            fontSize: 14, cursor: 'pointer' }}>
            <input type="checkbox" checked={wantMic} style={{ width: 18, height: 18 }}
              onChange={(e) => setWantMic(e.target.checked)} />
            <span>Join with my microphone on</span>
          </label>
          <label style={{ display: 'flex', gap: 10, alignItems: 'center',
            fontSize: 14, cursor: 'pointer' }}>
            <input type="checkbox" checked={wantCam} style={{ width: 18, height: 18 }}
              onChange={(e) => setWantCam(e.target.checked)} />
            <span>Join with my camera on</span>
          </label>
        </div>

        <div style={{ marginTop: 18 }}>
          <button style={btn('primary', busy || name.trim().length < 2)}
            disabled={busy || name.trim().length < 2} onClick={join}>
            {busy ? 'Joining…' : 'Join meeting'}
          </button>
        </div>

        {meeting.waiting_room && (
          <div style={{ fontSize: 12, color: MUTED, marginTop: 12, lineHeight: 1.6 }}>
            The host will be asked to admit you.
          </div>
        )}
      </div>

      <div style={{ textAlign: 'center', fontSize: 11.5, color: MUTED,
        marginBottom: 28, lineHeight: 1.6 }}>
        No account or download is needed. Your browser will ask permission to
        use your microphone and camera.
      </div>
    </div>
  );
}
