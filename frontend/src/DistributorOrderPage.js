// The distributor ordering page, opened from a shared link. NO LOGIN.
//
// Rendered by App.js before the auth gate, for any path /order/<token>.
//
// WHAT THIS PAGE DOES NOT HAVE
// ----------------------------
// Prices. Not hidden, not blurred, not zeroed — the catalogue that arrives from
// the server has no price field in it, because the query never selected one.
// There is nothing on this page to reveal, which is why there is no risk of a
// stray console.log or a future refactor spilling the price list.
//
// The one figure shown is the order total, and it is asked of the server. This
// page cannot compute a total, because it has never been sent the numbers that
// would let it.
//
// It uses plain fetch, not authedFetch: there is no session here and never will
// be. Nothing on this page reads anything but this distributor's own catalogue
// view and the total of the basket in front of them.

import React, { useCallback, useEffect, useMemo, useState } from 'react';

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

const naira = (v) => `₦${Number(v).toLocaleString('en-NG',
  { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

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

export default function DistributorOrderPage({ token }) {
  const [state, setState] = useState('loading');
  const [error, setError] = useState('');
  const [portal, setPortal] = useState(null);
  const [basket, setBasket] = useState({});      // key -> quantity
  const [search, setSearch] = useState('');
  const [quote, setQuote] = useState(null);
  const [placed, setPlaced] = useState(null);
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState(false);

  const call = useCallback(async (path, options) => {
    const res = await fetch(`/api/portal/${token}${path}`, {
      headers: { 'Content-Type': 'application/json' }, ...options,
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body.detail || 'Something went wrong. Please try again.');
    }
    return body;
  }, [token]);

  useEffect(() => {
    let live = true;
    call('').then((data) => {
      if (!live) return;
      setPortal(data);
      setState('ready');
    }).catch((e) => {
      if (!live) return;
      setError(e.message);
      setState('refused');
    });
    return () => { live = false; };
  }, [call]);

  const keyOf = (item) => `${item.product_id}::${item.unit}`;

  // The basket changed, so any total on screen is stale. Clearing it rather
  // than leaving it is the point: a figure that no longer matches the basket is
  // worse than no figure.
  const setQty = (item, qty) => {
    const key = keyOf(item);
    setQuote(null);
    setBasket((b) => {
      const next = { ...b };
      if (!qty || qty <= 0) delete next[key];
      else next[key] = qty;
      return next;
    });
  };

  const chosen = useMemo(() => {
    if (!portal) return [];
    return portal.catalogue
      .filter((i) => basket[keyOf(i)] > 0)
      .map((i) => ({ ...i, quantity: basket[keyOf(i)] }));
  }, [portal, basket]);

  const visible = useMemo(() => {
    if (!portal) return [];
    const q = search.trim().toLowerCase();
    if (!q) return portal.catalogue;
    return portal.catalogue.filter(
      (i) => `${i.name} ${i.sku} ${i.manufacturer || ''}`.toLowerCase().includes(q));
  }, [portal, search]);

  const payload = () => ({
    items: chosen.map((i) => ({
      product_id: i.product_id, unit: i.unit, quantity: i.quantity,
    })),
  });

  const getTotal = async () => {
    setBusy(true); setError('');
    try {
      setQuote(await call('/quote', {
        method: 'POST', body: JSON.stringify(payload()),
      }));
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  const submit = async () => {
    setBusy(true); setError('');
    try {
      setPlaced(await call('/orders', {
        method: 'POST',
        body: JSON.stringify({ ...payload(), notes: notes || null }),
      }));
    } catch (e) { setError(e.message); }
    setBusy(false);
  };

  // --- states ------------------------------------------------------------

  if (state === 'loading') {
    return (
      <div style={{ ...wrap, paddingTop: 60, textAlign: 'center', color: MUTED }}>
        Opening your ordering page…
      </div>
    );
  }

  if (state === 'refused') {
    return (
      <div style={{ ...wrap, paddingTop: 40 }}>
        <h1 style={{ fontSize: 20, color: BRAND, marginTop: 0 }}>
          Bonnesante Medicals
        </h1>
        <Notice tone="danger" title="This link cannot be used">
          {error}
        </Notice>
        <p style={{ fontSize: 14, color: MUTED, lineHeight: 1.7 }}>
          Ordering links are issued to one person and expire. Please contact
          your Bonnesante Medicals representative for a new one.
        </p>
      </div>
    );
  }

  if (placed) {
    return (
      <div style={{ ...wrap, paddingTop: 40 }}>
        <h1 style={{ fontSize: 20, color: BRAND, marginTop: 0 }}>
          Bonnesante Medicals
        </h1>
        <Notice tone="success" title={`Order ${placed.order_number} received`}>
          {placed.message}
        </Notice>
        <div style={card}>
          <div style={{ display: 'flex', justifyContent: 'space-between',
            alignItems: 'baseline', flexWrap: 'wrap', gap: 8 }}>
            <span style={{ color: MUTED, fontSize: 14 }}>
              {placed.line_count} item{placed.line_count === 1 ? '' : 's'}
            </span>
            <strong style={{ fontSize: 24 }}>{naira(placed.total)}</strong>
          </div>
        </div>
        <button style={btn('ghost')} onClick={() => {
          setPlaced(null); setBasket({}); setQuote(null); setNotes('');
        }}>Place another order</button>
      </div>
    );
  }

  const lineCount = chosen.length;
  const basketMatchesQuote = quote && quote.line_count === lineCount;

  return (
    <div style={wrap}>
      <div style={{ marginBottom: 18 }}>
        <div style={{ fontSize: 12, letterSpacing: '0.08em', color: MUTED,
          textTransform: 'uppercase', fontWeight: 700 }}>
          Bonnesante Medicals
        </div>
        <h1 style={{ fontSize: 22, color: BRAND, margin: '4px 0 2px' }}>
          {portal.distributor}
        </h1>
        <div style={{ fontSize: 13, color: MUTED }}>
          Place an order · {portal.distributor_code}
        </div>
      </div>

      {error && <Notice tone="danger">{error}</Notice>}

      <input
        value={search} onChange={(e) => setSearch(e.target.value)}
        placeholder="Search products…"
        style={{ width: '100%', padding: '12px 14px', fontSize: 15,
          borderRadius: 10, border: `1px solid ${LINE}`, marginBottom: 14,
          boxSizing: 'border-box', fontFamily: 'inherit' }} />

      <div style={{ marginBottom: 14 }}>
        {visible.length === 0 && (
          <div style={{ ...card, color: MUTED, textAlign: 'center' }}>
            Nothing matches “{search}”.
          </div>
        )}
        {visible.map((item) => {
          const key = keyOf(item);
          const qty = basket[key] || 0;
          return (
            <div key={key} style={{
              ...card, marginBottom: 10, padding: 14,
              borderColor: qty > 0 ? ACCENT : LINE,
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between',
                gap: 12, flexWrap: 'wrap' }}>
                <div style={{ flex: '1 1 200px', minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: 15 }}>{item.name}</div>
                  <div style={{ fontSize: 12.5, color: MUTED, marginTop: 2 }}>
                    per {item.unit}
                    {item.minimum_quantity > 1
                      && ` · from ${item.minimum_quantity} ${item.unit}`}
                    {!item.in_stock && (
                      <span style={{ color: WARN }}> · made to order</span>
                    )}
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <button aria-label={`Fewer ${item.name}`}
                    style={{ ...btn('ghost'), padding: '0 14px', minWidth: 44 }}
                    onClick={() => setQty(item,
                      Math.max(0, qty - (item.minimum_quantity || 1)))}>
                    −
                  </button>
                  <input
                    inputMode="numeric" value={qty || ''}
                    onChange={(e) => setQty(item, Number(e.target.value) || 0)}
                    placeholder="0"
                    style={{ width: 64, padding: '10px 8px', fontSize: 16,
                      textAlign: 'center', borderRadius: 8,
                      border: `1px solid ${LINE}`, fontFamily: 'inherit' }} />
                  <button aria-label={`More ${item.name}`}
                    style={{ ...btn('ghost'), padding: '0 14px', minWidth: 44 }}
                    onClick={() => setQty(item,
                      (qty || 0) + (item.minimum_quantity || 1))}>
                    +
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* The basket and the one figure this page ever shows. */}
      {lineCount > 0 && (
        <div style={{ ...card, position: 'sticky', bottom: 12,
          boxShadow: '0 -2px 16px rgba(15,23,42,0.08)' }}>
          <div style={{ fontWeight: 700, marginBottom: 8 }}>
            Your order — {lineCount} item{lineCount === 1 ? '' : 's'}
          </div>
          <div style={{ maxHeight: 150, overflowY: 'auto', marginBottom: 10 }}>
            {chosen.map((i) => (
              <div key={keyOf(i)} style={{ display: 'flex',
                justifyContent: 'space-between', fontSize: 13.5,
                padding: '4px 0', gap: 10 }}>
                <span style={{ minWidth: 0 }}>{i.name}</span>
                <span style={{ color: MUTED, whiteSpace: 'nowrap' }}>
                  {i.quantity} {i.unit}
                </span>
              </div>
            ))}
          </div>

          {basketMatchesQuote ? (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between',
                alignItems: 'baseline', padding: '10px 0',
                borderTop: `1px solid ${LINE}` }}>
                <span style={{ fontSize: 14, color: MUTED }}>Order value</span>
                <strong style={{ fontSize: 26 }}>{naira(quote.total)}</strong>
              </div>
              <textarea
                value={notes} onChange={(e) => setNotes(e.target.value)}
                placeholder="Anything we should know? (optional)"
                style={{ width: '100%', minHeight: 60, padding: 10,
                  fontSize: 14, borderRadius: 8, border: `1px solid ${LINE}`,
                  fontFamily: 'inherit', boxSizing: 'border-box',
                  marginBottom: 10, resize: 'vertical' }} />
              <div style={{ display: 'flex', gap: 8 }}>
                <button style={{ ...btn('ghost'), flex: '0 0 auto' }}
                  onClick={() => setQuote(null)} disabled={busy}>
                  Keep shopping
                </button>
                <button style={{ ...btn('accent', busy), flex: 1 }}
                  onClick={submit} disabled={busy}>
                  {busy ? 'Sending…' : 'Send this order'}
                </button>
              </div>
              <div style={{ fontSize: 12, color: MUTED, marginTop: 8,
                lineHeight: 1.6 }}>
                {quote.note}
              </div>
            </>
          ) : (
            <button style={{ ...btn('primary', busy), width: '100%' }}
              onClick={getTotal} disabled={busy}>
              {busy ? 'Working…' : 'Show order value'}
            </button>
          )}
        </div>
      )}

      {lineCount === 0 && (
        <div style={{ ...card, textAlign: 'center', color: MUTED }}>
          {portal.note}
        </div>
      )}

      <div style={{ textAlign: 'center', fontSize: 11.5, color: MUTED,
        marginTop: 20, lineHeight: 1.6 }}>
        This link is personal to you. Do not forward it — anyone who has it can
        order on your account.
      </div>
    </div>
  );
}
