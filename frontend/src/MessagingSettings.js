// Outbound messaging: the switches, and what the outbox has refused.
//
// Rendered as a pane inside Who to Contact.
//
// WHY THE MASTER SWITCH IS THE BIGGEST THING ON THE SCREEN
// --------------------------------------------------------
// Every system that sends messages on a schedule eventually sends the wrong
// one, usually at the worst time, and the difference between an incident and
// a catastrophe is how long it takes to stop it. The stop button is reachable
// by any admin without a deploy, and it is the first thing on the page rather
// than the last.
//
// NOTHING SENDS YET
// -----------------
// There is no sender in this system. These switches govern a queue that
// nothing drains, which is deliberate: the rules are easier to get right
// while nothing can actually go out.

import React, { useCallback, useEffect, useState } from 'react';
import { authedFetch, isAdmin } from './utils/api';
import { color, radius, space } from './ui/theme';
import { Banner, Btn, Card, Chip, ErrorBox, SkeletonCards } from './ui/kit';

async function req(url, opts) {
  const res = await authedFetch(url, opts);
  if (!res.ok) {
    let d = `Request failed (${res.status})`;
    try { const j = await res.json(); d = j.detail || j.message || d; } catch { /* keep */ }
    throw new Error(typeof d === 'string' ? d : 'Request failed');
  }
  return res.json();
}

const inputStyle = {
  padding: '7px 10px', borderRadius: radius.sm, fontSize: 13,
  border: `1px solid ${color.borderStrong}`, fontFamily: 'inherit',
  background: '#fff', color: color.text, width: 90,
};

const isTrue = (v) => String(v).toLowerCase() === 'true';

function Switch({ setting, onChange, busy, big = false }) {
  const on = isTrue(setting.value);
  return (
    <div style={{
      display: 'flex', gap: 14, alignItems: 'flex-start',
      padding: big ? '16px 18px' : '12px 0',
      background: big ? (on ? '#FEF2F2' : '#F8FAFC') : 'transparent',
      border: big ? `1px solid ${on ? '#FCA5A5' : color.border}` : 'none',
      borderBottom: big ? undefined : `1px solid ${color.border}`,
      borderRadius: big ? radius.md : 0,
      marginBottom: big ? space(2) : 0,
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center',
          flexWrap: 'wrap' }}>
          <span style={{ fontSize: big ? 14.5 : 13, fontWeight: 700,
            color: color.navy }}>
            {setting.key.replace(/_/g, ' ').toLowerCase()
              .replace(/^./, (ch) => ch.toUpperCase())}
          </span>
          <Chip tone={on ? 'danger' : 'neutral'}>{on ? 'ON' : 'OFF'}</Chip>
        </div>
        <div style={{ fontSize: 12, color: color.textSecondary, marginTop: 4,
          lineHeight: 1.6 }}>{setting.description}</div>
        {setting.updated_by && (
          <div style={{ fontSize: 11, color: color.textMuted, marginTop: 3 }}>
            Last changed by {setting.updated_by}
            {setting.updated_at
              ? ` · ${new Date(setting.updated_at).toLocaleString()}` : ''}
          </div>
        )}
      </div>
      {isAdmin() && (
        <Btn size="sm" variant={on ? 'danger' : 'secondary'} disabled={busy}
          onClick={() => onChange(setting.key, on ? 'false' : 'true')}>
          {on ? 'Switch off' : 'Switch on'}
        </Btn>
      )}
    </div>
  );
}

function Number({ setting, onChange, busy }) {
  const [value, setValue] = useState(setting.value);
  const dirty = value !== setting.value;
  return (
    <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start',
      padding: '12px 0', borderBottom: `1px solid ${color.border}` }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: color.navy }}>
          {setting.key.replace(/_/g, ' ').toLowerCase()
            .replace(/^./, (ch) => ch.toUpperCase())}
        </div>
        <div style={{ fontSize: 12, color: color.textSecondary, marginTop: 4,
          lineHeight: 1.6 }}>{setting.description}</div>
      </div>
      <input style={inputStyle} type="number" value={value}
        disabled={!isAdmin()}
        onChange={(e) => setValue(e.target.value)} />
      {isAdmin() && dirty && (
        <Btn size="sm" disabled={busy}
          onClick={() => onChange(setting.key, value)}>Save</Btn>
      )}
    </div>
  );
}

