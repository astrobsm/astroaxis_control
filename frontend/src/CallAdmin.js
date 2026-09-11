// Call Tracking — what the company's calls actually look like.
//
// Mounted from AppMain when activeModule === 'callTracking'. Visible to
// administrators and to anyone the wallet module granted approval authority,
// so "who counts as management" is one decision, not two.
//
// The provenance panel is the most important thing on this screen and the
// easiest to leave out. Call durations here are estimates: the app times how
// long a phone was away and the staff member confirms or overrides it. If most
// of the reported minutes were typed in by hand rather than timed, the totals
// beside them are opinion, and whoever is reading this needs to be told so
// rather than left to assume a precision that is not there.

import React, { useCallback, useEffect, useState } from 'react';
import { authedFetch } from './utils/api';
import { color, font, radius, space } from './ui/theme';
import {
  Banner, Btn, Card, Chip, DataTable, ErrorBox, KpiCard, SectionTitle,
  SkeletonCards,
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

const hms = (s) => {
  const n = Math.max(0, Math.round(Number(s) || 0));
  const h = Math.floor(n / 3600);
  const m = Math.floor((n % 3600) / 60);
  if (h) return `${h}h ${m}m`;
  return m ? `${m}m ${n % 60}s` : `${n}s`;
};

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
  padding: '9px 10px', border: `1px solid ${color.borderStrong}`,
  borderRadius: radius.sm, fontSize: 13, fontFamily: font.family,
  color: color.text, background: '#fff', boxSizing: 'border-box',
};

const todayISO = () => new Date().toISOString().slice(0, 10);
const daysAgoISO = (n) => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);

