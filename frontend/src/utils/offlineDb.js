/**
 * ASTRO-ASIX ERP - Offline Database with IndexedDB
 * Provides cross-device data sync and offline support
 */
import { openDB } from 'idb';

const DB_NAME = 'astroaxis-erp';
const DB_VERSION = 1;

// Initialize IndexedDB
export async function initDatabase() {
  const db = await openDB(DB_NAME, DB_VERSION, {
    upgrade(db, oldVersion, newVersion, transaction) {
      // Create object stores for each data type
      if (!db.objectStoreNames.contains('staff')) {
        const staffStore = db.createObjectStore('staff', { keyPath: 'id' });
        staffStore.createIndex('employee_id', 'employee_id', { unique: true });
      }
      
      if (!db.objectStoreNames.contains('products')) {
        const productStore = db.createObjectStore('products', { keyPath: 'id' });
        productStore.createIndex('sku', 'sku', { unique: true });
        productStore.createIndex('name', 'name');
      }
      
      if (!db.objectStoreNames.contains('rawMaterials')) {
        const rawMaterialStore = db.createObjectStore('rawMaterials', { keyPath: 'id' });
        rawMaterialStore.createIndex('sku', 'sku', { unique: true });
      }
      
      if (!db.objectStoreNames.contains('customers')) {
        const customerStore = db.createObjectStore('customers', { keyPath: 'id' });
        customerStore.createIndex('customer_code', 'customer_code', { unique: true });
      }
      
      if (!db.objectStoreNames.contains('warehouses')) {
        const warehouseStore = db.createObjectStore('warehouses', { keyPath: 'id' });
        warehouseStore.createIndex('code', 'code', { unique: true });
      }
      
      if (!db.objectStoreNames.contains('salesOrders')) {
        const salesStore = db.createObjectStore('salesOrders', { keyPath: 'id' });
        salesStore.createIndex('order_number', 'order_number', { unique: true });
        salesStore.createIndex('customer_id', 'customer_id');
        salesStore.createIndex('status', 'status');
      }
      
      if (!db.objectStoreNames.contains('productionOrders')) {
        db.createObjectStore('productionOrders', { keyPath: 'id' });
      }
      
      if (!db.objectStoreNames.contains('attendance')) {
        const attendanceStore = db.createObjectStore('attendance', { keyPath: 'id' });
        attendanceStore.createIndex('staff_id', 'staff_id');
        attendanceStore.createIndex('clock_in', 'clock_in');
      }
      
      if (!db.objectStoreNames.contains('stockLevels')) {
        const stockStore = db.createObjectStore('stockLevels', { keyPath: 'id' });
        stockStore.createIndex('warehouse_id', 'warehouse_id');
        stockStore.createIndex('product_id', 'product_id');
      }
      
      // Pending sync queue for offline mutations
      if (!db.objectStoreNames.contains('pendingSync')) {
        const syncStore = db.createObjectStore('pendingSync', { keyPath: 'id', autoIncrement: true });
        syncStore.createIndex('entity', 'entity');
        syncStore.createIndex('action', 'action');
        syncStore.createIndex('timestamp', 'timestamp');
      }
      
      // Sync metadata store
      if (!db.objectStoreNames.contains('syncMeta')) {
        db.createObjectStore('syncMeta', { keyPath: 'key' });
      }
    }
  });
  
  return db;
}

// Get database instance (singleton pattern)
let dbInstance = null;
export async function getDb() {
  if (!dbInstance) {
    dbInstance = await initDatabase();
  }
  return dbInstance;
}

/**
 * Save data to IndexedDB for offline access
 */
export async function saveToOfflineDb(storeName, data) {
  const db = await getDb();
  const tx = db.transaction(storeName, 'readwrite');
  const store = tx.objectStore(storeName);
  
  if (Array.isArray(data)) {
    for (const item of data) {
      await store.put(item);
    }
  } else {
    await store.put(data);
  }
  
  await tx.done;
}

/**
 * Get all items from a store
 */
export async function getAllFromOfflineDb(storeName) {
  const db = await getDb();
  return db.getAll(storeName);
}

/**
 * Get single item by ID
 */
export async function getByIdFromOfflineDb(storeName, id) {
  const db = await getDb();
  return db.get(storeName, id);
}

/**
 * Delete item from store
 */
export async function deleteFromOfflineDb(storeName, id) {
  const db = await getDb();
  return db.delete(storeName, id);
}

/**
 * Clear all data from a store
 */
export async function clearOfflineStore(storeName) {
  const db = await getDb();
  const tx = db.transaction(storeName, 'readwrite');
  await tx.objectStore(storeName).clear();
  await tx.done;
}

/**
 * Queue an action for background sync when offline
 */
export async function queueForSync(entity, action, data, endpoint) {
  const db = await getDb();
  const tx = db.transaction('pendingSync', 'readwrite');
  await tx.objectStore('pendingSync').add({
    entity,
    action, // 'create', 'update', 'delete'
    data,
    endpoint,
    timestamp: Date.now(),
    retryCount: 0
  });
  await tx.done;
  
  // Request background sync if supported
  if ('serviceWorker' in navigator && 'sync' in window.registration) {
    try {
      await window.registration.sync.register('sync-pending-data');
    } catch (err) {
      console.warn('Background sync registration failed:', err);
    }
  }
}

/**
 * Get all pending sync items
 */
export async function getPendingSync() {
  const db = await getDb();
  return db.getAll('pendingSync');
}

/**
 * Remove synced item from queue
 */
export async function removeSyncedItem(id) {
  const db = await getDb();
  return db.delete('pendingSync', id);
}

/**
 * Update last sync timestamp for an entity
 */
export async function updateSyncMeta(entity, lastSync) {
  const db = await getDb();
  await db.put('syncMeta', { key: entity, lastSync, syncedAt: Date.now() });
}

/**
 * Get last sync info for an entity
 */
export async function getSyncMeta(entity) {
  const db = await getDb();
  return db.get('syncMeta', entity);
}

/**
 * Process pending sync queue
 */
export async function processPendingSync(getAuthHeaders) {
  const pendingItems = await getPendingSync();
  const results = { success: [], failed: [] };
  
  for (const item of pendingItems) {
    try {
      const headers = {
        'Content-Type': 'application/json',
        ...getAuthHeaders()
      };
      
      let response;
      switch (item.action) {
        case 'create':
          response = await fetch(item.endpoint, {
            method: 'POST',
            headers,
            body: JSON.stringify(item.data)
          });
          break;
        case 'update':
          response = await fetch(item.endpoint, {
            method: 'PUT',
            headers,
            body: JSON.stringify(item.data)
          });
          break;
        case 'delete':
          response = await fetch(item.endpoint, {
            method: 'DELETE',
            headers
          });
          break;
        default:
          continue;
      }
      
      if (response.ok) {
        await removeSyncedItem(item.id);
        results.success.push(item);
      } else {
        results.failed.push({ ...item, error: await response.text() });
      }
    } catch (error) {
      results.failed.push({ ...item, error: error.message });
    }
  }
  
  return results;
}

/**
 * Check if device is online
 */
export function isOnline() {
  return navigator.onLine;
}

/**
 * Setup online/offline listeners
 */
export function setupConnectivityListeners(onOnline, onOffline) {
  window.addEventListener('online', onOnline);
  window.addEventListener('offline', onOffline);
  
  return () => {
    window.removeEventListener('online', onOnline);
    window.removeEventListener('offline', onOffline);
  };
}
