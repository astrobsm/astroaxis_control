# 🚀 ASTRO-ASIX ERP - Deployment Documentation

## 📋 Table of Contents

1. [Quick Start](#quick-start)
2. [Deployment Guides](#deployment-guides)
3. [Configuration](#configuration)
4. [Troubleshooting](#troubleshooting)
5. [Support](#support)

---

## ⚡ Quick Start

### For Users: Browser Cache Issues
If you don't see the latest updates after deployment:
👉 **[BROWSER_CACHE_FIX.md](./BROWSER_CACHE_FIX.md)** - 4 methods to clear cache (5 minutes)

### For Developers: Local Development
```bash
# Backend
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # Edit with your settings
uvicorn app.main:app --reload --port 8004

# Frontend
cd frontend
npm install
npm start

# Visit http://localhost:3000
```

### For DevOps: Docker Deployment
```bash
# Quick start with Docker Compose
cp backend/.env.example backend/.env  # Edit with production values
docker-compose up -d

# Visit http://localhost
```

---

## 📚 Deployment Guides

### 1️⃣ Staging Environment (Recommended First)
**Platform**: DigitalOcean  
**Cost**: $12-27/month  
**Time**: 2-3 hours  

👉 **[STAGING_DEPLOYMENT_GUIDE.md](./STAGING_DEPLOYMENT_GUIDE.md)**

**What's included:**
- Ubuntu 22.04 droplet setup
- PostgreSQL database configuration
- Nginx reverse proxy with SSL
- Systemd service management
- Monitoring and logging setup
- Security best practices

**Perfect for:**
- Testing deployment process
- Verifying all features work
- Training team members
- Client demos

---

### 2️⃣ Production Deployment (After Staging Success)
**Platform**: DigitalOcean (or any cloud provider)  
**Cost**: $40-79/month  
**Time**: 2-3 hours (with staging experience)

👉 **[PRODUCTION_DEPLOYMENT_CHECKLIST.md](./PRODUCTION_DEPLOYMENT_CHECKLIST.md)**

**What's included:**
- Pre-deployment checklist (80+ items)
- 3-phase deployment plan
- Rollback procedures
- Environment variables template
- Success criteria validation
- Support plan and escalation

**Deployment phases:**
1. **Preparation** (Day 1): Code freeze, final tests, DNS setup
2. **Deployment** (Day 2 Morning): Backend, database, frontend
3. **Go-Live** (Day 2 Evening): Switch to production, monitor

---

### 3️⃣ Docker Deployment (Any Platform)
**Platform**: Any (DigitalOcean, AWS, Azure, GCP, VPS)  
**Time**: 1-2 hours

**Files ready:**
- ✅ `docker-compose.yml` - Multi-container orchestration
- ✅ `backend/Dockerfile` - Backend container (production-optimized)
- ✅ `backend/.dockerignore` - Image size optimization
- ✅ `nginx/nginx.conf` - Reverse proxy with SSL support

**Quick deployment:**
```bash
# 1. Clone repository
git clone https://github.com/astrobsm/astroaxis_control.git
cd astroaxis_control

# 2. Configure environment
cp backend/.env.example backend/.env
nano backend/.env  # Edit with production values

# 3. Build and run
docker-compose up -d

# 4. Check health
docker-compose ps
curl http://localhost/api/health
```

**Production features:**
- Health checks on all services
- Auto-restart on failure
- Non-root user execution (security)
- Optimized layer caching (fast builds)
- Named volumes (data persistence)
- Dedicated network (isolation)

---

## ⚙️ Configuration

### Environment Variables
All configuration is in `.env` file:

```bash
# Copy template
cp backend/.env.example backend/.env

# Required settings
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/axis_db
SECRET_KEY=your-super-secret-key-min-32-characters
DEBUG=False
ENVIRONMENT=production

# Optional (but recommended)
SMTP_HOST=smtp.gmail.com
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
```

👉 **[backend/.env.example](./backend/.env.example)** - Complete template with all options

### Database Setup
```bash
# Create database
psql -U postgres
CREATE DATABASE axis_db;
\q

# Run migrations
cd backend
alembic upgrade head

# Create admin user (optional)
python reset_admin.py
```

### SSL/HTTPS Setup
```bash
# Install Certbot
sudo apt install certbot python3-certbot-nginx

# Get SSL certificate
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com

# Auto-renewal (already configured in guides)
sudo certbot renew --dry-run
```

---

## 🔧 Troubleshooting

### Common Issues

#### 1. "Can't see new features after deployment"
**Solution**: Clear browser cache  
👉 [BROWSER_CACHE_FIX.md](./BROWSER_CACHE_FIX.md)

#### 2. "Database connection failed"
**Checklist:**
- ✅ PostgreSQL running: `sudo systemctl status postgresql`
- ✅ DATABASE_URL correct in `.env`
- ✅ Database exists: `psql -l`
- ✅ User has permissions: `GRANT ALL PRIVILEGES ON DATABASE axis_db TO your_user;`

#### 3. "502 Bad Gateway (Nginx)"
**Checklist:**
- ✅ Backend running: `sudo systemctl status astroasix`
- ✅ Backend port correct: Check nginx.conf (default 8004)
- ✅ Nginx config valid: `sudo nginx -t`
- ✅ Check logs: `sudo journalctl -u astroasix -f`

#### 4. "Docker container keeps restarting"
**Checklist:**
- ✅ Check logs: `docker-compose logs backend`
- ✅ Database ready: `docker-compose ps` (healthy status)
- ✅ Environment variables: `docker-compose config`
- ✅ Health check: `curl http://localhost:8004/api/health`

#### 5. "Service Worker not updating"
**Solution:**
- Unregister old service worker
- Hard refresh (Ctrl+Shift+R)
- Check Service Worker cache policy in nginx.conf

---

## 📊 Monitoring

### Health Checks
```bash
# Backend API
curl http://your-domain.com/api/health

# Database
psql -U postgres -d axis_db -c "SELECT 1"

# Nginx
curl http://your-domain.com/health
```

### Logs
```bash
# Backend logs (systemd)
sudo journalctl -u astroasix -f

# Nginx access logs
sudo tail -f /var/log/nginx/access.log

# Nginx error logs
sudo tail -f /var/log/nginx/error.log

# PostgreSQL logs
sudo tail -f /var/log/postgresql/postgresql-15-main.log

# Docker logs
docker-compose logs -f backend
docker-compose logs -f nginx
```

### Performance
```bash
# Check system resources
htop

# Database connections
psql -U postgres -d axis_db -c "SELECT count(*) FROM pg_stat_activity"

# API response time
curl -w "@curl-format.txt" -o /dev/null -s http://your-domain.com/api/staff/staffs/
```

---

## 🆘 Support

### Getting Help

**1. Check Documentation First:**
- [STAGING_DEPLOYMENT_GUIDE.md](./STAGING_DEPLOYMENT_GUIDE.md) - Staging setup
- [PRODUCTION_DEPLOYMENT_CHECKLIST.md](./PRODUCTION_DEPLOYMENT_CHECKLIST.md) - Production deployment
- [BROWSER_CACHE_FIX.md](./BROWSER_CACHE_FIX.md) - Cache issues
- [README.md](./README.md) - Project overview

**2. Review Logs:**
```bash
# Backend errors
sudo journalctl -u astroasix --since "1 hour ago"

# Nginx errors
sudo tail -100 /var/log/nginx/error.log

# Database errors
sudo tail -100 /var/log/postgresql/postgresql-15-main.log
```

**3. Test Components:**
```bash
# Backend
curl http://localhost:8004/api/health

# Database
psql -U postgres -d axis_db -c "\dt"

# Nginx
sudo nginx -t
```

**4. Contact Team:**
- **Email**: dev@astroasix.com
- **Phone**: +234-803-332-8385
- **GitHub Issues**: https://github.com/astrobsm/astroaxis_control/issues

---

## 📈 Next Steps

### After Successful Deployment

1. **✅ Monitor for 48 Hours**
   - Check logs daily
   - Verify all modules working
   - Test all CRUD operations
   - Monitor performance metrics

2. **✅ Setup Automated Backups**
   - Database: Daily backups at 2 AM
   - Files: Weekly backups
   - Retention: 30 days
   - Test restore procedure

3. **✅ Configure Monitoring Alerts**
   - Email notifications for errors
   - SMS for critical failures
   - Uptime monitoring (UptimeRobot, Pingdom)
   - Performance monitoring (New Relic, Datadog)

4. **✅ User Training**
   - Admin panel walkthrough
   - Staff management demo
   - Attendance tracking tutorial
   - Payroll process training

5. **✅ Documentation Updates**
   - User manual
   - Admin guide
   - API documentation
   - Troubleshooting FAQ

---

## 📝 Change Log

### Version 1.0.0 (Current)
- ✅ Complete ERP system with 13 modules
- ✅ RBAC with 24 permissions
- ✅ Progressive Web App (PWA)
- ✅ Responsive design (mobile-friendly)
- ✅ Production-ready Docker configuration
- ✅ Comprehensive deployment documentation
- ✅ SSL/HTTPS support
- ✅ Health checks and monitoring

### Upcoming Features (v1.1.0)
- [ ] Mobile app (React Native)
- [ ] Advanced analytics dashboard
- [ ] Real-time notifications
- [ ] Multi-currency support
- [ ] Export to Excel/PDF
- [ ] Email automation
- [ ] SMS integration

---

## 🔐 Security

### Production Security Checklist
- ✅ DEBUG=False in production
- ✅ Strong SECRET_KEY (min 32 characters)
- ✅ HTTPS/SSL enabled
- ✅ Database password strength (min 16 characters)
- ✅ UFW firewall configured (ports 80, 443, 22 only)
- ✅ SSH key authentication (password disabled)
- ✅ Regular security updates
- ✅ Fail2ban for brute-force protection
- ✅ Database backups encrypted
- ✅ Environment variables not in code

---

## 📄 License

Copyright © 2024 ASTRO-ASIX. All rights reserved.

---

## 🙏 Credits

**Development Team:**
- Backend: FastAPI + PostgreSQL
- Frontend: React + PWA
- DevOps: Docker + Nginx
- Database: Alembic migrations
- Documentation: Comprehensive guides

**Technologies:**
- Python 3.11
- FastAPI
- React 18
- PostgreSQL 15
- Docker
- Nginx
- Let's Encrypt

---

**📌 Remember:** Always test on staging before deploying to production!

**🎯 Goal:** Zero-downtime deployments with rollback capability.

**✨ Success:** When all modules work flawlessly and users are happy!
