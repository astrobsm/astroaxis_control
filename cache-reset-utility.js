// Cache Reset Utility
// Run this in browser console to forcefully clear all caches
// and reload with fresh data

console.log('🧹 Starting cache reset...');

// 1. Clear all browser caches
if ('caches' in window) {
  caches.keys().then(cacheNames => {
    console.log('📦 Found caches:', cacheNames);
    
    const deletePromises = cacheNames.map(cacheName => {
      console.log(`🗑️ Deleting cache: ${cacheName}`);
      return caches.delete(cacheName);
    });
    
    return Promise.all(deletePromises);
  }).then(() => {
    console.log('✅ All caches cleared');
    
    // 2. Unregister service worker
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.getRegistrations().then(registrations => {
        registrations.forEach(registration => {
          console.log('🔄 Unregistering service worker...');
          registration.unregister();
        });
        
        console.log('✅ Service worker unregistered');
        console.log('🔄 Reloading page with fresh content...');
        
        // 3. Hard reload
        window.location.reload(true);
      });
    } else {
      // Just reload if no service worker
      window.location.reload(true);
    }
  }).catch(error => {
    console.error('❌ Error during cache reset:', error);
    // Force reload anyway
    window.location.reload(true);
  });
} else {
  console.log('ℹ️ No cache API available, just reloading...');
  window.location.reload(true);
}