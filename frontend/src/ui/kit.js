// Enterprise UI kit — the premium primitives every accounting screen is built
// from. Presentational only; all read from the design tokens in ./theme.
//
// Icons are inline outline SVG in the Lucide style (1.75 stroke, 24px), so
// there is no icon-font dependency and they inherit currentColor.

import React from 'react';
import { color, radius, shadow, space, font } from './theme';

// ---------------------------------------------------------------------------
// Icons (Lucide-style, outline)
// ---------------------------------------------------------------------------

const ICON_PATHS = {
  dashboard: 'M3 3h8v8H3zM13 3h8v5h-8zM13 12h8v9h-8zM3 15h8v6H3z',
  reports: 'M3 3v18h18 M7 15l3-3 3 2 4-5',
  ledger: 'M4 19.5A2.5 2.5 0 0 1 6.5 17H20 M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z',
  tag: 'M12 2H2v10l9.29 9.29a1 1 0 0 0 1.42 0l8.58-8.58a1 1 0 0 0 0-1.42L12 2z M7 7h.01',
  asset: 'M3 21h18 M5 21V7l7-4 7 4v14 M9 21v-6h6v6',
  budget: 'M3 3v18h18 M18 9l-5 5-3-3-4 4',
  vat: 'M12 2v20 M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6',
  qc: 'M9 11l3 3L22 4 M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11',
  payroll: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M22 21v-2a4 4 0 0 0-3-3.87 M16 3.13a4 4 0 0 1 0 7.75',
  wallet: 'M19 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0 0 4h16a1 1 0 0 1 1 1v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5 M18 12h.01',
  bank: 'M3 21h18 M3 10h18 M5 6l7-3 7 3 M4 10v11 M20 10v11 M8 14v3 M12 14v3 M16 14v3',
  receivable: 'M2 5h20v14H2z M2 10h20 M6 15h4',
  trendUp: 'M23 6l-9.5 9.5-5-5L1 18 M17 6h6v6',
  trendDown: 'M23 18l-9.5-9.5-5 5L1 6 M17 18h6v-6',
  alert: 'M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z M12 9v4 M12 17h.01',
  check: 'M22 11.08V12a10 10 0 1 1-5.93-9.14 M22 4L12 14.01l-3-3',
  box: 'M21 8l-9-5-9 5 9 5 9-5z M3 8v8l9 5 9-5V8 M12 13v8',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z M21 21l-4.35-4.35',
  refresh: 'M23 4v6h-6 M1 20v-6h6 M20.49 9A9 9 0 0 0 5.64 5.64L1 10 M3.51 15a9 9 0 0 0 14.85 3.36L23 14',
};

export function Icon({ name, size = 20, color: c = 'currentColor', style }) {
  const d = ICON_PATHS[name];
  if (!d) return null;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke={c} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"
      style={{ flexShrink: 0, ...style }} aria-hidden="true">
      {d.split(' M').map((seg, i) => <path key={i} d={(i ? 'M' : '') + seg} />)}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Surfaces
// ---------------------------------------------------------------------------

export function Card({ children, style, pad = 3, hover = false }) {
  const [h, setH] = React.useState(false);
  return (
    <div
      onMouseEnter={() => hover && setH(true)}
      onMouseLeave={() => hover && setH(false)}
      style={{
        background: color.card, border: `1px solid ${color.border}`,
        borderRadius: radius.lg, padding: space(pad),
        boxShadow: h ? shadow.md : shadow.sm,
        transition: 'box-shadow .18s ease, transform .18s ease',
        transform: h ? 'translateY(-1px)' : 'none', ...style,
      }}>
      {children}
    </div>
  );
}

export function SectionTitle({ children, right }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: space(2) }}>
      <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600, color: color.text, letterSpacing: '-0.01em' }}>{children}</h3>
      {right}
    </div>
  );
}

// ---------------------------------------------------------------------------
// KPI card — icon, label, big number, trend, sparkline
// ---------------------------------------------------------------------------

