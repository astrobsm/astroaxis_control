// Ordering links and the orders they produced. Shown in the distributor dossier.
//
// THE ONE THING THIS SCREEN MUST GET RIGHT
// ----------------------------------------
// The token is shown ONCE, on creation, and can never be retrieved. The server
// stores only its hash, so there is no endpoint that could show it again — this
// is not a policy the UI is enforcing, it is a fact about what exists.
//
// So the reveal panel says so plainly and does not disappear on a stray click.
// Getting this wrong means an admin closes the dialog, goes looking for a "show
// link" button that cannot exist, and concludes the feature is broken.
//
// The list deliberately shows only a six-character hint, never a token. If that
// ever changes, the guarantee above is gone.

import React, { useCallback, useEffect, useState } from 'react';
import { authedFetch } from './utils/api';
import { color, naira, radius, space } from './ui/theme';
import {
  Banner, Btn, Card, Chip, DataTable, ErrorBox, SectionTitle, SkeletonCards,
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

const input = {
  width: '100%', padding: '9px 11px', borderRadius: radius.sm,
  border: `1px solid ${color.borderStrong}`, fontSize: 13.5,
  fontFamily: 'inherit', boxSizing: 'border-box', background: '#fff',
};

function Field({ label, hint, children }) {
  return (
    <label style={{ display: 'block', marginBottom: space(1.5) }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: color.textSecondary,
        marginBottom: 5 }}>{label}</div>
      {children}
      {hint && (
        <div style={{ fontSize: 11, color: color.textMuted, marginTop: 4,
          lineHeight: 1.5 }}>{hint}</div>
      )}
    </label>
  );
}

// The token, shown once. Never rendered from a list — only from the response
// that created it.
function RevealLink({ issued, onDone }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(issued.url);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <Card pad={2.5}>
      <Banner tone="warning" title="Copy this now — it cannot be shown again">
        {issued.warning}
      </Banner>

      <div style={{
        padding: space(1.5), background: '#FAFBFC', borderRadius: radius.sm,
        border: `1px solid ${color.borderStrong}`, wordBreak: 'break-all',
        fontSize: 13, fontFamily: 'ui-monospace, Menlo, Consolas, monospace',
        marginBottom: space(1.5), lineHeight: 1.6,
      }}>{issued.url}</div>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <Btn size="sm" variant="accent" onClick={copy}>
          {copied ? 'Copied' : 'Copy link'}
        </Btn>
        <Btn size="sm" variant="ghost"
          onClick={() => window.open(
            `https://wa.me/?text=${encodeURIComponent(
              `Your Bonnesante Medicals ordering link: ${issued.url}`)}`,
            '_blank', 'noopener')}>
          Send on WhatsApp
        </Btn>
        <Btn size="sm" variant="secondary" style={{ marginLeft: 'auto' }}
          onClick={onDone}>
          I have saved it
        </Btn>
      </div>

      <div style={{ fontSize: 11.5, color: color.textMuted, marginTop: space(1.5),
        lineHeight: 1.6 }}>
        Only a fingerprint of this link is stored, so nobody — including this
        app — can recover it later. If it is lost, revoke it and issue another.
        It expires on {new Date(issued.expires_at).toLocaleDateString()}.
      </div>
    </Card>
  );
}

