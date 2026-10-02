// Hiding a member of staff from the lists their name appears on.
//
// Used anywhere staff names are listed. Two pieces:
//
//   <HideStaffControl>  — the per-person action, with the reason dialog
//   <HiddenStaffPanel>  — everyone currently hidden, and how to restore them
//
// WHY THE SECOND ONE EXISTS
// -------------------------
// The panel is not a convenience. Hiding colleagues is only safe while the
// whole set of hidden people is visible in one place to somebody who can undo
// it; without that, "hidden" quietly becomes "deleted, with no record and no
// way back". Every hiding and every restoration is written to an append-only
// log naming who did it and why, and the dialog below says so before anyone
// presses the button.
//
// WHAT HIDING DOES NOT DO
// -----------------------
// It does not end employment and it does not affect pay. The payroll screen
// shows hidden staff with a marker rather than dropping them, because a
// suspended employee is usually still owed wages and somebody hidden by
// mistake must not thereby become unpayable.

import React, { useCallback, useEffect, useState } from 'react';
import { authedFetch, isAdmin } from './utils/api';
import { color, radius, space } from './ui/theme';
import { Banner, Btn, Card, ErrorBox, SkeletonCards } from './ui/kit';

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
  width: '100%', padding: '9px 11px', borderRadius: radius.sm, fontSize: 13.5,
  border: `1px solid ${color.borderStrong}`, fontFamily: 'inherit',
  boxSizing: 'border-box', background: '#fff', color: color.text,
};

// Offered as suggestions, not as a closed list: the free-text box stays, so a
// reason nobody anticipated can still be recorded properly rather than being
// forced into the nearest wrong category.
const COMMON_REASONS = [
  'Resigned',
  'Suspended pending a query',
  'Duplicate record',
  'Bereavement — no birthday reminders',
  'Record created in error',
];

export function HideStaffDialog({ staff, onClose, onDone }) {
  const hiding = !staff.display_hidden;
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const submit = async () => {
    setBusy(true); setErr('');
    try {
      const result = await req(`/api/staff/${staff.id}/visibility`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hidden: hiding, reason: reason.trim() }),
      });
      onDone(result);
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.55)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      zIndex: 2000, padding: 16,
    }} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={{
        background: '#fff', borderRadius: radius.lg, width: 'min(520px, 100%)',
        maxHeight: '90vh', overflow: 'auto', padding: space(3),
      }}>
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 800,
          color: color.navy }}>
          {hiding ? 'Hide from staff lists' : 'Restore to staff lists'}
        </h3>
        <div style={{ fontSize: 13.5, marginTop: 6, fontWeight: 600 }}>
          {staff.first_name} {staff.last_name}
          <span style={{ fontWeight: 400, color: color.textSecondary }}>
            {' '}· {staff.employee_id}
          </span>
        </div>

        <div style={{ margin: `${space(2)} 0` }}>
          <Banner tone="info" title={hiding
            ? 'This hides the name, nothing else'
            : 'This puts the name back on the lists'}>
            {hiding
              ? 'They come off the dashboard birthday panel and the staff '
                + 'register. Their employment, attendance and pay are '
                + 'untouched — they still appear on payroll, marked as hidden, '
                + 'so nobody can be made unpayable by being hidden.'
              : 'Their name returns everywhere it was shown before.'}
          </Banner>
        </div>

        {err && <ErrorBox msg={err} />}

        <label style={{ fontSize: 12, fontWeight: 600,
          color: color.textSecondary }}>
          Reason {hiding ? 'for hiding' : 'for restoring'} — required
        </label>
        <input style={{ ...inputStyle, marginTop: 5 }} value={reason}
          autoFocus onChange={(e) => setReason(e.target.value)}
          placeholder="Say why, in a few words" />

        {hiding && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap',
            marginTop: 9 }}>
            {COMMON_REASONS.map((r) => (
              <button key={r} onClick={() => setReason(r)} style={{
                padding: '4px 10px', borderRadius: radius.pill, fontSize: 11.5,
                border: `1px solid ${color.borderStrong}`, background: '#fff',
                color: color.textSecondary, cursor: 'pointer',
              }}>{r}</button>
            ))}
          </div>
        )}

        <div style={{ fontSize: 11.5, color: color.textSecondary,
          marginTop: 12, lineHeight: 1.6 }}>
          Kept on the record permanently, with your name and the time. Hiding
          and restoring are both logged, and the log cannot be edited.
        </div>

        <div style={{ display: 'flex', gap: 10, marginTop: space(3),
          justifyContent: 'flex-end' }}>
          <Btn variant="secondary" onClick={onClose}>Cancel</Btn>
          <Btn variant={hiding ? 'danger' : 'primary'} onClick={submit}
            disabled={busy || reason.trim().length < 3}>
            {busy ? 'Saving…' : hiding ? 'Hide this person' : 'Restore'}
          </Btn>
        </div>
      </div>
    </div>
  );
}

