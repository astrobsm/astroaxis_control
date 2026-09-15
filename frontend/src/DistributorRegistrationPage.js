// The distributor application form, opened from a shared link. NO LOGIN.
//
// Rendered by App.js before the auth gate, for any path /register/<token>.
//
// THIS IS NOT THE ORDERING PAGE
// -----------------------------
// /order/<token> is a credential for one distributor's account. This link is
// meant to be forwarded — to a trade fair, a WhatsApp group, a sales rep's
// contacts — so it is a credential for nothing. Everything typed here is an
// unverified claim from a stranger and lands in a review queue. The page says
// so plainly, twice, because somebody who believes they have just become a
// distributor will try to order and will be told no.
//
// THE CUSTOMER DROPDOWN THAT IS NOT ONE
// -------------------------------------
// The obvious build is: type your business name, pick yourself from a list of
// customers. That list is the company's customer book, and this page is public
// — type "a", collect every customer starting with A, repeat through the
// alphabet. So the applicant enters the phone number their account is held
// under and gets back at most ONE masked name to recognise. They must already
// know the number; there is nothing here to enumerate.
//
// Real matching happens on the staff side, where an authenticated reviewer sees
// the full candidate list and why each one matched. That is also the better
// answer, because an applicant often does not know they are already a customer
// under a slightly different name.
//
// Plain fetch, not authedFetch: there is no session here and never will be.

import React, { useCallback, useEffect, useState } from 'react';

const BRAND = '#0B1F4A';
const ACCENT = '#2D6CDF';
const OK = '#16A34A';
const WARN = '#B45309';
const DANGER = '#DC2626';
const LINE = '#E5E7EB';
const MUTED = '#64748B';

const wrap = {
  maxWidth: 760, margin: '0 auto', padding: '16px',
  fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
  color: '#0F172A', boxSizing: 'border-box',
};
const card = {
  background: '#fff', border: `1px solid ${LINE}`, borderRadius: 12,
  padding: 16, marginBottom: 14,
};
const btn = (variant = 'primary', disabled = false) => ({
  padding: '12px 18px', borderRadius: 10, fontSize: 15, fontWeight: 600,
  border: variant === 'ghost' ? `1px solid ${LINE}` : '1px solid transparent',
  background: variant === 'ghost' ? '#fff'
    : variant === 'accent' ? ACCENT : BRAND,
  color: variant === 'ghost' ? '#334155' : '#fff',
  cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.5 : 1,
  fontFamily: 'inherit', minHeight: 44,
});
const input = {
  width: '100%', padding: '11px 12px', borderRadius: 9, fontSize: 15,
  border: `1px solid ${LINE}`, fontFamily: 'inherit', boxSizing: 'border-box',
  color: '#0F172A', background: '#fff', minHeight: 44,
};
const labelStyle = {
  display: 'block', fontSize: 12.5, fontWeight: 600, color: '#334155',
  marginBottom: 5,
};

function Notice({ tone = 'info', title, children }) {
  const colours = {
    info: ['#EFF4FE', ACCENT, '#1E3A5F'],
    warning: ['#FFFBEB', WARN, '#78350F'],
    danger: ['#FEF2F2', DANGER, '#7F1D1D'],
    success: ['#ECFDF5', OK, '#065F46'],
  }[tone];
  return (
    <div style={{
      background: colours[0], borderLeft: `4px solid ${colours[1]}`,
      borderRadius: 8, padding: '12px 14px', marginBottom: 14,
      color: colours[2], fontSize: 14, lineHeight: 1.6,
    }}>
      {title && <div style={{ fontWeight: 700, marginBottom: 3 }}>{title}</div>}
      {children}
    </div>
  );
}

function Field({ id, title, hint, required, children }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <label htmlFor={id} style={labelStyle}>
        {title}
        {required && <span style={{ color: DANGER }}> *</span>}
      </label>
      {children}
      {hint && (
        <div style={{ fontSize: 11.5, color: MUTED, marginTop: 4, lineHeight: 1.5 }}>
          {hint}
        </div>
      )}
    </div>
  );
}