export function Sparkline({ data = [], stroke = color.medical, width = 96, height = 30 }) {
  if (!data || data.length < 2) return <div style={{ width, height }} />;
  const min = Math.min(...data), max = Math.max(...data), rng = max - min || 1;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * (width - 2) + 1;
    const y = height - 2 - ((v - min) / rng) * (height - 4);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return (
    <svg width={width} height={height} style={{ display: 'block' }} aria-hidden="true">
      <polyline points={pts} fill="none" stroke={stroke} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function KpiCard({ icon, label, value, sub, trend, tone = 'info', spark }) {
  const tones = {
    info: color.medical, success: color.success, warning: color.warning,
    danger: color.danger, royal: color.royal,
  };
  const c = tones[tone] || color.medical;
  const tint = { info: color.infoBg, success: color.successBg, warning: color.warningBg, danger: color.dangerBg, royal: color.royalBg }[tone] || color.infoBg;
  return (
    <Card hover pad={3} style={{ minWidth: 200, flex: '1 1 200px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div style={{ width: 40, height: 40, borderRadius: radius.md, background: tint, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Icon name={icon} size={20} color={c} />
        </div>
        {trend != null && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, fontWeight: 600, color: trend >= 0 ? color.success : color.danger }}>
            <Icon name={trend >= 0 ? 'trendUp' : 'trendDown'} size={14} />
            {Math.abs(trend)}%
          </span>
        )}
      </div>
      <div style={{ marginTop: space(2), fontSize: 12, fontWeight: 500, color: color.textSecondary, textTransform: 'uppercase', letterSpacing: '0.04em' }}>{label}</div>
      <div style={{ marginTop: 2, fontSize: 26, fontWeight: 700, color: color.text, letterSpacing: '-0.02em', lineHeight: 1.15 }}>{value}</div>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginTop: 6 }}>
        <span style={{ fontSize: 12, color: color.textMuted }}>{sub}</span>
        {spark && <Sparkline data={spark} stroke={c} />}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Chips
// ---------------------------------------------------------------------------

const CHIP_TONES = {
  success: [color.successBg, color.success], warning: [color.warningBg, '#B45309'],
  danger: [color.dangerBg, color.danger], info: [color.infoBg, color.royal],
  neutral: ['#F1F5F9', color.textSecondary],
};

export function Chip({ children, tone = 'neutral' }) {
  const [bg, fg] = CHIP_TONES[tone] || CHIP_TONES.neutral;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5, background: bg, color: fg,
      fontSize: 11.5, fontWeight: 600, padding: '3px 9px', borderRadius: radius.pill,
      letterSpacing: '0.01em', whiteSpace: 'nowrap',
    }}>
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: fg, opacity: 0.9 }} />
      {children}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Buttons
// ---------------------------------------------------------------------------

export function Btn({ children, onClick, variant = 'primary', size = 'md', disabled, icon, style, type }) {
  const [h, setH] = React.useState(false);
  const variants = {
    primary: { bg: h ? color.royal : color.navy, fg: '#fff', bd: 'transparent' },
    secondary: { bg: h ? '#F8FAFC' : '#fff', fg: color.text, bd: color.borderStrong },
    ghost: { bg: h ? '#F1F5F9' : 'transparent', fg: color.textSecondary, bd: 'transparent' },
    accent: { bg: h ? '#255FC5' : color.medical, fg: '#fff', bd: 'transparent' },
    danger: { bg: h ? '#B91C1C' : color.danger, fg: '#fff', bd: 'transparent' },
  };
  const v = variants[variant] || variants.primary;
  return (
    <button type={type || 'button'} onClick={onClick} disabled={disabled}
      onMouseEnter={() => setH(true)} onMouseLeave={() => setH(false)}
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 7,
        height: size === 'sm' ? 34 : 44, padding: size === 'sm' ? '0 14px' : '0 18px',
        background: v.bg, color: v.fg, border: `1px solid ${v.bd}`, borderRadius: radius.md,
        fontSize: size === 'sm' ? 13 : 14, fontWeight: 600, fontFamily: font.family,
        cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.55 : 1,
        transition: 'background .15s ease, box-shadow .15s ease', ...style,
      }}>
      {icon && <Icon name={icon} size={size === 'sm' ? 15 : 17} />}
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Table — sticky header, zebra hover, alignment, empty state
// ---------------------------------------------------------------------------