function Grid({ children, min = 210 }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(auto-fit, minmax(${min}px, 1fr))`, gap: space(2) }}>
      {children}
    </div>
  );
}

export default function CallAdmin() {
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [dash, setDash] = useState(null);
  const [calls, setCalls] = useState([]);
  const [from, setFrom] = useState(daysAgoISO(30));
  const [to, setTo] = useState(todayISO());
  const [tab, setTab] = useState('overview');
  const [recs, setRecs] = useState(null);
  const [playing, setPlaying] = useState(null);

  const load = useCallback(async () => {
    setErr('');
    try {
      const [d, l, r] = await Promise.all([
        req('/api/calls/dashboard?days=30'),
        req(`/api/calls?date_from=${from}&date_to=${to}&limit=500`),
        // Administrators only; a 403 here must not blank the whole screen.
        req('/api/calls/recordings').catch(() => null),
      ]);
      setDash(d);
      setCalls(l.calls || []);
      setRecs(r);
    } catch (e) { setErr(e.message); }
    setLoading(false);
  }, [from, to]);

  useEffect(() => { load(); }, [load]);

  if (loading) return <SkeletonCards n={4} />;
  if (err && !dash) return <ErrorBox msg={err} />;

  const t = dash ? dash.totals : {};
  const prov = (dash && dash.provenance) || [];
  const totalSecs = prov.reduce((a, p) => a + Number(p.seconds || 0), 0);
  const soft = prov
    .filter((p) => p.duration_source === 'MANUAL' || p.duration_source === 'UNKNOWN')
    .reduce((a, p) => a + Number(p.seconds || 0), 0);
  const softPct = totalSecs > 0 ? Math.round((soft / totalSecs) * 100) : 0;

  return (
    <div>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <div style={{ display: 'flex', gap: space(1), flexWrap: 'wrap', marginBottom: space(2.5) }}>
        {[['overview', 'Overview'], ['log', 'Call log'],
          ...(recs ? [['recordings', 'Recordings']] : [])].map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)} style={{
            padding: '8px 15px', borderRadius: radius.pill, fontSize: 13, fontWeight: 600,
            border: `1px solid ${tab === k ? color.medical : color.borderStrong}`,
            background: tab === k ? color.infoBg : '#fff',
            color: tab === k ? color.royal : color.textSecondary, cursor: 'pointer',
          }}>{label}</button>
        ))}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} style={inputStyle} />
          <input type="date" value={to} max={todayISO()} onChange={(e) => setTo(e.target.value)} style={inputStyle} />
          <Btn size="sm" variant="secondary" icon="reports"
            onClick={() => window.open(`/api/calls/reports/calls.csv?date_from=${from}&date_to=${to}`, '_blank')}>
            Export
          </Btn>
          <Btn size="sm" variant="ghost" icon="refresh" onClick={load}>Refresh</Btn>
        </div>
      </div>

      {tab === 'overview' && dash && (
        <>
          <Grid>
            <KpiCard icon="payroll" label="Calls made" value={t.completed}
              sub="Last 30 days" tone="royal" />
            <KpiCard icon="reports" label="Time on calls" value={hms(t.seconds)}
              sub="Reported, not measured" tone="info" />
            <KpiCard icon="users" label="Customers reached" value={t.customers_reached}
              sub="Distinct customers called" tone="success" />
            <KpiCard icon="alert" label="Not connected" value={t.cancelled}
              sub="Marked as not made"
              tone={t.cancelled > 0 ? 'warning' : 'success'} />
          </Grid>

          <div style={{ height: space(2.5) }} />

          <Card>
            <SectionTitle>How reliable are these figures?</SectionTitle>
            <Banner tone={softPct > 50 ? 'warning' : 'info'}
              title={`${softPct}% of the reported time was typed in or unknown`}>
              No web app can read a phone's call log. The app times how long the
              phone was away and the staff member confirms or corrects it. A
              high proportion of hand-typed figures does not mean anyone is
              being dishonest — but it does mean these totals are an account of
              activity, not a measurement of it.
            </Banner>
            <div style={{ height: space(2) }} />
            <DataTable
              cols={[
                { key: 'duration_source', label: 'Where the figure came from' },
                { key: 'calls', label: 'Calls', align: 'right' },
                { key: 'seconds', label: 'Time', align: 'right' },
                { key: 'share', label: 'Share of time', align: 'right' },
              ]}
              rows={prov}
              empty="No completed calls in this period."
              render={(r, c) => {
                if (c.key === 'duration_source') return <SourceChip s={r.duration_source} />;
                if (c.key === 'seconds') return hms(r.seconds);
                if (c.key === 'share') {
                  const pct = totalSecs > 0 ? Math.round((Number(r.seconds) / totalSecs) * 100) : 0;
                  return `${pct}%`;
                }
                return r[c.key];
              }}
            />
          </Card>

          <div style={{ height: space(2.5) }} />

          <Grid min={320}>
            <Card>
              <SectionTitle>By staff member</SectionTitle>
              <DataTable
                cols={[
                  { key: 'caller', label: 'Who' },
                  { key: 'calls', label: 'Calls', align: 'right' },
                  { key: 'seconds', label: 'Time', align: 'right' },
                  { key: 'customers', label: 'Customers', align: 'right' },
                ]}
                rows={dash.by_staff}
                empty="No calls yet."
                render={(r, c) => {
                  if (c.key === 'seconds') return hms(r.seconds);
                  if (c.key === 'caller') {
                    return (
                      <div>
                        <div style={{ fontWeight: 600 }}>{r.caller}</div>
                        <div style={{ fontSize: 11.5, color: color.textMuted }}>{r.department || '—'}</div>
                      </div>
                    );
                  }
                  return r[c.key];
                }}
              />
            </Card>

            <Card>
              <SectionTitle>Most-called customers</SectionTitle>
              <DataTable
                cols={[
                  { key: 'customer', label: 'Customer' },
                  { key: 'calls', label: 'Calls', align: 'right' },
                  { key: 'seconds', label: 'Time', align: 'right' },
                  { key: 'last_called', label: 'Last' },
                ]}
                rows={dash.by_customer}
                empty="No customer calls yet."
                render={(r, c) => {
                  if (c.key === 'seconds') return hms(r.seconds);
                  if (c.key === 'last_called') {
                    return r.last_called ? new Date(r.last_called).toLocaleDateString() : '—';
                  }
                  if (c.key === 'customer') {
                    return (
                      <div>
                        <div style={{ fontWeight: 600 }}>{r.customer}</div>
                        <div style={{ fontSize: 11.5, color: color.textMuted }}>{r.customer_code}</div>
                      </div>
                    );
                  }
                  return r[c.key];
                }}
              />
            </Card>
          </Grid>
        </>
      )}

      {tab === 'log' && (
        <Card>
          <SectionTitle right={<Chip tone="neutral">{calls.length} call(s)</Chip>}>
            Every call, {from} to {to}
          </SectionTitle>
          <DataTable
            cols={[
              { key: 'started_at', label: 'When' },
              { key: 'caller', label: 'Staff' },
              { key: 'contact', label: 'Called', wrap: true },
              { key: 'channel', label: 'Via' },
              { key: 'duration_seconds', label: 'Length', align: 'right' },
              { key: 'duration_source', label: 'Figure' },
              { key: 'outcome', label: 'Outcome' },
            ]}
            rows={calls}
            empty="No calls in this period."
            maxHeight={600}
            render={(r, c) => {
              if (c.key === 'started_at') {
                const d = new Date(r.started_at);
                return <span style={{ fontSize: 12.5 }}>{d.toLocaleDateString()}<br />{d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>;
              }
              if (c.key === 'caller') {
                return (
                  <div>
                    <div style={{ fontWeight: 600 }}>{r.caller}</div>
                    <div style={{ fontSize: 11.5, color: color.textMuted }}>{r.department || '—'}</div>
                  </div>
                );
              }
              if (c.key === 'contact') {
                return (
                  <div style={{ maxWidth: 220, whiteSpace: 'normal' }}>
                    <div>{r.customer_name || r.contact_name || r.contact_phone}</div>
                    <div style={{ fontSize: 11.5, color: color.textMuted }}>
                      {r.contact_phone}{r.purpose ? ` · ${r.purpose}` : ''}
                    </div>
                  </div>
                );
              }
              if (c.key === 'channel') return r.channel === 'WHATSAPP' ? 'WhatsApp' : 'Phone';
              if (c.key === 'duration_seconds') {
                if (r.status !== 'COMPLETED') {
                  return <Chip tone="neutral">{r.status === 'CANCELLED' ? 'Not made' : 'Open'}</Chip>;
                }
                const edited = r.measured_seconds != null
                  && Number(r.measured_seconds) !== Number(r.duration_seconds);
                return (
                  <div>
                    <strong>{hms(r.duration_seconds)}</strong>
                    {edited && (
                      <div style={{ fontSize: 11, color: color.textMuted }}>
                        app timed {hms(r.measured_seconds)}
                      </div>
                    )}
                  </div>
                );
              }
              if (c.key === 'duration_source') {
                return r.status === 'COMPLETED' ? <SourceChip s={r.duration_source} /> : null;
              }
              if (c.key === 'outcome') return r.outcome || '—';
              return r[c.key];
            }}
          />
        </Card>
      )}

      {tab === 'recordings' && recs && (
        <>
          <Grid>
            <KpiCard icon="ledger" label="Recordings held" value={recs.summary.stored}
              sub={`${(Number(recs.summary.bytes_held) / 1048576).toFixed(0)} MB stored`}
              tone="royal" />
            <KpiCard icon="alert" label="Past retention" value={recs.summary.overdue}
              sub="Should already be destroyed"
              tone={Number(recs.summary.overdue) > 0 ? 'danger' : 'success'} />
            <KpiCard icon="refresh" label="Awaiting transfer" value={recs.summary.pending}
              sub="Still only at the provider"
              tone={Number(recs.summary.pending) > 0 ? 'warning' : 'success'} />
            <KpiCard icon="check" label="Destroyed" value={recs.summary.deleted}
              sub="Deleted on schedule or request" tone="info" />
          </Grid>

          <div style={{ height: space(2.5) }} />

          {Number(recs.summary.overdue) > 0 && (
            <div style={{ marginBottom: space(2) }}>
              <Banner tone="danger" title={`${recs.summary.overdue} recording(s) are past their retention date`}>
                Your retention policy says these should no longer exist. Run the
                sweep now. If the number does not drop to zero, something is
                failing to delete and it needs looking at — a retention promise
                that has quietly stopped being kept is worse than never having
                made one.
              </Banner>
            </div>
          )}

          <Card>
            <SectionTitle right={
              <div style={{ display: 'flex', gap: 8 }}>
                <Btn size="sm" variant="accent" icon="refresh" onClick={async () => {
                  try {
                    const r = await req('/api/calls/recordings/sweep', { method: 'POST' });
                    setErr('');
                    alert(`Destroyed ${r.destroyed} recording(s). `
                      + `Still overdue: ${r.still_overdue}.`);
                    await load();
                  } catch (e) { setErr(e.message); }
                }}>Run retention sweep</Btn>
              </div>
            }>Call recordings</SectionTitle>

            <div style={{ marginBottom: space(2) }}>
              <Banner tone="warning" title="Everything here is personal data about a customer">
                Playback is restricted to administrators and every play, refusal
                and deletion is written to a log that cannot be edited — including
                by you. If a customer ever asks who has heard their call, that log
                is the answer.
              </Banner>
            </div>

            <DataTable
              cols={[
                { key: 'created_at', label: 'When' },
                { key: 'who', label: 'Call', wrap: true },
                { key: 'duration_seconds', label: 'Length', align: 'right' },
                { key: 'announced', label: 'Notice' },
                { key: 'retention_until', label: 'Destroy by' },
                { key: 'status', label: 'Status' },
                { key: 'act', label: '', align: 'right' },
              ]}
              rows={recs.recordings}
              empty="No recordings. Recording is off unless it has been switched on deliberately."
              maxHeight={560}
              render={(r, c) => {
                if (c.key === 'created_at') {
                  return new Date(r.created_at).toLocaleDateString();
                }
                if (c.key === 'who') {
                  return (
                    <div style={{ maxWidth: 240, whiteSpace: 'normal' }}>
                      <div style={{ fontWeight: 600 }}>
                        {r.customer_name || r.contact_name || r.contact_phone}
                      </div>
                      <div style={{ fontSize: 11.5, color: color.textMuted }}>
                        {r.caller} · {r.call_reference}
                      </div>
                    </div>
                  );
                }
                if (c.key === 'duration_seconds') {
                  return r.duration_seconds != null ? hms(r.duration_seconds) : '—';
                }
                if (c.key === 'announced') {
                  // A recording made without the notice is a compliance
                  // failure, and showing it plainly is the point.
                  return r.announced
                    ? <Chip tone="success">Given</Chip>
                    : <Chip tone="danger">Not given</Chip>;
                }
                if (c.key === 'retention_until') {
                  return (
                    <span style={{
                      color: r.overdue ? color.danger : color.text,
                      fontWeight: r.overdue ? 700 : 400,
                    }}>{r.retention_until}</span>
                  );
                }
                if (c.key === 'status') {
                  const tone = { STORED: 'success', PENDING: 'warning',
                    FAILED: 'danger', DELETED: 'neutral' }[r.status] || 'neutral';
                  return (
                    <div>
                      <Chip tone={tone}>{r.status}</Chip>
                      {r.failure_reason && (
                        <div style={{ fontSize: 11, color: color.danger, whiteSpace: 'normal', maxWidth: 180 }}>
                          {r.failure_reason}
                        </div>
                      )}
                      {r.delete_reason && (
                        <div style={{ fontSize: 11, color: color.textMuted, whiteSpace: 'normal', maxWidth: 180 }}>
                          {r.delete_reason}
                        </div>
                      )}
                    </div>
                  );
                }
                if (c.key === 'act') {
                  if (r.status !== 'STORED') return null;
                  return (
                    <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                      <Btn size="sm" variant="secondary" onClick={async () => {
                        try {
                          const d = await req(`/api/calls/${r.call_id}/recording`);
                          setPlaying({ ...r, url: d.url });
                          setErr('');
                        } catch (e) { setErr(e.message); }
                      }}>Play</Btn>
                      <Btn size="sm" variant="danger" onClick={async () => {
                        const reason = window.prompt(
                          'Why is this recording being destroyed? '
                          + '(for example: customer requested erasure)');
                        if (!reason || reason.trim().length < 3) return;
                        try {
                          await req(`/api/calls/${r.call_id}/recording`, {
                            method: 'DELETE',
                            body: JSON.stringify({ reason: reason.trim() }),
                          });
                          setErr('');
                          await load();
                        } catch (e) { setErr(e.message); }
                      }}>Destroy</Btn>
                    </div>
                  );
                }
                return r[c.key];
              }}
            />
          </Card>

          {playing && (
            <div onClick={() => setPlaying(null)} style={{
              position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)',
              zIndex: 1000, display: 'flex', alignItems: 'center',
              justifyContent: 'center', padding: space(2),
            }}>
              <div onClick={(e) => e.stopPropagation()} style={{
                background: '#fff', borderRadius: radius.lg, padding: space(3),
                maxWidth: 520, width: '100%',
              }}>
                <h3 style={{ margin: `0 0 6px`, fontSize: 16 }}>
                  {playing.customer_name || playing.contact_name || playing.contact_phone}
                </h3>
                <div style={{ fontSize: 12.5, color: color.textMuted, marginBottom: space(2) }}>
                  {playing.caller} · {playing.call_reference}
                </div>
                {/* The link expires in five minutes; it is a credential, not
                    a permanent address for the audio. */}
                <audio controls autoPlay src={playing.url} style={{ width: '100%' }} />
                <div style={{ marginTop: space(2), fontSize: 11.5, color: color.textMuted, lineHeight: 1.5 }}>
                  This playback has been recorded against your name in the access
                  log. The link expires in five minutes.
                </div>
                <div style={{ marginTop: space(2), display: 'flex', gap: 8 }}>
                  <Btn size="sm" variant="ghost" onClick={async () => {
                    try {
                      const d = await req(`/api/calls/recordings/${playing.id}/access`);
                      alert((d.access || []).map((a) =>
                        `${new Date(a.created_at).toLocaleString()} — `
                        + `${a.action} by ${a.full_name || a.actor_label || 'unknown'}`
                      ).join('\n') || 'No access recorded yet.');
                    } catch (e) { setErr(e.message); }
                  }}>Who has heard this?</Btn>
                  <Btn size="sm" variant="secondary" onClick={() => setPlaying(null)}>Close</Btn>
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
