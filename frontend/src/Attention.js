// What needs attention, across the whole distributor module.
//
// THERE IS NO "MARK AS READ"
// --------------------------
// Every row is computed live from the data that holds the truth, so an item
// disappears the moment the underlying problem is fixed. There is nothing
// stored to mark, and a read flag would be a lie the first time somebody ticked
// it without fixing anything.
//
// Snooze exists, and it always expires. Critical items cannot be snoozed at all
// — recalled stock sitting in a distributor's store is not something one person
// decides nobody else needs to see. The button is absent rather than disabled,
// because a disabled button invites somebody to go looking for permission.
//
// The jobs tab says plainly that nothing is sent anywhere. The app's push
// notification store lives in a file destroyed on every deploy, is not tied to
// users, and broadcasts to every subscriber — so a message about one
// distributor's performance would reach whoever happened to be listening.

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
      d = typeof j.detail === 'string' ? j.detail : (j.detail?.message || j.message || d);
    } catch { /* keep the status */ }
    throw new Error(d);
  }
  return res.json();
}
const getJSON = (u) => req(u);
const postJSON = (u, b) => req(u, { method: 'POST', body: JSON.stringify(b || {}) });

const SEVERITY_TONE = {
  CRITICAL: 'danger', HIGH: 'warning', MEDIUM: 'info', LOW: 'neutral',
};
const CATEGORY_LABEL = {
  RECALL: 'Recall', COMPLIANCE: 'Compliance', STOCK: 'Stock',
  TERRITORY: 'Territory', SELL_THROUGH: 'Sell-through',
  PERFORMANCE: 'Performance', ORDERING: 'Ordering',
};

