// ASTRO-ASIX ERP - Enhanced Offline-First Service Worker
// Provides: static asset caching, API response caching in IndexedDB,
// background sync for offline mutations, push notifications

const CACHE_NAME = 'astro-asix-v6.0';
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/manifest.json',
  '/company-logo.png',
  '/logo192.png',
  '/logo512.png',
  '/favicon.ico'
];

// ==========================================
// INSTALL - Cache shell + static assets
// ==========================================
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => {
        console.log('[SW] Caching static shell');
        return cache.addAll(STATIC_ASSETS.map(url =>
          new Request(url, { cache: 'no-cache' })
        )).catch(err => console.warn('[SW] Cache addAll partial failure:', err));
      })
  );
  self.skipWaiting();
});

// ==========================================
// ACTIVATE - Clean old caches
// ==========================================
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names
          .filter(name => name !== CACHE_NAME)
          .map(name => {
            console.log('[SW] Removing old cache:', name);
            return caches.delete(name);
          })
      )
    )
  );
  self.clients.claim();
});

// ==========================================
// FETCH - Offline-first strategy
// ==========================================
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Skip cross-origin requests
  if (url.origin !== self.location.origin) return;

  // Skip PDF and binary file downloads - let them go directly to network
  if (url.pathname.endsWith('/pdf') || url.pathname.endsWith('.pdf') ||
      event.request.headers.get('Accept')?.includes('application/pdf')) {
    return;
  }

  // Navigation (HTML pages)
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request, { cache: 'no-cache' })
        .then((response) => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
          return response;
        })
        .catch(() =>
          caches.match(event.request)
            .then(cached => cached || caches.match('/index.html'))
        )
    );
    return;
  }

  // API calls: GET uses network-first with Cache API fallback
  if (url.pathname.startsWith('/api/')) {
    if (event.request.method === 'GET') {
      event.respondWith(
        fetch(event.request)
          .then((response) => {
            if (response.ok) {
              const clone = response.clone();
              caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
            }
            return response;
          })
          .catch(() =>
            caches.match(event.request).then(cached => {
              if (cached) return cached;
              return new Response(
                JSON.stringify({ error: 'Offline - no cached data', offline: true }),
                { status: 503, headers: { 'Content-Type': 'application/json' } }
              );
            })
          )
      );
    } else {
      // Non-GET: pass through (offlineEngine handles offline queuing)
      event.respondWith(
        fetch(event.request).catch(() =>
          new Response(
            JSON.stringify({ error: 'Offline', offline: true }),
            { status: 503, headers: { 'Content-Type': 'application/json' } }
          )
        )
      );
    }
    return;
  }

  // Static assets: cache-first
  if (url.pathname.startsWith('/static/') ||
      url.pathname.endsWith('.png') ||
      url.pathname.endsWith('.ico') ||
      url.pathname.endsWith('.svg') ||
      url.pathname.endsWith('.woff2')) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) return cached;
        return fetch(event.request).then(response => {
          if (response && response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
          }
          return response;
        });
      })
    );
    return;
  }

  // Everything else: network-first
  event.respondWith(
    fetch(event.request)
      .then(response => {
        if (response && response.status === 200) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
        }
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});

// ==========================================
// BACKGROUND SYNC
// ==========================================
self.addEventListener('sync', (event) => {
  console.log('[SW] Background sync triggered:', event.tag);
  if (event.tag === 'sync-mutations') {
    event.waitUntil(replayMutationQueue());
  }
  if (event.tag === 'sync-pull-data') {
    event.waitUntil(pullCriticalData());
  }
});

async function replayMutationQueue() {
  try {
    const db = await openIDB();
    const tx = db.transaction('mutationQueue', 'readonly');
    const store = tx.objectStore('mutationQueue');
    const items = await idbGetAll(store);
    if (!items.length) return;
    console.log('[SW] Replaying ' + items.length + ' queued mutations');
    for (const item of items) {
      try {
        const response = await fetch(item.url, {
          method: item.method,
          headers: { ...item.headers, 'Content-Type': 'application/json' },
          body: item.body,
        });
        if (response.ok || response.status === 409 || response.status < 500) {
          const delTx = db.transaction('mutationQueue', 'readwrite');
          delTx.objectStore('mutationQueue').delete(item.id);
        }
      } catch (e) {
        console.warn('[SW] Sync item failed:', item.url, e);
      }
    }
    const clients = await self.clients.matchAll();
    clients.forEach(client => {
      client.postMessage({ type: 'SYNC_COMPLETE', source: 'background-sync' });
    });
  } catch (e) {
    console.error('[SW] Background sync error:', e);
  }
}

async function pullCriticalData() {
  const endpoints = [
    '/api/staff/staffs', '/api/products/', '/api/warehouses/',
    '/api/sales/orders', '/api/sales/customers', '/api/stock/levels',
  ];
  const cache = await caches.open(CACHE_NAME);
  for (const endpoint of endpoints) {
    try {
      const response = await fetch(endpoint);
      if (response.ok) cache.put(new Request(endpoint), response.clone());
    } catch (e) { console.warn('[SW] Pull failed:', endpoint); }
  }
}

// ==========================================
// PUSH NOTIFICATIONS
// ==========================================
self.addEventListener('push', (event) => {
  let data = {
    title: 'AstroBSM StockMaster', body: 'You have a new notification',
    icon: '/logo192.png', badge: '/favicon.ico', url: '/', tag: 'default'
  };
  if (event.data) {
    try { const p = event.data.json(); data = { ...data, ...p }; }
    catch (e) { data.body = event.data.text(); }
  }
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body, icon: data.icon, badge: data.badge,
      tag: data.tag, renotify: true, vibrate: [200, 100, 200],
      data: { url: data.url },
      actions: [{ action: 'open', title: 'Open App' }, { action: 'dismiss', title: 'Dismiss' }]
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  if (event.action === 'dismiss') return;
  const urlToOpen = event.notification.data?.url || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      for (const client of list) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          client.navigate(urlToOpen);
          return client.focus();
        }
      }
      return clients.openWindow(urlToOpen);
    })
  );
});

// ==========================================
// PERIODIC SYNC
// ==========================================
self.addEventListener('periodicsync', (event) => {
  if (event.tag === 'refresh-data') event.waitUntil(pullCriticalData());
});

// ==========================================
// MESSAGE HANDLER
// ==========================================
self.addEventListener('message', (event) => {
  if (event.data?.type === 'SKIP_WAITING') self.skipWaiting();
  if (event.data?.type === 'PULL_DATA') event.waitUntil(pullCriticalData());
});

// ==========================================
// IndexedDB helpers (raw API for SW context)
// ==========================================
function openIDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('astroaxis-offline-v2', 1);
    req.onerror = () => reject(req.error);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains('apiResponses'))
        db.createObjectStore('apiResponses', { keyPath: 'url' });
      if (!db.objectStoreNames.contains('mutationQueue')) {
        const q = db.createObjectStore('mutationQueue', { keyPath: 'id', autoIncrement: true });
        q.createIndex('timestamp', 'timestamp');
      }
      if (!db.objectStoreNames.contains('syncMeta'))
        db.createObjectStore('syncMeta', { keyPath: 'key' });
    };
    req.onsuccess = () => resolve(req.result);
  });
}

function idbGetAll(store) {
  return new Promise((resolve, reject) => {
    const req = store.getAll();
    req.onerror = () => reject(req.error);
    req.onsuccess = () => resolve(req.result || []);
  });
}
