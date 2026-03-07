/**
 * ASTRO-ASIX ERP - Offline-First Engine
 * 
 * Transparently intercepts all /api/ fetch calls to provide:
 * - IndexedDB caching for GET requests (network-first, cache fallback)
 * - Mutation queuing when offline (POST/PUT/DELETE queued for replay)
 * - Background sync when coming back online
 * - "Pull from Cloud" for multi-device data sync
 * - Status tracking (online/offline, pending count, last sync)
 */
import { openDB } from 'idb';

// ─── Constants ──────────────────────────────────────────────────────────────
const DB_NAME = 'astroaxis-offline-v2';
const DB_VERSION = 1;
const STORE_API = 'apiResponses';
const STORE_QUEUE = 'mutationQueue';
const STORE_META = 'syncMeta';

// All critical endpoints to pre-cache for full offline operation
const CRITICAL_ENDPOINTS = [
  '/api/staff/staffs',
  '/api/products/',
  '/api/raw-materials/',
  '/api/stock/levels',
  '/api/warehouses/',
  '/api/production/orders',
  '/api/sales/orders',
  '/api/attendance/',
  '/api/sales/customers',
  '/api/staff/birthdays/upcoming?days_ahead=7',
  '/api/procurement/dashboard',
  '/api/procurement/requests',
  '/api/procurement/orders',
  '/api/procurement/invoices',
  '/api/procurement/expenses',
  '/api/logistics/dashboard',
  '/api/logistics/deliveries',
  '/api/logistics/analytics',
  '/api/marketing/dashboard',
  '/api/marketing/plans',
  '/api/marketing/logs',
  '/api/marketing/proposals',
  '/api/marketing/products-catalog',
  '/api/hr-customercare/dashboard',
  '/api/hr-customercare/staff',
  '/api/hr-customercare/products-catalog',
  '/api/hr-customercare/sales-orders',
  '/api/hr-customercare/customers',
  '/api/payment-tracking/reconciliation',
  '/api/payment-tracking/invoices',
  '/api/payment-tracking/debtors',
  '/api/production-consumables/',
  '/api/production-consumables/low-stock',
  '/api/machines/',
  '/api/machines/dashboard/summary',
  '/api/production-completions/',
];

// ─── State ──────────────────────────────────────────────────────────────────
let _db = null;
let _originalFetch = null;
let _listeners = new Set();
let _isOnline = navigator.onLine;
let _pendingCount = 0;
let _lastFullSync = null;
let _syncing = false;
let _initialized = false;

// ─── IndexedDB ──────────────────────────────────────────────────────────────
async function getDb() {
  if (_db) return _db;
  _db = await openDB(DB_NAME, DB_VERSION, {
    upgrade(db) {
      if (!db.objectStoreNames.contains(STORE_API)) {
        const store = db.createObjectStore(STORE_API, { keyPath: 'url' });
        store.createIndex('timestamp', 'timestamp');
      }
      if (!db.objectStoreNames.contains(STORE_QUEUE)) {
        const queue = db.createObjectStore(STORE_QUEUE, { keyPath: 'id', autoIncrement: true });
        queue.createIndex('timestamp', 'timestamp');
      }
      if (!db.objectStoreNames.contains(STORE_META)) {
        db.createObjectStore(STORE_META, { keyPath: 'key' });
      }
    }
  });
  return _db;
}

// ─── Notify listeners ───────────────────────────────────────────────────────
function _notify() {
  const status = {
    online: _isOnline,
    pendingCount: _pendingCount,
    lastFullSync: _lastFullSync,
    syncing: _syncing,
  };
  _listeners.forEach(fn => {
    try { fn(status); } catch (e) { /* ignore */ }
  });
}

// ─── Cache a GET response in IndexedDB ──────────────────────────────────────
async function cacheResponse(url, data, status) {
  try {
    const db = await getDb();
    await db.put(STORE_API, {
      url,
      data,
      status: status || 200,
      timestamp: Date.now(),
    });
  } catch (e) {
    console.warn('[Offline] Cache write failed:', e);
  }
}