const ENTITY_TYPES = [
  ['COMPANY', 'Limited company'],
  ['INDIVIDUAL', 'Sole trader / individual'],
  ['PARTNERSHIP', 'Partnership'],
  ['COOPERATIVE', 'Cooperative'],
];

export default function DistributorRegistrationPage({ token }) {
  const [state, setState] = useState('loading');
  const [error, setError] = useState('');
  const [opened, setOpened] = useState(null);
  const [lgas, setLgas] = useState([]);
  const [done, setDone] = useState(null);
  const [busy, setBusy] = useState(false);

  // The existing-customer check: a confirmation, not a search.
  const [lookupPhone, setLookupPhone] = useState('');
  const [lookup, setLookup] = useState(null);
  const [lookupBusy, setLookupBusy] = useState(false);
  const [lookupError, setLookupError] = useState('');
  const [claimConfirmed, setClaimConfirmed] = useState(false);

  const [form, setForm] = useState({
    legal_name: '', trading_name: '', entity_type: 'COMPANY',
    contact_name: '', phone: '', whatsapp: '', email: '',
    business_address: '', state_id: '', lga_id: '', town: '',
    cac_number: '', tin: '', years_in_operation: '', business_type: '',
    employee_count: '', marketer_count: '', storage_description: '',
    products_of_interest: '', applicant_note: '',
    claims_existing_customer: false,
  });

  const takenHere = lgas.filter((l) => !l.available).length;

  const set = (key) => (e) => {
    const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [key]: value }));
  };

  const call = useCallback(async (path, options) => {
    const res = await fetch(`/api/portal/register/${token}${path}`, {
      headers: { 'Content-Type': 'application/json' }, ...options,
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body.detail || 'Something went wrong. Please try again.');
    }
    return body;
  }, [token]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const body = await call('');
        if (!alive) return;
        setOpened(body);
        setState('ready');
      } catch (e) {
        if (!alive) return;
        setError(e.message);
        setState('error');
      }
    })();
    return () => { alive = false; };
  }, [call]);

  const pickState = async (e) => {
    const stateId = e.target.value;
    setForm((f) => ({ ...f, state_id: stateId, lga_id: '' }));
    setLgas([]);
    if (!stateId) return;
    try {
      const body = await call(`/lgas/${stateId}`);
      setLgas(body.lgas || []);
    } catch (err) {
      setLgas([]);
    }
  };

  const checkCustomer = async () => {
    setLookupBusy(true);
    setLookupError('');
    setLookup(null);
    setClaimConfirmed(false);
    try {
      const body = await call('/check-customer', {
        method: 'POST', body: JSON.stringify({ phone: lookupPhone }),
      });
      setLookup(body);
    } catch (e) {
      setLookupError(e.message);
    } finally {
      setLookupBusy(false);
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    const numeric = ['years_in_operation', 'employee_count', 'marketer_count'];
    const payload = {};
    Object.entries(form).forEach(([k, v]) => {
      if (v === '' || v === null) return;
      payload[k] = numeric.includes(k) ? Number(v) : v;
    });
    // The claimed account is only sent when the applicant confirmed the masked
    // name is theirs. A number they merely typed proves nothing.
    if (claimConfirmed && lookup && lookup.found) {
      payload.claims_existing_customer = true;
      payload.claimed_customer_id = lookup.customer_id;
    }
    try {
      const body = await call('', {
        method: 'POST', body: JSON.stringify(payload),
      });
      setDone(body);
      window.scrollTo(0, 0);
    } catch (err) {
      setError(err.message);
      window.scrollTo(0, 0);
    } finally {
      setBusy(false);
    }
  };

  if (state === 'loading') {
    return (
      <div style={{ ...wrap, textAlign: 'center', paddingTop: 80, color: MUTED }}>
        Loading…
      </div>
    );
  }

  if (state === 'error') {
    return (
      <div style={wrap}>
        <div style={{ ...card, marginTop: 40 }}>
          <Notice tone="danger" title="This form cannot be opened">
            {error}
          </Notice>
          <div style={{ fontSize: 13, color: MUTED, lineHeight: 1.6 }}>
            Registration links are issued for a period and can be withdrawn.
            Ask whoever sent you this one for a current link.
          </div>
        </div>
      </div>
    );
  }

  if (done) {
    return (
      <div style={wrap}>
        <div style={{ textAlign: 'center', paddingTop: 28 }}>
          <img src="/company-logo.png?v=20260118" alt="Bonnesante Medicals"
            onError={(e) => { e.target.style.display = 'none'; }}
            style={{ height: 64, width: 'auto', objectFit: 'contain' }} />
        </div>
        <div style={{ ...card, marginTop: 16 }}>
          <Notice tone="success" title="Application received">
            {done.message}
          </Notice>
          <div style={{
            fontSize: 22, fontWeight: 700, letterSpacing: 0.5,
            textAlign: 'center', padding: '14px 0', color: BRAND,
          }}>
            {done.registration_reference}
          </div>
          <div style={{ fontSize: 13, color: MUTED, lineHeight: 1.7 }}>
            Keep that reference — quote it when you call. {done.note}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={wrap}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 14, padding: '18px 0 10px',
      }}>
        {/* Served from the site root, so it needs no session. onError hides it
            rather than leaving a broken-image icon at the top of the form the
            applicant is being asked to trust. */}
        <img src="/company-logo.png?v=20260118" alt="Bonnesante Medicals"
          onError={(e) => { e.target.style.display = 'none'; }}
          style={{
            height: 58, width: 'auto', maxWidth: 120, objectFit: 'contain',
            flexShrink: 0,
          }} />
        <div>
          <div style={{ fontSize: 20, fontWeight: 800, color: BRAND }}>
            Bonnesante Medicals
          </div>
          <div style={{ fontSize: 14, color: MUTED, marginTop: 3 }}>
            Distributor application{opened.campaign ? ` — ${opened.campaign}` : ''}
          </div>
        </div>
      </div>

      <Notice tone="info" title="Before you start">
        {opened.note}
      </Notice>

      {error && <Notice tone="danger" title="Not sent">{error}</Notice>}

      <form onSubmit={submit}>
        {/* Already a customer? ------------------------------------------- */}
        <div style={card}>
          <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>
            Do you already buy from us?
          </div>
          <div style={{ fontSize: 13, color: MUTED, lineHeight: 1.6, marginBottom: 12 }}>
            If you do, enter the phone number your account is held under. We will
            confirm the account rather than create a second one, and your
            purchase history comes with you.
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <input style={{ ...input, flex: '1 1 220px' }} type="tel"
              placeholder="e.g. 08031234567" value={lookupPhone}
              onChange={(e) => setLookupPhone(e.target.value)} />
            <button type="button" style={btn('ghost', lookupBusy || !lookupPhone)}
              onClick={checkCustomer} disabled={lookupBusy || !lookupPhone}>
              {lookupBusy ? 'Checking…' : 'Check'}
            </button>
          </div>

          {lookupError && (
            <div style={{ marginTop: 12 }}>
              <Notice tone="warning">{lookupError}</Notice>
            </div>
          )}

          {lookup && !lookup.found && (
            <div style={{ marginTop: 12 }}>
              <Notice tone="warning">{lookup.note}</Notice>
            </div>
          )}

          {lookup && lookup.found && (
            <div style={{ marginTop: 12 }}>
              <Notice tone="success" title="We found one account">
                <div style={{
                  fontSize: 17, fontWeight: 700, letterSpacing: 1,
                  padding: '6px 0',
                }}>
                  {lookup.masked_name}
                </div>
                {lookup.note}
              </Notice>
              <label style={{
                display: 'flex', gap: 10, alignItems: 'flex-start',
                fontSize: 14, cursor: 'pointer', lineHeight: 1.6,
              }}>
                <input type="checkbox" checked={claimConfirmed}
                  style={{ marginTop: 3, width: 18, height: 18 }}
                  onChange={(e) => setClaimConfirmed(e.target.checked)} />
                <span>That is my business. Use this account.</span>
              </label>
            </div>
          )}

          {!lookup && (
            <label style={{
              display: 'flex', gap: 10, alignItems: 'flex-start', marginTop: 14,
              fontSize: 14, cursor: 'pointer', lineHeight: 1.6,
            }}>
              <input type="checkbox" checked={form.claims_existing_customer}
                style={{ marginTop: 3, width: 18, height: 18 }}
                onChange={set('claims_existing_customer')} />
              <span>
                I already buy from Bonnesante Medicals but cannot find the
                number — match my account when you review this.
              </span>
            </label>
          )}
        </div>

        {/* The business --------------------------------------------------- */}
        <div style={card}>
          <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 12 }}>
            The business
          </div>

          <Field id="legal_name" title="Registered business name" required
            hint="Exactly as it appears on your CAC certificate, if you have one.">
            <input id="legal_name" style={input} required value={form.legal_name}
              onChange={set('legal_name')} />
          </Field>

          <Field id="trading_name" title="Trading name"
            hint="If you trade under a different name from the one above.">
            <input id="trading_name" style={input} value={form.trading_name}
              onChange={set('trading_name')} />
          </Field>

          <Field id="entity_type" title="Type of business">
            <select id="entity_type" style={input} value={form.entity_type}
              onChange={set('entity_type')}>
              {ENTITY_TYPES.map(([value, text]) => (
                <option key={value} value={value}>{text}</option>
              ))}
            </select>
          </Field>

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 200px' }}>
              <Field id="cac_number" title="CAC / RC number">
                <input id="cac_number" style={input} value={form.cac_number}
                  onChange={set('cac_number')} />
              </Field>
            </div>
            <div style={{ flex: '1 1 200px' }}>
              <Field id="tin" title="Tax identification number">
                <input id="tin" style={input} value={form.tin}
                  onChange={set('tin')} />
              </Field>
            </div>
          </div>

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 200px' }}>
              <Field id="business_type" title="What the business does"
                hint="Pharmacy, wholesaler, clinic supplier…">
                <input id="business_type" style={input} value={form.business_type}
                  onChange={set('business_type')} />
              </Field>
            </div>
            <div style={{ flex: '1 1 140px' }}>
              <Field id="years_in_operation" title="Years trading">
                <input id="years_in_operation" style={input} type="number"
                  min="0" max="200" value={form.years_in_operation}
                  onChange={set('years_in_operation')} />
              </Field>
            </div>
          </div>
        </div>

        {/* Who to contact ------------------------------------------------- */}
        <div style={card}>
          <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 12 }}>
            How we reach you
          </div>

          <Field id="contact_name" title="Contact person">
            <input id="contact_name" style={input} value={form.contact_name}
              onChange={set('contact_name')} />
          </Field>

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 200px' }}>
              <Field id="phone" title="Phone" required
                hint="We will call this number about your application.">
                <input id="phone" style={input} type="tel" required
                  value={form.phone} onChange={set('phone')} />
              </Field>
            </div>
            <div style={{ flex: '1 1 200px' }}>
              <Field id="whatsapp" title="WhatsApp">
                <input id="whatsapp" style={input} type="tel"
                  value={form.whatsapp} onChange={set('whatsapp')} />
              </Field>
            </div>
          </div>

          <Field id="email" title="Email">
            <input id="email" style={input} type="email" value={form.email}
              onChange={set('email')} />
          </Field>
        </div>

        {/* Where ----------------------------------------------------------- */}
        <div style={card}>
          <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 12 }}>
            Where you trade
          </div>

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 200px' }}>
              <Field id="state_id" title="State">
                <select id="state_id" style={input} value={form.state_id}
                  onChange={pickState}>
                  <option value="">Choose a state…</option>
                  {(opened.states || []).map((s) => (
                    <option key={s.id} value={s.id} disabled={!s.available}>
                      {s.name}
                      {s.available
                        ? (s.available_lgas < s.total_lgas
                          ? ` — ${s.available_lgas} of ${s.total_lgas} areas open`
                          : '')
                        : ' — fully covered'}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <div style={{ flex: '1 1 200px' }}>
              <Field id="lga_id" title="Local government area">
                <select id="lga_id" style={input} value={form.lga_id}
                  onChange={set('lga_id')} disabled={!lgas.length}>
                  <option value="">
                    {form.state_id ? 'Choose an LGA…' : 'Choose a state first'}
                  </option>
                  {lgas.map((l) => (
                    <option key={l.id} value={l.id} disabled={!l.available}>
                      {l.name}{l.available ? '' : ` — ${l.note}`}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
          </div>

          {opened.coverage_note && (
            <div style={{
              fontSize: 12.5, color: MUTED, lineHeight: 1.6,
              marginTop: -4, marginBottom: 14,
            }}>
              {opened.coverage_note}{' '}
              {takenHere > 0 && (
                <strong>
                  {takenHere} area{takenHere === 1 ? ' is' : 's are'} already
                  covered in this state.
                </strong>
              )}
            </div>
          )}

          <Field id="town" title="Town or city">
            <input id="town" style={input} value={form.town}
              onChange={set('town')} />
          </Field>

          <Field id="business_address" title="Business address">
            <textarea id="business_address" rows={3}
              style={{ ...input, minHeight: 78, resize: 'vertical' }}
              value={form.business_address} onChange={set('business_address')} />
          </Field>
        </div>

        {/* Capacity -------------------------------------------------------- */}
        <div style={card}>
          <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>
            What you can handle
          </div>
          <div style={{ fontSize: 13, color: MUTED, lineHeight: 1.6, marginBottom: 12 }}>
            None of this is required, but it is what the review looks at. Storage
            in particular — our products have storage conditions to meet.
          </div>

          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 160px' }}>
              <Field id="employee_count" title="Staff">
                <input id="employee_count" style={input} type="number" min="0"
                  value={form.employee_count} onChange={set('employee_count')} />
              </Field>
            </div>
            <div style={{ flex: '1 1 160px' }}>
              <Field id="marketer_count" title="Marketers / sales reps">
                <input id="marketer_count" style={input} type="number" min="0"
                  value={form.marketer_count} onChange={set('marketer_count')} />
              </Field>
            </div>
          </div>

          <Field id="storage_description" title="Storage and premises"
            hint="Warehouse size, shelving, whether you have cold storage.">
            <textarea id="storage_description" rows={3}
              style={{ ...input, minHeight: 78, resize: 'vertical' }}
              value={form.storage_description}
              onChange={set('storage_description')} />
          </Field>

          <Field id="products_of_interest" title="Products you want to carry">
            <textarea id="products_of_interest" rows={2}
              style={{ ...input, minHeight: 62, resize: 'vertical' }}
              value={form.products_of_interest}
              onChange={set('products_of_interest')} />
          </Field>

          <Field id="applicant_note" title="Anything else we should know">
            <textarea id="applicant_note" rows={3}
              style={{ ...input, minHeight: 78, resize: 'vertical' }}
              value={form.applicant_note} onChange={set('applicant_note')} />
          </Field>
        </div>

        <div style={card}>
          <Notice tone="warning" title="What happens when you send this">
            It goes to a review queue. It does not create an account, it does not
            let you order, and nothing is agreed until somebody from Bonnesante
            Medicals has spoken to you.
          </Notice>
          <button type="submit" style={{ ...btn('primary', busy), width: '100%' }}
            disabled={busy}>
            {busy ? 'Sending…' : 'Send application'}
          </button>
        </div>
      </form>

      <div style={{
        textAlign: 'center', fontSize: 11.5, color: MUTED, marginTop: 20,
        marginBottom: 24, lineHeight: 1.6,
      }}>
        You may forward this link to another business that wants to apply.
      </div>
    </div>
  );
}
