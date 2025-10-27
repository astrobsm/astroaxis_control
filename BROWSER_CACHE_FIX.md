# Browser Cache Fix Guide

## Issue
After updating the application, you may see old content due to browser caching.

## Solution (Choose One Method)

### Method 1: Hard Refresh (Recommended - 10 seconds)
1. Open the application in your browser
2. Press **Ctrl + Shift + R** (Windows/Linux) or **Cmd + Shift + R** (Mac)
3. This forces the browser to reload all assets without cache

### Method 2: Clear Browser Cache (30 seconds)
1. Press **Ctrl + Shift + Delete**
2. Select "Cached images and files"
3. Select "All time" for time range
4. Click "Clear data"
5. Reload the page

### Method 3: Incognito/Private Mode (Testing)
1. Press **Ctrl + Shift + N** (Chrome/Edge) or **Ctrl + Shift + P** (Firefox)
2. Navigate to http://127.0.0.1:8004
3. Test the application
4. If it works, use Method 1 or 2 on your regular browser

### Method 4: Unregister Service Worker (Advanced)
1. Press **F12** to open Developer Tools
2. Go to **Application** tab
3. Click **Service Workers** in left menu
4. Find http://127.0.0.1:8004
5. Click **Unregister**
6. Close Developer Tools
7. Press **Ctrl + Shift + R**

## Verification
After clearing cache, you should see:
- ✅ No admin credentials displayed on login page
- ✅ Latest staff members appear in the list
- ✅ All features working correctly
- ✅ New bundle loaded: `main.d5b46c57.js`

## For Developers
To prevent cache issues during development:

### Disable Cache in Browser
1. Open Developer Tools (F12)
2. Go to **Network** tab
3. Check **Disable cache** checkbox
4. Keep Developer Tools open while developing

### Service Worker Control
Add this to your browser for testing:
```javascript
// In browser console
navigator.serviceWorker.getRegistrations().then(registrations => {
  registrations.forEach(reg => reg.unregister());
  console.log('All service workers unregistered');
});
```

## Prevention for Production
The application automatically handles cache busting through:
- Content hash in filename: `main.[hash].js`
- Service worker updates every 60 seconds
- No-cache headers on service worker file
- Cache-first strategy for static assets only

## Need Help?
If cache issues persist:
1. Check browser console (F12) for errors
2. Verify correct bundle is loaded in Network tab
3. Contact support with browser name and version