export default function Attention() {
  const [tab, setTab] = useState('items');
  const [data, setData] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [runs, setRuns] = useState([]);
  const [showSnoozed, setShowSnoozed] = useState(false);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [d, j, h] = await Promise.all([
        getJSON(`/api/inbox?include_snoozed=${showSnoozed}`),
        getJSON('/api/inbox/jobs'),
        getJSON('/api/inbox/jobs/history?limit=30'),
      ]);
      setData(d); setJobs(j.jobs || []); setRuns(h.runs || []);
      setErr('');
    } catch (e) { setErr(e.message); }
  }, [showSnoozed]);

  useEffect(() => { load(); }, [load]);

  if (!data) return <SkeletonCards n={4} />;

  const snooze = async (item) => {
    const days = window.prompt(
      `Hide "${item.title}" from your list for how many days?\n\n`
      + 'It comes back then — there is no permanent dismissal.', '7');
    if (!days) return;
    setBusy(true);
    try {
      await postJSON('/api/inbox/snooze',
        { item_key: item.key, days: Number(days), reason: null });
      await load();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  const runJob = async (name) => {
    setBusy(true);
    try {
      const r = await postJSON(`/api/inbox/jobs/${name}/run`, {});
      await load();
      if (!r.ran) window.alert(r.note);
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  return (
    <div>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <div style={{ display: 'flex', gap: space(1), flexWrap: 'wrap',
        marginBottom: space(2.5) }}>
        {[['items', 'Needs attention'], ['jobs', 'Scheduled jobs']].map(
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
        <Btn size="sm" variant="ghost" icon="refresh"
          style={{ marginLeft: 'auto' }} onClick={load}>Refresh</Btn>
      </div>

      {tab === 'items' && (
        <>
          <div style={{ display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))',
            gap: space(2), marginBottom: space(2) }}>
            <KpiCard icon="alert" label="Critical" value={data.counts.CRITICAL}
              sub="Unsafe right now"
              tone={data.counts.CRITICAL > 0 ? 'danger' : 'success'} />
            <KpiCard icon="alert" label="High" value={data.counts.HIGH}
              sub="Needs attention soon"
              tone={data.counts.HIGH > 0 ? 'warning' : 'success'} />
            <KpiCard icon="reports" label="Medium" value={data.counts.MEDIUM}
              sub="Worth planning for" tone="info" />
            <KpiCard icon="reports" label="Low" value={data.counts.LOW}
              sub="Housekeeping" tone="neutral" />
          </div>

          {data.counts.CRITICAL > 0 && (
            <Banner tone="danger" title="Critical items cannot be snoozed">
              These are unsafe or actively losing money now. They disappear when
              the underlying problem is fixed, and not before.
            </Banner>
          )}

          <Card pad={2.5}>
            <SectionTitle right={(
              <label style={{ fontSize: 12, display: 'flex',
                alignItems: 'center', gap: 6 }}>
                <input type="checkbox" checked={showSnoozed}
                  onChange={(e) => setShowSnoozed(e.target.checked)} />
                Include snoozed ({data.snoozed_hidden})
              </label>
            )}>{data.total} item{data.total === 1 ? '' : 's'}</SectionTitle>

            <DataTable
              cols={[
                { key: 'severity', label: '' },
                { key: 'title', label: 'What', wrap: true },
                { key: 'category', label: 'Area' },
                { key: 'due', label: 'By' },
                { key: 'act', label: '', align: 'right' },
              ]}
              rows={data.items}
              empty="Nothing needs attention. This list is computed live, so that is a real answer rather than an empty table."
              render={(r, c) => {
                if (c.key === 'severity') {
                  return <Chip tone={SEVERITY_TONE[r.severity]}>{r.severity}</Chip>;
                }
                if (c.key === 'title') {
                  return (
                    <span>
                      <strong>{r.title}</strong>
                      <div style={{ fontSize: 12, color: color.textSecondary,
                        marginTop: 3, lineHeight: 1.5 }}>{r.detail}</div>
                      {r.action && (
                        <div style={{ fontSize: 11.5, color: color.royal,
                          marginTop: 3 }}>→ {r.action}</div>
                      )}
                      {r.snoozed_until && (
                        <div style={{ fontSize: 11, color: color.textMuted,
                          marginTop: 3 }}>
                          Snoozed until {new Date(r.snoozed_until).toLocaleDateString()}
                        </div>
                      )}
                    </span>
                  );
                }
                if (c.key === 'category') {
                  return CATEGORY_LABEL[r.category] || r.category;
                }
                if (c.key === 'act') {
                  // Absent, not disabled: a greyed button invites somebody to
                  // go looking for permission to press it.
                  return r.snoozeable && !r.snoozed_until ? (
                    <Btn size="sm" variant="ghost" disabled={busy}
                      onClick={() => snooze(r)}>Snooze</Btn>
                  ) : null;
                }
                return r[c.key] || '—';
              }}
            />

            <div style={{ fontSize: 11.5, color: color.textMuted,
              marginTop: 12, lineHeight: 1.7 }}>
              {data.note}
            </div>
          </Card>
        </>
      )}

      {tab === 'jobs' && (
        <>
          <Banner tone="info" title="Jobs record what they find. They send nothing.">
            The app's push notifications store subscriptions in a file that is
            replaced on every deploy, are not tied to user accounts, and the only
            send path goes to every subscriber at once — so an alert about one
            distributor would reach whoever happened to be listening. These jobs
            therefore write findings you can read here.
          </Banner>

          <Card pad={2.5} style={{ marginBottom: space(2) }}>
            <SectionTitle>Available</SectionTitle>
            <DataTable
              cols={[
                { key: 'name', label: 'Job' },
                { key: 'description', label: 'What it does', wrap: true },
                { key: 'act', label: '', align: 'right' },
              ]}
              rows={jobs}
              empty="None registered."
              render={(r, c) => {
                if (c.key === 'description') {
                  return (r.description || '').split('\n')[0];
                }
                if (c.key === 'act') {
                  return <Btn size="sm" variant="secondary" disabled={busy}
                    onClick={() => runJob(r.name)}>Run</Btn>;
                }
                return r[c.key];
              }}
            />
            <div style={{ fontSize: 11.5, color: color.textMuted, marginTop: 10,
              lineHeight: 1.6 }}>
              Safe to press twice. A job runs once per period by database
              constraint, so pressing Run again returns what the first run found
              rather than repeating the work.
            </div>
          </Card>

          <Card pad={2.5}>
            <SectionTitle>Recent runs</SectionTitle>
            <DataTable
              cols={[
                { key: 'job_name', label: 'Job' },
                { key: 'run_key', label: 'Period' },
                { key: 'status', label: 'Status' },
                { key: 'started_at', label: 'Started' },
                { key: 'summary', label: 'Found', wrap: true },
              ]}
              rows={runs}
              empty="Nothing has run yet."
              render={(r, c) => {
                if (c.key === 'status') {
                  const tone = { COMPLETED: 'success', FAILED: 'danger',
                    RUNNING: 'info' }[r.status] || 'neutral';
                  return <Chip tone={tone}>{r.status}</Chip>;
                }
                if (c.key === 'started_at') {
                  return new Date(r.started_at).toLocaleString();
                }
                if (c.key === 'summary') {
                  if (r.error) {
                    return <span style={{ color: color.danger, fontSize: 12 }}>
                      {String(r.error).split('\n')[0]}</span>;
                  }
                  if (!r.summary) return '—';
                  const bits = Object.entries(r.summary)
                    .filter(([, v]) => typeof v === 'number' && v > 0)
                    .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${v}`);
                  return bits.length ? bits.join(' · ') : 'nothing outstanding';
                }
                return r[c.key] || '—';
              }}
            />
          </Card>
        </>
      )}
    </div>
  );
}
