// The field portal. A distributor's marketers, on their own phones.
//
// Rendered by App.js before the auth gate, for any path under /field.
//
// WHAT THIS IS NOT
// ----------------
// It is not the ERP with some menus hidden. The people using it work for
// Penacea and Tripleluminance, not for Bonnesante Medicals, and they hold a
// token signed with a different key that no other part of this application
// accepts. There is no navigation from here into stock, payroll, production
// or anybody else's customers, because there is nothing to navigate to.
//
// EVERYTHING IS SCOPED BY THE TOKEN
// ---------------------------------
// No screen sends a distributor id, because no endpoint takes one. The server
// reads the tenant from the signed session. A marketer cannot ask for another
// distributor's prices; the question has no parameter.
//
// BUILT FOR A PHONE IN A MARKET
// -----------------------------
// Single column, large targets, and every figure it shows comes from one
// request. Connections here are mobile and often poor.

import React, { useCallback, useEffect, useRef, useState } from 'react';

const BRAND = '#0B1F4A';
const ACCENT = '#2D6CDF';
const OK = '#16A34A';
const WARN = '#B45309';
const DANGER = '#DC2626';
const LINE = '#E5E7EB';
const MUTED = '#64748B';

const TOKEN_KEY = 'field_token';

const wrap = {
  maxWidth: 560, margin: '0 auto', padding: '14px',
  fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
  color: '#0F172A', boxSizing: 'border-box', paddingBottom: 84,
};
const card = {
  background: '#fff', border: `1px solid ${LINE}`, borderRadius: 12,
  padding: 16, marginBottom: 12,
};
const btn = (variant = 'primary', disabled = false) => ({
  padding: '13px 18px', borderRadius: 10, fontSize: 15, fontWeight: 600,
  border: variant === 'ghost' ? `1px solid ${LINE}` : '1px solid transparent',
  background: variant === 'ghost' ? '#fff'
    : variant === 'danger' ? DANGER : variant === 'accent' ? ACCENT : BRAND,
  color: variant === 'ghost' ? '#334155' : '#fff',
  cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.5 : 1,
  fontFamily: 'inherit', minHeight: 48, width: '100%',
});
const input = {
  width: '100%', padding: '12px', borderRadius: 9, fontSize: 16,
  border: `1px solid ${LINE}`, fontFamily: 'inherit', boxSizing: 'border-box',
  color: '#0F172A', background: '#fff', minHeight: 48,
};
const label = {
  display: 'block', fontSize: 12.5, fontWeight: 600, color: '#334155',
  marginBottom: 6,
};

function Notice({ tone = 'info', title, children }) {
  const c = {
    info: ['#EFF4FE', ACCENT, '#1E3A5F'],
    warning: ['#FFFBEB', WARN, '#78350F'],
    danger: ['#FEF2F2', DANGER, '#7F1D1D'],
    success: ['#ECFDF5', OK, '#065F46'],
  }[tone];
  return (
    <div style={{
      background: c[0], borderLeft: `4px solid ${c[1]}`, borderRadius: 8,
      padding: '12px 14px', marginBottom: 12, color: c[2], fontSize: 14,
      lineHeight: 1.6,
    }}>
      {title && <div style={{ fontWeight: 700, marginBottom: 3 }}>{title}</div>}
      {children}
    </div>
  );
}

const naira = (v) => `NGN ${Number(v || 0).toLocaleString('en-NG',
  { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

async function api(path, { token, ...options } = {}) {
  const res = await fetch(`/api/field${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...options,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(body.detail || 'Something went wrong.');
    err.status = res.status;
    throw err;
  }
  return body;
}

// ---------------------------------------------------------------------------
// Signing in, and joining
// ---------------------------------------------------------------------------

