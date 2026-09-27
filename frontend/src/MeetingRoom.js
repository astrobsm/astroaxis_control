// The meeting itself. Used by both the guest page and the staff screen.
//
// WHY THIS IS A THIN WRAPPER AND NOT A WEBRTC CLIENT
// --------------------------------------------------
// The media — audio, video, screen share, the participant grid, the in-meeting
// chat, device pickers, active-speaker detection, network adaptation — is
// Jitsi's. Reimplementing any of it would mean running an SFU, and the server
// this ERP sits on has one core and under a gigabyte of RAM. So this component
// mounts Jitsi and does the one thing Jitsi cannot: tell our server who is in
// the room, so attendance is a record rather than a guess.
//
// EVERYTHING HERE ARRIVES FROM THE SERVER
// ----------------------------------------
// The room name, the signed token, whether this person may share their screen,
// whether they may chat, whether they are a moderator. None of it is decided
// in the browser, because a browser can be edited. When JaaS is configured the
// media server checks the signature itself, so a tampered prop buys nothing.
//
// LOADED ON DEMAND
// ----------------
// The external_api.js script is injected when a meeting opens, not at app
// start. Somebody checking stock levels should not pay for the conferencing
// stack they are not using.

import React, { useEffect, useRef, useState } from 'react';

const LOAD_TIMEOUT_MS = 20000;

function loadJitsiScript(domain) {
  return new Promise((resolve, reject) => {
    if (window.JitsiMeetExternalAPI) { resolve(); return; }

    const src = `https://${domain}/external_api.js`;
    const existing = document.querySelector(`script[src="${src}"]`);
    if (existing) {
      existing.addEventListener('load', () => resolve());
      existing.addEventListener('error', () => reject(
        new Error('The meeting service could not be reached.')));
      return;
    }

    const script = document.createElement('script');
    script.src = src;
    script.async = true;
    const timer = setTimeout(
      () => reject(new Error('The meeting service is not responding.')),
      LOAD_TIMEOUT_MS);
    script.onload = () => { clearTimeout(timer); resolve(); };
    script.onerror = () => {
      clearTimeout(timer);
      reject(new Error('The meeting service could not be reached. Check your '
        + 'internet connection.'));
    };
    document.head.appendChild(script);
  });
}

export default function MeetingRoom({ conference, meeting, onLeave, onEvent }) {
  const containerRef = useRef(null);
  const apiRef = useRef(null);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('connecting');

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        await loadJitsiScript(conference.domain);
        if (cancelled || !containerRef.current) return;

        const options = {
          roomName: conference.room,
          parentNode: containerRef.current,
          width: '100%',
          height: '100%',
          userInfo: {
            displayName: conference.display_name,
            email: conference.email || '',
          },
          configOverwrite: {
            // The guest page has already asked for devices and explained any
            // refusal, so Jitsi's own pre-join screen would be a second
            // identical question.
            prejoinPageEnabled: false,
            disableDeepLinking: true,
            startWithAudioMuted: false,
            startWithVideoMuted: false,
            // Bandwidth here is variable and often mobile. Last-N caps how
            // many video streams are pulled at once, which is the single most
            // effective thing available on a poor connection.
            channelLastN: 6,
            enableNoAudioDetection: true,
            enableNoisyMicDetection: true,
          },
          interfaceConfigOverwrite: {
            SHOW_JITSI_WATERMARK: false,
            SHOW_WATERMARK_FOR_GUESTS: false,
            MOBILE_APP_PROMO: false,
            DEFAULT_BACKGROUND: '#0B1F4A',
            TOOLBAR_BUTTONS: [
              'microphone', 'camera',
              ...(conference.can_screen_share ? ['desktop'] : []),
              ...(conference.can_chat ? ['chat'] : []),
              'raisehand', 'participants-pane', 'tileview', 'settings',
              'videoquality', 'fullscreen', 'select-background', 'hangup',
            ],
          },
        };
        // Only sent when the server minted one. On the public server there is
        // nothing to sign a token for, and passing an empty one is refused.
        if (conference.jwt) options.jwt = conference.jwt;

        const api = new window.JitsiMeetExternalAPI(conference.domain, options);
        apiRef.current = api;

        api.addEventListener('videoConferenceJoined', () => {
          setStatus('connected');
          onEvent && onEvent('joined');
        });
        api.addEventListener('readyToClose', () => {
          onEvent && onEvent('left');
          onLeave && onLeave();
        });
        api.addEventListener('participantJoined', () =>
          onEvent && onEvent('participantJoined'));
        api.addEventListener('participantLeft', () =>
          onEvent && onEvent('participantLeft'));
        // Jitsi reports its own connection trouble; surfacing it means the
        // user is told the call is struggling instead of wondering why nobody
        // is answering them.
        api.addEventListener('connectionEstablished', () => setStatus('connected'));
        api.addEventListener('connectionFailed', () => setStatus('trouble'));
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
    })();

    return () => {
      cancelled = true;
      if (apiRef.current) {
        try { apiRef.current.dispose(); } catch { /* already gone */ }
        apiRef.current = null;
      }
    };
  }, [conference, onLeave, onEvent]);

  if (error) {
    return (
      <div style={{
        padding: 28, textAlign: 'center',
        fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
      }}>
        <div style={{ fontSize: 16, fontWeight: 700, color: '#991b1b' }}>
          The meeting could not be opened
        </div>
        <div style={{ fontSize: 14, color: '#64748B', marginTop: 8,
          lineHeight: 1.7, maxWidth: 420, margin: '8px auto 0' }}>
          {error}
        </div>
        <button onClick={() => window.location.reload()} style={{
          marginTop: 18, padding: '11px 22px', borderRadius: 9, border: 'none',
          background: '#0B1F4A', color: '#fff', fontSize: 14, fontWeight: 600,
          cursor: 'pointer',
        }}>Try again</button>
      </div>
    );
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, background: '#0B1F4A', display: 'flex',
      flexDirection: 'column', zIndex: 9999,
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px',
        color: '#fff', fontSize: 14,
        fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
        borderBottom: '1px solid rgba(255,255,255,0.12)', flexShrink: 0,
      }}>
        <strong style={{ fontSize: 14.5 }}>
          {(meeting && meeting.title) || 'Meeting'}
        </strong>
        {conference.moderator && (
          <span style={{
            fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 10,
            background: 'rgba(255,255,255,0.16)',
          }}>HOST</span>
        )}
        <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center',
          gap: 7, fontSize: 12.5, opacity: 0.85 }}>
          <span aria-hidden="true" style={{
            width: 8, height: 8, borderRadius: '50%',
            background: status === 'connected' ? '#16A34A'
              : status === 'trouble' ? '#DC2626' : '#F59E0B',
          }} />
          {status === 'connected' ? 'Connected'
            : status === 'trouble' ? 'Connection trouble' : 'Connecting…'}
        </span>
      </div>

      <div ref={containerRef} style={{ flex: 1, minHeight: 0 }} />
    </div>
  );
}
