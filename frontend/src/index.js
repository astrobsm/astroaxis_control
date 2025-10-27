import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles.css';

// Global error handlers to prevent console errors
window.addEventListener('unhandledrejection', (event) => {
  // Silently handle unhandled promise rejections from browser extensions
  if (event.reason && event.reason.message && 
      event.reason.message.includes('message channel closed')) {
    event.preventDefault();
  }
});

window.addEventListener('error', (event) => {
  // Handle other potential errors silently if they're from extensions
  if (event.message && event.message.includes('Extension')) {
    event.preventDefault();
  }
});

const container = document.getElementById('root');
const root = createRoot(container);
root.render(<App />);

// Register service worker for PWA functionality
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker
      .register('/serviceWorker.js')
      .then((registration) => {
        console.log('✅ Service Worker registered successfully:', registration.scope);
        
        // Check for updates periodically
        setInterval(() => {
          registration.update();
        }, 60000); // Check every minute
        
        // Listen for updates
        registration.addEventListener('updatefound', () => {
          const newWorker = registration.installing;
          newWorker.addEventListener('statechange', () => {
            if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
              // New service worker available, prompt user to refresh
              if (confirm('New version available! Reload to update?')) {
                newWorker.postMessage({ type: 'SKIP_WAITING' });
                window.location.reload();
              }
            }
          });
        });
      })
      .catch((error) => {
        console.log('❌ Service Worker registration failed:', error);
      });
  });
}