function SignIn({ onSignedIn }) {
  const [phone, setPhone] = useState('');
  const [password, setPassword] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true); setErr('');
    try {
      onSignedIn(await api('/sign-in', {
        method: 'POST', body: JSON.stringify({ phone, password }),
      }));
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div style={wrap}>
      <div style={{ textAlign: 'center', padding: '30px 0 12px' }}>
        <img src="/company-logo.png?v=20260118" alt="Bonnesante Medicals"
          onError={(e) => { e.target.style.display = 'none'; }}
          style={{ height: 54, width: 'auto', objectFit: 'contain' }} />
        <div style={{ fontSize: 18, fontWeight: 800, color: BRAND, marginTop: 8 }}>
          Field Portal
        </div>
        <div style={{ fontSize: 13, color: MUTED, marginTop: 3 }}>
          For distributor marketers
        </div>
      </div>

      {err && <Notice tone="danger" title="Could not sign in">{err}</Notice>}

      <div style={card}>
        <label htmlFor="f-phone" style={label}>Phone number</label>
        <input id="f-phone" style={input} type="tel" value={phone}
          autoComplete="username"
          onChange={(e) => setPhone(e.target.value)} />

        <div style={{ marginTop: 12 }}>
          <label htmlFor="f-pass" style={label}>Password</label>
          <input id="f-pass" style={input} type="password" value={password}
            autoComplete="current-password"
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && phone && password) submit(); }} />
        </div>

        <div style={{ marginTop: 16 }}>
          <button style={btn('primary', busy || !phone || !password)}
            disabled={busy || !phone || !password} onClick={submit}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </div>
      </div>

      <div style={{ textAlign: 'center', fontSize: 12, color: MUTED,
        lineHeight: 1.6 }}>
        No account? Your distributor sends you a joining link.
      </div>
    </div>
  );
}