export function OrderingPanel({ distributorId, distributorStatus }) {
  const [links, setLinks] = useState(null);
  const [orders, setOrders] = useState([]);
  const [err, setErr] = useState('');
  const [issuing, setIssuing] = useState(null);
  const [issued, setIssued] = useState(null);
  const [activity, setActivity] = useState(null);
  const [showDead, setShowDead] = useState(false);

  const load = useCallback(async () => {
    try {
      const [l, o] = await Promise.all([
        getJSON(`/api/distributors/${distributorId}/order-links`
                + `?include_dead=${showDead}`),
        getJSON(`/api/distributors/${distributorId}/orders`),
      ]);
      setLinks(l.links || []);
      setOrders(o.orders || []);
      setErr('');
    } catch (e) { setErr(e.message); }
  }, [distributorId, showDead]);

  useEffect(() => { load(); }, [load]);

  const issue = async () => {
    try {
      const r = await postJSON(
        `/api/distributors/${distributorId}/order-links`, issuing);
      setIssuing(null);
      setIssued(r);
      await load();
    } catch (e) { setErr(e.message); }
  };

  const revoke = async (link) => {
    const reason = window.prompt(
      `Revoke "${link.label}"? It stops working immediately and permanently.\n\n`
      + 'Why?');
    if (!reason || reason.trim().length < 3) return;
    try {
      await postJSON(`/api/distributors/order-links/${link.id}/revoke`,
        { reason: reason.trim() });
      await load();
    } catch (e) { setErr(e.message); }
  };

  const openActivity = async (link) => {
    try {
      const r = await getJSON(
        `/api/distributors/order-links/${link.id}/activity`);
      setActivity({ link, events: r.activity || [] });
    } catch (e) { setErr(e.message); }
  };

  if (!links) return <SkeletonCards n={2} />;

  if (issued) return <RevealLink issued={issued} onDone={() => setIssued(null)} />;

  if (activity) {
    return (
      <div>
        <Btn size="sm" variant="ghost" style={{ marginBottom: space(2) }}
          onClick={() => setActivity(null)}>← Back</Btn>
        <Card pad={2.5}>
          <SectionTitle>
            Everything done with “{activity.link.label}”
          </SectionTitle>
          <DataTable
            cols={[
              { key: 'created_at', label: 'When' },
              { key: 'event_type', label: 'What' },
              { key: 'order_number', label: 'Order' },
              { key: 'ip_address', label: 'From' },
              { key: 'detail', label: '', wrap: true },
            ]}
            rows={activity.events}
            empty="Never used."
            render={(r, c) => {
              if (c.key === 'created_at') {
                return new Date(r.created_at).toLocaleString();
              }
              if (c.key === 'event_type') {
                const tone = { ORDERED: 'success', OPENED: 'info',
                  QUOTED: 'neutral', REJECTED: 'danger', EXPIRED: 'warning',
                  REVOKED: 'danger' }[r.event_type] || 'neutral';
                return <Chip tone={tone}>{r.event_type}</Chip>;
              }
              return r[c.key] || '—';
            }}
          />
          <div style={{ fontSize: 11, color: color.textMuted, marginTop: 10,
            lineHeight: 1.6 }}>
            This record is append-only. If this link is thought to have leaked,
            this is where it was opened from and what was ordered with it.
          </div>
        </Card>
      </div>
    );
  }

  const canIssue = distributorStatus === 'ACTIVE';

  return (
    <div>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <SectionTitle right={!issuing && canIssue && (
          <Btn size="sm" variant="accent"
            onClick={() => setIssuing({ label: '', recipient_name: '',
              recipient_phone: '', valid_days: 30 })}>
            Issue an ordering link
          </Btn>
        )}>Ordering links</SectionTitle>

        {!canIssue && (
          <Banner tone="warning" title="Ordering is closed on this account">
            A link can only be issued to an active distributor, and any existing
            link stops working while the account is not active.
          </Banner>
        )}

        {issuing ? (
          <div>
            <Banner tone="info" title="This link is a password">
              Anyone holding it can place orders billed to this distributor
              until it expires or is revoked. Send it to one named person, not
              a group chat.
            </Banner>
            <Field label="Who is it for"
              hint="You will read this months from now when deciding which link to revoke. 'Chinedu, Aba depot' beats 'Link 2'.">
              <input style={input} value={issuing.label} autoFocus
                placeholder="Chinedu, Aba depot"
                onChange={(e) => setIssuing({ ...issuing, label: e.target.value })} />
            </Field>
            <Field label="Their name (optional)">
              <input style={input} value={issuing.recipient_name}
                onChange={(e) => setIssuing(
                  { ...issuing, recipient_name: e.target.value })} />
            </Field>
            <Field label="Their phone (optional)">
              <input style={input} value={issuing.recipient_phone}
                onChange={(e) => setIssuing(
                  { ...issuing, recipient_phone: e.target.value })} />
            </Field>
            <Field label="Valid for"
              hint="There is no 'never expires'. A permanent unauthenticated link outlives the relationship it was issued for.">
              <select style={input} value={issuing.valid_days}
                onChange={(e) => setIssuing(
                  { ...issuing, valid_days: Number(e.target.value) })}>
                <option value={7}>7 days</option>
                <option value={30}>30 days</option>
                <option value={90}>90 days</option>
                <option value={180}>6 months</option>
                <option value={365}>1 year</option>
              </select>
            </Field>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <Btn size="sm" variant="ghost"
                onClick={() => setIssuing(null)}>Cancel</Btn>
              <Btn size="sm" variant="accent"
                disabled={issuing.label.trim().length < 3} onClick={issue}>
                Create link
              </Btn>
            </div>
          </div>
        ) : (
          <>
            <DataTable
              cols={[
                { key: 'label', label: 'Issued to', wrap: true },
                { key: 'token_hint', label: 'Ends in' },
                { key: 'expires_at', label: 'Expires' },
                { key: 'use_count', label: 'Opened', align: 'right' },
                { key: 'order_count', label: 'Orders', align: 'right' },
                { key: 'act', label: '', align: 'right' },
              ]}
              rows={links}
              empty="No ordering link has been issued."
              render={(r, c) => {
                if (c.key === 'label') {
                  return (
                    <span>
                      {r.label}
                      {!r.is_live && (
                        <>
                          {' '}
                          <Chip tone="danger">
                            {r.revoked_at ? 'Revoked' : 'Expired'}
                          </Chip>
                        </>
                      )}
                      {r.recipient_phone && (
                        <div style={{ fontSize: 11, color: color.textMuted }}>
                          {r.recipient_name} {r.recipient_phone}
                        </div>
                      )}
                      {r.revoke_reason && (
                        <div style={{ fontSize: 11, color: color.textMuted }}>
                          {r.revoke_reason}
                        </div>
                      )}
                    </span>
                  );
                }
                if (c.key === 'token_hint') {
                  return <code style={{ fontSize: 11.5 }}>…{r.token_hint}</code>;
                }
                if (c.key === 'expires_at') {
                  return new Date(r.expires_at).toLocaleDateString();
                }
                if (c.key === 'act') {
                  return (
                    <div style={{ display: 'flex', gap: 6,
                      justifyContent: 'flex-end' }}>
                      <Btn size="sm" variant="ghost"
                        onClick={() => openActivity(r)}>Activity</Btn>
                      {r.is_live && (
                        <Btn size="sm" variant="danger"
                          onClick={() => revoke(r)}>Revoke</Btn>
                      )}
                    </div>
                  );
                }
                return r[c.key] ?? '—';
              }}
            />
            <label style={{ fontSize: 12, display: 'flex', alignItems: 'center',
              gap: 6, marginTop: 10 }}>
              <input type="checkbox" checked={showDead}
                onChange={(e) => setShowDead(e.target.checked)} />
              Include revoked and expired links
            </label>
          </>
        )}
      </Card>

      <Card pad={2.5}>
        <SectionTitle>Orders placed</SectionTitle>
        <DataTable
          cols={[
            { key: 'order_number', label: 'Order' },
            { key: 'order_date', label: 'Date' },
            { key: 'line_count', label: 'Items', align: 'right' },
            { key: 'total_amount', label: 'Value', align: 'right' },
            { key: 'status', label: 'Status' },
            { key: 'link_label', label: 'Via', wrap: true },
          ]}
          rows={orders}
          empty="No orders yet."
          render={(r, c) => {
            if (c.key === 'total_amount') return naira(r.total_amount);
            if (c.key === 'order_date') {
              return r.order_date
                ? new Date(r.order_date).toLocaleDateString() : '—';
            }
            if (c.key === 'status') {
              const tone = { pending: 'warning', confirmed: 'info',
                delivered: 'success', cancelled: 'danger' }[r.status] || 'neutral';
              return <Chip tone={tone}>{r.status}</Chip>;
            }
            if (c.key === 'link_label') {
              return r.link_label
                ? <span>{r.link_label}{' '}
                    <code style={{ fontSize: 10.5, color: color.textMuted }}>
                      …{r.token_hint}
                    </code>
                  </span>
                : <span style={{ color: color.textMuted }}>Entered by staff</span>;
            }
            return r[c.key] ?? '—';
          }}
        />
        <div style={{ fontSize: 11, color: color.textMuted, marginTop: 10,
          lineHeight: 1.6 }}>
          These are ordinary sales orders. They appear in sales reporting,
          receivables and despatch exactly like any other — there is no separate
          distributor order book to reconcile.
        </div>
      </Card>
    </div>
  );
}

export default OrderingPanel;
