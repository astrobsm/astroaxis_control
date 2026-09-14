// Distribution — territories and distributors.
//
// Mounted from AppMain when activeModule === 'distributors'.
// ('distribution' was already taken by the MAPD payment module.)
//
// The screen that matters most here is the distributor dossier's LINKAGE panel.
// It shows the customer account and the warehouse this distributor resolves to
// in the existing systems, and the stock figures are read live from those
// warehouses rather than copied. That is the proof, visible to whoever is
// looking, that this module orchestrates the existing accounting and inventory
// rather than keeping a second set of books beside them.

import React, { useCallback, useEffect, useState } from 'react';
import { authedFetch } from './utils/api';
import { color, font, naira, radius, space } from './ui/theme';
import {
  Banner, Btn, Card, Chip, DataTable, ErrorBox, Icon, KpiCard, SectionTitle,
  SkeletonCards,
} from './ui/kit';
import { CompliancePanel, CorrectiveActionQueue } from './DistributorCompliance';
import { ApplicationQueue, TerritoryHoldings } from './TerritoryApplications';
import { OrderingPanel } from './DistributorOrdering';
import { DownstreamPanel } from './DownstreamSales';
import { PerformancePanel } from './Performance';

async function req(url, opts) {
  const res = await authedFetch(url, opts);
  if (!res.ok) {
    let d = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      d = typeof j.detail === 'object' ? j.detail : (j.detail || j.message || d);
    } catch { /* keep status */ }
    const err = new Error(typeof d === 'string' ? d : (d.message || 'Request failed'));
    err.payload = d;
    err.status = res.status;
    throw err;
  }
  return res.json();
}
const getJSON = (u) => req(u);
const postJSON = (u, b) => req(u, { method: 'POST', body: JSON.stringify(b || {}) });

const money = (v) => naira(v);
const todayISO = () => new Date().toISOString().slice(0, 10);
const firstOfNextMonth = () => {
  const d = new Date();
  return new Date(d.getFullYear(), d.getMonth() + 1, 1).toISOString().slice(0, 10);
};

const STATUS_TONE = {
  ACTIVE: 'success', APPROVED: 'success', AVAILABLE: 'info',
  ASSIGNED: 'success', DRAFT: 'neutral', APPLIED: 'warning',
  UNDER_REVIEW: 'warning', RESERVED: 'warning', SUSPENDED: 'danger',
  TERMINATED: 'neutral', REJECTED: 'danger', RETIRED: 'neutral',
  VERIFIED: 'success', PENDING: 'warning', EXPIRED: 'danger',
};
const tone = (s) => STATUS_TONE[String(s || '').toUpperCase()] || 'neutral';

const inputStyle = {
  padding: '9px 10px', border: `1px solid ${color.borderStrong}`,
  borderRadius: radius.sm, fontSize: 13, fontFamily: font.family,
  color: color.text, background: '#fff', width: '100%', boxSizing: 'border-box',
};

function Field({ label, hint, children }) {
  return (
    <label style={{ display: 'block', fontSize: 12, color: color.textSecondary, fontWeight: 600 }}>
      {label}
      <div style={{ marginTop: 4 }}>{children}</div>
      {hint && <div style={{ marginTop: 3, fontSize: 11.5, color: color.textMuted, fontWeight: 400, lineHeight: 1.45 }}>{hint}</div>}
    </label>
  );
}

