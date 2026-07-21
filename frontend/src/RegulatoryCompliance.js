import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { authedFetch } from './utils/api';

// Regulatory Compliance — BONNESANTE MEDICALS / ASTROBSM
// Full-stack pharma GMP / NAFDAC document & quality system.
// Tabs: Dashboard | Documents | New SOP | Deviations & CAPA | Env Monitoring.

const STATUS_COLORS = {
  DRAFT:     '#6b7280',
  IN_REVIEW: '#f59e0b',
  APPROVED:  '#2563eb',
  EFFECTIVE: '#16a34a',
  OBSOLETE:  '#9ca3af',
};

const SEVERITY_COLORS = {
  MINOR:    '#16a34a',
  MAJOR:    '#f59e0b',
  CRITICAL: '#dc2626',
};

const authHeaders = () => {
  const t = localStorage.getItem('token');
  return t ? { Authorization: `Bearer ${t}` } : {};
};

async function api(path, opts = {}) {
  const res = await authedFetch(path, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(opts.headers || {}),
    },
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { const j = await res.json(); msg = j.detail || j.message || msg; } catch {}
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  const ct = res.headers.get('content-type') || '';
  return ct.includes('json') ? res.json() : res.text();
}

function fmt(d) { if (!d) return ''; try { return new Date(d).toLocaleString(); } catch { return d; } }
function fmtDate(d) { if (!d) return ''; try { return new Date(d).toLocaleDateString(); } catch { return d; } }

const Badge = ({ children, color = '#6b7280' }) => (
  <span style={{
    background: color, color: '#fff', padding: '2px 10px', borderRadius: 12,
    fontSize: 11, fontWeight: 600, letterSpacing: 0.3, textTransform: 'uppercase',
  }}>{children}</span>
);

const Card = ({ title, value, hint, accent = '#2563eb' }) => (
  <div style={{
    background: '#fff', borderRadius: 12, padding: 18, minWidth: 180,
    boxShadow: '0 2px 6px rgba(15,23,42,0.06)', borderLeft: `4px solid ${accent}`,
  }}>
    <div style={{ color: '#64748b', fontSize: 12, textTransform: 'uppercase', fontWeight: 600 }}>{title}</div>
    <div style={{ fontSize: 28, fontWeight: 700, color: '#0f172a', margin: '6px 0' }}>{value}</div>
    {hint && <div style={{ color: '#94a3b8', fontSize: 11 }}>{hint}</div>}
  </div>
);