function Join({ token, onJoined }) {
  const [invite, setInvite] = useState(null);
  const [form, setForm] = useState({ full_name: '', phone: '', password: '' });
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api(`/join/${token}`).then(setInvite).catch((e) => setErr(e.message));
  }, [token]);

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const submit = async () => {
    setBusy(true); setErr('');
    try {
      onJoined(await api(`/join/${token}`, {
        method: 'POST', body: JSON.stringify(form),
      }));
    } catch (e) { setErr(e.message); }
    finally { setBusy(false); }
  };

  if (err && !invite) {
    return (
      <div style={wrap}>
        <div style={{ ...card, marginTop: 40 }}>
          <Notice tone="danger" title="This link cannot be used">{err}</Notice>
          <div style={{ fontSize: 13, color: MUTED, lineHeight: 1.7 }}>
            Joining links are issued for a period and can be withdrawn. Ask
            your distributor for a current one.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={wrap}>
      <div style={{ textAlign: 'center', padding: '26px 0 10px' }}>
        <img src="/company-logo.png?v=20260118" alt="Bonnesante Medicals"
          onError={(e) => { e.target.style.display = 'none'; }}
          style={{ height: 50, width: 'auto', objectFit: 'contain' }} />
      </div>

      <div style={card}>
        <div style={{ fontSize: 12, color: MUTED, textTransform: 'uppercase',
          letterSpacing: '0.05em', fontWeight: 600 }}>Joining as a marketer for</div>
        <div style={{ fontSize: 18, fontWeight: 800, color: BRAND, marginTop: 4 }}>
          {invite ? invite.distributor : '…'}
        </div>
        {invite && (
          <div style={{ fontSize: 13, color: MUTED, marginTop: 8, lineHeight: 1.6 }}>
            {invite.note}
          </div>
        )}
      </div>

      {err && <Notice tone="danger" title="Could not create the account">{err}</Notice>}

      <div style={card}>
        <label htmlFor="j-name" style={label}>Your full name</label>
        <input id="j-name" style={input} value={form.full_name}
          onChange={set('full_name')} />

        <div style={{ marginTop: 12 }}>
          <label htmlFor="j-phone" style={label}>
            Phone number
            <span style={{ fontWeight: 400, color: MUTED }}> — you will sign in with this</span>
          </label>
          <input id="j-phone" style={input} type="tel" value={form.phone}
            onChange={set('phone')} />
        </div>

        <div style={{ marginTop: 12 }}>
          <label htmlFor="j-pass" style={label}>Choose a password</label>
          <input id="j-pass" style={input} type="password" value={form.password}
            autoComplete="new-password" onChange={set('password')} />
          <div style={{ fontSize: 11.5, color: MUTED, marginTop: 4 }}>
            At least 8 characters.
          </div>
        </div>

        <div style={{ marginTop: 16 }}>
          <button style={btn('primary', busy
            || form.full_name.trim().length < 3 || !form.phone
            || form.password.length < 8)}
            disabled={busy || form.full_name.trim().length < 3 || !form.phone
              || form.password.length < 8}
            onClick={submit}>
            {busy ? 'Creating…' : 'Create my account'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Consent. Asked once, plainly, and reversible.
// ---------------------------------------------------------------------------

function Consent({ token, text, onDone }) {
  const [busy, setBusy] = useState(false);

  const answer = async (agreed) => {
    setBusy(true);
    try {
      await api('/consent', {
        token, method: 'POST', body: JSON.stringify({ agreed }),
      });
      onDone(agreed);
    } finally { setBusy(false); }
  };

  return (
    <div style={wrap}>
      <div style={{ ...card, marginTop: 30 }}>
        <h2 style={{ fontSize: 17, fontWeight: 800, color: BRAND, margin: '0 0 10px' }}>
          Recording where you are
        </h2>
        <div style={{ fontSize: 14, lineHeight: 1.75, color: '#334155' }}>
          {text}
        </div>

        <div style={{ marginTop: 18, display: 'grid', gap: 10 }}>
          <button style={btn('primary', busy)} disabled={busy}
            onClick={() => answer(true)}>I agree</button>
          <button style={btn('ghost', busy)} disabled={busy}
            onClick={() => answer(false)}>Not now</button>
        </div>

        <div style={{ fontSize: 12, color: MUTED, marginTop: 14, lineHeight: 1.6 }}>
          You can use the portal either way. Saying no means no location is
          recorded; visits you log will simply not carry a position. You can
          change your mind at any time in Profile.
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The portal
// ---------------------------------------------------------------------------

function Portal({ token, profile, onSignOut, onProfile }) {
  const [tab, setTab] = useState('today');
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');

  const call = useCallback((path, options) =>
    api(path, { token, ...(options || {}) }), [token]);

  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(''), 5000); };

  return (
    <div style={{ ...wrap }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10,
        padding: '14px 0 10px' }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 800, color: BRAND,
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {profile.name}
          </div>
          <div style={{ fontSize: 12, color: MUTED, overflow: 'hidden',
            textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {profile.distributor}
          </div>
        </div>
        <button onClick={onSignOut} style={{
          marginLeft: 'auto', padding: '7px 13px', borderRadius: 8,
          border: `1px solid ${LINE}`, background: '#fff', color: '#334155',
          fontSize: 12.5, fontWeight: 600, cursor: 'pointer', flexShrink: 0,
        }}>Sign out</button>
      </div>

      {err && <Notice tone="danger" title="Something went wrong">{err}</Notice>}
      {msg && <Notice tone="success">{msg}</Notice>}

      {tab === 'today' && <Today call={call} onError={setErr} />}
      {tab === 'prices' && <Prices call={call} onError={setErr} />}
      {tab === 'customers' && (
        <Customers call={call} onError={setErr} onSaved={flash} />
      )}
      {tab === 'visits' && <Visits call={call} onError={setErr} onSaved={flash} />}
      {tab === 'profile' && (
        <Profile call={call} profile={profile} onError={setErr}
          onChanged={onProfile} />
      )}

      <nav style={{
        position: 'fixed', left: 0, right: 0, bottom: 0, background: '#fff',
        borderTop: `1px solid ${LINE}`, display: 'flex',
        paddingBottom: 'env(safe-area-inset-bottom)', zIndex: 10,
      }}>
        {[['today', 'Today'], ['prices', 'Prices'], ['customers', 'Customers'],
          ['visits', 'Visits'], ['profile', 'Profile']].map(([key, text]) => (
          <button key={key} onClick={() => { setTab(key); setErr(''); }}
            aria-current={tab === key ? 'page' : undefined}
            style={{
              flex: 1, padding: '11px 2px', border: 'none', background: 'none',
              cursor: 'pointer', fontFamily: 'inherit', fontSize: 11.5,
              fontWeight: tab === key ? 700 : 500,
              color: tab === key ? BRAND : MUTED,
              borderTop: `3px solid ${tab === key ? BRAND : 'transparent'}`,
              minHeight: 52,
            }}>{text}</button>
        ))}
      </nav>
    </div>
  );
}

function Today({ call, onError }) {
  const [data, setData] = useState(null);

  useEffect(() => {
    call('/performance?days=30').then(setData).catch((e) => onError(e.message));
  }, [call, onError]);

  if (!data) return <div style={{ padding: 30, textAlign: 'center', color: MUTED }}>Loading…</div>;

  const tiles = [
    ['Visits', data.visits],
    ['With an order', data.visits_with_order],
    ['Order value', naira(data.order_value)],
    ['Customers added', data.customers_registered],
    ['Days active', data.days_active],
    ['Follow-ups due', data.follow_ups_due],
  ];

  return (
    <>
      <div style={{ ...card }}>
        <div style={{ fontSize: 12.5, fontWeight: 700, color: MUTED,
          textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 12 }}>
          Your last 30 days
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          {tiles.map(([name, value]) => (
            <div key={name} style={{ background: '#F8FAFC', borderRadius: 9,
              padding: '12px 13px' }}>
              <div style={{ fontSize: 19, fontWeight: 800, color: BRAND }}>
                {value}
              </div>
              <div style={{ fontSize: 11.5, color: MUTED, marginTop: 2 }}>{name}</div>
            </div>
          ))}
        </div>
        <div style={{ fontSize: 11.5, color: MUTED, marginTop: 12, lineHeight: 1.6 }}>
          {data.note}
        </div>
      </div>
    </>
  );
}

function Prices({ call, onError }) {
  const [data, setData] = useState(null);
  const [term, setTerm] = useState('');

  useEffect(() => {
    call('/catalogue').then(setData).catch((e) => onError(e.message));
  }, [call, onError]);

  if (!data) return <div style={{ padding: 30, textAlign: 'center', color: MUTED }}>Loading…</div>;

  const shown = term
    ? data.items.filter((i) => i.name.toLowerCase().includes(term.toLowerCase()))
    : data.items;

  return (
    <>
      <input style={{ ...input, marginBottom: 12 }} value={term}
        placeholder="Search products…" onChange={(e) => setTerm(e.target.value)} />

      {data.items.length === 0 ? (
        <Notice tone="warning" title="No prices yet">
          Your distributor has not set any prices. Until they do, there is
          nothing to quote from here.
        </Notice>
      ) : (
        <>
          {shown.map((item) => (
            <div key={`${item.product_id}-${item.unit}`} style={{
              ...card, display: 'flex', alignItems: 'center', gap: 12,
              marginBottom: 8, padding: 14,
            }}>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 14, fontWeight: 600 }}>{item.name}</div>
                <div style={{ fontSize: 11.5, color: MUTED, marginTop: 2 }}>
                  per {item.unit}
                </div>
              </div>
              <div style={{ fontSize: 15, fontWeight: 800, color: BRAND,
                whiteSpace: 'nowrap' }}>
                {naira(item.price)}
              </div>
            </div>
          ))}
          <div style={{ fontSize: 11.5, color: MUTED, padding: '4px 4px 0',
            lineHeight: 1.6 }}>
            {data.note}
          </div>
        </>
      )}
    </>
  );
}

function Customers({ call, onError, onSaved }) {
  const [list, setList] = useState(null);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ name: '', phone: '', address: '' });
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    call('/customers').then((b) => setList(b.customers || []))
      .catch((e) => onError(e.message));
  }, [call, onError]);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setBusy(true);
    try {
      await call('/customers', { method: 'POST', body: JSON.stringify(form) });
      setForm({ name: '', phone: '', address: '' });
      setAdding(false);
      onSaved('Customer registered.');
      load();
    } catch (e) { onError(e.message); }
    finally { setBusy(false); }
  };

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <>
      {!adding ? (
        <button style={{ ...btn('accent'), marginBottom: 12 }}
          onClick={() => setAdding(true)}>Register a customer</button>
      ) : (
        <div style={card}>
          <label style={label}>Business name</label>
          <input style={input} value={form.name} onChange={set('name')} />
          <div style={{ marginTop: 12 }}>
            <label style={label}>Phone</label>
            <input style={input} type="tel" value={form.phone} onChange={set('phone')} />
          </div>
          <div style={{ marginTop: 12 }}>
            <label style={label}>Address</label>
            <textarea style={{ ...input, minHeight: 70, resize: 'vertical' }}
              value={form.address} onChange={set('address')} />
          </div>
          <div style={{ marginTop: 14, display: 'grid', gap: 8 }}>
            <button style={btn('primary', busy || form.name.trim().length < 2)}
              disabled={busy || form.name.trim().length < 2} onClick={save}>
              {busy ? 'Saving…' : 'Register customer'}
            </button>
            <button style={btn('ghost')} onClick={() => setAdding(false)}>Cancel</button>
          </div>
          <div style={{ fontSize: 11.5, color: MUTED, marginTop: 10, lineHeight: 1.6 }}>
            This adds them to the Bonnesante Medicals customer records, noted
            as introduced by you.
          </div>
        </div>
      )}

      {list === null ? (
        <div style={{ padding: 20, textAlign: 'center', color: MUTED }}>Loading…</div>
      ) : list.length === 0 ? (
        <Notice tone="info">You have not registered any customers yet.</Notice>
      ) : list.map((c) => (
        <div key={c.id} style={{ ...card, marginBottom: 8, padding: 14 }}>
          <div style={{ fontSize: 14, fontWeight: 600 }}>{c.name}</div>
          <div style={{ fontSize: 12, color: MUTED, marginTop: 3 }}>
            {c.customer_code}{c.phone ? ` · ${c.phone}` : ''}
          </div>
        </div>
      ))}
    </>
  );
}

