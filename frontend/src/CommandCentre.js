// The distribution command centre.
//
// THE COLOUR THIS SCREEN WILL NOT USE
// -----------------------------------
// A single grey-to-green ramp across the states. That ramp renders "nobody is
// selling in Kano" and "Kano's LGAs were never entered" identically, and those
// are opposite findings — one says sell harder, the other says finish the data
// entry. States with no coverage data get hatching and the word UNKNOWN, not a
// pale shade at the bottom of the scale.
//
// And there is no health score. Every dashboard wants one number for the board
// pack; that number would have to average a compliance failure against a good
// sales month, which every other screen in this system refuses to do.

import React, { useCallback, useEffect, useState } from 'react';
import { authedFetch } from './utils/api';
import { color, naira, radius, space } from './ui/theme';
import {
  Banner, Btn, Card, Chip, DataTable, ErrorBox, KpiCard, SectionTitle,
  SkeletonCards,
} from './ui/kit';

async function req(url) {
  const res = await authedFetch(url);
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

const STATUS_STYLE = {
  COVERED: { bg: '#ECFDF5', fg: '#065F46', label: 'Covered' },
  NO_DISTRIBUTOR: { bg: '#FFFBEB', fg: '#78350F', label: 'No distributor' },
  NO_COVERAGE_DATA: {
    // Hatched, never a pale green. An unknown must not look like a small number.
    bg: 'repeating-linear-gradient(45deg, #F1F5F9, #F1F5F9 6px, #E2E8F0 6px, #E2E8F0 12px)',
    fg: '#475569', label: 'Unknown',
  },
};

const BAND_TONE = {
  ON_TARGET: 'success', BEHIND: 'warning', WELL_BEHIND: 'danger',
  NO_TARGET: 'neutral',
};

export default function CommandCentre() {
  const [tab, setTab] = useState('overview');
  const [data, setData] = useState(null);
  const [coverage, setCoverage] = useState(null);
  const [rank, setRank] = useState(null);
  const [exports, setExports] = useState([]);
  const [err, setErr] = useState('');

  const today = new Date();
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth() + 1);

  const load = useCallback(async () => {
    try {
      const [o, c, r, e] = await Promise.all([
        req('/api/command-centre'),
        req('/api/command-centre/coverage'),
        req(`/api/command-centre/ranking?year=${year}&month=${month}`),
        req('/api/command-centre/exports'),
      ]);
      setData(o); setCoverage(c); setRank(r); setExports(e.datasets || []);
      setErr('');
    } catch (ex) { setErr(ex.message); }
  }, [year, month]);

  useEffect(() => { load(); }, [load]);

  if (!data || !coverage) return <SkeletonCards n={4} />;

  const download = (dataset) => {
    window.open(
      `/api/command-centre/exports/${dataset}?year=${year}&month=${month}`,
      '_blank');
  };

  return (
    <div>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <div style={{ display: 'flex', gap: space(1), flexWrap: 'wrap',
        marginBottom: space(2.5) }}>
        {[['overview', 'Overview'], ['coverage', 'Coverage'],
          ['ranking', 'Ranking'], ['exports', 'Exports']].map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)} style={{
            padding: '8px 15px', borderRadius: radius.pill, fontSize: 13,
            fontWeight: 600,
            border: `1px solid ${tab === k ? color.medical : color.borderStrong}`,
            background: tab === k ? color.infoBg : '#fff',
            color: tab === k ? color.royal : color.textSecondary,
            cursor: 'pointer',
          }}>{label}</button>
        ))}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6,
          alignItems: 'center' }}>
          <input type="number" value={month} min={1} max={12}
            onChange={(e) => setMonth(Number(e.target.value))}
            style={{ width: 56, padding: '7px 8px', borderRadius: radius.sm,
              border: `1px solid ${color.borderStrong}`, fontSize: 13 }} />
          <input type="number" value={year} min={2000} max={2100}
            onChange={(e) => setYear(Number(e.target.value))}
            style={{ width: 76, padding: '7px 8px', borderRadius: radius.sm,
              border: `1px solid ${color.borderStrong}`, fontSize: 13 }} />
          <Btn size="sm" variant="ghost" icon="refresh" onClick={load}>
            Refresh
          </Btn>
        </div>
      </div>

      {/* The caveat that governs every geographic number below it. */}
      {coverage.coverage.lgas_not_loaded > 0 && (
        <Banner tone="warning" title="Most of Nigeria is not loaded">
          {coverage.coverage.caveat}
        </Banner>
      )}

      {tab === 'overview' && (
        <>
          <div style={{ display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))',
            gap: space(2), marginBottom: space(2) }}>
            <KpiCard icon="users" label="Active distributors"
              value={data.distributors.active}
              sub={`${data.distributors.in_review} in review`} tone="royal" />
            <KpiCard icon="map" label="Territories assigned"
              value={data.territories.assigned}
              sub={`${data.territories.available} available`} tone="info" />
            <KpiCard icon="check" label="Verified this month"
              value={naira(data.this_month.verified_amount, { compact: true })}
              sub={`${naira(data.this_month.reported_amount, { compact: true })} claimed, unchecked`}
              tone="success" />
            <KpiCard icon="alert" label="Not measurable"
              value={data.territories.not_measurable}
              sub="Territories with no target in force"
              tone={data.territories.not_measurable > 0 ? 'warning' : 'success'} />
          </div>

          <div style={{ fontSize: 12, color: color.textMuted,
            marginBottom: space(2), lineHeight: 1.7 }}>
            {data.this_month.note} {data.territories.not_measurable_note}
          </div>

          <Card pad={2.5} style={{ marginBottom: space(2) }}>
            <SectionTitle>Needs a decision</SectionTitle>
            <div style={{ display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))',
              gap: space(2) }}>
              {[
                ['Recalled batches', data.attention.recalled_batches, 'danger'],
                ['Critical corrective actions',
                  data.attention.critical_actions, 'danger'],
                ['Trading with no agreement',
                  data.attention.trading_unsigned, 'warning'],
                ['Open performance reviews',
                  data.attention.open_reviews, 'warning'],
                ['Sales claiming more than shipped',
                  data.this_month.stock_discrepancies, 'warning'],
              ].map(([label, value, tone]) => (
                <div key={label} style={{ padding: space(1.5),
                  borderRadius: radius.md,
                  border: `1px solid ${Number(value) > 0 ? color.borderStrong : color.border}`,
                  background: Number(value) > 0 ? '#FFFBEB' : '#fff' }}>
                  <div style={{ fontSize: 24, fontWeight: 800,
                    color: Number(value) > 0
                      ? (tone === 'danger' ? color.danger : '#B45309')
                      : color.textMuted }}>{value}</div>
                  <div style={{ fontSize: 12, color: color.textSecondary,
                    marginTop: 3, lineHeight: 1.4 }}>{label}</div>
                </div>
              ))}
            </div>
          </Card>

          <div style={{ fontSize: 12, color: color.textMuted, lineHeight: 1.7 }}>
            {data.note}
          </div>
        </>
      )}

      {tab === 'coverage' && (
        <Card pad={2.5}>
          <SectionTitle>Where the company actually operates</SectionTitle>

          <div style={{ display: 'flex', gap: space(2), flexWrap: 'wrap',
            marginBottom: space(2), fontSize: 12 }}>
            {Object.entries(STATUS_STYLE).map(([k, v]) => (
              <span key={k} style={{ display: 'inline-flex', alignItems: 'center',
                gap: 7 }}>
                <span style={{ width: 20, height: 14, borderRadius: 3,
                  background: v.bg, border: `1px solid ${color.border}`,
                  display: 'inline-block' }} />
                <span style={{ color: color.textSecondary }}>
                  {v.label} — {coverage.legend[k]}
                </span>
              </span>
            ))}
          </div>

          <DataTable
            cols={[
              { key: 'name', label: 'State' },
              { key: 'region', label: 'Zone' },
              { key: 'lgas_loaded', label: 'LGAs loaded', align: 'right' },
              { key: 'territories', label: 'Territories', align: 'right' },
              { key: 'distributors', label: 'Distributors', align: 'right' },
              { key: 'verified_amount', label: 'Verified sales', align: 'right' },
            ]}
            rows={coverage.states}
            empty="No states loaded."
            render={(r, c) => {
              if (c.key === 'name') {
                const style = STATUS_STYLE[r.status];
                return (
                  <span style={{ display: 'inline-flex', alignItems: 'center',
                    gap: 8 }}>
                    <span style={{ width: 14, height: 14, borderRadius: 3,
                      background: style.bg,
                      border: `1px solid ${color.border}` }} />
                    {r.name}
                  </span>
                );
              }
              if (c.key === 'verified_amount') {
                // NULL, not zero. An unknown reads as a word, not a number.
                return r.verified_amount === null
                  ? <span style={{ color: color.textMuted, fontStyle: 'italic' }}>
                      not known
                    </span>
                  : naira(r.verified_amount);
              }
              if (c.key === 'lgas_loaded') {
                return r.lgas_loaded === 0
                  ? <span style={{ color: color.danger }}>none</span>
                  : r.lgas_loaded;
              }
              return r[c.key] ?? '—';
            }}
          />

          <div style={{ fontSize: 11.5, color: color.textMuted, marginTop: 12,
            lineHeight: 1.7 }}>
            A state showing “not known” is not a state with no sales — it is a
            state where no sale could have been recorded, because its LGAs have
            never been loaded. {coverage.coverage.lgas_loaded} of{' '}
            {coverage.coverage.lgas_nationally} are in the system.
          </div>
        </Card>
      )}

      {tab === 'ranking' && rank && (
        <Card pad={2.5}>
          <SectionTitle right={(
            <Chip tone="info">
              {rank.measurable} measurable · {rank.not_measurable} without a target
            </Chip>
          )}>{rank.month} — ranked on verified sales</SectionTitle>
          <DataTable
            cols={[
              { key: 'legal_name', label: 'Distributor', wrap: true },
              { key: 'target_amount', label: 'Target', align: 'right' },
              { key: 'verified_amount', label: 'Verified', align: 'right' },
              { key: 'reported_amount', label: 'Claimed', align: 'right' },
              { key: 'achievement_pct', label: 'Achieved', align: 'right' },
              { key: 'band', label: '' },
            ]}
            rows={rank.distributors}
            empty="No active distributors."
            render={(r, c) => {
              if (c.key === 'legal_name') {
                return (
                  <span>{r.legal_name}<br />
                    <code style={{ fontSize: 11, color: color.textMuted }}>
                      {r.distributor_code}
                    </code>
                  </span>
                );
              }
              if (c.key === 'target_amount') {
                return Number(r.target_amount) > 0
                  ? naira(r.target_amount)
                  : <span style={{ color: color.textMuted }}>none set</span>;
              }
              if (c.key === 'verified_amount') {
                return <strong>{naira(r.verified_amount)}</strong>;
              }
              if (c.key === 'reported_amount') {
                return <span style={{ color: color.textMuted }}>
                  {naira(r.reported_amount)}</span>;
              }
              if (c.key === 'achievement_pct') {
                return r.achievement_pct === null
                  ? <span style={{ color: color.textMuted }}>—</span>
                  : `${Number(r.achievement_pct).toFixed(1)}%`;
              }
              if (c.key === 'band') {
                return <Chip tone={BAND_TONE[r.band]}>
                  {r.band.replace(/_/g, ' ')}</Chip>;
              }
              return r[c.key] ?? '—';
            }}
          />
          <div style={{ fontSize: 11.5, color: color.textMuted, marginTop: 12,
            lineHeight: 1.7 }}>
            {rank.note}
          </div>
        </Card>
      )}

      {tab === 'exports' && (
        <Card pad={2.5}>
          <SectionTitle>Download</SectionTitle>
          <Banner tone="info" title="Every download is recorded">
            A distributor export carries names, phone numbers and trading
            history out of the building. Who downloaded what, and when, goes
            into the distributor audit trail.
          </Banner>
          <DataTable
            cols={[
              { key: 'name', label: 'Dataset' },
              { key: 'description', label: 'Contents', wrap: true },
              { key: 'act', label: '', align: 'right' },
            ]}
            rows={exports}
            empty="None available."
            render={(r, c) => {
              if (c.key === 'act') {
                return <Btn size="sm" variant="secondary"
                  onClick={() => download(r.name)}>CSV</Btn>;
              }
              return r[c.key];
            }}
          />
          <div style={{ fontSize: 11.5, color: color.textMuted, marginTop: 12,
            lineHeight: 1.7 }}>
            The sales export has separate verified and claimed columns rather
            than one total. A spreadsheet is where a claim and a confirmed fact
            become the same column, and nothing here can put the distinction
            back once the file has been sent on.
          </div>
        </Card>
      )}
    </div>
  );
}