// ─────────────────────────────────────────────────────────────────────────────
// Dashboard tab
// ─────────────────────────────────────────────────────────────────────────────
function DashboardTab() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    try { setData(await api('/api/regulatory/dashboard')); }
    catch (e) { setError(e.message); }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  if (error) return <div style={{ color: '#dc2626' }}>{error}</div>;
  if (!data) return <div>Loading dashboard…</div>;

  const d = data.documents || {};
  return (
    <div>
      <h3 style={{ marginTop: 0 }}>GMP Document Status</h3>
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
        <Card title="Total" value={d.total || 0} accent="#0f172a" />
        <Card title="Draft" value={d.DRAFT || 0} accent={STATUS_COLORS.DRAFT} />
        <Card title="In Review" value={d.IN_REVIEW || 0} accent={STATUS_COLORS.IN_REVIEW} />
        <Card title="Approved" value={d.APPROVED || 0} accent={STATUS_COLORS.APPROVED} />
        <Card title="Effective" value={d.EFFECTIVE || 0} accent={STATUS_COLORS.EFFECTIVE} />
        <Card title="Obsolete" value={d.OBSOLETE || 0} accent={STATUS_COLORS.OBSOLETE} />
      </div>

      <h3 style={{ marginTop: 24 }}>Quality Signals</h3>
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
        <Card title="Open Deviations" value={data.deviations?.open || 0}
              hint={`${data.deviations?.overdue || 0} overdue`} accent="#dc2626" />
        <Card title="Reviews Due (30d)" value={data.reviews_due_30d || 0}
              hint="Effective SOPs due re-review" accent="#f59e0b" />
        <Card title="Env OOS (7d)" value={data.env_oos_7d || 0}
              hint="Out-of-spec environmental readings" accent="#7c3aed" />
      </div>

      <div style={{ marginTop: 18, color: '#94a3b8', fontSize: 12 }}>
        As of {fmt(data.as_of)} · NAFDAC / WHO GMP aligned
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Document detail modal
// ─────────────────────────────────────────────────────────────────────────────
function DocumentModal({ docId, onClose, onChanged }) {
  const [doc, setDoc] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [signerName, setSignerName] = useState('');
  const [signerRole, setSignerRole] = useState('QA Manager');
  const [signMeaning, setSignMeaning] = useState('Approved');

  const load = useCallback(async () => {
    try { setDoc(await api(`/api/regulatory/documents/${docId}`)); }
    catch (e) { setError(e.message); }
  }, [docId]);

  useEffect(() => { load(); }, [load]);

  async function act(action) {
    if (!window.confirm(`Confirm: ${action}?`)) return;
    setBusy(true);
    try {
      await api(`/api/regulatory/documents/${docId}/transition/${action}`, { method: 'POST' });
      await load(); onChanged && onChanged();
    } catch (e) { alert(e.message); }
    finally { setBusy(false); }
  }

  async function sign() {
    if (!signerName.trim()) { alert('Enter signer name'); return; }
    setBusy(true);
    try {
      await api(`/api/regulatory/documents/${docId}/sign`, {
        method: 'POST',
        body: JSON.stringify({ signer_name: signerName, signer_role: signerRole, meaning: signMeaning }),
      });
      setSignerName('');
      await load(); onChanged && onChanged();
    } catch (e) { alert(e.message); }
    finally { setBusy(false); }
  }

  function downloadPdf() {
    const url = `/api/regulatory/documents/${docId}/pdf`;
    window.open(url, '_blank', 'noopener');
  }

  if (error) {
    return (
      <div style={overlay}>
        <div style={modal}>
          <h3>Error</h3>
          <p style={{ color: '#dc2626' }}>{error}</p>
          <button onClick={onClose}>Close</button>
        </div>
      </div>
    );
  }
  if (!doc) {
    return (
      <div style={overlay}>
        <div style={modal}>Loading document…<br /><button onClick={onClose}>Close</button></div>
      </div>
    );
  }

  return (
    <div style={overlay}>
      <div style={{ ...modal, maxWidth: 920, maxHeight: '92vh', overflow: 'auto' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
          <div>
            <div style={{ color: '#64748b', fontSize: 12 }}>{doc.doc_number} · v{doc.version}</div>
            <h2 style={{ margin: '4px 0' }}>{doc.title}</h2>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <Badge color={STATUS_COLORS[doc.status] || '#6b7280'}>{doc.status}</Badge>
              <Badge color="#0f172a">{doc.doc_type}</Badge>
              <Badge color="#1d4ed8">{doc.category}</Badge>
              {doc.effective_date && (
                <span style={{ color: '#64748b', fontSize: 12 }}>
                  Effective: {fmtDate(doc.effective_date)} · Review: {fmtDate(doc.review_date)}
                </span>
              )}
            </div>
          </div>
          <button onClick={onClose} style={btnGhost}>✕ Close</button>
        </div>

        {/* Lifecycle actions */}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '14px 0' }}>
          {doc.status === 'DRAFT' && <button disabled={busy} onClick={() => act('submit-review')} style={btn}>Submit for Review</button>}
          {doc.status === 'IN_REVIEW' && <button disabled={busy} onClick={() => act('approve')} style={btn}>Approve</button>}
          {doc.status === 'IN_REVIEW' && <button disabled={busy} onClick={() => act('reject')} style={btnGhost}>Return to Draft</button>}
          {doc.status === 'APPROVED' && <button disabled={busy} onClick={() => act('effect')} style={btn}>Make Effective</button>}
          {doc.status === 'EFFECTIVE' && <button disabled={busy} onClick={() => act('obsolete')} style={btnDanger}>Obsolete</button>}
          <button onClick={downloadPdf} style={btnSecondary}>⬇ Download PDF</button>
        </div>

        {/* Sections */}
        <div style={{ background: '#f8fafc', padding: 14, borderRadius: 8 }}>
          {(doc.content?.sections || []).map((s, i) => (
            <div key={i} style={{ marginBottom: 10 }}>
              <div style={{ fontWeight: 700, color: '#1d4ed8', fontSize: 13 }}>{s.heading}</div>
              <div style={{ whiteSpace: 'pre-wrap', fontSize: 13, color: '#0f172a' }}>{s.body || <em style={{color:'#94a3b8'}}>—</em>}</div>
            </div>
          ))}
        </div>

        {/* Signatures */}
        <h4 style={{ marginTop: 18 }}>Electronic Signatures</h4>
        {(doc.signatures || []).length === 0 && <div style={{ color: '#94a3b8', fontSize: 13 }}>No signatures yet.</div>}
        {(doc.signatures || []).length > 0 && (
          <table className="reg-tbl" style={tbl}>
            <thead><tr><th>Role</th><th>Name</th><th>Meaning</th><th>Signed At</th><th>Hash</th></tr></thead>
            <tbody>
              {doc.signatures.map(s => (
                <tr key={s.id}>
                  <td>{s.role}</td><td>{s.name}</td><td>{s.meaning}</td>
                  <td>{fmt(s.signed_at)}</td>
                  <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{(s.content_hash || '').slice(0, 12)}…</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {/* Add signature */}
        {doc.status !== 'OBSOLETE' && (
          <div style={{ marginTop: 12, padding: 12, background: '#fff', border: '1px solid #e2e8f0', borderRadius: 8 }}>
            <h4 style={{ marginTop: 0 }}>Add Electronic Signature</h4>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <input placeholder="Signer name" value={signerName} onChange={e => setSignerName(e.target.value)} style={inp} />
              <select value={signerRole} onChange={e => setSignerRole(e.target.value)} style={inp}>
                <option>QA Manager</option><option>QA Officer</option><option>Production Manager</option>
                <option>QC Manager</option><option>Plant Manager</option><option>Regulatory Affairs</option>
              </select>
              <select value={signMeaning} onChange={e => setSignMeaning(e.target.value)} style={inp}>
                <option>Approved</option><option>Reviewed</option><option>Authorized</option><option>Witnessed</option>
              </select>
              <button onClick={sign} disabled={busy} style={btn}>Sign</button>
            </div>
            <div style={{ color: '#94a3b8', fontSize: 11, marginTop: 6 }}>
              I understand that my electronic signature is the legally binding equivalent of my handwritten signature.
            </div>
          </div>
        )}

        {/* Audit trail */}
        <h4 style={{ marginTop: 18 }}>Audit Trail</h4>
        <table className="reg-tbl" style={tbl}>
          <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>From → To</th></tr></thead>
          <tbody>
            {(doc.audit_trail || []).map((t, i) => (
              <tr key={i}>
                <td>{fmt(t.at)}</td>
                <td>{t.actor}</td>
                <td>{t.action}</td>
                <td>{t.from_state || '—'} → {t.to_state || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Documents tab
// ─────────────────────────────────────────────────────────────────────────────
function DocumentsTab() {
  const [docs, setDocs] = useState([]);
  const [status, setStatus] = useState('');
  const [category, setCategory] = useState('');
  const [q, setQ] = useState('');
  const [error, setError] = useState('');
  const [activeId, setActiveId] = useState(null);

  const load = useCallback(async () => {
    setError('');
    try {
      const params = new URLSearchParams();
      if (status) params.set('status', status);
      if (category) params.set('category', category);
      if (q) params.set('q', q);
      setDocs(await api(`/api/regulatory/documents?${params}`));
    } catch (e) { setError(e.message); }
  }, [status, category, q]);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        <input placeholder="Search title or doc-no" value={q} onChange={e => setQ(e.target.value)} style={inp} />
        <select value={status} onChange={e => setStatus(e.target.value)} style={inp}>
          <option value="">All statuses</option>
          {Object.keys(STATUS_COLORS).map(s => <option key={s}>{s}</option>)}
        </select>
        <select value={category} onChange={e => setCategory(e.target.value)} style={inp}>
          <option value="">All categories</option>
          {['QA','PROD','QC','WH','ENG','HVAC','HR','REG'].map(c => <option key={c}>{c}</option>)}
        </select>
        <button onClick={load} style={btn}>Refresh</button>
      </div>

      {error && <div style={{ color: '#dc2626' }}>{error}</div>}

      <table className="reg-tbl" style={tbl}>
        <thead>
          <tr><th>Doc No.</th><th>Type</th><th>Category</th><th>Title</th><th>Version</th><th>Status</th><th>Updated</th><th /></tr>
        </thead>
        <tbody>
          {docs.length === 0 && <tr><td colSpan={8} style={{ textAlign:'center', color:'#94a3b8', padding:24 }}>No documents yet. Create one from the “New SOP” tab.</td></tr>}
          {docs.map(d => (
            <tr key={d.id}>
              <td style={{ fontFamily:'monospace' }}>{d.doc_number}</td>
              <td>{d.doc_type}</td>
              <td>{d.category}</td>
              <td>{d.title}</td>
              <td>v{d.version}</td>
              <td><Badge color={STATUS_COLORS[d.status] || '#6b7280'}>{d.status}</Badge></td>
              <td>{fmt(d.updated_at)}</td>
              <td><button onClick={() => setActiveId(d.id)} style={btnGhost}>Open</button></td>
            </tr>
          ))}
        </tbody>
      </table>

      {activeId && (
        <DocumentModal docId={activeId} onClose={() => setActiveId(null)} onChanged={load} />
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// New SOP tab (wizard)
// ─────────────────────────────────────────────────────────────────────────────
function NewSopTab({ onCreated }) {
  const [tplList, setTplList] = useState([]);
  const [templateKey, setTemplateKey] = useState('');
  const [title, setTitle] = useState('');
  const [category, setCategory] = useState('QA');
  const [author, setAuthor] = useState('');
  const [owner, setOwner] = useState('');
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api('/api/regulatory/templates').then(r => setTplList(r.templates || [])).catch(e => setError(e.message));
  }, []);

  async function generate() {
    if (!templateKey) { alert('Choose a template'); return; }
    setBusy(true);
    try {
      const r = await api('/api/regulatory/sop/generate', {
        method: 'POST',
        body: JSON.stringify({ template_key: templateKey, title }),
      });
      setPreview(r.content);
      if (!title) setTitle(r.title || '');
      const code = templateKey.split('.')[0].toUpperCase();
      if (['QA','PROD','QC','WH','ENG','HVAC','HR','REG'].includes(code)) setCategory(code);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  function updateSection(i, body) {
    const next = { ...preview, sections: preview.sections.map((s, idx) => idx === i ? { ...s, body } : s) };
    setPreview(next);
  }

  async function save() {
    if (!preview || !title.trim()) { alert('Generate a template and set a title first.'); return; }
    setBusy(true);
    try {
      const created = await api('/api/regulatory/documents', {
        method: 'POST',
        body: JSON.stringify({
          title, category, doc_type: 'SOP', author, owner,
          content: { title, sections: preview.sections },
        }),
      });
      alert(`Created ${created.doc_number}`);
      setPreview(null); setTitle(''); setTemplateKey('');
      onCreated && onCreated();
    } catch (e) { alert(e.message); }
    finally { setBusy(false); }
  }

  return (
    <div>
      <h3 style={{ marginTop: 0 }}>Generate a new SOP / Controlled Document</h3>
      {error && <div style={{ color: '#dc2626' }}>{error}</div>}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        <select value={templateKey} onChange={e => setTemplateKey(e.target.value)} style={{ ...inp, minWidth: 280 }}>
          <option value="">Select a template…</option>
          {tplList.map(t => (
            <option key={t.key} value={t.key}>[{t.category_code}] {t.title}</option>
          ))}
        </select>
        <input placeholder="Override title (optional)" value={title} onChange={e => setTitle(e.target.value)} style={{ ...inp, minWidth: 300 }} />
        <select value={category} onChange={e => setCategory(e.target.value)} style={inp}>
          {['QA','PROD','QC','WH','ENG','HVAC','HR','REG'].map(c => <option key={c}>{c}</option>)}
        </select>
        <input placeholder="Author" value={author} onChange={e => setAuthor(e.target.value)} style={inp} />
        <input placeholder="Owner" value={owner} onChange={e => setOwner(e.target.value)} style={inp} />
        <button onClick={generate} disabled={busy} style={btn}>Generate Draft</button>
        {preview && <button onClick={save} disabled={busy} style={btnSecondary}>Save as DRAFT</button>}
      </div>

      {preview && (
        <div style={{ background: '#f8fafc', padding: 14, borderRadius: 8 }}>
          <h4 style={{ marginTop: 0 }}>{title || preview.title}</h4>
          {preview.sections.map((s, i) => (
            <div key={i} style={{ marginBottom: 12 }}>
              <div style={{ fontWeight: 700, color: '#1d4ed8', fontSize: 13 }}>{s.heading}</div>
              <textarea value={s.body || ''} onChange={e => updateSection(i, e.target.value)}
                style={{ width: '100%', minHeight: 80, fontFamily: 'inherit', fontSize: 13, padding: 8, border: '1px solid #cbd5e1', borderRadius: 6 }} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Deviations / CAPA tab
// ─────────────────────────────────────────────────────────────────────────────
function DeviationsTab() {
  const [items, setItems] = useState([]);
  const [error, setError] = useState('');
  const [showNew, setShowNew] = useState(false);
  const [form, setForm] = useState({ title: '', description: '', deviation_type: 'UNPLANNED', severity: 'MINOR', owner: '', due_date: '' });

  const load = useCallback(async () => {
    try { setItems(await api('/api/regulatory/deviations?limit=200')); }
    catch (e) { setError(e.message); }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function create() {
    if (!form.title || !form.description) { alert('Title and description are required'); return; }
    try {
      await api('/api/regulatory/deviations', {
        method: 'POST',
        body: JSON.stringify({ ...form, due_date: form.due_date || null }),
      });
      setShowNew(false); setForm({ title: '', description: '', deviation_type: 'UNPLANNED', severity: 'MINOR', owner: '', due_date: '' });
      load();
    } catch (e) { alert(e.message); }
  }

  async function updateStatus(id, status) {
    if (!window.confirm(`Set status to ${status}?`)) return;
    try { await api(`/api/regulatory/deviations/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }); load(); }
    catch (e) { alert(e.message); }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
        <h3 style={{ margin: 0 }}>Deviations & CAPA</h3>
        <button onClick={() => setShowNew(true)} style={btn}>+ New Deviation</button>
      </div>
      {error && <div style={{ color: '#dc2626' }}>{error}</div>}

      <table className="reg-tbl" style={tbl}>
        <thead><tr><th>Ref</th><th>Title</th><th>Type</th><th>Severity</th><th>Owner</th><th>Status</th><th>Opened</th><th>Due</th><th /></tr></thead>
        <tbody>
          {items.length === 0 && <tr><td colSpan={9} style={{ textAlign: 'center', color: '#94a3b8', padding: 18 }}>No deviations logged.</td></tr>}
          {items.map(d => (
            <tr key={d.id}>
              <td style={{ fontFamily: 'monospace' }}>{d.ref_number}</td>
              <td>{d.title}</td>
              <td>{d.deviation_type}</td>
              <td><Badge color={SEVERITY_COLORS[d.severity] || '#6b7280'}>{d.severity}</Badge></td>
              <td>{d.owner || '—'}</td>
              <td>{d.status}</td>
              <td>{fmt(d.opened_at)}</td>
              <td>{d.due_date ? fmtDate(d.due_date) : '—'}</td>
              <td>
                {d.status === 'OPEN' && <button onClick={() => updateStatus(d.id, 'INVESTIGATING')} style={btnGhost}>Investigate</button>}
                {d.status === 'INVESTIGATING' && <button onClick={() => updateStatus(d.id, 'CAPA_PENDING')} style={btnGhost}>To CAPA</button>}
                {d.status === 'CAPA_PENDING' && <button onClick={() => updateStatus(d.id, 'CLOSED')} style={btn}>Close</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {showNew && (
        <div style={overlay}>
          <div style={modal}>
            <h3>New Deviation</h3>
            <input placeholder="Title" value={form.title} onChange={e => setForm({ ...form, title: e.target.value })} style={{ ...inp, width: '100%', marginBottom: 8 }} />
            <textarea placeholder="Description / discovery details" value={form.description} onChange={e => setForm({ ...form, description: e.target.value })}
                      style={{ width: '100%', minHeight: 100, padding: 8, border: '1px solid #cbd5e1', borderRadius: 6, marginBottom: 8 }} />
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
              <select value={form.deviation_type} onChange={e => setForm({ ...form, deviation_type: e.target.value })} style={inp}>
                <option>UNPLANNED</option><option>PLANNED</option>
              </select>
              <select value={form.severity} onChange={e => setForm({ ...form, severity: e.target.value })} style={inp}>
                <option>MINOR</option><option>MAJOR</option><option>CRITICAL</option>
              </select>
              <input placeholder="Owner" value={form.owner} onChange={e => setForm({ ...form, owner: e.target.value })} style={inp} />
              <input type="date" value={form.due_date} onChange={e => setForm({ ...form, due_date: e.target.value })} style={inp} />
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button onClick={() => setShowNew(false)} style={btnGhost}>Cancel</button>
              <button onClick={create} style={btn}>Create</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Environmental monitoring tab
// ─────────────────────────────────────────────────────────────────────────────
function EnvMonitoringTab() {
  const [logs, setLogs] = useState([]);
  const [error, setError] = useState('');
  const [form, setForm] = useState({ area: '', param_type: 'TEMP', value: '', unit: '°C', lower_limit: '', upper_limit: '', recorded_by: '', notes: '' });
  const [days, setDays] = useState(7);

  const load = useCallback(async () => {
    try { setLogs(await api(`/api/regulatory/env-logs?days=${days}&limit=300`)); }
    catch (e) { setError(e.message); }
  }, [days]);

  useEffect(() => { load(); }, [load]);

  async function submit() {
    if (!form.area || form.value === '') { alert('Area and value required'); return; }
    try {
      await api('/api/regulatory/env-logs', {
        method: 'POST',
        body: JSON.stringify({
          ...form,
          value: parseFloat(form.value),
          lower_limit: form.lower_limit === '' ? null : parseFloat(form.lower_limit),
          upper_limit: form.upper_limit === '' ? null : parseFloat(form.upper_limit),
        }),
      });
      setForm({ ...form, value: '', notes: '' });
      load();
    } catch (e) { alert(e.message); }
  }

  return (
    <div>
      <h3 style={{ marginTop: 0 }}>Environmental Monitoring Log</h3>
      <div style={{ background: '#f8fafc', padding: 12, borderRadius: 8, marginBottom: 12 }}>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <input placeholder="Area (e.g. Gel Suite)" value={form.area} onChange={e => setForm({ ...form, area: e.target.value })} style={inp} />
          <select value={form.param_type} onChange={e => setForm({ ...form, param_type: e.target.value })} style={inp}>
            <option value="TEMP">Temperature</option>
            <option value="HUMIDITY">Humidity</option>
            <option value="DIFF_PRESSURE">Differential Pressure</option>
            <option value="PARTICLE_0.5">Particles ≥0.5µm</option>
            <option value="PARTICLE_5.0">Particles ≥5.0µm</option>
            <option value="VIABLE_AIR">Viable Air (CFU)</option>
          </select>
          <input placeholder="Value" type="number" step="0.01" value={form.value} onChange={e => setForm({ ...form, value: e.target.value })} style={{ ...inp, width: 90 }} />
          <input placeholder="Unit" value={form.unit} onChange={e => setForm({ ...form, unit: e.target.value })} style={{ ...inp, width: 80 }} />
          <input placeholder="Lower limit" type="number" step="0.01" value={form.lower_limit} onChange={e => setForm({ ...form, lower_limit: e.target.value })} style={{ ...inp, width: 110 }} />
          <input placeholder="Upper limit" type="number" step="0.01" value={form.upper_limit} onChange={e => setForm({ ...form, upper_limit: e.target.value })} style={{ ...inp, width: 110 }} />
          <input placeholder="Recorded by" value={form.recorded_by} onChange={e => setForm({ ...form, recorded_by: e.target.value })} style={inp} />
          <input placeholder="Notes" value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} style={{ ...inp, flex: 1, minWidth: 160 }} />
          <button onClick={submit} style={btn}>Log Reading</button>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
        <span style={{ color: '#64748b', fontSize: 13 }}>Window:</span>
        {[1, 7, 30, 90].map(d => (
          <button key={d} onClick={() => setDays(d)} style={d === days ? btn : btnGhost}>{d}d</button>
        ))}
      </div>

      {error && <div style={{ color: '#dc2626' }}>{error}</div>}
      <table className="reg-tbl" style={tbl}>
        <thead><tr><th>When</th><th>Area</th><th>Param</th><th>Value</th><th>Limits</th><th>By</th><th>Notes</th></tr></thead>
        <tbody>
          {logs.length === 0 && <tr><td colSpan={7} style={{ textAlign:'center', color:'#94a3b8', padding:18 }}>No readings in this window.</td></tr>}
          {logs.map(l => (
            <tr key={l.id} style={l.oos ? { background: '#fef2f2' } : {}}>
              <td>{fmt(l.recorded_at)}</td>
              <td>{l.area}</td>
              <td>{l.param_type}</td>
              <td style={{ fontWeight: 600, color: l.oos ? '#dc2626' : '#0f172a' }}>
                {l.value} {l.unit || ''} {l.oos && <Badge color="#dc2626">OOS</Badge>}
              </td>
              <td style={{ fontSize: 12, color: '#64748b' }}>
                {l.lower_limit ?? '–'} … {l.upper_limit ?? '–'}
              </td>
              <td>{l.recorded_by || '—'}</td>
              <td>{l.notes || ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Root component
// ─────────────────────────────────────────────────────────────────────────────
export default function RegulatoryCompliance() {
  const [tab, setTab] = useState('dashboard');
  const tabs = useMemo(() => ([
    { k: 'dashboard',   label: 'Dashboard' },
    { k: 'documents',   label: 'Documents' },
    { k: 'new-sop',     label: 'New SOP' },
    { k: 'deviations',  label: 'Deviations & CAPA' },
    { k: 'env',         label: 'Env Monitoring' },
  ]), []);

  return (
    <div style={{ padding: 18, background: '#f1f5f9', minHeight: '100vh' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div>
          <h2 style={{ margin: 0, color: '#0f172a' }}>Regulatory Compliance</h2>
          <div style={{ color: '#64748b', fontSize: 12 }}>
            BONNESANTE MEDICALS · ASTROBSM · NAFDAC / WHO GMP aligned
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 6, borderBottom: '1px solid #cbd5e1', marginBottom: 16 }}>
        {tabs.map(t => (
          <button key={t.k} onClick={() => setTab(t.k)}
            style={{
              padding: '10px 14px', border: 'none', cursor: 'pointer',
              background: tab === t.k ? '#fff' : 'transparent',
              borderBottom: tab === t.k ? '3px solid #1d4ed8' : '3px solid transparent',
              fontWeight: tab === t.k ? 700 : 500,
              color: tab === t.k ? '#1d4ed8' : '#475569',
            }}>{t.label}</button>
        ))}
      </div>

      <div style={{ background: '#fff', padding: 16, borderRadius: 10, boxShadow: '0 1px 3px rgba(15,23,42,0.06)' }}>
        {tab === 'dashboard'  && <DashboardTab />}
        {tab === 'documents'  && <DocumentsTab />}
        {tab === 'new-sop'    && <NewSopTab onCreated={() => setTab('documents')} />}
        {tab === 'deviations' && <DeviationsTab />}
        {tab === 'env'        && <EnvMonitoringTab />}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Inline styles
// ─────────────────────────────────────────────────────────────────────────────
const inp = { padding: '8px 10px', border: '1px solid #cbd5e1', borderRadius: 6, fontSize: 13, background: '#fff' };
const btn = { padding: '8px 14px', background: '#1d4ed8', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 600 };
const btnSecondary = { padding: '8px 14px', background: '#0f766e', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 600 };
const btnDanger = { padding: '8px 14px', background: '#dc2626', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 600 };
const btnGhost = { padding: '8px 14px', background: '#fff', color: '#1d4ed8', border: '1px solid #cbd5e1', borderRadius: 6, cursor: 'pointer', fontWeight: 600 };
const tbl = { width: '100%', borderCollapse: 'collapse', fontSize: 13, background: '#fff', marginTop: 8 };
const overlay = { position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 };
const modal = { background: '#fff', padding: 22, borderRadius: 12, width: 'min(640px, 92vw)', boxShadow: '0 12px 40px rgba(0,0,0,0.2)' };

// Scoped table styling (does not bleed into other modules)
if (typeof document !== 'undefined' && !document.getElementById('reg-compliance-style')) {
  const s = document.createElement('style');
  s.id = 'reg-compliance-style';
  s.innerHTML = `
    .reg-tbl { border-collapse: collapse; width: 100%; }
    .reg-tbl th, .reg-tbl td { padding: 8px 10px; border-bottom: 1px solid #e2e8f0; text-align: left; vertical-align: top; }
    .reg-tbl thead th { background: #f1f5f9; color: #475569; font-size: 11px; text-transform: uppercase; letter-spacing: 0.4px; font-weight: 600; }
  `;
  document.head.appendChild(s);
}
