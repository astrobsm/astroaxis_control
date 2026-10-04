// Staff birthdays and work anniversaries.
//
// Rendered in Staff Management.
//
// WHY THIS SCREEN LOOKS EMPTY AT FIRST
// ------------------------------------
// Nobody appears on the calendar until they have agreed to appear, and nobody
// is messaged until they have separately agreed to that. That is the intended
// state on day one, not a fault — so the screen says so rather than showing a
// blank panel and letting somebody conclude it is broken.
//
// NO YEARS, NO AGES
// -----------------
// The server never returns a birth year, so this screen cannot show one.
// Marking a birthday and broadcasting somebody's age are different things,
// and only the first was asked for.

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
  background: '#fff', color: color.text,
};

const VISIBILITY = [
  ['NOBODY', 'Nobody'],
  ['MANAGER', 'Their manager'],
  ['DEPARTMENT', 'Their department'],
  ['ORGANISATION', 'Everyone'],
];

function ConsentRow({ person, onSaved, notify }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const save = async (patch) => {
    setBusy(true); setErr('');
    try {
      await req(`/api/staff-engagement/${person.staff_id}/consent`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...patch, source: 'STAFF_MEMBER' }),
      });
      if (notify) notify(`Recorded for ${person.name}.`, 'success');
      onSaved();
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'center',
      flexWrap: 'wrap', padding: '11px 0',
      borderBottom: `1px solid ${color.border}` }}>
      <div style={{ minWidth: 190, flex: 1 }}>
        <div style={{ fontSize: 13.5, fontWeight: 600 }}>
          {person.name}
          {person.display_hidden && (
            <span style={{ marginLeft: 7, fontSize: 10, fontWeight: 700,
              color: '#B45309', background: '#FFFBEB', padding: '2px 7px',
              borderRadius: 10 }}>HIDDEN</span>
          )}
        </div>
        <div style={{ fontSize: 11.5, color: color.textSecondary }}>
          {person.employee_id} · {person.position || 'Staff'}
          {!person.has_phone && ' · no phone on record'}
          {!person.has_birthday && ' · no date of birth on record'}
        </div>
      </div>

      {err && <div style={{ fontSize: 11.5, color: color.danger }}>{err}</div>}

      <label style={{ fontSize: 12, display: 'flex', gap: 6,
        alignItems: 'center', cursor: 'pointer' }}>
        <input type="checkbox" checked={!!person.birthday_messages} disabled={busy}
          onChange={(e) => save({ birthday_messages: e.target.checked })} />
        Birthday message
      </label>

      <label style={{ fontSize: 12, display: 'flex', gap: 6,
        alignItems: 'center', cursor: 'pointer' }}>
        <input type="checkbox" checked={!!person.anniversary_messages}
          disabled={busy}
          onChange={(e) => save({ anniversary_messages: e.target.checked })} />
        Work anniversary
      </label>

      <select style={inputStyle} value={person.visibility || 'NOBODY'}
        disabled={busy}
        onChange={(e) => save({ visibility: e.target.value })}>
        {VISIBILITY.map(([v, label]) => (
          <option key={v} value={v}>Visible to: {label}</option>
        ))}
      </select>
    </div>
  );
}