function Visits({ call, onError, onSaved }) {
  const [list, setList] = useState(null);
  const [customers, setCustomers] = useState([]);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [position, setPosition] = useState(null);
  const [form, setForm] = useState({
    place_name: '', purpose: 'VISIT', customer_id: '', outcome: '',
    products_discussed: '', order_value: '', follow_up_on: '',
  });

  const load = useCallback(() => {
    call('/visits?days=30').then((b) => setList(b.visits || []))
      .catch((e) => onError(e.message));
    call('/customers').then((b) => setCustomers(b.customers || [])).catch(() => {});
  }, [call, onError]);

  useEffect(() => { load(); }, [load]);

  // Taken when the form opens, so the position belongs to the moment of the
  // visit rather than whenever the marketer got round to saving it.
  const takePosition = () => {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      (p) => setPosition({
        latitude: p.coords.latitude, longitude: p.coords.longitude,
        accuracy_m: p.coords.accuracy,
      }),
      () => setPosition(null),
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 });
  };

  const save = async () => {
    setBusy(true);
    try {
      const body = { ...form, ...(position || {}) };
      if (!body.customer_id) delete body.customer_id;
      if (!body.order_value) delete body.order_value;
      if (!body.follow_up_on) delete body.follow_up_on;
      await call('/visits', { method: 'POST', body: JSON.stringify(body) });
      setForm({ place_name: '', purpose: 'VISIT', customer_id: '', outcome: '',
        products_discussed: '', order_value: '', follow_up_on: '' });
      setPosition(null);
      setAdding(false);
      onSaved('Visit logged.');
      load();
    } catch (e) { onError(e.message); }
    finally { setBusy(false); }
  };

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <>
      {!adding ? (
        <button style={{ ...btn('accent'), marginBottom: 12 }}
          onClick={() => { setAdding(true); takePosition(); }}>Log a visit</button>
      ) : (
        <div style={card}>
          <label style={label}>Where were you?</label>
          <input style={input} value={form.place_name} onChange={set('place_name')}
            placeholder="e.g. Ogui Road Pharmacy" />

          <div style={{ marginTop: 12 }}>
            <label style={label}>What was it?</label>
            <select style={input} value={form.purpose} onChange={set('purpose')}>
              {[['VISIT', 'Visit'], ['CALL', 'Phone call'],
                ['DELIVERY', 'Delivery'], ['COLLECTION', 'Collection'],
                ['PROSPECTING', 'Prospecting'], ['OTHER', 'Other']]
                .map(([v, t]) => <option key={v} value={v}>{t}</option>)}
            </select>
          </div>

          {customers.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <label style={label}>Customer (optional)</label>
              <select style={input} value={form.customer_id}
                onChange={set('customer_id')}>
                <option value="">Not one of mine</option>
                {customers.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
          )}

          <div style={{ marginTop: 12 }}>
            <label style={label}>What happened?</label>
            <textarea style={{ ...input, minHeight: 70, resize: 'vertical' }}
              value={form.outcome} onChange={set('outcome')} />
          </div>

          <div style={{ display: 'flex', gap: 10, marginTop: 12 }}>
            <div style={{ flex: 1 }}>
              <label style={label}>Order value</label>
              <input style={input} type="number" min="0" value={form.order_value}
                onChange={set('order_value')} />
            </div>
            <div style={{ flex: 1 }}>
              <label style={label}>Follow up on</label>
              <input style={input} type="date" value={form.follow_up_on}
                onChange={set('follow_up_on')} />
            </div>
          </div>

          <div style={{ fontSize: 11.5, color: position ? OK : MUTED,
            marginTop: 10 }}>
            {position ? 'Position captured for this visit.'
              : 'No position captured — the visit will still be saved.'}
          </div>

          <div style={{ marginTop: 14, display: 'grid', gap: 8 }}>
            <button style={btn('primary', busy || form.place_name.trim().length < 2)}
              disabled={busy || form.place_name.trim().length < 2} onClick={save}>
              {busy ? 'Saving…' : 'Save visit'}
            </button>
            <button style={btn('ghost')} onClick={() => setAdding(false)}>Cancel</button>
          </div>
        </div>
      )}

      {list === null ? (
        <div style={{ padding: 20, textAlign: 'center', color: MUTED }}>Loading…</div>
      ) : list.length === 0 ? (
        <Notice tone="info">No visits logged in the last 30 days.</Notice>
      ) : list.map((v) => (
        <div key={v.id} style={{ ...card, marginBottom: 8, padding: 14 }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
            <div style={{ fontSize: 14, fontWeight: 600 }}>{v.place_name}</div>
            <div style={{ fontSize: 11, color: MUTED, marginLeft: 'auto',
              whiteSpace: 'nowrap' }}>
              {new Date(v.started_at).toLocaleDateString()}
            </div>
          </div>
          <div style={{ fontSize: 12, color: MUTED, marginTop: 3 }}>
            {v.purpose}{v.customer_name ? ` · ${v.customer_name}` : ''}
            {v.order_value ? ` · ${naira(v.order_value)}` : ''}
          </div>
          {v.outcome && (
            <div style={{ fontSize: 13, color: '#334155', marginTop: 6,
              lineHeight: 1.6 }}>{v.outcome}</div>
          )}
        </div>
      ))}
    </>
  );
}