// ─── Read cached response from IndexedDB ────────────────────────────────────
async function getCachedResponse(url) {
  try {
    const db = await getDb();
    return await db.get(STORE_API, url);
  } catch (e) {
    return null;
  }
}

// ─── Build a synthetic Response from cached data ────────────────────────────
function buildCachedResponse(cached) {
  return new Response(JSON.stringify(cached.data), {
    status: cached.status || 200,
    statusText: 'OK (from offline cache)',
    headers: {
      'Content-Type': 'application/json',
      'X-From-Cache': 'true',
      'X-Cache-Time': new Date(cached.timestamp).toISOString(),
    },
  });
}

// ─── Queue a mutation for later sync ────────────────────────────────────────
async function queueMutation(url, options) {
  try {
    const db = await getDb();
    await db.add(STORE_QUEUE, {
      url,
      method: options.method || 'POST',
      headers: Object.fromEntries(
        Object.entries(options.headers || {}).filter(([k]) => k.toLowerCase() !== 'authorization')
      ),
      body: options.body || null,
      timestamp: Date.now(),
      retryCount: 0,
    });
    await _refreshPendingCount();
  } catch (e) {
    console.error('[Offline] Failed to queue mutation:', e);
  }
}

// ─── Count pending mutations ────────────────────────────────────────────────
async function _refreshPendingCount() {
  try {
    const db = await getDb();
    _pendingCount = await db.count(STORE_QUEUE);
  } catch (e) {
    _pendingCount = 0;
  }
  _notify();
}

// ─── Handle GET request (network-first, cache fallback) ─────────────────────
async function handleGet(url, options) {
  if (_isOnline) {
    try {
      const response = await _originalFetch(url, options);
      if (response.ok) {
        // Clone and cache
        const cloned = response.clone();
        try {
          const data = await cloned.json();
          await cacheResponse(url, data, response.status);
        } catch (e) { /* response wasn't JSON, skip caching */ }
      }
      return response;
    } catch (networkError) {
      // Network failed, fall through to cache
      console.warn('[Offline] Network failed for', url, '- checking cache');
    }
  }

  // Return from cache
  const cached = await getCachedResponse(url);
  if (cached) {
    console.log('[Offline] Serving from cache:', url, `(cached ${Math.round((Date.now() - cached.timestamp) / 1000)}s ago)`);
    return buildCachedResponse(cached);
  }

  // Nothing available
  return new Response(JSON.stringify({ error: 'Offline - no cached data', offline: true }), {
    status: 503,
    headers: { 'Content-Type': 'application/json' },
  });
}

// ─── Handle mutation request (online → send, offline → queue) ───────────────
async function handleMutation(url, options) {
  if (_isOnline) {
    try {
      const response = await _originalFetch(url, options);
      return response;
    } catch (networkError) {
      // Network failed while supposedly online; queue it
      console.warn('[Offline] Mutation network error, queuing:', url);
    }
  }

  // Queue for later sync
  await queueMutation(url, options);
  
  return new Response(JSON.stringify({
    queued: true,
    offline: true,
    message: 'Saved offline - will sync when connection is restored',
  }), {
    status: 202,
    statusText: 'Accepted (queued offline)',
    headers: { 'Content-Type': 'application/json', 'X-Queued-Offline': 'true' },
  });
}

// ═══════════════════════════════════════════════════════════════════════════
//  PUBLIC API
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Initialize the offline engine. Call once at app startup.
 * Patches window.fetch to transparently add offline-first behavior for /api/ calls.
 */
