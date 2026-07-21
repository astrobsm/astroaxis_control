// Centralised authenticated fetch.
//
// Every call to /api/** must carry the bearer token. Previously each call site
// re-implemented this by hand, and ~55 of them simply forgot -- which is why
// payroll, financials, procurement and settings could be read by anyone. Route
// all API traffic through here so the token can never be omitted by accident.

import API_BASE_URL from '../config';

export const TOKEN_KEY = 'access_token';

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

/** Clear the session and bounce to the login screen. */
export function endSession() {
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem('currentUser');
    localStorage.removeItem('user');
  } catch {
    /* storage unavailable (private mode) -- redirect anyway */
  }
  if (window.location.pathname !== '/') {
    window.location.href = '/';
  } else {
    window.location.reload();
  }
}

/** Prefix relative /api paths with the configured base URL. */
function resolveUrl(url) {
  if (/^https?:\/\//i.test(url)) return url;
  return `${API_BASE_URL}${url}`;
}

function buildHeaders(options) {
  // Preserve whatever the caller passed, in whichever shape they passed it.
  const headers = new Headers(options.headers || {});
  const token = getToken();
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  // Only default the content type for plain bodies. FormData must set its own
  // multipart boundary, and forcing a value here silently breaks uploads.
  const isFormData =
    typeof FormData !== 'undefined' && options.body instanceof FormData;
  if (options.body && !isFormData && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  return headers;
}

/**
 * fetch() with the bearer token attached and expired sessions handled once,
 * centrally. Returns the raw Response, so existing call sites that inspect
 * res.ok / res.json() / res.blob() keep working unchanged.
 */
export async function authedFetch(url, options = {}) {
  const res = await fetch(resolveUrl(url), {
    ...options,
    headers: buildHeaders(options),
  });

  // A 401 from the credential endpoints means "wrong password", not "session
  // expired" -- bouncing to login there would reload the page out from under
  // someone who simply mistyped.
  const isCredentialEndpoint = /\/api\/auth\/(login|register)/.test(url);

  if (res.status === 401 && !isCredentialEndpoint && getToken()) {
    endSession();
  }
  return res;
}

/**
 * Open an authenticated URL in a new tab.
 *
 * window.open() cannot carry an Authorization header, so anything behind auth
 * (PDF exports, thermal print views) has to be fetched as a blob first and
 * opened from an object URL.
 */
export async function openAuthed(url, { filename } = {}) {
  const res = await authedFetch(url);
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      detail = j.detail || j.message || detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }

  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);

  if (filename) {
    const a = document.createElement('a');
    a.href = objectUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } else {
    const win = window.open(objectUrl, '_blank');
    if (!win) {
      URL.revokeObjectURL(objectUrl);
      throw new Error('Popup blocked. Allow popups for this site to view the document.');
    }
  }

  // Give the tab time to load before releasing the blob.
  setTimeout(() => URL.revokeObjectURL(objectUrl), 60000);
  return objectUrl;
}

export default authedFetch;