function Profile({ call, profile, onError, onChanged }) {
  const [busy, setBusy] = useState(false);

  const toggle = async (agreed) => {
    setBusy(true);
    try {
      await call('/consent', { method: 'POST', body: JSON.stringify({ agreed }) });
      onChanged({ ...profile, consented: agreed });
    } catch (e) { onError(e.message); }
    finally { setBusy(false); }
  };

  return (
    <>
      <div style={card}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>{profile.name}</div>
        <div style={{ fontSize: 13, color: MUTED, marginTop: 3 }}>
          Marketer for {profile.distributor}
        </div>
      </div>

      <div style={card}>
        <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 8 }}>
          Location recording
        </div>
        <div style={{ fontSize: 13, color: '#334155', lineHeight: 1.7 }}>
          {profile.consented
            ? 'Your location is being recorded while you work. It is kept for '
              + `${profile.location_retention_days} days and then deleted.`
            : 'Your location is not being recorded.'}
        </div>
        <div style={{ marginTop: 14 }}>
          <button style={btn(profile.consented ? 'ghost' : 'primary', busy)}
            disabled={busy} onClick={() => toggle(!profile.consented)}>
            {profile.consented ? 'Stop recording my location'
              : 'Allow location recording'}
          </button>
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------

export default function FieldPortal({ path }) {
  const [token, setToken] = useState(() => {
    try { return localStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; }
  });
  const [profile, setProfile] = useState(null);
  const [checking, setChecking] = useState(true);
  const pingRef = useRef(null);

  const joinToken = path.startsWith('/field/join/')
    ? path.slice('/field/join/'.length) : '';

  const store = useCallback((value) => {
    try {
      if (value) localStorage.setItem(TOKEN_KEY, value);
      else localStorage.removeItem(TOKEN_KEY);
    } catch { /* private mode: the session simply will not survive a reload */ }
    setToken(value || '');
  }, []);

  useEffect(() => {
    if (!token) { setChecking(false); return undefined; }
    let alive = true;
    api('/me', { token })
      .then((p) => { if (alive) { setProfile(p); setChecking(false); } })
      .catch(() => { if (alive) { store(''); setProfile(null); setChecking(false); } });
    return () => { alive = false; };
  }, [token, store]);

  // Position reporting, only while consented. The server throttles as well;
  // this interval exists so a phone in a pocket is not asked constantly.
  useEffect(() => {
    if (!token || !profile || !profile.consented) return undefined;
    if (!navigator.geolocation) return undefined;

    const send = () => {
      navigator.geolocation.getCurrentPosition(
        (p) => {
          api('/location', {
            token, method: 'POST',
            body: JSON.stringify({
              latitude: p.coords.latitude, longitude: p.coords.longitude,
              accuracy_m: p.coords.accuracy,
              speed_mps: p.coords.speed === null ? undefined : p.coords.speed,
            }),
          }).catch(() => { /* a dropped ping is not worth interrupting anyone */ });
        },
        () => { /* refused or unavailable: nothing to send */ },
        { enableHighAccuracy: false, timeout: 15000, maximumAge: 60000 });
    };

    send();
    pingRef.current = setInterval(send, 5 * 60 * 1000);
    return () => clearInterval(pingRef.current);
  }, [token, profile]);

  if (joinToken) {
    return <Join token={joinToken} onJoined={(r) => {
      store(r.token);
      setProfile({
        name: null, distributor: r.distributor, consented: false,
        consent_text: r.consent_text, location_retention_days: 90,
      });
    }} />;
  }

  if (checking) {
    return <div style={{ ...wrap, textAlign: 'center', paddingTop: 80,
      color: MUTED }}>Loading…</div>;
  }

  if (!token || !profile) {
    return <SignIn onSignedIn={(r) => {
      store(r.token);
      setProfile({
        name: r.name, distributor: r.distributor, consented: r.consented,
        consent_text: r.consent_text, location_retention_days: 90,
      });
    }} />;
  }

  if (!profile.consented && profile.consent_text) {
    return <Consent token={token} text={profile.consent_text}
      onDone={(agreed) => setProfile((p) => ({
        ...p, consented: agreed, consent_text: agreed ? p.consent_text : null,
      }))} />;
  }

  return <Portal token={token} profile={profile}
    onProfile={setProfile}
    onSignOut={() => { store(''); setProfile(null); }} />;
}
