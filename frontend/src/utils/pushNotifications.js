/**
 * ASTRO-ASIX ERP - Push Notification Service
 * Handles Web Push API integration for real-time notifications
 */

// VAPID public key - This should match the private key on your server
// Generated using: vapid --gen && vapid --applicationServerKey
const VAPID_PUBLIC_KEY = 'BCNDdkFgeDx5Z4pds1SBkokEehIKOMXmfw4bn9804AqUM3brbDWac3gaciTfULA2eOKKfKyhPujzC1toVrlQQzY';

/**
 * Convert VAPID public key from URL-safe base64 to Uint8Array
 */
function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding)
    .replace(/-/g, '+')
    .replace(/_/g, '/');
  
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

/**
 * Check if push notifications are supported
 */
export function isPushSupported() {
  return 'serviceWorker' in navigator && 
         'PushManager' in window && 
         'Notification' in window;
}

/**
 * Get current notification permission status
 */
export function getNotificationPermission() {
  if (!('Notification' in window)) {
    return 'unsupported';
  }
  return Notification.permission; // 'granted', 'denied', or 'default'
}

/**
 * Request notification permission from user
 */
export async function requestNotificationPermission() {
  if (!('Notification' in window)) {
    console.warn('Notifications not supported');
    return 'unsupported';
  }
  
  const permission = await Notification.requestPermission();
  return permission;
}

/**
 * Subscribe to push notifications
 */
export async function subscribeToPush() {
  if (!isPushSupported()) {
    throw new Error('Push notifications are not supported in this browser');
  }
  
  const permission = await requestNotificationPermission();
  if (permission !== 'granted') {
    throw new Error('Notification permission denied');
  }
  
  const registration = await navigator.serviceWorker.ready;
  
  // Check for existing subscription
  let subscription = await registration.pushManager.getSubscription();
  
  if (!subscription) {
    // Create new subscription
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY)
    });
  }
  
  // Send subscription to backend
  const token = localStorage.getItem('access_token');
  const response = await fetch('/api/notifications/subscribe', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`
    },
    body: JSON.stringify({
      subscription: subscription.toJSON()
    })
  });
  
  if (!response.ok) {
    throw new Error('Failed to register push subscription on server');
  }
  
  return subscription;
}

/**
 * Unsubscribe from push notifications
 */
export async function unsubscribeFromPush() {
  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.getSubscription();
  
  if (subscription) {
    // Notify server to remove subscription
    const token = localStorage.getItem('access_token');
    await fetch('/api/notifications/unsubscribe', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify({
        endpoint: subscription.endpoint
      })
    });
    
    await subscription.unsubscribe();
  }
  
  return true;
}

/**
 * Check if user is subscribed to push notifications
 */
export async function isSubscribedToPush() {
  if (!isPushSupported()) {
    return false;
  }
  
  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.getSubscription();
  return !!subscription;
}

/**
 * Show a local notification (for immediate in-app notifications)
 */
export function showLocalNotification(title, options = {}) {
  if (Notification.permission !== 'granted') {
    console.warn('Notification permission not granted');
    return;
  }
  
  const defaultOptions = {
    icon: '/logo192.png',
    badge: '/favicon.ico',
    vibrate: [200, 100, 200],
    tag: 'astroaxis-notification',
    renotify: true,
    requireInteraction: false,
    data: {
      url: window.location.origin
    }
  };
  
  navigator.serviceWorker.ready.then(registration => {
    registration.showNotification(title, { ...defaultOptions, ...options });
  });
}

/**
 * Notification categories for the app
 */
export const NotificationTypes = {
  LOW_STOCK: 'low_stock',
  ORDER_UPDATE: 'order_update',
  BIRTHDAY: 'birthday',
  PRODUCTION: 'production',
  PAYMENT: 'payment',
  SYSTEM: 'system'
};

/**
 * Send push notification via server (for testing or manual triggers)
 */
export async function sendTestNotification() {
  const token = localStorage.getItem('access_token');
  const response = await fetch('/api/notifications/test', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`
    }
  });
  
  if (!response.ok) {
    throw new Error('Failed to send test notification');
  }
  
  return response.json();
}