function Grid({ children, min = 220 }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(auto-fit, minmax(${min}px, 1fr))`, gap: space(2) }}>
      {children}
    </div>
  );
}

function Modal({ title, onClose, children, footer, width = 620 }) {
  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: space(2),
    }}>
      <div onClick={(e) => e.stopPropagation()} style={{
        background: '#fff', width: '100%', maxWidth: width, maxHeight: '88vh',
        display: 'flex', flexDirection: 'column', borderRadius: radius.lg,
        boxShadow: '0 24px 48px rgba(15,23,42,0.24)',
      }}>
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: `${space(2)} ${space(2.5)}`, borderBottom: `1px solid ${color.border}`,
        }}>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>{title}</h3>
          <button onClick={onClose} aria-label="Close" style={{
            border: 'none', background: 'transparent', fontSize: 24, lineHeight: 1,
            color: color.textMuted, cursor: 'pointer',
          }}>&times;</button>
        </div>
        <div style={{ padding: space(2.5), overflowY: 'auto' }}>{children}</div>
        {footer && <div style={{ padding: space(2.5), borderTop: `1px solid ${color.border}`, background: '#FbFcFe' }}>{footer}</div>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Register a distributor — duplicate check runs as you type
// ---------------------------------------------------------------------------

function NewDistributor({ states, onClose, onDone }) {
  const [form, setForm] = useState({
    legal_name: '', trading_name: '', entity_type: 'COMPANY', phone: '',
    email: '', business_address: '', state_id: '', lga_id: '', town: '',
    cac_number: '', tin: '', years_in_operation: '', business_type: '',
  });
  const [lgas, setLgas] = useState([]);
  const [dupes, setDupes] = useState([]);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  useEffect(() => {
    if (!form.state_id) { setLgas([]); return; }
    getJSON(`/api/geography/lgas?state_id=${form.state_id}`)
      .then((d) => setLgas(d.lgas || [])).catch(() => setLgas([]));
  }, [form.state_id]);

  // Checked while the form is still being filled in, so a duplicate is caught
  // before twenty fields have been typed rather than at submit.
  useEffect(() => {
    const q = [form.legal_name, form.phone, form.email, form.cac_number, form.tin];
    if (!q.some((v) => (v || '').trim().length >= 3)) { setDupes([]); return; }
    const t = setTimeout(() => {
      const params = new URLSearchParams({
        legal_name: form.legal_name || '', phone: form.phone || '',
        email: form.email || '', cac_number: form.cac_number || '',
        tin: form.tin || '',
      });
      getJSON(`/api/distributors/check-duplicate?${params}`)
        .then((d) => setDupes(d.candidates || [])).catch(() => {});
    }, 400);
    return () => clearTimeout(t);
  }, [form.legal_name, form.phone, form.email, form.cac_number, form.tin]);

  const strong = dupes.filter((d) => d.strength >= 80);

  const submit = async () => {
    setErr('');
    if (form.legal_name.trim().length < 2) { setErr('A legal name is required.'); return; }
    if (strong.length && !acknowledged) {
      setErr('Review the possible duplicates and confirm this is a new business.');
      return;
    }
    setBusy(true);
    try {
      const r = await postJSON('/api/distributors', {
        ...form,
        state_id: form.state_id || null,
        lga_id: form.lga_id || null,
        years_in_operation: form.years_in_operation === '' ? null : Number(form.years_in_operation),
        acknowledge_duplicates: acknowledged,
      });
      onDone(`${r.distributor_code} registered for ${r.legal_name}.`);
    } catch (e) { setErr(e.message); setBusy(false); }
  };

  return (
    <Modal title="Register a distributor" onClose={busy ? () => {} : onClose}
      footer={<Btn variant="accent" onClick={submit} disabled={busy} style={{ width: '100%' }}>
        {busy ? 'Registering…' : 'Register in draft'}</Btn>}>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      {strong.length > 0 && (
        <div style={{ marginBottom: space(2) }}>
          <Banner tone="warning" title="This may already exist in the system">
            <div style={{ marginTop: 6 }}>
              {strong.map((c, i) => (
                <div key={i} style={{ marginBottom: 6, fontSize: 12.5 }}>
                  <strong>{c.name}</strong> {c.code ? `(${c.code})` : ''}
                  {' — '}
                  <span style={{ color: color.textSecondary }}>
                    {c.kind === 'customer' ? 'existing customer' : 'existing distributor'}
                    {': '}{c.reasons.join('; ')}
                  </span>
                </div>
              ))}
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8, fontSize: 12.5 }}>
              <input type="checkbox" checked={acknowledged}
                onChange={(e) => setAcknowledged(e.target.checked)} />
              I have reviewed these and this is a different business
            </label>
          </Banner>
        </div>
      )}

      <div style={{ display: 'grid', gap: space(2) }}>
        <Grid min={240}>
          <Field label="Legal name">
            <input value={form.legal_name} onChange={set('legal_name')} style={inputStyle} />
          </Field>
          <Field label="Trading name">
            <input value={form.trading_name} onChange={set('trading_name')} style={inputStyle} />
          </Field>
        </Grid>
        <Grid min={200}>
          <Field label="Type">
            <select value={form.entity_type} onChange={set('entity_type')} style={inputStyle}>
              <option value="COMPANY">Company</option>
              <option value="INDIVIDUAL">Individual</option>
            </select>
          </Field>
          <Field label="Phone"><input value={form.phone} onChange={set('phone')} style={inputStyle} /></Field>
          <Field label="Email"><input value={form.email} onChange={set('email')} style={inputStyle} /></Field>
        </Grid>
        <Grid min={200}>
          <Field label="State">
            <select value={form.state_id} onChange={set('state_id')} style={inputStyle}>
              <option value="">Choose…</option>
              {states.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </Field>
          <Field label="LGA" hint={lgas.length ? null : 'Import LGAs for this state first.'}>
            <select value={form.lga_id} onChange={set('lga_id')} style={inputStyle}
              disabled={!lgas.length}>
              <option value="">Choose…</option>
              {lgas.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
          </Field>
          <Field label="Town"><input value={form.town} onChange={set('town')} style={inputStyle} /></Field>
        </Grid>
        <Field label="Business address">
          <textarea value={form.business_address} onChange={set('business_address')}
            rows={2} style={{ ...inputStyle, resize: 'vertical' }} />
        </Field>
        <Grid min={200}>
          <Field label="CAC number"
            hint={form.entity_type === 'COMPANY' ? 'Required before approval.' : null}>
            <input value={form.cac_number} onChange={set('cac_number')} style={inputStyle} />
          </Field>
          <Field label="TIN"><input value={form.tin} onChange={set('tin')} style={inputStyle} /></Field>
          <Field label="Years trading">
            <input type="number" min="0" value={form.years_in_operation}
              onChange={set('years_in_operation')} style={inputStyle} />
          </Field>
        </Grid>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// New territory
// ---------------------------------------------------------------------------

function NewTerritory({ states, onClose, onDone }) {
  const [form, setForm] = useState({
    code: '', name: '', state_id: '', monthly_target: '1000000',
    description: '', towns: '', is_exclusive: true,
  });
  const [lgas, setLgas] = useState([]);
  const [picked, setPicked] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  useEffect(() => {
    if (!form.state_id) { setLgas([]); setPicked([]); return; }
    getJSON(`/api/geography/lgas?state_id=${form.state_id}`)
      .then((d) => setLgas(d.lgas || [])).catch(() => setLgas([]));
  }, [form.state_id]);

  const submit = async () => {
    setErr('');
    if (!form.code.trim() || !form.name.trim() || !form.state_id) {
      setErr('Code, name and state are required.'); return;
    }
    setBusy(true);
    try {
      const r = await postJSON('/api/geography/territories', {
        code: form.code.trim(), name: form.name.trim(),
        state_id: form.state_id, lga_ids: picked,
        description: form.description || null, towns: form.towns || null,
        is_exclusive: form.is_exclusive,
        monthly_target: form.monthly_target === '' ? null : Number(form.monthly_target),
      });
      onDone(`Territory ${r.code} created.`);
    } catch (e) { setErr(e.message); setBusy(false); }
  };

  return (
    <Modal title="Create a territory" onClose={busy ? () => {} : onClose}
      footer={<Btn variant="accent" onClick={submit} disabled={busy} style={{ width: '100%' }}>
        {busy ? 'Creating…' : 'Create territory'}</Btn>}>
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <Banner tone="info" title="A territory is a commercial decision, not a map">
        It can cover part of one LGA or several whole ones, and you can re-cut
        territories later without the administrative geography changing.
      </Banner>
      <div style={{ height: space(2) }} />

      <div style={{ display: 'grid', gap: space(2) }}>
        <Grid min={200}>
          <Field label="Code" hint="Short and stable, e.g. LAG-Z01">
            <input value={form.code} onChange={set('code')} style={inputStyle} />
          </Field>
          <Field label="Name">
            <input value={form.name} onChange={set('name')} placeholder="Lagos Zone 1" style={inputStyle} />
          </Field>
        </Grid>
        <Grid min={200}>
          <Field label="State">
            <select value={form.state_id} onChange={set('state_id')} style={inputStyle}>
              <option value="">Choose…</option>
              {states.map((s) => (
                <option key={s.id} value={s.id}>{s.name} ({s.lga_count} LGAs)</option>
              ))}
            </select>
          </Field>
          <Field label="Monthly target"
            hint="A starting assumption. Change it later with a reason; the history is kept.">
            <input type="number" min="0" value={form.monthly_target}
              onChange={set('monthly_target')} style={inputStyle} />
          </Field>
        </Grid>

        {lgas.length > 0 && (
          <Field label={`LGAs covered (${picked.length} of ${lgas.length})`}>
            <div style={{
              maxHeight: 180, overflowY: 'auto', border: `1px solid ${color.border}`,
              borderRadius: radius.sm, padding: space(1),
            }}>
              {lgas.map((l) => (
                <label key={l.id} style={{
                  display: 'flex', alignItems: 'center', gap: 8, padding: '4px 6px',
                  fontSize: 13, cursor: 'pointer',
                }}>
                  <input type="checkbox" checked={picked.includes(l.id)}
                    onChange={(e) => setPicked(e.target.checked
                      ? [...picked, l.id] : picked.filter((x) => x !== l.id))} />
                  {l.name}
                </label>
              ))}
            </div>
          </Field>
        )}

        <Field label="Towns (descriptive)">
          <input value={form.towns} onChange={set('towns')} style={inputStyle} />
        </Field>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
          <input type="checkbox" checked={form.is_exclusive}
            onChange={(e) => setForm({ ...form, is_exclusive: e.target.checked })} />
          Exclusive — only one distributor may hold it at a time
        </label>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Distributor dossier
// ---------------------------------------------------------------------------

function Dossier({ distributorId, territories, onClose, onChanged }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState('');
  const [pane, setPane] = useState('profile');

  const load = useCallback(async () => {
    try { setData(await getJSON(`/api/distributors/${distributorId}`)); setErr(''); }
    catch (e) { setErr(e.message); }
  }, [distributorId]);

  useEffect(() => { load(); }, [load]);

  if (err) return <Modal title="Distributor" onClose={onClose}><ErrorBox msg={err} /></Modal>;
  if (!data) return <Modal title="Distributor" onClose={onClose}><SkeletonCards n={2} /></Modal>;

  const d = data.distributor;
  const link = data.linkage || {};

  const advance = async (status, prompt) => {
    const reason = window.prompt(prompt);
    if (!reason || reason.trim().length < 3) return;
    try {
      await postJSON(`/api/distributors/${distributorId}/status`,
        { status, reason: reason.trim() });
      await load();
      onChanged();
    } catch (e) { setErr(e.message); }
  };

  const NEXT = {
    DRAFT: [['APPLIED', 'Submit the application']],
    APPLIED: [['UNDER_REVIEW', 'Begin review'], ['REJECTED', 'Reject']],
    UNDER_REVIEW: [['APPROVED', 'Approve'], ['REJECTED', 'Reject']],
    APPROVED: [['ACTIVE', 'Activate'], ['SUSPENDED', 'Suspend']],
    ACTIVE: [['SUSPENDED', 'Suspend'], ['TERMINATED', 'Terminate']],
    SUSPENDED: [['ACTIVE', 'Reinstate'], ['TERMINATED', 'Terminate']],
  }[d.status] || [];

  return (
    <Modal title={`${d.distributor_code} — ${d.legal_name}`} onClose={onClose} width={860}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: space(2) }}>
        <Chip tone={tone(d.status)}>{d.status}</Chip>
        <Chip tone="info">{d.tier}</Chip>
        <Chip tone="neutral">{d.entity_type}</Chip>
        {NEXT.map(([status, label]) => (
          <Btn key={status} size="sm"
            variant={status === 'REJECTED' || status === 'TERMINATED' || status === 'SUSPENDED' ? 'danger' : 'accent'}
            onClick={() => advance(status, `${label} — why?`)}>{label}</Btn>
        ))}
      </div>

      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: space(2) }}>
        {[['profile', 'Profile'], ['territory', 'Territory'],
          ['ordering', 'Ordering'], ['downstream', 'Sell-through'],
          ['performance', 'Performance'],
          ['compliance', 'Facility & agreement']].map(([k, label]) => (
          <button key={k} onClick={() => setPane(k)} style={{
            padding: '6px 13px', borderRadius: radius.pill, fontSize: 12.5, fontWeight: 600,
            border: `1px solid ${pane === k ? color.medical : color.borderStrong}`,
            background: pane === k ? color.infoBg : '#fff',
            color: pane === k ? color.royal : color.textSecondary, cursor: 'pointer',
          }}>{label}</button>
        ))}
      </div>

      {pane === 'territory' && (
        <TerritoryHoldings distributorId={distributorId}
          territories={territories}
          onChanged={() => { load(); onChanged(); }} />
      )}

      {pane === 'ordering' && (
        <OrderingPanel distributorId={distributorId}
          distributorStatus={d.status} />
      )}

      {pane === 'downstream' && (
        <DownstreamPanel distributorId={distributorId} />
      )}

      {pane === 'performance' && (
        <PerformancePanel distributorId={distributorId} />
      )}

      {pane === 'compliance' && (
        <CompliancePanel distributorId={distributorId} documents={data.documents}
          onChanged={() => { load(); onChanged(); }} />
      )}

      {pane === 'profile' && (<>

      {/* The proof that this is an integration and not a parallel system. */}
      <Card pad={2.5} style={{ marginBottom: space(2) }}>
        <SectionTitle>Where this distributor lives in the existing systems</SectionTitle>
        {link.customer_code ? (
          <div style={{ display: 'grid', gap: 8, fontSize: 13 }}>
            <div>
              <strong>Accounting identity:</strong>{' '}
              customer <code>{link.customer_code}</code> — {link.customer_name}
              <div style={{ fontSize: 11.5, color: color.textMuted }}>
                Invoices, payments and AR ageing run through this account. There
                is no separate distributor ledger.
              </div>
            </div>
            <div>
              <strong>Stock location:</strong>{' '}
              warehouse <code>{link.warehouse_code}</code>
              {link.warehouse_active === false && (
                <span style={{ color: color.danger }}> — closed to new movement</span>
              )}
              <div style={{ fontSize: 11.5, color: color.textMuted }}>
                Shipping to this distributor is a stock transfer into this
                warehouse, with the usual balance checks and audit trail.
              </div>
            </div>
          </div>
        ) : (
          <Banner tone="warning" title="Not yet provisioned">
            This distributor has no accounting identity or stock location yet.
            Approving it creates both automatically.
          </Banner>
        )}
      </Card>

      <Grid min={260}>
        <Card pad={2.5}>
          <SectionTitle>Territories</SectionTitle>
          <DataTable
            cols={[
              { key: 'code', label: 'Territory' },
              { key: 'state', label: 'State' },
              { key: 'monthly_target', label: 'Target', align: 'right' },
              { key: 'assigned_from', label: 'Since' },
            ]}
            rows={data.territories}
            empty="No territories assigned."
            render={(r, c) => (c.key === 'monthly_target'
              ? money(r.monthly_target) : r[c.key])}
          />
        </Card>

        <Card pad={2.5}>
          <SectionTitle>Stock held</SectionTitle>
          <DataTable
            cols={[
              { key: 'name', label: 'Product', wrap: true },
              { key: 'current_stock', label: 'Qty', align: 'right' },
            ]}
            rows={data.stock}
            empty="No stock held."
          />
          <div style={{ fontSize: 11, color: color.textMuted, marginTop: 8, lineHeight: 1.5 }}>
            {data.stock_source}
          </div>
        </Card>
      </Grid>

      <div style={{ height: space(2) }} />

      <Card pad={2.5}>
        <SectionTitle>Documents</SectionTitle>
        <DataTable
          cols={[
            { key: 'doc_type', label: 'Type' },
            { key: 'filename', label: 'File', wrap: true },
            { key: 'expiry_date', label: 'Expires' },
            { key: 'verification_status', label: 'Status' },
            { key: 'act', label: '', align: 'right' },
          ]}
          rows={data.documents}
          empty="No documents uploaded."
          render={(r, c) => {
            if (c.key === 'verification_status') {
              return (
                <div>
                  <Chip tone={tone(r.verification_status)}>{r.verification_status}</Chip>
                  {r.expired && <div style={{ marginTop: 3 }}><Chip tone="danger">Expired</Chip></div>}
                </div>
              );
            }
            if (c.key === 'act') {
              return (
                <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                  <Btn size="sm" variant="ghost"
                    onClick={() => window.open(`/api/distributors/documents/${r.id}`, '_blank')}>
                    View
                  </Btn>
                  {r.verification_status === 'PENDING' && (
                    <Btn size="sm" variant="accent" onClick={async () => {
                      try {
                        await postJSON(`/api/distributors/documents/${r.id}/verify`,
                          { verified: true });
                        await load();
                      } catch (e) { setErr(e.message); }
                    }}>Verify</Btn>
                  )}
                </div>
              );
            }
            return r[c.key] || '—';
          }}
        />
      </Card>

      </>)}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Main screen
// ---------------------------------------------------------------------------

export default function Distribution() {
  const [tab, setTab] = useState('overview');
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [toast, setToast] = useState('');
  const [states, setStates] = useState([]);
  const [territories, setTerritories] = useState([]);
  const [distributors, setDistributors] = useState([]);
  const [rollup, setRollup] = useState(null);
  const [expiring, setExpiring] = useState(null);
  const [modal, setModal] = useState(null);

  const flash = (m) => { setToast(m); setTimeout(() => setToast(''), 6000); };

  const load = useCallback(async () => {
    setErr('');
    try {
      const [s, t, d, r, e] = await Promise.all([
        getJSON('/api/geography/states'),
        getJSON('/api/geography/territories'),
        getJSON('/api/distributors'),
        getJSON('/api/geography/targets/rollup'),
        getJSON('/api/distributors/compliance/expiring?within_days=90').catch(() => null),
      ]);
      setStates(s.states || []);
      setTerritories(t.territories || []);
      setDistributors(d.distributors || []);
      setRollup(r);
      setExpiring(e);
    } catch (ex) { setErr(ex.message); }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return <SkeletonCards n={4} />;

  const active = distributors.filter((d) => d.status === 'ACTIVE').length;
  const pending = distributors.filter(
    (d) => ['APPLIED', 'UNDER_REVIEW'].includes(d.status)).length;
  const unassigned = territories.filter((t) => !t.holder).length;
  const lgaTotal = states.reduce((a, s) => a + Number(s.lga_count || 0), 0);

  return (
    <div>
      {toast && <div style={{ marginBottom: space(2) }}><Banner tone="success" title="Done">{toast}</Banner></div>}
      {err && <div style={{ marginBottom: space(2) }}><ErrorBox msg={err} /></div>}

      <div style={{ display: 'flex', gap: space(1), flexWrap: 'wrap', marginBottom: space(2.5) }}>
        {[['overview', 'Overview'], ['territories', 'Territories'],
          ['distributors', 'Distributors'], ['applications', 'Applications'],
          ['compliance', 'Compliance']].map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)} style={{
            padding: '8px 15px', borderRadius: radius.pill, fontSize: 13, fontWeight: 600,
            border: `1px solid ${tab === k ? color.medical : color.borderStrong}`,
            background: tab === k ? color.infoBg : '#fff',
            color: tab === k ? color.royal : color.textSecondary, cursor: 'pointer',
          }}>{label}</button>
        ))}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <Btn size="sm" variant="secondary" icon="tag"
            onClick={() => setModal({ kind: 'territory' })}>New territory</Btn>
          <Btn size="sm" variant="accent" icon="users"
            onClick={() => setModal({ kind: 'distributor' })}>Register distributor</Btn>
          <Btn size="sm" variant="ghost" icon="refresh" onClick={load}>Refresh</Btn>
        </div>
      </div>

      {tab === 'overview' && rollup && (
        <>
          <Grid>
            <KpiCard icon="budget" label="National target"
              value={money(rollup.national_target)}
              sub={`${rollup.territory_count} territories`} tone="royal" />
            <KpiCard icon="users" label="Active distributors" value={active}
              sub={`${pending} awaiting review`} tone="success" />
            <KpiCard icon="alert" label="Unassigned territories" value={unassigned}
              sub="No distributor holds them"
              tone={unassigned > 0 ? 'warning' : 'success'} />
            <KpiCard icon="asset" label="LGAs loaded" value={lgaTotal}
              sub="of 774 nationally"
              tone={lgaTotal < 774 ? 'warning' : 'success'} />
          </Grid>

          {lgaTotal < 774 && (
            <div style={{ marginTop: space(2.5) }}>
              <Banner tone="warning" title={`Only ${lgaTotal} of Nigeria's 774 LGAs are loaded`}>
                Lagos and the FCT area councils were seeded. The rest should be
                imported from an authoritative source (NBS or INEC) rather than
                typed in — a misspelt LGA silently corrupts every territory
                report built on it. Upload a CSV of <code>state_code,lga_name</code>
                {' '}to <code>/api/geography/lgas/import</code>.
              </Banner>
            </div>
          )}

          <div style={{ height: space(2.5) }} />

          <Card>
            <SectionTitle>Target by state</SectionTitle>
            <DataTable
              cols={[
                { key: 'state', label: 'State' },
                { key: 'target', label: 'Monthly target', align: 'right' },
              ]}
              rows={rollup.by_state}
              empty="No territories defined yet."
              render={(r, c) => (c.key === 'target' ? money(r.target) : r[c.key])}
            />
          </Card>
        </>
      )}

      {tab === 'territories' && (
        <Card>
          <SectionTitle right={<Chip tone="neutral">{territories.length} defined</Chip>}>
            Territories
          </SectionTitle>
          <DataTable
            cols={[
              { key: 'code', label: 'Code' },
              { key: 'name', label: 'Name', wrap: true },
              { key: 'state', label: 'State' },
              { key: 'lga_count', label: 'LGAs', align: 'right' },
              { key: 'monthly_target', label: 'Target', align: 'right' },
              { key: 'holder', label: 'Held by', wrap: true },
              { key: 'status', label: 'Status' },
            ]}
            rows={territories}
            empty="No territories yet. Create one to begin."
            render={(r, c) => {
              if (c.key === 'monthly_target') return money(r.monthly_target || 0);
              if (c.key === 'status') return <Chip tone={tone(r.status)}>{r.status}</Chip>;
              if (c.key === 'holder') {
                return r.holder
                  ? <span>{r.holder}<br /><span style={{ fontSize: 11, color: color.textMuted }}>{r.holder_code}</span></span>
                  : <Chip tone="info">Available</Chip>;
              }
              return r[c.key];
            }}
          />
        </Card>
      )}

      {tab === 'distributors' && (
        <Card>
          <SectionTitle right={<Chip tone="neutral">{distributors.length} registered</Chip>}>
            Distributors
          </SectionTitle>
          <DataTable
            cols={[
              { key: 'distributor_code', label: 'Code' },
              { key: 'legal_name', label: 'Name', wrap: true },
              { key: 'state', label: 'State' },
              { key: 'territory_count', label: 'Territories', align: 'right' },
              { key: 'provisioned', label: 'Linked' },
              { key: 'status', label: 'Status' },
              { key: 'act', label: '', align: 'right' },
            ]}
            rows={distributors}
            empty="No distributors registered yet."
            render={(r, c) => {
              if (c.key === 'status') return <Chip tone={tone(r.status)}>{r.status}</Chip>;
              if (c.key === 'provisioned') {
                return r.customer_id
                  ? <Chip tone="success">Customer + warehouse</Chip>
                  : <Chip tone="neutral">Not yet</Chip>;
              }
              if (c.key === 'act') {
                return (
                  <Btn size="sm" variant="secondary"
                    onClick={() => setModal({ kind: 'dossier', id: r.id })}>Open</Btn>
                );
              }
              return r[c.key] || '—';
            }}
          />
        </Card>
      )}

      {tab === 'applications' && <ApplicationQueue onChanged={load} />}

      {tab === 'compliance' && (
        <>
          <CorrectiveActionQueue onChanged={load} />
          <div style={{ height: space(2.5) }} />
        </>
      )}

      {tab === 'compliance' && expiring && (
        <>
          <Grid min={240}>
            <KpiCard icon="alert" label="Expired" value={expiring.expired.length}
              sub="Documents past their date"
              tone={expiring.expired.length ? 'danger' : 'success'} />
            <KpiCard icon="reports" label="Expiring soon"
              value={expiring.expiring_soon.length} sub="Within 90 days"
              tone={expiring.expiring_soon.length ? 'warning' : 'success'} />
          </Grid>
          <div style={{ height: space(2.5) }} />
          <Card>
            <SectionTitle>Documents and qualifications needing attention</SectionTitle>
            <DataTable
              cols={[
                { key: 'legal_name', label: 'Distributor', wrap: true },
                { key: 'kind', label: 'Kind' },
                { key: 'label', label: 'What' },
                { key: 'expiry_date', label: 'Expires' },
                { key: 'days_left', label: 'Days', align: 'right' },
              ]}
              rows={[...expiring.expired, ...expiring.expiring_soon]}
              empty="Nothing expiring in the next 90 days."
              render={(r, c) => {
                if (c.key === 'days_left') {
                  return (
                    <span style={{ color: r.expired ? color.danger : color.text,
                      fontWeight: r.expired ? 700 : 400 }}>
                      {r.expired ? `${Math.abs(r.days_left)} overdue` : r.days_left}
                    </span>
                  );
                }
                if (c.key === 'legal_name') {
                  return <span>{r.legal_name}<br /><span style={{ fontSize: 11, color: color.textMuted }}>{r.distributor_code}</span></span>;
                }
                return r[c.key];
              }}
            />
          </Card>
        </>
      )}

      {modal && modal.kind === 'distributor' && (
        <NewDistributor states={states} onClose={() => setModal(null)}
          onDone={async (msg) => { setModal(null); flash(msg); await load(); }} />
      )}
      {modal && modal.kind === 'territory' && (
        <NewTerritory states={states} onClose={() => setModal(null)}
          onDone={async (msg) => { setModal(null); flash(msg); await load(); }} />
      )}
      {modal && modal.kind === 'dossier' && (
        <Dossier distributorId={modal.id} territories={territories}
          onClose={() => setModal(null)} onChanged={load} />
      )}
    </div>
  );
}