export async function initOfflineEngine() {
  if (_initialized) return;
  _initialized = true;

  // Open DB
  await getDb();

  // Save original fetch
  _originalFetch = window.fetch.bind(window);

  // Patch window.fetch
  window.fetch = async function offlineFetch(input, init = {}) {
    const url = typeof input === 'string' ? input : (input instanceof Request ? input.url : String(input));

    // Only intercept /api/ calls on same origin
    const isApiCall = url.startsWith('/api/') ||
      (url.startsWith(window.location.origin) && url.includes('/api/'));

    if (!isApiCall) {
      return _originalFetch(input, init);
    }

    // Normalize URL to path only for caching
    let cachePath = url;
    try {
      if (url.startsWith('http')) {
        cachePath = new URL(url).pathname + new URL(url).search;
      }
    } catch(e) { /* use as-is */ }

    // Skip PDF/binary downloads - don't intercept or cache these
    if (cachePath.endsWith('/pdf') || cachePath.endsWith('.pdf') || cachePath.includes('/pdf?') ||
        cachePath.endsWith('/download') || cachePath.includes('/download?') ||
        cachePath.endsWith('/printout') || cachePath.includes('/printout?')) {
      return _originalFetch(input, init);
    }

    const method = (init.method || 'GET').toUpperCase();

    if (method === 'GET') {
      return handleGet(cachePath, { ...init, method });
    } else {
      return handleMutation(cachePath, { ...init, method });
    }
  };

  // Online/offline listeners
  window.addEventListener('online', () => {
    _isOnline = true;
    _notify();
    // Auto-process queue when coming back online
    setTimeout(() => processMutationQueue(), 1500);
  });
  
  window.addEventListener('offline', () => {
    _isOnline = false;
    _notify();
  });

  // Load initial state
  await _refreshPendingCount();
  
  // Load last sync time
  try {
    const db = await getDb();
    const meta = await db.get(STORE_META, 'lastFullSync');
    if (meta) _lastFullSync = meta.value;
  } catch(e) { /* ignore */ }

  // Periodic queue processing (every 45 seconds when online)
  setInterval(() => {
    if (_isOnline && _pendingCount > 0) {
      processMutationQueue();
    }
  }, 45000);

  console.log('[Offline] Engine initialized. Online:', _isOnline, 'Pending:', _pendingCount);
}

/**
 * Subscribe to offline status changes.
 * @param {Function} callback - Receives { online, pendingCount, lastFullSync, syncing }
 * @returns {Function} unsubscribe function
 */
export function subscribeOffline(callback) {
  _listeners.add(callback);
  // Immediately fire current state
  callback({
    online: _isOnline,
    pendingCount: _pendingCount,
    lastFullSync: _lastFullSync,
    syncing: _syncing,
  });
  return () => _listeners.delete(callback);
}

/**
 * Get current offline status.
 */
export function getOfflineStatus() {
  return {
    online: _isOnline,
    pendingCount: _pendingCount,
    lastFullSync: _lastFullSync,
    syncing: _syncing,
  };
}

/**
 * Process the mutation queue - replay all queued POST/PUT/DELETE operations.
 * Called automatically when coming back online.
 */
export async function processMutationQueue() {
  if (!_isOnline || _syncing) return { success: 0, failed: 0 };
  
  _syncing = true;
  _notify();

  let success = 0;
  let failed = 0;

  try {
    const db = await getDb();
    const allItems = await db.getAll(STORE_QUEUE);

    if (allItems.length === 0) {
      _syncing = false;
      _notify();
      return { success: 0, failed: 0 };
    }

    console.log(`[Offline] Processing ${allItems.length} queued mutations...`);
    
    // Get fresh auth token
    const token = localStorage.getItem('access_token');
    
    for (const item of allItems) {
      try {
        const headers = {
          ...item.headers,
          'Content-Type': 'application/json',
        };
        if (token) headers['Authorization'] = `Bearer ${token}`;

        const response = await _originalFetch(item.url, {
          method: item.method,
          headers,
          body: item.body,
        });

        if (response.ok || response.status === 409) {
          // Success or conflict (duplicate) → remove from queue
          await db.delete(STORE_QUEUE, item.id);
          success++;
        } else if (response.status >= 500) {
          // Server error → retry later
          failed++;
        } else {
          // Client error (4xx) → remove to prevent infinite retry
          console.warn(`[Offline] Mutation failed (${response.status}), removing from queue:`, item.url);
          await db.delete(STORE_QUEUE, item.id);
          failed++;
        }
      } catch (e) {
        // Network error → keep in queue
        failed++;
        console.warn('[Offline] Sync network error for:', item.url, e);
      }
    }

    await _refreshPendingCount();
    console.log(`[Offline] Sync complete: ${success} synced, ${failed} failed, ${_pendingCount} remaining`);
  } catch (e) {
    console.error('[Offline] Queue processing error:', e);
  }

  _syncing = false;
  _notify();
  return { success, failed };
}