export function DataTable({ cols, rows, render, empty = 'No records.', maxHeight }) {
  return (
    <div style={{ overflow: 'auto', maxHeight, borderRadius: radius.md, border: `1px solid ${color.border}` }}>
      <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, fontSize: 13, fontFamily: font.family }}>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c.key} style={{
                position: 'sticky', top: 0, zIndex: 1, textAlign: c.align || 'left',
                padding: '11px 14px', background: '#FbFcFe', color: color.textSecondary,
                fontSize: 11.5, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em',
                borderBottom: `1px solid ${color.border}`, whiteSpace: 'nowrap',
              }}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr><td colSpan={cols.length} style={{ padding: space(4), textAlign: 'center', color: color.textMuted }}>{empty}</td></tr>
          ) : rows.map((r, i) => <Row key={i} r={r} cols={cols} render={render} />)}
        </tbody>
      </table>
    </div>
  );
}

function Row({ r, cols, render }) {
  const [h, setH] = React.useState(false);
  return (
    <tr onMouseEnter={() => setH(true)} onMouseLeave={() => setH(false)}
      style={{ background: h ? color.royalBg : '#fff', transition: 'background .12s ease' }}>
      {cols.map((c) => (
        <td key={c.key} style={{
          padding: '11px 14px', textAlign: c.align || 'left', color: color.text,
          borderBottom: `1px solid #F1F5F9`, whiteSpace: c.wrap ? 'normal' : 'nowrap',
        }}>{render ? render(r, c) : r[c.key]}</td>
      ))}
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Loading skeletons + feedback
// ---------------------------------------------------------------------------

export function Skeleton({ w = '100%', h = 16, style }) {
  return <div style={{ width: w, height: h, borderRadius: 6, background: 'linear-gradient(90deg,#EEF2F7 25%,#E2E8F0 37%,#EEF2F7 63%)', backgroundSize: '400% 100%', animation: 'bmShimmer 1.4s ease infinite', ...style }} />;
}

export function SkeletonCards({ n = 4 }) {
  return (
    <div style={{ display: 'flex', gap: space(2), flexWrap: 'wrap' }}>
      {Array.from({ length: n }).map((_, i) => (
        <Card key={i} style={{ minWidth: 200, flex: '1 1 200px' }}>
          <Skeleton w={40} h={40} style={{ borderRadius: 10 }} />
          <Skeleton w="60%" h={11} style={{ marginTop: 16 }} />
          <Skeleton w="80%" h={22} style={{ marginTop: 8 }} />
        </Card>
      ))}
    </div>
  );
}

export function Banner({ tone = 'info', title, children }) {
  const map = {
    info: [color.infoBg, color.royal, '#1E3A5F'], warning: [color.warningBg, color.warning, '#78350F'],
    danger: [color.dangerBg, color.danger, '#7F1D1D'], success: [color.successBg, color.success, '#065F46'],
  };
  const [bg, accent, fg] = map[tone] || map.info;
  return (
    <div style={{ background: bg, borderLeft: `3px solid ${accent}`, borderRadius: radius.sm, padding: `${space(1.5)} ${space(2)}`, marginBottom: space(2) }}>
      {title && <div style={{ display: 'flex', alignItems: 'center', gap: 7, fontWeight: 600, fontSize: 13, color: fg }}>
        <Icon name={tone === 'success' ? 'check' : 'alert'} size={15} color={accent} />{title}</div>}
      <div style={{ fontSize: 13, color: fg, marginTop: title ? 4 : 0, lineHeight: 1.5 }}>{children}</div>
    </div>
  );
}

export function ErrorBox({ msg }) {
  return msg ? <Banner tone="danger">{msg}</Banner> : null;
}
