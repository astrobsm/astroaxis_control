// Bonnesante Medicals — enterprise design tokens.
//
// One source of truth for colour, spacing, radius, elevation and type. Every
// premium component reads from here so the system stays consistent: an 8px
// spacing rhythm, restrained elevation, a medical navy/blue palette, and the
// Inter typeface. Change a value here and it propagates everywhere.

export const color = {
  // Brand
  navy: '#0B1F4A',
  royal: '#1F4E9E',
  medical: '#2D6CDF',
  // Sidebar
  sidebar: '#081D4F',
  sidebarHover: '#153A8A',
  // Status
  success: '#16A34A',
  warning: '#F59E0B',
  danger: '#DC2626',
  info: '#2D6CDF',
  // Surfaces
  bg: '#F6F8FC',
  card: '#FFFFFF',
  border: '#E5E7EB',
  borderStrong: '#D8DEE9',
  // Text
  text: '#0F172A',
  textSecondary: '#64748B',
  textMuted: '#94A3B8',
  // Tints (soft backgrounds for chips / accents)
  successBg: '#ECFDF5',
  warningBg: '#FFFBEB',
  dangerBg: '#FEF2F2',
  infoBg: '#EFF4FE',
  royalBg: '#F0F5FF',
};

// 8px spacing scale.
export const space = (n) => `${n * 8}px`;

export const radius = { sm: '8px', md: '10px', lg: '12px', pill: '999px' };

// Restrained elevation — no heavy shadows.
export const shadow = {
  xs: '0 1px 2px rgba(15, 23, 42, 0.04)',
  sm: '0 1px 3px rgba(15, 23, 42, 0.06), 0 1px 2px rgba(15, 23, 42, 0.04)',
  md: '0 4px 12px rgba(15, 23, 42, 0.06), 0 2px 4px rgba(15, 23, 42, 0.04)',
  focus: '0 0 0 3px rgba(45, 108, 223, 0.25)',
};

export const font = {
  family: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
  mono: "'IBM Plex Mono', 'SF Mono', ui-monospace, Menlo, monospace",
};

// Format a Naira amount consistently across the suite.
export const naira = (v, { compact = false } = {}) => {
  const n = Number(v || 0);
  if (compact && Math.abs(n) >= 1_000_000) return '₦' + (n / 1_000_000).toFixed(2) + 'M';
  if (compact && Math.abs(n) >= 1_000) return '₦' + (n / 1_000).toFixed(1) + 'k';
  return '₦' + n.toLocaleString('en-NG', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};
