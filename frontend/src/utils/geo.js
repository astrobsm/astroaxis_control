// Lightweight geolocation helper used across the app to attach
// {latitude, longitude, accuracy} to attendance and access-control events.
// Resolves to null on denial / unavailable / timeout — never throws.

const DEFAULT_OPTS = {
  timeout: 7000,
  maximumAge: 30_000, // accept a fix up to 30s old
  enableHighAccuracy: true,
};

let _lastFix = null;
let _lastFixAt = 0;

export function getCachedLocation(maxAgeMs = 60_000) {
  if (_lastFix && Date.now() - _lastFixAt < maxAgeMs) return _lastFix;
  return null;
}

export function getCurrentLocation(opts = {}) {
  return new Promise((resolve) => {
    if (typeof navigator === 'undefined' || !navigator.geolocation) {
      resolve(null);
      return;
    }
    const options = { ...DEFAULT_OPTS, ...opts };
    let settled = false;
    const finish = (v) => { if (!settled) { settled = true; resolve(v); } };

    // Safety timer in case the platform never fires either callback
    const safety = setTimeout(() => finish(null), options.timeout + 1500);

    try {
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          clearTimeout(safety);
          const fix = {
            latitude: pos.coords.latitude,
            longitude: pos.coords.longitude,
            accuracy: pos.coords.accuracy ?? null,
          };
          _lastFix = fix;
          _lastFixAt = Date.now();
          finish(fix);
        },
        () => { clearTimeout(safety); finish(null); },
        options,
      );
    } catch {
      clearTimeout(safety);
      finish(null);
    }
  });
}

// Convenience: returns an object that's safe to spread into a request body.
export async function getLocationPayload() {
  const loc = await getCurrentLocation();
  if (!loc) return {};
  return loc;
}

// Strict variant: requires a successful geolocation fix.
// Returns { latitude, longitude, accuracy } on success, or throws
// an Error whose .code is one of:
//   'unsupported' — browser has no geolocation API
//   'insecure'    — page is not HTTPS/localhost (browsers block geo here)
//   'denied'      — user denied permission
//   'unavailable' — position unavailable
//   'timeout'     — request timed out
//   'failed'      — other / unknown failure
export function requireLocation(opts = {}) {
  return new Promise((resolve, reject) => {
    if (typeof navigator === 'undefined' || !navigator.geolocation) {
      const e = new Error('Geolocation is not supported in this browser.');
      e.code = 'unsupported';
      return reject(e);
    }
    if (typeof window !== 'undefined'
        && window.location
        && window.location.protocol !== 'https:'
        && window.location.hostname !== 'localhost'
        && window.location.hostname !== '127.0.0.1') {
      const e = new Error('Location requires HTTPS. Please open the app over https://.');
      e.code = 'insecure';
      return reject(e);
    }
    const options = { timeout: 10000, maximumAge: 15_000, enableHighAccuracy: true, ...opts };
    let settled = false;
    const safety = setTimeout(() => {
      if (settled) return;
      settled = true;
      const e = new Error('Timed out waiting for your location. Please try again.');
      e.code = 'timeout';
      reject(e);
    }, options.timeout + 1500);
    try {
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          if (settled) return;
          settled = true;
          clearTimeout(safety);
          const fix = {
            latitude: pos.coords.latitude,
            longitude: pos.coords.longitude,
            accuracy: pos.coords.accuracy ?? null,
          };
          _lastFix = fix;
          _lastFixAt = Date.now();
          resolve(fix);
        },
        (err) => {
          if (settled) return;
          settled = true;
          clearTimeout(safety);
          let code = 'failed';
          let msg = 'Could not get your location.';
          if (err && typeof err.code === 'number') {
            // 1 PERMISSION_DENIED, 2 POSITION_UNAVAILABLE, 3 TIMEOUT
            if (err.code === 1) { code = 'denied'; msg = 'Location permission denied. Please allow location access and try again.'; }
            else if (err.code === 2) { code = 'unavailable'; msg = 'Your location is currently unavailable. Move to a place with better GPS/Wi-Fi signal and try again.'; }
            else if (err.code === 3) { code = 'timeout'; msg = 'Timed out waiting for your location. Please try again.'; }
          }
          const e = new Error(msg);
          e.code = code;
          reject(e);
        },
        options,
      );
    } catch (ex) {
      if (settled) return;
      settled = true;
      clearTimeout(safety);
      const e = new Error('Could not get your location.');
      e.code = 'failed';
      reject(e);
    }
  });
}
