# 🚀 DigitalOcean App Platform Deployment Guide

## 📋 Overview

This guide shows you how to deploy ASTRO-ASIX ERP to DigitalOcean App Platform with automatic builds, managed database, and SSL.

**Deployment Time**: 15-20 minutes  
**Monthly Cost**: $17-35/month  
**Includes**: Auto-scaling, SSL, Database, Monitoring

---

## ✅ Prerequisites

1. **DigitalOcean Account** - [Sign up here](https://www.digitalocean.com/) ($200 free credit)
2. **GitHub Repository** - Your code at https://github.com/astrobsm/astroaxis_control
3. **Repository Access** - Make sure it's public OR give DigitalOcean access

---

## 🚀 Quick Deploy (Recommended)

### Method 1: Using App Platform UI

**Step 1: Create New App**
1. Go to https://cloud.digitalocean.com/apps
2. Click **"Create App"**
3. Choose **"GitHub"** as source
4. Select repository: `astrobsm/astroaxis_control`
5. Select branch: `main`
6. Click **"Next"**

**Step 2: Configure Resources**

DigitalOcean should auto-detect your components. If not, configure manually:

**Backend Service:**
- **Name**: `backend`
- **Source Directory**: `backend`
- **Type**: Docker (Dockerfile detected)
- **Dockerfile Path**: `backend/Dockerfile`
- **HTTP Port**: `8004`
- **Health Check Path**: `/api/health`
- **Instance Size**: Basic ($5/month)
- **Instance Count**: 1

**Frontend Static Site:**
- **Name**: `frontend`
- **Source Directory**: `frontend`
- **Type**: Static Site
- **Build Command**: `npm install && npm run build`
- **Output Directory**: `build`
- **Instance Size**: Starter ($3/month)

**Step 3: Add Database**
1. Click **"Add Resource"** → **"Database"**
2. Choose **PostgreSQL 15**
3. Name: `astroasix-db`
4. Plan: **Basic** ($7/month for development, $15/month for production)
5. Click **"Add"**

**Step 4: Configure Environment Variables**

Click on **Backend Service** → **Environment Variables**:

```env
# Required
DATABASE_URL=${astroasix-db.DATABASE_URL}
SECRET_KEY=your-super-secret-key-min-32-characters-change-this
DEBUG=False
ENVIRONMENT=production

# Server
HOST=0.0.0.0
PORT=8004
WORKERS=2

# JWT
JWT_SECRET_KEY=your-jwt-secret-different-from-secret-key
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=7

# Company
COMPANY_NAME=ASTRO-ASIX
COMPANY_CURRENCY=NGN
```

**Important**: Generate strong secrets:
```bash
# On your local machine:
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Step 5: Configure Routes**
- Backend: `/api` → `backend` service
- Frontend: `/` → `frontend` static site

**Step 6: Review & Deploy**
1. Review all settings
2. Click **"Create Resources"**
3. Wait 5-10 minutes for deployment
4. DigitalOcean will:
   - Build backend Docker image
   - Build frontend static files
   - Create PostgreSQL database
   - Setup SSL certificate
   - Deploy everything

---

## 📦 Method 2: Using App Spec YAML

**Step 1: Prepare App Spec**

The repository already has `.do/app.yaml`. Edit if needed:

```bash
# Local machine:
cd C:\Users\USER\ASTROAXIS
code .do\app.yaml
```

**Step 2: Deploy via CLI**

Install DigitalOcean CLI ([doctl](https://docs.digitalocean.com/reference/doctl/how-to/install/)):

```bash
# Install doctl (Windows)
choco install doctl

# Or download from: https://github.com/digitalocean/doctl/releases

# Authenticate
doctl auth init

# Create app from spec
doctl apps create --spec .do/app.yaml

# Get app ID
doctl apps list

# Monitor deployment
doctl apps logs <app-id> --type build
doctl apps logs <app-id> --type deploy
```

---

## 🔧 Configuration Details

### Backend Configuration

**Dockerfile Location**: `backend/Dockerfile`

The Dockerfile is already optimized for production:
- ✅ Non-root user execution
- ✅ Health check endpoint
- ✅ 2 Uvicorn workers
- ✅ Optimized layer caching

**Environment Variables Required:**
```env
DATABASE_URL       # Auto-provided by DigitalOcean
SECRET_KEY         # You must provide
DEBUG=False        # Important!
PORT=8004          # Must match Dockerfile
```

**Health Check:**
- Endpoint: `/api/health`
- Initial Delay: 10 seconds
- Timeout: 3 seconds
- Success Threshold: 1
- Failure Threshold: 3

### Frontend Configuration

**Build Settings:**
- Build Command: `npm install && npm run build`
- Output Directory: `build`
- Node Version: 18.x (default)

**Static Site Configuration:**
- Index Document: `index.html`
- Error Document: `index.html`
- Catchall Document: `index.html` (for React Router)

**Environment Variables (Build Time):**
```env
NODE_ENV=production
REACT_APP_API_URL=https://your-backend-url.ondigitalocean.app
```

### Database Configuration

**PostgreSQL Managed Database:**
- Version: 15
- Plan Options:
  - **Development**: Basic ($7/month) - 1GB RAM, 10GB disk
  - **Production**: Basic ($15/month) - 2GB RAM, 25GB disk
  - **Professional**: Professional ($50/month) - 4GB RAM, 80GB disk

**Connection:**
- DigitalOcean auto-configures `DATABASE_URL`
- SSL enabled by default
- Connection pooling: 25 connections
- Automatic backups: Daily

---

## 🔐 Security Configuration

### 1. Generate Strong Secrets

**SECRET_KEY** (Django/FastAPI):
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Example: xvT9mK3pQ7zR8nL2wS4vH6jB1dF0gC5yA
```

**JWT_SECRET_KEY** (Authentication):
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Example: bN7mP4xK2qW9sT1vH6jL3dC8fG5zA0yR
```

### 2. Environment Variables Security

In DigitalOcean App Platform:
1. Go to **Settings** → **Environment Variables**
2. Mark sensitive vars as **"Secret"** (encrypted)
3. Never commit secrets to Git

### 3. Database Security

- ✅ SSL/TLS enforced
- ✅ Private network access only
- ✅ Firewall rules auto-configured
- ✅ Daily backups enabled

### 4. SSL/HTTPS

- ✅ Free Let's Encrypt SSL certificate
- ✅ Auto-renewal every 90 days
- ✅ HTTP → HTTPS redirect automatic

---

## 🚀 Deployment Process

### What Happens During Deployment:

**1. Build Phase (3-5 minutes):**
- Clone repository from GitHub
- Build backend Docker image
- Install frontend dependencies
- Run frontend build (npm run build)
- Create optimized static files

**2. Database Migration (30 seconds):**
- Run Alembic migrations: `alembic upgrade head`
- Create all tables
- Insert default permissions

**3. Deploy Phase (2-3 minutes):**
- Start backend containers
- Deploy frontend static files to CDN
- Configure load balancer
- Setup SSL certificate
- Health check validation

**4. Post-Deploy:**
- Monitor logs for errors
- Test all endpoints
- Verify database connection

---

## 📊 Monitoring & Logging

### Access Logs

**Via DigitalOcean Console:**
1. Go to your App
2. Click **"Runtime Logs"**
3. Select component (backend/frontend)
4. View real-time logs

**Via CLI:**
```bash
# Backend logs
doctl apps logs <app-id> --type run --component backend

# Build logs
doctl apps logs <app-id> --type build --component backend

# Follow logs
doctl apps logs <app-id> --type run --component backend --follow
```

### Health Checks

**Backend Health:**
```bash
curl https://your-app-name-backend.ondigitalocean.app/api/health
```

**Expected Response:**
```json
{"status": "healthy", "timestamp": "2024-10-27T..."}
```

### Metrics

DigitalOcean provides:
- CPU usage
- Memory usage
- Response time
- Request rate
- Error rate

Access via: **App → Insights**

---

## 🔄 Continuous Deployment

### Automatic Deployments

DigitalOcean auto-deploys when you push to GitHub:

```bash
# On your local machine
cd C:\Users\USER\ASTROAXIS

# Make changes
git add .
git commit -m "Update feature"
git push origin main

# DigitalOcean automatically:
# 1. Detects push
# 2. Builds new version
# 3. Runs health checks
# 4. Deploys if successful
# 5. Rolls back if failed
```

### Manual Deployment

**Via Console:**
1. Go to your App
2. Click **"Actions"** → **"Force Rebuild and Deploy"**

**Via CLI:**
```bash
doctl apps create-deployment <app-id>
```

---

## 🐛 Troubleshooting

### Issue 1: "No components detected"

**Symptoms:**
- DigitalOcean says "No components detected"
- Cannot proceed with deployment

**Solution:**
1. Make sure `.do/app.yaml` is committed to GitHub:
   ```bash
   git add .do/app.yaml
   git commit -m "Add DigitalOcean app spec"
   git push origin main
   ```

2. Use **"Use a specification"** option in DigitalOcean UI
3. Upload `.do/app.yaml` file
4. Or use CLI: `doctl apps create --spec .do/app.yaml`

### Issue 2: "Build failed - Dockerfile not found"

**Solution:**
1. Check source directory is set to `backend`
2. Dockerfile path should be `backend/Dockerfile` (relative to repo root)
3. Verify Dockerfile exists in GitHub

### Issue 3: "Backend health check failed"

**Solution:**
1. Check backend logs: `doctl apps logs <app-id> --type run --component backend`
2. Verify `PORT=8004` in environment variables
3. Check database connection string
4. Ensure migrations ran successfully

### Issue 4: "Frontend shows blank page"

**Solution:**
1. Check if build completed: Look for "Build successful" in logs
2. Verify `build` directory was created
3. Check for JavaScript errors in browser console
4. Ensure API URL is correct in environment variables

### Issue 5: "Database connection refused"

**Solution:**
1. Verify `DATABASE_URL` is set correctly
2. Check database is in same region as app
3. Ensure database is running (check database dashboard)
4. Check connection pool limits

---

## 💰 Cost Breakdown

### Development Environment:
- **Backend**: Basic ($5/month)
- **Frontend**: Starter ($3/month)
- **Database**: Basic ($7/month)
- **Total**: **$15/month**

### Production Environment:
- **Backend**: Professional ($12/month)
- **Frontend**: Starter ($3/month)
- **Database**: Basic ($15/month)
- **Optional**: Professional Database ($50/month)
- **Total**: **$30-62/month**

### Free Credits:
- New users get **$200 credit** (60 days)
- Can run production for **3-6 months free**

---

## 🎯 Post-Deployment Checklist

### ✅ Verify Deployment

1. **Backend Health:**
   ```bash
   curl https://your-app-backend.ondigitalocean.app/api/health
   ```

2. **Frontend Loading:**
   - Visit: `https://your-app.ondigitalocean.app`
   - Should see login page
   - Check browser console for errors

3. **Database Connection:**
   ```bash
   # Check backend logs for database connection success
   doctl apps logs <app-id> --type run --component backend | grep -i "database"
   ```

4. **API Endpoints:**
   ```bash
   # Test staff endpoint
   curl https://your-app-backend.ondigitalocean.app/api/staff/staffs/
   ```

### ✅ Configure Custom Domain (Optional)

1. Go to **Settings** → **Domains**
2. Click **"Add Domain"**
3. Enter your domain: `erp.astroasix.com`
4. Add DNS records:
   ```
   Type: CNAME
   Name: erp
   Value: <your-app>.ondigitalocean.app
   ```
5. SSL auto-configures for custom domain

### ✅ Setup Monitoring Alerts

1. Go to **Settings** → **Alerts**
2. Configure:
   - High CPU usage (>80%)
   - High memory usage (>80%)
   - Failed health checks
   - High error rate (>5%)
3. Add email/Slack notifications

### ✅ Configure Backups

Database backups are automatic, but also:
1. Go to Database → **Backups**
2. Download daily backup
3. Store in secure location
4. Test restore procedure

---

## 🚀 Next Steps After Deployment

1. **Test All Modules:**
   - Staff management
   - Attendance tracking
   - Payroll calculation
   - Inventory management
   - Sales orders
   - Production orders

2. **Create Admin User:**
   - Access backend console
   - Run: `python reset_admin.py`

3. **Configure Email/SMS:**
   - Add SMTP settings
   - Add Twilio credentials
   - Test notifications

4. **User Training:**
   - Share app URL with team
   - Provide login credentials
   - Conduct training session

5. **Monitor Performance:**
   - Check response times
   - Monitor error rates
   - Review logs daily

---

## 📞 Support

### DigitalOcean Support:
- **Docs**: https://docs.digitalocean.com/products/app-platform/
- **Community**: https://www.digitalocean.com/community/
- **Tickets**: support@digitalocean.com

### ASTRO-ASIX Support:
- **Email**: dev@astroasix.com
- **Phone**: +234-803-332-8385
- **GitHub**: https://github.com/astrobsm/astroaxis_control/issues

---

## 📝 Useful Commands

```bash
# List all apps
doctl apps list

# Get app details
doctl apps get <app-id>

# View logs
doctl apps logs <app-id> --type run --follow

# Force redeploy
doctl apps create-deployment <app-id>

# Update app spec
doctl apps update <app-id> --spec .do/app.yaml

# Delete app
doctl apps delete <app-id>

# List databases
doctl databases list

# Get database connection
doctl databases connection <db-id>
```

---

**🎉 You're all set! Your ASTRO-ASIX ERP is now deployed on DigitalOcean with automatic scaling, SSL, and managed database!**