export default function MessagingSettings({ notify }) {
  const [settings, setSettings] = useState(null);
  const [box, setBox] = useState(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, b] = await Promise.all([
        req('/api/messaging/settings'),
        req('/api/messaging/outbox?limit=40').catch(() => null),
      ]);
      setSettings(s.settings || []); setBox(b); setErr('');
    } catch (e) { setErr(e.message); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const change = async (key, value) => {
    setBusy(true);
    try {
      await req(`/api/messaging/settings/${key}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value }),
      });
      if (notify) notify(`${key} is now ${value}.`, 'success');
      await load();
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  if (err) return <ErrorBox msg={err} />;
  if (!settings) return <SkeletonCards n={2} />;

  const master = settings.find((s) => s.key === 'OUTBOUND_MESSAGING_ENABLED');
  const switches = settings.filter(
    (s) => s.type === 'BOOLEAN' && s.key !== 'OUTBOUND_MESSAGING_ENABLED');
  const numbers = settings.filter((s) => s.type === 'INTEGER');
  const blocked = box ? (box.counts.BLOCKED || 0) : 0;
  const queued = box ? (box.counts.QUEUED || 0) : 0;

  return (
    <div>
      <Banner tone="info" title="Nothing is sent to anyone yet">
        There is no sender in this system. Messages are queued or refused, and
        the refusals are kept so the rules can be checked. These switches
        govern what will happen once a provider is connected.
      </Banner>

      {master && (
        <Card>
          <div style={{ fontSize: 14, fontWeight: 800, color: color.navy,
            marginBottom: space(2) }}>
            The stop button
          </div>
          <Switch setting={master} onChange={change} busy={busy} big />
          <div style={{ fontSize: 12, color: color.textSecondary,
            lineHeight: 1.7 }}>
            Switching this off stops every message on every channel
            immediately, including order and delivery updates. A stop button
            with exceptions is not a stop button. No deploy is needed and the
            change is recorded against your name.
          </div>
        </Card>
      )}

      <Card style={{ marginTop: space(2) }}>
        <div style={{ fontSize: 14, fontWeight: 800, color: color.navy,
          marginBottom: 4 }}>Channels and categories</div>
        {switches.map((s) => (
          <Switch key={s.key} setting={s} onChange={change} busy={busy} />
        ))}
      </Card>

      <Card style={{ marginTop: space(2) }}>
        <div style={{ fontSize: 14, fontWeight: 800, color: color.navy,
          marginBottom: 4 }}>How often a customer may be messaged</div>
        <div style={{ fontSize: 12, color: color.textSecondary,
          marginBottom: space(1), lineHeight: 1.6 }}>
          Promotional messages only. A customer with four orders this week
          still gets four delivery updates.
        </div>
        {numbers.map((s) => (
          <Number key={s.key} setting={s} onChange={change} busy={busy} />
        ))}
      </Card>

      {box && (
        <Card style={{ marginTop: space(2) }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center',
            flexWrap: 'wrap', marginBottom: space(2) }}>
            <span style={{ fontSize: 14, fontWeight: 800, color: color.navy }}>
              The outbox
            </span>
            <Chip tone="neutral">{queued} queued</Chip>
            {blocked > 0 && <Chip tone="warning">{blocked} refused</Chip>}
          </div>

          {box.messages.length === 0 ? (
            <div style={{ fontSize: 13, color: color.textSecondary,
              padding: space(2), textAlign: 'center' }}>
              Nothing has been queued.
            </div>
          ) : box.messages.map((m) => (
            <div key={m.id} style={{ padding: '10px 0',
              borderBottom: `1px solid ${color.border}` }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center',
                flexWrap: 'wrap' }}>
                <Chip tone={m.status === 'BLOCKED' ? 'warning'
                  : m.status === 'SENT' ? 'success'
                    : m.status === 'FAILED' ? 'danger' : 'neutral'}>
                  {m.status}
                </Chip>
                <span style={{ fontSize: 12.5, fontWeight: 600 }}>
                  {m.customer || m.to}
                </span>
                <span style={{ fontSize: 11.5, color: color.textSecondary }}>
                  {m.channel} · {m.category.toLowerCase()}
                </span>
              </div>
              <div style={{ fontSize: 12.5, color: color.text, marginTop: 4 }}>
                {m.body.length > 130 ? `${m.body.slice(0, 130)}…` : m.body}
              </div>
              <div style={{ fontSize: 11.5, color: color.textSecondary,
                marginTop: 3 }}>
                Reason: {m.reason}
              </div>
              {m.blocked_reason && (
                <div style={{ fontSize: 11.5, color: '#B45309', marginTop: 3 }}>
                  Refused: {m.blocked_reason}
                </div>
              )}
            </div>
          ))}

          <div style={{ fontSize: 11.5, color: color.textSecondary,
            marginTop: space(2), lineHeight: 1.6 }}>{box.note}</div>
        </Card>
      )}

      {!isAdmin() && (
        <div style={{ fontSize: 12, color: color.textSecondary,
          marginTop: space(2) }}>
          Changing these is admin-only.
        </div>
      )}
    </div>
  );
}
