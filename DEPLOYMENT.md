# ASTRO-ASIX ERP - Unified Deployment Guide with PWA

## 🎉 Unified Application Overview

Your ASTRO-ASIX ERP now runs as a **single unified application** on port 8004 with **Progressive Web App (PWA)** capabilities! The FastAPI backend serves the React frontend, all API endpoints, and PWA assets from one server.

## 🌟 Benefits of Unified Architecture + PWA

- **Single Port**: Everything runs on http://127.0.0.1:8004
- **No CORS Issues**: Frontend and backend are served from same origin
- **Simplified Deployment**: Only one server to manage
- **Better Performance**: Direct file serving, no separate frontend server
- **Offline Support**: PWA works without internet connection
- **App Installation**: Install on desktop, mobile, tablet
- **Auto Updates**: Service worker checks for updates every 60 seconds
- **Production Ready**: Optimized React build with FastAPI static file serving

## 🏃‍♂️ Running the Application

### Option 1: Automated Script (Recommended)
```powershell
.\build-and-serve.ps1
```

### Option 2: Manual Commands
```powershell
# Build frontend
cd frontend
npm run build

# Start unified server (with PWA support)
cd ../backend
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8004
```

## 📱 Access Points

| Service | URL | Description |
|---------|-----|-------------|
| **Main App** | http://127.0.0.1:8004 | React ERP Dashboard (PWA) |
| **API Docs** | http://127.0.0.1:8004/docs | Interactive API Documentation |
| **Health Check** | http://127.0.0.1:8004/api/health | Server Status |
| **Staff API** | http://127.0.0.1:8004/api/staff/* | Staff Management Endpoints |
| **Attendance API** | http://127.0.0.1:8004/api/attendance/* | Attendance Tracking |
| **Payroll PDF** | http://127.0.0.1:8004/api/staff/payslip/{id}/pdf | Download Payslips |
| **PWA Manifest** | http://127.0.0.1:8004/manifest.json | Web App Manifest |
| **Service Worker** | http://127.0.0.1:8004/serviceWorker.js | PWA Service Worker |

## 🛠️ Technical Details

### Static File Serving
- React build files are served from `/frontend/build/`
- CSS/JS assets are served from `/static/`
- All API routes start with `/api/`
- PWA files (manifest.json, serviceWorker.js, icons) served from root

### PWA Architecture
```
Backend (FastAPI on port 8004)
├── Serves API endpoints at /api/*
├── Serves React build from /frontend/build
├── Handles PWA files (manifest.json, serviceWorker.js)
├── Serves static assets (CSS, JS, images, icons)
└── Falls back to index.html for all non-API routes
```

### Routing
- **API Routes**: `/api/*` → FastAPI handlers
- **Frontend Routes**: Everything else → React Router (SPA)
- **Docs**: `/docs` → FastAPI documentation
- **Root**: `/` → React application

### File Structure
```
/backend/app/main.py          # Unified server configuration
/frontend/build/              # Production React build
/frontend/build/static/       # CSS, JS, images
/frontend/build/index.html    # Main React entry point
```

## 🔄 Development Workflow

1. **Frontend Changes**: 
   ```powershell
   cd frontend
   npm run build
   # Server auto-reloads (if using --reload)
   ```

2. **Backend Changes**: 
   - Server auto-reloads with uvicorn --reload

3. **Full Restart**:
   ```powershell
   .\build-and-serve.ps1
   ```

## 🚀 Production Deployment

For production, you can:

1. **Direct Deployment**: Use the unified server as-is
2. **Docker**: Build a single container with both frontend and backend
3. **Reverse Proxy**: Put Nginx in front for SSL and load balancing

## ✅ Complete Feature Set

Your ASTRO-ASIX ERP includes:
- ✅ **Staff Management** with bank details
- ✅ **Attendance Tracking** with clock-in/out
- ✅ **Payroll Calculation** with Nigerian Naira
- ✅ **PDF Payslip Generation**
- ✅ **Professional UI** with modal interfaces
- ✅ **Real-time Updates** and error handling
- ✅ **Production-ready** unified architecture

## 🎯 Next Steps

- Add user authentication
- Implement role-based access control
- Set up automated backups
- Configure SSL certificates
- Add monitoring and logging