/** The per-person control. Render it next to a name anywhere staff are listed. */
export function HideStaffControl({ staff, onChanged, compact = false }) {
  const [open, setOpen] = useState(false);
  if (!isAdmin()) return null;

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        title={staff.display_hidden
          ? `Hidden: ${staff.hidden_reason || 'no reason recorded'}`
          : 'Hide this person from staff lists'}
        style={{
          padding: compact ? '3px 9px' : '5px 12px',
          borderRadius: radius.pill, fontSize: compact ? 11 : 12,
          fontWeight: 600, cursor: 'pointer',
          border: `1px solid ${staff.display_hidden
            ? '#FCD34D' : color.borderStrong}`,
          background: staff.display_hidden ? '#FFFBEB' : '#fff',
          color: staff.display_hidden ? '#B45309' : color.textSecondary,
          whiteSpace: 'nowrap',
        }}>
        {staff.display_hidden ? 'Hidden — restore' : 'Hide'}
      </button>
      {open && (
        <HideStaffDialog staff={staff} onClose={() => setOpen(false)}
          onDone={(r) => { setOpen(false); if (onChanged) onChanged(r); }} />
      )}
    </>
  );
}

/** A small marker for use beside a name that is currently hidden. */
export function HiddenBadge({ reason }) {
  return (
    <span title={reason ? `Hidden: ${reason}` : 'Hidden from staff lists'}
      style={{
        marginLeft: 7, fontSize: 10, fontWeight: 700, color: '#B45309',
        background: '#FFFBEB', padding: '2px 7px', borderRadius: 10,
        whiteSpace: 'nowrap',
      }}>HIDDEN</span>
  );
}

/** Everyone currently hidden, why, and by whom. */
export function HiddenStaffPanel({ onChanged }) {
  const [list, setList] = useState(null);
  const [err, setErr] = useState('');
  const [restoring, setRestoring] = useState(null);

  const load = useCallback(async () => {
    try {
      const r = await req('/api/staff/hidden');
      setList(r.hidden || []); setErr('');
    } catch (e) { setErr(e.message); }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (!isAdmin()) return null;
  if (err) return <ErrorBox msg={err} />;
  if (list === null) return <SkeletonCards n={1} />;

  return (
    <Card>
      <div style={{ fontSize: 14, fontWeight: 700, color: color.navy,
        marginBottom: 6 }}>
        Hidden staff ({list.length})
      </div>
      <div style={{ fontSize: 12, color: color.textSecondary,
        marginBottom: space(2), lineHeight: 1.6 }}>
        These names are kept off the dashboard and the staff register. Their
        pay is unaffected — they still appear on payroll, marked as hidden.
      </div>

      {list.length === 0 ? (
        <div style={{ fontSize: 13, color: color.textSecondary,
          padding: space(2), textAlign: 'center' }}>
          Nobody is hidden.
        </div>
      ) : list.map((h) => (
        <div key={h.staff_id} style={{
          display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap',
          padding: '10px 0', borderBottom: `1px solid ${color.border}`,
        }}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontSize: 13.5, fontWeight: 600 }}>
              {h.name}
              <span style={{ fontWeight: 400, color: color.textSecondary }}>
                {' '}· {h.employee_id}
              </span>
            </div>
            <div style={{ fontSize: 12, color: '#B45309', marginTop: 2 }}>
              {h.reason}
            </div>
            <div style={{ fontSize: 11, color: color.textMuted, marginTop: 2 }}>
              {h.hidden_at ? new Date(h.hidden_at).toLocaleString() : ''}
              {h.hidden_by ? ` · by ${h.hidden_by}` : ''}
            </div>
          </div>
          <Btn size="sm" variant="secondary"
            onClick={() => setRestoring({
              id: h.staff_id, employee_id: h.employee_id,
              first_name: h.name.split(' ')[0],
              last_name: h.name.split(' ').slice(1).join(' '),
              display_hidden: true, hidden_reason: h.reason,
            })}>Restore</Btn>
        </div>
      ))}

      {restoring && (
        <HideStaffDialog staff={restoring} onClose={() => setRestoring(null)}
          onDone={() => {
            setRestoring(null); load(); if (onChanged) onChanged();
          }} />
      )}
    </Card>
  );
}

export default HiddenStaffPanel;
