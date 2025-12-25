/**
 * ASTRO-ASIX ERP - Data Sync Hook
 * Provides real-time cross-device data synchronization
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import {
  getDb,
  saveToOfflineDb,
  getAllFromOfflineDb,
  processPendingSync,
  isOnline,
  setupConnectivityListeners,
  updateSyncMeta,
  getSyncMeta
} from './offlineDb';

// Sync interval in milliseconds (30 seconds)
const SYNC_INTERVAL = 30000;

// Entity to endpoint mapping
const ENTITY_ENDPOINTS = {
  staff: '/api/staff/staffs',
  products: '/api/products/',
  rawMaterials: '/api/raw-materials/',
  customers: '/api/sales/customers',
  warehouses: '/api/warehouses/',
  salesOrders: '/api/sales/orders',
  productionOrders: '/api/production/orders',
  attendance: '/api/attendance/',
  stockLevels: '/api/stock/levels'
};

/**
 * Custom hook for data synchronization across devices
 */
export function useDataSync(onDataUpdate, getAuthHeaders) {
  const [syncStatus, setSyncStatus] = useState('idle'); // 'idle' | 'syncing' | 'error' | 'offline'
  const [lastSyncTime, setLastSyncTime] = useState(null);
  const [pendingChanges, setPendingChanges] = useState(0);
  const [online, setOnline] = useState(navigator.onLine);
  const syncIntervalRef = useRef(null);
  const isMountedRef = useRef(true);

  // Handle online/offline status
  useEffect(() => {
    const cleanup = setupConnectivityListeners(
      () => {
        setOnline(true);
        setSyncStatus('idle');
        // Trigger sync when coming back online
        syncAllData();
      },
      () => {
        setOnline(false);
        setSyncStatus('offline');
      }
    );

    return cleanup;
  }, []);

  // Setup periodic sync
  useEffect(() => {
    isMountedRef.current = true;

    // Initial sync
    syncAllData();

    // Setup interval
    syncIntervalRef.current = setInterval(() => {
      if (isMountedRef.current && online) {
        syncAllData();
      }
    }, SYNC_INTERVAL);

    return () => {
      isMountedRef.current = false;
      if (syncIntervalRef.current) {
        clearInterval(syncIntervalRef.current);
      }
    };
  }, [online]);

  // Listen for service worker sync messages
  useEffect(() => {
    const handleMessage = (event) => {
      if (event.data?.type === 'SYNC_COMPLETE') {
        syncAllData();
      }
    };

    navigator.serviceWorker?.addEventListener('message', handleMessage);
    return () => {
      navigator.serviceWorker?.removeEventListener('message', handleMessage);
    };
  }, []);

  /**
   * Fetch data from API and update offline cache
   */
  const fetchAndCache = useCallback(async (entity, endpoint) => {
    try {
      const headers = getAuthHeaders ? getAuthHeaders() : {};
      const response = await fetch(endpoint, { headers });
      
      if (!response.ok) {
        throw new Error(`Failed to fetch ${entity}`);
      }
      
      let data = await response.json();
      
      // Handle wrapped responses
      if (data && typeof data === 'object' && Array.isArray(data.items)) {
        data = data.items;
      }
      
      if (Array.isArray(data)) {
        await saveToOfflineDb(entity, data);
        await updateSyncMeta(entity, Date.now());
      }
      
      return data;
    } catch (error) {
      console.warn(`Failed to sync ${entity}:`, error);
      // Return cached data on error
      return getAllFromOfflineDb(entity);
    }
  }, [getAuthHeaders]);

  /**
   * Sync all data entities
   */
  const syncAllData = useCallback(async () => {
    if (!online || syncStatus === 'syncing') {
      return;
    }

    setSyncStatus('syncing');

    try {
      // First, process any pending offline changes
      if (getAuthHeaders) {
        const syncResult = await processPendingSync(getAuthHeaders);
        setPendingChanges(syncResult.failed.length);
      }

      // Fetch all entities in parallel
      const syncPromises = Object.entries(ENTITY_ENDPOINTS).map(
        async ([entity, endpoint]) => {
          const data = await fetchAndCache(entity, endpoint);
          return [entity, data];
        }
      );

      const results = await Promise.all(syncPromises);
      const dataMap = Object.fromEntries(results);

      // Notify parent component of updated data
      if (onDataUpdate && isMountedRef.current) {
        onDataUpdate(dataMap);
      }

      setLastSyncTime(new Date());
      setSyncStatus('idle');

    } catch (error) {
      console.error('Sync failed:', error);
      setSyncStatus('error');
    }
  }, [online, syncStatus, fetchAndCache, onDataUpdate, getAuthHeaders]);

  /**
   * Force immediate sync
   */
  const forceSync = useCallback(() => {
    return syncAllData();
  }, [syncAllData]);

  /**
   * Get cached data for offline use
   */
  const getOfflineData = useCallback(async (entity) => {
    return getAllFromOfflineDb(entity);
  }, []);

  /**
   * Check when entity was last synced
   */
  const getLastSync = useCallback(async (entity) => {
    const meta = await getSyncMeta(entity);
    return meta?.lastSync || null;
  }, []);

  return {
    syncStatus,
    lastSyncTime,
    pendingChanges,
    online,
    forceSync,
    getOfflineData,
    getLastSync
  };
}

/**
 * Broadcast channel for cross-tab synchronization
 */
let broadcastChannel = null;

export function setupCrossTabSync(onMessage) {
  if ('BroadcastChannel' in window) {
    broadcastChannel = new BroadcastChannel('astroaxis-sync');
    
    broadcastChannel.onmessage = (event) => {
      if (event.data?.type === 'DATA_UPDATED') {
        onMessage(event.data);
      }
    };

    return () => {
      broadcastChannel?.close();
    };
  }
  return () => {};
}

export function broadcastDataUpdate(entity, action, data) {
  if (broadcastChannel) {
    broadcastChannel.postMessage({
      type: 'DATA_UPDATED',
      entity,
      action,
      data,
      timestamp: Date.now()
    });
  }
}

/**
 * Server-Sent Events for real-time updates
 */
export function createSSEConnection(onEvent, getAuthToken) {
  const token = getAuthToken?.() || localStorage.getItem('access_token');
  
  // Note: SSE typically doesn't support custom headers, 
  // so we'd need to pass token as query param or use a different approach
  const eventSource = new EventSource(`/api/events/stream?token=${token}`);
  
  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      onEvent(data);
    } catch (e) {
      console.warn('Failed to parse SSE message:', e);
    }
  };
  
  eventSource.onerror = (error) => {
    console.warn('SSE connection error:', error);
    // Auto-reconnect is handled by EventSource
  };
  
  return () => {
    eventSource.close();
  };
}

export default useDataSync;