export default function StaffEngagement({ notify }) {
  const [cal, setCal] = useState(null);
  const [staff, setStaff] = useState([]);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [showConsent, setShowConsent] = useState(false);

  const load = useCallback(async () => {
    try {
      // The roster is a separate call from the calendar on purpose: the
      // calendar omits anybody who has not agreed to be listed, which is
      // exactly the people whose answer still needs recording.
      const [c, roster] = await Promise.all([
        req('/api/staff-engagement/calendar?days=60'),
        req('/api/staff-engagement/consent').catch(() => ({ staff: [] })),
      ]);
      setCal(c);
      setStaff(roster.staff || []);
      setErr('');
    } catch (e) { setErr(e.message); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const prepare = async () => {
    setBusy(true);
    try {
      const r = await req('/api/staff-engagement/prepare', { method: 'POST' });
      if (notify) notify(`${r.queued} greeting(s) prepared. ${r.note}`,
        r.queued ? 'success' : 'info');
      load();
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  if (err) return <ErrorBox msg={err} />;
  if (!cal) return <SkeletonCards n={2} />;

  const nothingListed = cal.birthdays.length === 0
    && cal.anniversaries.length === 0;

  return (
    <div>
      {nothingListed && cal.excluded_count > 0 && (
        <Banner tone="info"
          title={`${cal.excluded_count} occasion${cal.excluded_count === 1 ? '' : 's'} coming up, nobody listed yet`}>
          Nobody appears here until they have agreed to appear, and nobody is
          messaged until they have separately agreed to that. That is the
          intended state, not a fault. Record each person&apos;s answer below.
        </Banner>
      )}

      <div style={{ display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))',
        gap: 12, marginBottom: space(2) }}>
        <Card>
          <div style={{ fontSize: 13, fontWeight: 700, color: color.navy,
            marginBottom: 8 }}>Birthdays, next 60 days</div>
          {cal.birthdays.length === 0 ? (
            <div style={{ fontSize: 12.5, color: color.textSecondary }}>
              Nobody listed.
            </div>
          ) : cal.birthdays.map((b) => (
            <div key={b.staff_id} style={{ display: 'flex', gap: 8,
              alignItems: 'baseline', padding: '5px 0', fontSize: 13 }}>
              <span style={{ fontWeight: 600 }}>{b.name}</span>
              <span style={{ fontSize: 11.5, color: color.textSecondary }}>
                {b.position}
              </span>
              <span style={{ marginLeft: 'auto', fontSize: 11.5,
                color: color.textSecondary }}>
                {b.days_away === 0 ? 'today' : `in ${b.days_away} days`}
              </span>
              {b.may_message && <Chip tone="success">msg</Chip>}
            </div>
          ))}
        </Card>

        <Card>
          <div style={{ fontSize: 13, fontWeight: 700, color: color.navy,
            marginBottom: 8 }}>
            Work anniversaries
            <span style={{ fontWeight: 400, fontSize: 11.5,
              color: color.textSecondary }}>
              {' '}· years {cal.milestones.join(', ')}
            </span>
          </div>
          {cal.anniversaries.length === 0 ? (
            <div style={{ fontSize: 12.5, color: color.textSecondary }}>
              None listed.
            </div>
          ) : cal.anniversaries.map((a) => (
            <div key={a.staff_id} style={{ display: 'flex', gap: 8,
              alignItems: 'baseline', padding: '5px 0', fontSize: 13 }}>
              <span style={{ fontWeight: 600 }}>{a.name}</span>
              <span style={{ fontSize: 11.5, color: color.textSecondary }}>
                {a.years} years
              </span>
              <span style={{ marginLeft: 'auto', fontSize: 11.5,
                color: color.textSecondary }}>
                {a.days_away === 0 ? 'today' : `in ${a.days_away} days`}
              </span>
              {a.may_message && <Chip tone="success">msg</Chip>}
            </div>
          ))}
        </Card>
      </div>

      {isAdmin() && (
        <div style={{ display: 'flex', gap: 10, marginBottom: space(2),
          flexWrap: 'wrap' }}>
          <Btn variant="secondary" onClick={() => setShowConsent((v) => !v)}>
            {showConsent ? 'Hide consent settings' : 'Record who has agreed'}
          </Btn>
          <Btn onClick={prepare} disabled={busy}>
            {busy ? 'Preparing…' : "Prepare today's greetings"}
          </Btn>
        </div>
      )}

      {showConsent && isAdmin() && (
        <Card>
          <div style={{ fontSize: 13.5, fontWeight: 700, color: color.navy }}>
            What each person has agreed to
          </div>
          <div style={{ fontSize: 12, color: color.textSecondary,
            marginTop: 4, marginBottom: space(2), lineHeight: 1.6 }}>
            Ask each person before ticking. Everything starts at no, and
            somebody hidden from staff displays is never messaged whatever is
            set here.
          </div>
          {staff.map((p) => (
            <ConsentRow key={p.staff_id} person={p} notify={notify}
              onSaved={load} />
          ))}
        </Card>
      )}

      <div style={{ fontSize: 11.5, color: color.textSecondary,
        marginTop: space(2), lineHeight: 1.7, maxWidth: 760 }}>
        {cal.note}
      </div>
    </div>
  );
}