/**
 * Pull ALL data from the cloud for full offline availability.
 * Fetches every critical endpoint and caches the response in IndexedDB.
 * Call this on each device to prepare for offline use.
 * @param {Function} onProgress - Optional callback({ done, total, current })
 */
export async function pullFromCloud(onProgress) {
  if (!_isOnline) {
    throw new Error('Cannot pull from cloud while offline');
  }

  _syncing = true;
  _notify();

  const total = CRITICAL_ENDPOINTS.length;
  let done = 0;
  let errors = 0;
  const token = localStorage.getItem('access_token');
  const headers = token ? { 'Authorization': `Bearer ${token}` } : {};

  console.log(`[Offline] Pulling ${total} endpoints from cloud...`);

  // Fetch in batches of 6 to avoid overwhelming the server
  const batchSize = 6;
  for (let i = 0; i < CRITICAL_ENDPOINTS.length; i += batchSize) {
    const batch = CRITICAL_ENDPOINTS.slice(i, i + batchSize);
    
    await Promise.all(batch.map(async (endpoint) => {
      try {
        const response = await _originalFetch(endpoint, { headers });
        if (response.ok) {
          const data = await response.json();
          await cacheResponse(endpoint, data, response.status);
        } else {
          errors++;
        }
      } catch (e) {
        errors++;
        console.warn('[Offline] Failed to pull:', endpoint, e);
      } finally {
        done++;
        if (onProgress) {
          onProgress({ done, total, current: endpoint, errors });
        }
      }
    }));
  }

  // Update last sync time
  try {
    const db = await getDb();
    const now = Date.now();
    await db.put(STORE_META, { key: 'lastFullSync', value: now });
    _lastFullSync = now;
  } catch(e) { /* ignore */ }

  _syncing = false;
  _notify();

  console.log(`[Offline] Pull complete: ${done - errors} cached, ${errors} failed`);
  return { total, cached: done - errors, errors };
}

/**
 * Get the age of cached data for a specific endpoint.
 * @returns {number|null} milliseconds since last cache, or null if not cached
 */
export async function getCacheAge(url) {
  const cached = await getCachedResponse(url);
  return cached ? (Date.now() - cached.timestamp) : null;
}

/**
 * Clear all offline cached data (for troubleshooting).
 */
export async function clearOfflineCache() {
  try {
    const db = await getDb();
    await db.clear(STORE_API);
    await db.clear(STORE_QUEUE);
    _pendingCount = 0;
    _lastFullSync = null;
    _notify();
    console.log('[Offline] Cache cleared');
  } catch(e) {
    console.error('[Offline] Failed to clear cache:', e);
  }
}

/**
 * Get all entries in the mutation queue (for debugging/display).
 */
export async function getMutationQueue() {
  try {
    const db = await getDb();
    return await db.getAll(STORE_QUEUE);
  } catch(e) {
    return [];
  }
}

/**
 * Remove a specific item from the mutation queue.
 */
export async function removeMutationItem(id) {
  try {
    const db = await getDb();
    await db.delete(STORE_QUEUE, id);
    await _refreshPendingCount();
  } catch(e) {
    console.error('[Offline] Failed to remove queue item:', e);
  }
}

export default {
  initOfflineEngine,
  subscribeOffline,
  getOfflineStatus,
  processMutationQueue,
  pullFromCloud,
  getCacheAge,
  clearOfflineCache,
  getMutationQueue,
  removeMutationItem,
};
